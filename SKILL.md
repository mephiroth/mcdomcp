---
name: mcd-gacha
description: 把麦当劳菜单编译成二次元卡牌。基于麦当劳 MCP 的官方营养数据生成角色六维、职业、元素与稀有度，支持图鉴查询、卡牌对比、十连抽与性价比分析。当用户想「抽卡」「看图鉴」「比较两个套餐谁更强」「算某个餐品的属性」「找性价比最高的餐品」时使用。
---

# 麦门幻境 · McdGacha

把麦当劳菜单变成卡牌游戏。**每一张卡的六维都来自官方营养表，不是手调数值。**

## 何时使用

- 「抽个卡」「十连」「看看我的图鉴」
- 「巨无霸和麦辣鸡腿汉堡谁更强」
- 「有没有性价比高的」「每元蛋白质最高的是哪个」
- 「这份套餐多少热量 / 钠超标吗」
- 「把菜单做成卡牌」

## 核心概念

| 属性 | 营养来源 | 说明 |
|---|---|---|
| ATK 攻击 | 能量 kcal | 热量即输出 |
| DEF 防御 | 蛋白质 g | 每克都是护甲 |
| HP 生命 | 脂肪 g | 脂肪是血条 |
| SPD 速度 | 碳水 g | 碳水灌注 |
| CRT 暴击 | 钠 mg | 咸度决定暴击 |
| HEAL 治疗 | 钙 mg | 钙质庇护 |

职业不看份量看结构：**每千卡占比在全体卡池中的分位**最高者决定职业
（战士/坦克/法师/刺客/牧师/灵体）。

## 数据从哪来

```
query-nearby-stores  → storeCode
query-meals          → 菜单
query-meal-detail    → 套餐默认搭配（拆单品）
list-nutrition-foods → 官方营养表
```

套餐营养 = 默认搭配各单品营养 × 份数 累加。**不用估算。**

## 怎么用

### 环境

```bash
export MCD_TOKEN=<你的麦当劳 MCP Token>
```

Token 从 [open.mcd.cn/mcp](https://open.mcd.cn/mcp) 申请。

### 构建卡池

```bash
python src/build_roster.py --city 上海市 --keyword 人民广场
python src/stats.py
python src/render.py
```

### 查询已有卡池

`data/roster.json` 已包含编译好的全部卡牌，可直接读取，无需重新调 MCP。

单卡字段：

```json
{
  "name": "巨无霸",
  "rarity": "R", "element": "炎", "job": "刺客",
  "title": "炽焰影刃",
  "stats": {"ATK": 699, "DEF": 660, "HP": 694, "SPD": 696, "CRT": 572, "HEAL": 710},
  "power": 2403,
  "price": 25.5,
  "powerPerYuan": 94.2,
  "nutrition": {"kcal": 513, "protein": 27, "fat": 33, "carb": 43, "sodium": 961, "calcium": 190},
  "components": [{"name": "巨无霸", "qty": 1, "matched": "巨无霸", "how": "exact"}]
}
```

## 回答用户时的原则

1. **数值必须来自 `data/roster.json`**，不要凭印象编
2. 涉及价格比较时用 `powerPerYuan`（每元战力），不要只看绝对战力
3. 卡池里有 **30 项新品官方营养表未收录**，被排除在卡池外。
   用户问到这些（如龙焰鸡腿堡、心形薯饼、麦炫酷）时，如实说明「官方营养表暂未收录」，
   **不要估算**
4. 营养话题不做健康/医疗建议，必要时提示「以麦当劳官方渠道为准」

## 免责

参赛作品，非麦当劳官方产品。卡牌属性为营养数据的数学映射，仅供游戏化展示。
