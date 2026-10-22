"""把营养数据编译成角色六维、职业、属性、稀有度、称号与台词。

设计约束：
  1. 六维只由官方营养值决定，不手工调数、不随机。
  2. 缩放用幂函数压缩极端值，让 400kcal 的汉堡和 2100kcal 的双人餐都有合理数值。
  3. 职业按"营养占比在同批卡里的分位"决定 —— 单位不同（克/毫克）不可直接比，
     必须先各自排分位，再看哪一维最突出。
  4. 元素看主食：套餐的属性继承它的主食。
  5. 稀有度按战力分位，SSR 约占 12%。

输出 data/roster.json
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

STAT_MAP = {
    "kcal": ("ATK", "能量"),
    "protein": ("DEF", "蛋白质"),
    "fat": ("HP", "脂肪"),
    "carb": ("SPD", "碳水"),
    "sodium": ("CRT", "钠"),
    "calcium": ("HEAL", "钙"),
}

WEIGHTS = {"ATK": 1.0, "DEF": 1.6, "HP": 0.8, "SPD": 0.6, "CRT": 0.5, "HEAL": 0.4}

# 单品的固定属性（经典款，人工指定，避免"巨无霸因为配了麦乐鸡变成风"）
ITEM_ELEMENT = {
    "巨无霸": "炎", "吉士汉堡包": "炎", "双层吉士汉堡": "炎", "汉堡包": "炎",
    "培根蔬萃双层牛堡": "炎", "不素之霸双层牛堡": "炎",
    "培根安格斯厚牛堡": "炎", "芝士安格斯厚牛堡": "炎",
    "芝士双层安格斯厚牛堡": "炎", "培根双层安格斯厚牛堡": "炎",
    "麦辣鸡腿汉堡": "风", "板烧鸡腿堡": "风", "麦香鸡": "风", "双层脆鸡堡": "风",
    "麦香鱼": "水", "双层深海鳕鱼堡": "水", "儿童鱼排堡": "水",
}

ELEMENTS = [
    ("炎", ("牛", "安格斯", "培根", "吉士")),
    ("风", ("鸡", "翅", "麦乐鸡", "脆汁鸡", "鸡排", "鸡球")),
    ("水", ("鱼", "鳕", "虾", "纯悦")),
    ("草", ("薯", "玉米", "蔬", "生菜", "苹果", "沙拉", "笋")),
    ("冰", ("冰淇淋", "新地", "麦旋风", "奶昔", "雪冰", "圆筒", "阿芙佳朵", "麦旋酷")),
    ("暗", ("咖啡", "美式", "奶铁", "红茶", "卡布奇诺", "伯爵", "黑巧", "抹茶")),
    ("光", ("派", "拉明顿", "蛋糕", "甜")),
]
DEFAULT_ELEMENT = "雷"

ELEMENT_COLOR = {
    "炎": "#D85A30", "风": "#1D9E75", "水": "#378ADD", "草": "#639922",
    "冰": "#5DCAA5", "暗": "#534AB7", "光": "#BA7517", "雷": "#D4537E",
}

ELEM_PREFIX = {
    "炎": "炽焰", "风": "疾风", "水": "碧涛", "草": "苍翠",
    "冰": "霜华", "暗": "幽夜", "光": "曦光", "雷": "惊雷",
}
JOB_SUFFIX = {
    "战士": "斗士", "坦克": "壁垒", "法师": "术士",
    "刺客": "影刃", "牧师": "祭司", "狂战": "狂徒", "灵体": "残影",
}

# 营养维度 -> 职业
DIM_JOB = {
    "protein": "战士",
    "fat": "坦克",
    "carb": "法师",
    "sodium": "刺客",
    "calcium": "牧师",
}

SKILL_PATTERNS = {
    "战士": ["{n}·正面突破", "{n}·铁壁推进", "{n}·饱和打击"],
    "坦克": ["{n}·不动如山", "{n}·热量壁垒", "{n}·厚甲展开"],
    "法师": ["{n}·碳水洪流", "{n}·糖分灌注", "{n}·能量过载"],
    "刺客": ["{n}·钠盐突刺", "{n}·咸度暴击", "{n}·致命一口"],
    "牧师": ["{n}·钙质庇护", "{n}·奶香治愈", "{n}·骨密度祝福"],
    "狂战": ["{n}·热量狂潮", "{n}·无差别碾压", "{n}·饱腹暴走"],
    "灵体": ["{n}·虚空一饮", "{n}·零卡幻影"],
}

FLAVOR = {
    "ATK": "热量即力量，一口就是一次重击。",
    "DEF": "每一克蛋白质，都是一层护甲。",
    "HP": "脂肪不是负担，是血条。",
    "SPD": "碳水灌注，速度拉满。",
    "CRT": "咸度决定暴击率，懂？",
    "HEAL": "钙质庇护，骨骼强化。",
}

RARITY_ORDER = ["N", "R", "SR", "SSR"]


def scale(value: float, vmax: float, power: float = 0.55) -> int:
    if vmax <= 0 or value <= 0:
        return 1
    return max(1, min(999, round(999 * (value / vmax) ** power)))


def percentile_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for pos, i in enumerate(order):
        ranks[i] = (pos + 0.5) / len(values)
    return ranks


def resolve_element(name: str, components: list[dict]) -> str:
    """元素看主食：套餐继承第一个组成单品的属性。"""
    if name in ITEM_ELEMENT:
        return ITEM_ELEMENT[name]
    if components:
        main = components[0]["name"]
        if main in ITEM_ELEMENT:
            return ITEM_ELEMENT[main]
        for elem, keys in ELEMENTS:
            if any(k in main for k in keys):
                return elem
    for elem, keys in ELEMENTS:
        if any(k in name for k in keys):
            return elem
    return DEFAULT_ELEMENT


def clean_short(name: str, n: int = 6) -> str:
    s = name[:n].rstrip("（(·-— 、")
    return s or name[:n]


def main() -> None:
    src = json.loads((DATA / "roster_raw.json").read_text(encoding="utf-8"))
    meals = [m for m in src["meals"] if m["nutritionComplete"]]
    meals = [m for m in meals if not m["name"].endswith("酱")]

    vmax = {f: max(m["nutrition"][f] for m in meals) for f in STAT_MAP}

    # 先算全体的营养占比，再做分位，最后定职业
    ratios = {d: [] for d in DIM_JOB}
    for m in meals:
        raw = m["nutrition"]
        kcal = raw["kcal"] if raw["kcal"] > 0 else 1
        for d in DIM_JOB:
            ratios[d].append(raw[d] / kcal)
    pct = {d: percentile_ranks(ratios[d]) for d in DIM_JOB}

    cards = []
    for i, m in enumerate(meals):
        raw = m["nutrition"]
        stats = {STAT_MAP[f][0]: scale(raw[f], vmax[f]) for f in STAT_MAP}
        power = round(sum(stats[k] * w for k, w in WEIGHTS.items()))
        elem = resolve_element(m["name"], m["components"])

        if raw["kcal"] < 20:  # 水、黑咖啡这类接近零营养的，单列灵体
            job = "灵体"
        else:
            job = max(DIM_JOB.values(), key=lambda j: 0)  # 占位，下面覆盖
            dim = max(DIM_JOB, key=lambda d: pct[d][i])
            job = DIM_JOB[dim]

        cards.append(
            {
                "code": m["code"],
                "name": m["name"],
                "category": m["category"],
                "image": m["image"],
                "price": m["price"],
                "isCombo": m["isCombo"],
                "tags": m["tags"],
                "element": elem,
                "elementColor": ELEMENT_COLOR[elem],
                "job": job,
                "title": ELEM_PREFIX[elem] + JOB_SUFFIX[job],
                "stats": stats,
                "power": power,
                "nutrition": raw,
                "components": m["components"],
                "flavor": FLAVOR[max(stats.items(), key=lambda kv: kv[1])[0]],
            }
        )

    cards.sort(key=lambda c: -c["power"])
    n = len(cards)
    for i, c in enumerate(cards):
        p = (i + 1) / n
        c["rarity"] = "SSR" if p <= 0.12 else "SR" if p <= 0.32 else "R" if p <= 0.62 else "N"
        c["rank"] = i + 1

    seen: dict[str, int] = {}
    for c in cards:
        pool = SKILL_PATTERNS[c["job"]]
        k = seen.get(c["job"], 0)
        seen[c["job"]] = k + 1
        c["skill"] = pool[k % len(pool)].format(n=clean_short(c["name"]))

    for c in cards:
        c["powerPerYuan"] = round(c["power"] / c["price"], 1) if c["price"] else 0

    by_rarity = {r: sum(1 for c in cards if c["rarity"] == r) for r in RARITY_ORDER}
    by_job: dict[str, int] = {}
    by_elem: dict[str, int] = {}
    for c in cards:
        by_job[c["job"]] = by_job.get(c["job"], 0) + 1
        by_elem[c["element"]] = by_elem.get(c["element"], 0) + 1

    out = {
        "source": src["sources"],
        "store": src["store"],
        "note": "六维由官方营养值幂函数归一化得出，非人工设定；职业按营养占比分位、元素按主食成分推导。",
        "statFormula": {
            "ATK": "kcal", "DEF": "protein", "HP": "fat",
            "SPD": "carb", "CRT": "sodium", "HEAL": "calcium",
            "scale": "round(999 * (v / max)^0.55)",
            "power": "ATK*1.0 + DEF*1.6 + HP*0.8 + SPD*0.6 + CRT*0.5 + HEAL*0.4",
        },
        "count": len(cards),
        "byRarity": by_rarity,
        "byJob": by_job,
        "byElement": by_elem,
        "cards": cards,
    }
    (DATA / "roster.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print(f"生成 {len(cards)} 张卡：{by_rarity}")
    print(f"职业分布：{by_job}")
    print(f"元素分布：{by_elem}")
    print()
    print("--- SSR ---")
    for c in cards[: max(1, int(n * 0.12))]:
        s = c["stats"]
        print(
            f"  #{c['rank']:<2} {c['name'][:13]:<15} {c['element']}·{c['job']:<3} {c['title']:<5}"
            f"战力{c['power']:<5} ATK{s['ATK']:<4} DEF{s['DEF']:<4} HP{s['HP']:<4} "
            f"SPD{s['SPD']:<4} CRT{s['CRT']:<4} HEAL{s['HEAL']:<4} ¥{c['price']}"
        )
    print()
    print("--- 各职业代表 ---")
    for job in JOB_SUFFIX:
        pool = [c for c in cards if c["job"] == job]
        if not pool:
            continue
        c = pool[0]
        print(f"  {job:<3} {c['name'][:13]:<15} {c['element']}·{c['title']:<5} 战力{c['power']:<5} 「{c['skill']}」")
    print()
    print("--- 性价比榜 ---")
    for c in sorted(cards, key=lambda x: -x["powerPerYuan"])[:6]:
        print(f"  {c['name'][:14]:<16} {c['rarity']:<3} 战力{c['power']:<5} ¥{c['price']:<6} 每元{c['powerPerYuan']}")


if __name__ == "__main__":
    main()
