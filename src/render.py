"""把 data/roster.json 渲染成静态图鉴页面 docs/index.html。

无框架、无构建、无外部依赖（除了麦当劳官方的商品图 CDN），
推到 GitHub 后打开 Pages 就能直接玩。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"


def main() -> None:
    roster = json.loads((DATA / "roster.json").read_text(encoding="utf-8"))
    raw = json.loads((DATA / "roster_raw.json").read_text(encoding="utf-8"))
    tpl = (Path(__file__).resolve().parent / "template.html").read_text(encoding="utf-8")

    payload = {
        "store": roster["store"],
        "count": roster["count"],
        "byRarity": roster["byRarity"],
        "byJob": roster["byJob"],
        "byElement": roster["byElement"],
        "menuTotal": raw["count"],
        "excludedToys": len(raw.get("excluded", [])),
        "cards": roster["cards"],
    }
    html = tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "index.html"
    out.write_text(html, encoding="utf-8")
    pending = raw["count"] - roster["count"]
    print(
        f"已生成 {out}  ({len(html)/1024:.0f} KB, {roster['count']} 张卡, "
        f"另有 {pending} 项未入池)"
    )


if __name__ == "__main__":
    main()
