"""卡面立绘生成：调用 flux-klein3 图生图接口，把官方产品图转成二次元 Q 版角色。

设计原则
--------
1. 参考图用官方产品图 —— 保证角色配色/元素与真实餐品一致，不是凭空捏造
2. 提示词由四层参数决定：元素(发色/光环) × 职业(武器/服装) × 品名(发饰) × 编号哈希(发型/表情/性别)
3. 出图后去白底 → 裁切内容 → 补边成正方形 → 压到 512px，保证卡面框内构图一致
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
ART = DOCS / "art"
CACHE = ROOT / ".cache" / "ref"
OUT_SIZE = 448  # 卡面显示约 200px，448 足够 2x 清晰度，且能控制仓库体积

API_URL = "https://9x6mepfgp4t9wnuh-8263.container.x-gpu.com/v1/images/edits"
API_KEY = os.environ.get("CV_API_KEY", "")

# ---------------------------------------------------------------- 元素调色
# 每个元素给 3 组配色，用编号哈希挑一组，避免同元素 29 张卡全是一个发色
ELEMENT_STYLE = {
    "炎": {
        "hair": ["crimson red", "vivid orange", "coral pink"],
        "eyes": ["amber gold", "crimson red", "sunset orange"],
        "accent": "flame red and ember orange",
        "aura": "small flame sparks",
    },
    "风": {
        "hair": ["emerald green", "mint green", "teal green"],
        "eyes": ["jade green", "spring green", "aqua green"],
        "accent": "emerald green and leaf accents",
        "aura": "floating leaf particles",
    },
    "水": {
        "hair": ["azure blue", "deep sea blue", "sky blue"],
        "eyes": ["ocean blue", "cyan blue", "sapphire blue"],
        "accent": "azure blue and water ripples",
        "aura": "water droplet sparkles",
    },
    "草": {
        "hair": ["olive green", "lime green", "moss green"],
        "eyes": ["grass green", "lime green", "olive green"],
        "accent": "lime green and vine patterns",
        "aura": "tiny sprout buds",
    },
    "冰": {
        "hair": ["icy pale blue", "silver white blue", "frost cyan"],
        "eyes": ["ice blue", "pale cyan", "glacier blue"],
        "accent": "frost blue and crystal shards",
        "aura": "snowflake sparkles",
    },
    "雷": {
        "hair": ["violet purple", "electric indigo", "platinum blonde"],
        "eyes": ["violet purple", "electric blue", "golden yellow"],
        "accent": "violet purple and lightning motifs",
        "aura": "small lightning arcs",
    },
    "暗": {
        "hair": ["black with purple sheen", "dark violet", "charcoal black"],
        "eyes": ["crimson red", "deep violet", "golden yellow"],
        "accent": "deep violet and black with red trim",
        "aura": "dark shadow wisps",
    },
    "光": {
        "hair": ["platinum blonde", "golden blonde", "silver white"],
        "eyes": ["golden yellow", "sky blue", "amber gold"],
        "accent": "white and gold with holy light",
        "aura": "soft golden halo glow",
    },
}

# ---------------------------------------------------------------- 职业设定
JOB_STYLE = {
    "战士": {
        "weapon": "holding a large broadsword with a glowing blade",
        "outfit": "light knight armor over a short jacket, shoulder pauldrons",
        "pose": "heroic stance with the sword raised",
    },
    "坦克": {
        "weapon": "holding a huge round shield in front of the body",
        "outfit": "heavy plated armor with thick shoulder guards and gauntlets",
        "pose": "defensive stance, feet planted wide",
    },
    "法师": {
        "weapon": "holding a tall wooden staff topped with a glowing orb",
        "outfit": "flowing robe with a short cape and wide sleeves",
        "pose": "one hand raised casting a spell",
    },
    "刺客": {
        "weapon": "holding two curved daggers, one in each hand",
        "outfit": "slim hooded cloak over a fitted bodysuit, scarf",
        "pose": "low agile crouching stance, ready to dash",
    },
    "牧师": {
        "weapon": "holding a golden healing staff with a cross-shaped top",
        "outfit": "white and gold cleric robe with a small capelet",
        "pose": "gentle praying gesture, staff held upright",
    },
    "灵体": {
        "weapon": "no weapon, hands glowing faintly",
        "outfit": "translucent floating robe with wispy hem",
        "pose": "floating slightly above the ground",
    },
}

# ---------------------------------------------------------------- 品名 → 发饰
# 发饰是"拟人"的识别锚点：看到头顶的汉堡，才知道这是巨无霸而不是随便一个红发角色。
# 先精确匹配（巨无霸不含"堡"字，必须显式列出），再走关键词兜底。
MOTIF_EXACT = {
    "巨无霸": "a miniature double-decker hamburger",
    "巨无霸四件套": "a miniature double-decker hamburger",
    "巨无霸三件套": "a miniature double-decker hamburger",
    "双层吉士汉堡": "a miniature double cheeseburger",
    "吉士汉堡包套餐": "a miniature cheeseburger",
    "麦辣鸡腿汉堡": "a miniature spicy chicken burger",
    "麦辣鸡腿汉堡三件套": "a miniature spicy chicken burger",
    "麦辣鸡腿堡四件套": "a miniature spicy chicken burger",
    "板烧鸡腿堡": "a miniature grilled chicken burger",
    "板烧鸡腿堡三件套": "a miniature grilled chicken burger",
    "板烧鸡腿堡四件套": "a miniature grilled chicken burger",
    "麦香鸡": "a miniature fried chicken sandwich",
    "麦香鸡套餐": "a miniature fried chicken sandwich",
    "麦香鱼": "a miniature fish burger",
    "麦香鱼套餐": "a miniature fish burger",
    "鱼排堡开心乐园餐": "a miniature fish burger",
    "培根安格斯厚牛堡": "a miniature angus beef burger",
    "芝士安格斯厚牛堡": "a miniature angus beef burger with cheese",
    "不素之霸双层牛堡": "a miniature double beef burger",
    "培根蔬萃双层牛堡": "a miniature beef burger with lettuce",
    "鱼牛汉堡四件套随心选": "a miniature fish and beef burger",
    "牛肉堡单品精选": "a miniature beef burger",
    "那么大鸡排（椒盐风味）": "a miniature fried chicken cutlet",
    "蘸酱麦麦脆汁鸡": "a miniature fried chicken drumstick",
    "蘸酱炸鸡": "a miniature fried chicken drumstick",
    "麦麦脆汁鸡1块": "a miniature fried chicken drumstick",
    "薄皮V翅双拼十翅": "a mini bucket of fried chicken wings",
    "薄皮焦香V翅": "a mini bucket of fried chicken wings",
    "麦辣鸡翅": "a mini bucket of spicy chicken wings",
    "麦乐鸡": "a mini box of chicken nuggets",
    "麦乐鸡套餐": "a mini box of chicken nuggets",
    "经典麦旋风": "a miniature ice cream sundae cup",
    "新地": "a miniature ice cream sundae cup",
    "圆筒冰淇淋": "a miniature ice cream cone",
    "咖啡阿芙佳朵": "a miniature ice cream coffee cup",
    "抹茶阿芙佳朵": "a miniature ice cream coffee cup",
    "薯条": "a mini red box of french fries",
    "玉米杯": "a small corn cup",
    "苹果片": "a tiny apple slice",
    "100% 苹果汁(盒装)": "a tiny apple juice box",
    "派": "a miniature pie",
    "纯悦": "a mini water bottle",
    "纯牛奶(盒装)": "a mini milk carton",
    "牛奶": "a mini milk carton",
    "可乐": "a mini cola cup",
    "雪碧": "a mini soda cup",
    "怡泉+C": "a mini soda cup",
    "【美汁源】“黄金橙橙”": "a mini orange juice cup",
    "阳光柠檬红茶": "a mini iced tea cup",
    "锡兰红茶": "a mini tea cup",
    "麦咖啡™奶铁": "a mini latte cup",
    "燕麦奶铁": "a mini latte cup",
    "卡布奇诺": "a mini cappuccino cup",
    "麦咖啡™美式": "a mini coffee cup",
    "浓缩咖啡": "a mini espresso cup",
    "鲜萃咖啡": "a mini coffee cup",
    "浓浓黑巧（冰/热）": "a mini chocolate drink cup",
    "浓浓燕麦黑巧（冰/热）": "a mini chocolate drink cup",
    "燕麦奶": "a mini oat milk carton",
}

# 关键词兜底，越具体的越靠前
FOOD_MOTIF = [
    (("冰淇淋", "圣代", "甜筒", "圆筒", "新地", "阿芙佳朵"), "a miniature ice cream cone"),
    (("薯条", "薯饼", "薯格"), "a mini red box of french fries"),
    (("奶铁", "拿铁", "卡布奇诺", "美式", "浓缩", "鲜萃", "咖啡"), "a mini coffee cup"),
    (("可乐", "雪碧", "汽水", "怡泉", "红茶", "橙橙", "果汁", "纯悦"), "a mini soda cup"),
    (("牛奶", "燕麦奶"), "a mini milk carton"),
    (("鸡翅", "V翅"), "a mini bucket of fried chicken wings"),
    (("鸡块", "麦乐鸡"), "a mini box of chicken nuggets"),
    (("炸鸡", "脆汁鸡", "鸡排"), "a miniature fried chicken drumstick"),
    (("派",), "a miniature pie"),
    (("玉米",), "a small corn cup"),
    (("苹果",), "a tiny apple slice"),
    (("沙拉", "蔬萃"), "a small salad bowl"),
    (("堡", "汉堡"), "a miniature hamburger"),
    (("套餐", "拼盘", "分享", "双人", "全家", "随心拼", "四件套", "三件套", "八件", "单人餐", "乐园餐"),
     "a small golden crown"),
]

# 发型 / 表情 / 性别池，用编号哈希抽
HAIR_STYLES = [
    "long straight hair with bangs",
    "twin tails with ribbons",
    "short bob cut",
    "high ponytail",
    "twin braids",
    "wavy shoulder-length hair",
    "messy short hair with ahoge",
    "half-up long hair with a side braid",
]
EXPRESSIONS = [
    "cheerful open smile",
    "confident smirk",
    "shy gentle smile with blush",
    "determined serious look",
    "surprised wide eyes",
    "soft sleepy half-closed eyes",
]
POSES_GIRL = ["standing straight facing viewer", "one hand on hip", "waving one hand"]
POSES_BOY = ["standing straight facing viewer", "arms crossed", "one hand in pocket"]

STYLE_TAIL = (
    "masterpiece, best quality, anime chibi character, super deformed proportions, "
    "full body, single character, centered, white background, "
    "cel shading, thick clean lineart, vibrant saturated colors, soft rim light, "
    "large detailed sparkling eyes, glossy highlights, game card illustration"
)


def hnum(code: str, salt: str) -> int:
    return int(hashlib.md5(f"{code}:{salt}".encode()).hexdigest()[:8], 16)


def pick(code: str, salt: str, seq):
    return seq[hnum(code, salt) % len(seq)]


def food_motif(name: str) -> str:
    n = name.strip()
    if n in MOTIF_EXACT:
        return MOTIF_EXACT[n]
    for keys, motif in FOOD_MOTIF:
        if any(k in n for k in keys):
            return motif
    return "a small golden star clip"


def build_prompt(card: dict) -> str:
    code = str(card["code"])
    el = ELEMENT_STYLE.get(card["element"], ELEMENT_STYLE["炎"])
    job = JOB_STYLE.get(card["job"], JOB_STYLE["战士"])

    is_girl = hnum(code, "gender") % 100 < 68
    gender = "1girl, cute anime girl" if is_girl else "1boy, handsome anime boy"
    pose = pick(code, "pose", POSES_GIRL if is_girl else POSES_BOY)
    hair = pick(code, "hair", HAIR_STYLES)
    expr = pick(code, "expr", EXPRESSIONS)
    hcolor = pick(code, "hcolor", el["hair"])
    ecolor = pick(code, "ecolor", el["eyes"])
    motif = food_motif(card["name"])

    rarity_extra = {
        "SSR": "ornate detailed costume with gold embroidery and glowing aura, legendary hero",
        "SR": "detailed costume with metallic trim and subtle glow, elite hero",
        "R": "clean simple costume with colored trim",
        "N": "simple plain costume, ordinary citizen",
    }.get(card["rarity"], "")

    parts = [
        gender,
        f"{hair}, {hcolor} hair",
        f"{ecolor} eyes, {expr}",
        f"{job['outfit']} in {el['accent']}",
        job["weapon"],
        f"{pose}",
        f"wearing {motif} as a hair ornament on top of the head",
        f"{el['aura']} around the character",
        rarity_extra,
        STYLE_TAIL,
    ]
    return ", ".join(p for p in parts if p)


# ---------------------------------------------------------------- HTTP
def multipart(fields: dict, files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = "----wb" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, filename, blob in files:
        out += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n"
        ).encode()
        out += blob + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def to_png(blob: bytes) -> bytes:
    """官方图有 18 张是 GIF（部分还带 .png 后缀），接口只收真 PNG，统一转一遍。"""
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return blob
    from io import BytesIO

    from PIL import Image

    im = Image.open(BytesIO(blob))
    im.seek(0)
    im = im.convert("RGBA")
    canvas = Image.new("RGBA", im.size, (255, 255, 255, 255))
    canvas.alpha_composite(im)
    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def fetch_ref(card: dict) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{card['code']}.png"
    if p.exists() and p.stat().st_size > 1000 and p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n":
        return p.read_bytes()
    req = urllib.request.Request(card["image"], headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        blob = to_png(r.read())
    p.write_bytes(blob)
    return blob


def generate(card: dict, retries: int = 6) -> bytes:
    prompt = build_prompt(card)
    ref = fetch_ref(card)
    body, ctype = multipart(
        {
            "model": "flux-klein3",
            "prompt": prompt,
            "size": "1024x1024",
            "seed": str(hnum(str(card["code"]), "seed") % 100000),
            "output_format": "png",
        },
        [("image", "ref.png", ref)],
    )
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                API_URL,
                data=body,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": ctype},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                blob = r.read()
            if len(blob) > 5000 and blob[:4] == b"\x89PNG":
                return blob
            last = RuntimeError(f"bad payload {len(blob)}B: {blob[:120]!r}")
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:  # 限流：指数退避，别把配额打穿
                time.sleep(min(60, 12 * (2**i)))
                continue
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 + i * 3)
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------- 后处理
def postprocess(blob: bytes) -> bytes:
    """去白底 → 裁切 → 正方形补边 → 512px。"""
    from io import BytesIO

    from PIL import Image

    im = Image.open(BytesIO(blob)).convert("RGBA")
    im = im.resize((640, 640), Image.LANCZOS)

    px = im.load()
    w, h = im.size
    # 从四边泛洪，把与边缘连通的近白像素判为背景
    visited = bytearray(w * h)
    stack = []
    for x in range(w):
        stack.append((x, 0))
        stack.append((x, h - 1))
    for y in range(h):
        stack.append((0, y))
        stack.append((w - 1, y))

    def near_white(r, g, b):
        return r > 236 and g > 236 and b > 236

    while stack:
        x, y = stack.pop()
        idx = y * w + x
        if visited[idx]:
            continue
        r, g, b, _a = px[x, y]
        if not near_white(r, g, b):
            continue
        visited[idx] = 1
        px[x, y] = (r, g, b, 0)
        if x > 0:
            stack.append((x - 1, y))
        if x < w - 1:
            stack.append((x + 1, y))
        if y > 0:
            stack.append((x, y - 1))
        if y < h - 1:
            stack.append((x, y + 1))

    bbox = im.getbbox()
    if not bbox:
        raise RuntimeError("去底后为空图")
    im = im.crop(bbox)

    # 补成正方形，四周留 6% 边距，保证所有卡构图一致
    cw, ch = im.size
    side = int(max(cw, ch) * 1.12)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - cw) // 2, (side - ch) // 2), im)
    canvas = canvas.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)

    # 二次元平涂用 256 色量化几乎无可见损失，体积从 ~200KB 降到 ~46KB
    canvas = canvas.quantize(colors=256, method=Image.FASTOCTREE)

    out = BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def one(card: dict) -> tuple[str, int, str]:
    t0 = time.time()
    raw = generate(card)
    png = postprocess(raw)
    ART.mkdir(parents=True, exist_ok=True)
    (ART / f"{card['code']}.png").write_bytes(png)
    return card["code"], len(png), f"{time.time()-t0:.0f}s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rarity", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not API_KEY:
        sys.exit("缺少环境变量 CV_API_KEY")

    roster = json.loads((DATA / "roster.json").read_text(encoding="utf-8"))
    cards = roster["cards"]
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        cards = [c for c in cards if str(c["code"]) in want]
    if args.rarity:
        want = {s.strip() for s in args.rarity.split(",")}
        cards = [c for c in cards if c["rarity"] in want]
    if not args.force:
        cards = [c for c in cards if not (ART / f"{c['code']}.png").exists()]
    if args.limit:
        cards = cards[: args.limit]

    print(f"待生成 {len(cards)} 张，并发 {args.workers}")
    ok, fail = 0, []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, c): c for c in cards}
        for f in as_completed(futs):
            c = futs[f]
            try:
                code, size, cost = f.result()
                ok += 1
                print(f"  ✓ {c['name'][:18]:<20} {c['rarity']:<3} {size//1024:>4}KB {cost}")
            except Exception as e:  # noqa: BLE001
                fail.append(c["name"])
                print(f"  ✗ {c['name'][:18]:<20} {e}")
    print(f"\n完成 {ok} / 失败 {len(fail)}")
    if fail:
        print("失败清单:", fail)


if __name__ == "__main__":
    main()
