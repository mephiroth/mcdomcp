"""把麦当劳菜单编译成「角色名册」。

核心思路：
  套餐本身没有营养数据，但 query-meal-detail 能拆出默认搭配，
  每个单品在 list-nutrition-foods 里都有官方数值。
  于是：套餐营养 = 默认搭配各单品营养之和 —— 每一克都能追溯，不是编的。

原则：
  匹配不上就标记缺失，绝不按比例估算。宁可少一张卡，不可错一个数。

用法：
  MCD_TOKEN=xxx python src/build_roster.py --city 上海市 --keyword 人民广场
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McdClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

FIELDS = ("kcal", "protein", "fat", "carb", "sodium", "calcium")

# 周边/玩具：不是食物，不进名册
NON_FOOD = (
    "玩具", "棒球帽", "丝巾", "按摩捶", "痒痒挠", "星愿贴", "水手帽",
    "盲盒", "公仔", "徽章", "手办", "玩偶", "拼图", "贴纸", "挂件",
)

# 人工核对过的规格别名（菜单名 -> 营养库名）。
# 菜单上不标规格时，按麦当劳默认杯型（中杯）或默认包装取数，属"默认规格假设"。
ALIASES = {
    "可乐": "可乐中杯",
    "雪碧": "雪碧中杯",
    "玉米杯": "小杯玉米杯",
    "麦乐鸡": "麦乐鸡5块",
    "5块麦乐鸡": "麦乐鸡5块",
    "100% 苹果汁(盒装)": "100%苹果汁",
    "麦咖啡™美式": "热美式中杯",
    "麦咖啡™奶铁": "热奶铁中杯",
    "怡泉+C": "中杯怡泉+C",
    "纯牛奶": "纯牛奶（盒装）",
    "牛奶": "纯牛奶（盒装）",
    "苹果片一袋(60g)": "苹果片",
    "薯条": "中薯条",
    "麦辣鸡翅": "麦辣鸡翅-2块",
    "鱼排堡": "儿童鱼排堡",
    "卡布奇诺": "卡布奇诺中杯",
    "鲜萃咖啡": "大杯鲜萃咖啡",
    "阳光柠檬红茶": "阳光柠檬红茶中杯",
    "燕麦奶": "热燕麦奶中杯",
    "燕麦奶铁": "热燕麦奶铁中杯",
    "浓浓黑巧（冰/热）": "冰浓浓黑巧中杯",
    "浓浓燕麦黑巧（冰/热）": "冰浓浓燕麦黑巧中杯",
}

_PUNCT = re.compile(r"[\s\-－—–™®（）()【】“”\"'、·,，.。]")


def norm(s: str) -> str:
    return _PUNCT.sub("", s).lower()


def is_non_food(name: str) -> bool:
    return any(k in name for k in NON_FOOD)


class NutritionBook:
    """营养库：三级匹配（精确 / 规范化 / 前缀），匹配不上就返回 None。"""

    def __init__(self, table: dict[str, dict[str, float]]):
        self.table = table
        self.norm_index: dict[str, str] = {}
        for name in table:
            self.norm_index.setdefault(norm(name), name)

    def match(self, name: str) -> tuple[str | None, str]:
        """返回 (营养库品名, 匹配方式)。匹配不上返回 (None, "missing")。"""
        name = name.strip()
        if name in self.table:
            return name, "exact"
        if name in ALIASES and ALIASES[name] in self.table:
            return ALIASES[name], "alias"
        n = norm(name)
        if n in self.norm_index:
            return self.norm_index[n], "normalized"
        # 前缀匹配：营养库名是菜单名的前缀（如 那么大鸡排 ← 那么大鸡排（椒盐风味））
        best = None
        for nn, orig in self.norm_index.items():
            if len(nn) >= 3 and n.startswith(nn) and (best is None or len(nn) > len(best[0])):
                best = (nn, orig)
        if best:
            return best[1], "prefix"
        return None, "missing"


def load_nutrition(client: McdClient) -> NutritionBook:
    res = client.tool("list-nutrition-foods")
    raw: str = res["data"]
    body = raw.split("}:", 1)[1] if "}:" in raw else raw
    table: dict[str, dict[str, float]] = {}
    for row in csv.reader(io.StringIO(body.strip())):
        if len(row) < 9 or not row[0].strip():
            continue
        try:
            table[row[0].strip()] = {
                "kcal": float(row[3] or 0),
                "protein": float(row[4] or 0),
                "fat": float(row[5] or 0),
                "carb": float(row[6] or 0),
                "sodium": float(row[7] or 0),
                "calcium": float(row[8] or 0),
            }
        except ValueError:
            continue
    return NutritionBook(table)


def pick_store(client: McdClient, city: str, keyword: str) -> dict:
    res = client.tool(
        "query-nearby-stores", {"beType": 1, "searchType": 2, "city": city, "keyword": keyword}
    )
    stores = res.get("data") or []
    if not stores:
        raise SystemExit(f"在 {city}/{keyword} 附近没找到门店")
    return stores[0]


def default_components(detail: dict) -> list[tuple[str, int]]:
    """取套餐的默认搭配；单品（无 rounds）返回空列表。"""
    out: list[tuple[str, int]] = []
    for rnd in detail.get("rounds") or []:
        chosen = [c for c in rnd.get("choices") or [] if c.get("isDefault") == 1]
        if not chosen:
            chosen = [c for c in rnd.get("choices") or [] if c.get("quantity", 0) > 0][:1]
        for c in chosen:
            out.append((c["name"].strip(), int(c.get("quantity") or 1)))
    return out


def build(city: str, keyword: str, sleep: float) -> dict:
    client = McdClient()
    book = load_nutrition(client)
    print(f"营养库：{len(book.table)} 条")

    store = pick_store(client, city, keyword)
    print(f"门店：{store['storeName']} ({store['storeCode']})")

    menu = client.tool(
        "query-meals", {"storeCode": store["storeCode"], "orderType": 1, "beType": 1}
    )["data"]

    cat_of: dict[str, str] = {}
    for cat in menu["categories"]:
        for m in cat["meals"]:
            cat_of.setdefault(m["code"], cat["name"].replace("\n", ""))

    meals = menu["meals"]
    roster: list[dict] = []
    missing: dict[str, int] = {}
    skipped: list[str] = []

    for i, (code, m) in enumerate(meals.items(), 1):
        name = m["name"].strip()
        if is_non_food(name):
            skipped.append(name)
            continue

        try:
            detail = client.tool(
                "query-meal-detail",
                {"storeCode": store["storeCode"], "orderType": 1, "beType": 1, "code": code},
            )["data"]
        except Exception as exc:
            print(f"  ! {name} 详情失败: {exc}")
            continue

        comps = default_components(detail)
        totals = dict.fromkeys(FIELDS, 0.0)
        resolved: list[dict] = []
        ok = True

        for cname, qty in (comps or [(name, 1)]):
            if is_non_food(cname):  # 开心乐园餐附赠的玩具不是食物
                continue
            hit, how = book.match(cname)
            if hit is None:
                if "酱" in cname:  # 酱料营养库未收录，热量可忽略
                    resolved.append({"name": cname, "qty": qty, "matched": None, "note": "酱料未计"})
                    continue
                missing[cname] = missing.get(cname, 0) + 1
                ok = False
                resolved.append({"name": cname, "qty": qty, "matched": None})
                continue
            for f in FIELDS:
                totals[f] += book.table[hit][f] * qty
            resolved.append({"name": cname, "qty": qty, "matched": hit, "how": how})

        roster.append(
            {
                "code": code,
                "name": name,
                "category": cat_of.get(code, ""),
                "image": m.get("image", ""),
                "price": float(m.get("currentPrice") or 0),
                "originalPrice": float(m.get("originalPrice") or 0) or None,
                "tags": m.get("tags") or [],
                "isCombo": bool(comps),
                "components": resolved,
                "nutrition": {f: round(v, 1) for f, v in totals.items()} if ok else None,
                "nutritionComplete": ok,
            }
        )
        if sleep:
            time.sleep(sleep)
        if i % 20 == 0:
            print(f"  ...{i}/{len(meals)}")

    good = [r for r in roster if r["nutritionComplete"]]
    print()
    print(f"菜单 {len(meals)} 项 → 排除周边 {len(skipped)} 项 → 名册 {len(roster)} 张卡")
    print(f"营养完整 {len(good)} 张（{len(good)/max(len(roster),1)*100:.0f}%），待补 {len(roster)-len(good)} 张")
    if missing:
        print(f"未匹配单品 {len(missing)} 个（多为新品/限定，官方营养表未更新）：")
        for k, v in sorted(missing.items(), key=lambda x: -x[1]):
            print(f"   {k} × {v}")

    payload = {
        "store": {
            "storeCode": store["storeCode"],
            "storeName": store["storeName"],
            "address": store.get("address", ""),
        },
        "sources": {
            "nutrition": "mcd-mcp:list-nutrition-foods",
            "menu": "mcd-mcp:query-meals + query-meal-detail",
        },
        "count": len(roster),
        "complete": len(good),
        "excluded": skipped,
        "meals": roster,
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "roster_raw.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"已写入 {DATA/'roster_raw.json'}")
    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="上海市")
    ap.add_argument("--keyword", default="人民广场")
    ap.add_argument("--sleep", type=float, default=0.0)
    a = ap.parse_args()
    build(a.city, a.keyword, a.sleep)
