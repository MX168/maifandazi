#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦饭搭子 · 预算锁死智能搭配算法

输入一份「菜单 + 需求」JSON，输出零超支、忌口已过滤、偏好命中最高的拼单方案，
并把可直接粘贴给用户的结果 Markdown 一并算好。

用法:
    python planner.py --input request.json [--out result.json]
    python planner.py --selftest

request.json 结构:
{
  "headcount": 3,                 // 用餐人数, 必填
  "budget_fen": 10000,            // 总预算, 单位【分】, 必填 (预算元 x 100)
  "menu": [                       // 来自 MCP query-meals 的菜单, 必填
    {"code":"MB001","name":"板烧鸡腿堡","price_fen":2100,"category":"汉堡",
     "tags":["鸡","堡"],"original_fen":2500,"serves":1}
  ],
  "avoid": ["辣","洋葱"],         // 忌口关键词(命中名称或 tag 即剔除)
  "blacklist": ["麦香鱼"],        // 永久避雷菜品
  "prefer": ["炸鸡"],             // 口味偏好关键词(命中加权)
  "nutrition": [                  // 来自 MCP list-nutrition-foods, 选填
    {"name":"板烧鸡腿堡","energy_kcal":404,"fat_g":18,"protein_g":25,"carb_g":38,"sodium_mg":800}
  ],
  "coupons": [                    // 来自 MCP query-store-coupons, 选填
    {"name":"汉堡立减3元","save_fen":300,"threshold_fen":2000}
  ],
  "seed": 20261009                // 选填, 无意义时可省略
}
"""

from __future__ import annotations

import argparse
import json
import itertools
import math
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------- 常量与权重

BASE_WEIGHT = {
    "套餐": 12.0,
    "主食": 10.0,
    "小食": 4.0,
    "饮料": 3.0,
    "甜品": 3.5,
    "其他": 2.0,
}

COMBO_WORDS = ["套餐", "超值", "欢享", "三人餐", "双人餐", "全家桶", "分享盒", "聚餐", "宴",
               "随心配", "随心搭", "1+1"]
DESSERT_WORDS = ["派", "圣代", "新地", "麦旋风", "甜筒", "圆筒", "冰淇淋", "蛋糕", "甜品",
                 "巧克力", "朱古力", "雪冰", "奶冻", "布丁", "拉明顿", "阿芙佳朵", "麦旋酷",
                 "苹果片", "杯蛋糕", "爆珠"]
DRINK_WORDS = [
    "可乐", "雪碧", "芬达", "咖啡", "美式", "拿铁", "卡布奇诺", "玛奇朵", "奶铁", "澳白",
    "奶茶", "牛奶", "燕麦", "豆奶", "豆浆", "果汁", "橙汁", "柠檬", "红茶", "绿茶", "茶",
    "气泡水", "矿泉水", "纯净水", "怡泉", "纯悦", "水", "椰汁",
]
SNACK_WORDS = [
    "薯条", "薯格", "薯饼", "鸡块", "鸡翅", "鸡排", "鸡米花", "鸡柳", "鸡球", "洋葱圈",
    "玉米", "沙拉", "凤梨", "脆汁鸡", "V翅", "香肠", "鸡扒", "趣鸡球", "多笋卷",
]
MEAL_WORDS = [
    "堡", "汉堡", "卷", "饭", "饼", "披萨", "面", "早餐", "烧饼", "满分", "粥", "油条",
    "炒双蛋", "安心蛋",
]

WASTE_PENALTY = 6.0        # 预算剩余占比的惩罚系数（越大越倾向于花满预算）
DIVERSITY_BONUS = 0.8      # 每个不重复 SKU 的多样性加分
DUP_PENALTY = 1.5          # 同一 SKU 每多一份的重复惩罚
EXCESS_PENALTY = 20.0      # 多出的用餐份额的惩罚（远大于浪费惩罚，避免为了花满预算而多点）
PREFER_BONUS = 3.0         # 命中口味偏好的加分
MAX_ITER = 400
MAX_CAND_MEAL = 40
MAX_CAND_EXTRA = 40

CNY = lambda fen: "{:.2f}".format(fen / 100.0)


# ---------------------------------------------------------------- 工具函数

def classify(name: str, declared: str = "", price_fen: int = 0) -> str:
    """推断餐品大类。调用方传入的 category 优先级最高。"""
    if declared and declared not in ("", "其他", "未知", "None"):
        return declared
    name = (name or "").strip()
    for w in COMBO_WORDS:
        if w in name:
            return "套餐"
    for w in DESSERT_WORDS:
        if w in name:
            return "甜品"
    for w in DRINK_WORDS:
        if w in name:
            return "饮料"
    for w in SNACK_WORDS:
        if w in name:
            return "小食"
    for w in MEAL_WORDS:
        if w in name:
            return "主食"
    # 兜底：偶发的创意命名无法靠名字判断，按价格粗细归类
    return "主食" if price_fen >= 1500 else "小食"


def num_to_fen(value, unit: str) -> int:
    """按统一判定出的单位，把价格转成整数【分】。"""
    if value is None:
        return 0
    if isinstance(value, str):
        s = value.strip().replace("¥", "").replace("元", "").replace("分", "").strip()
        if not s:
            return 0
        try:
            value = float(s)
        except ValueError:
            return 0
    v = float(value)
    return int(round(v * 100)) if unit == "yuan" else int(round(v))


def detect_price_unit(menu: list, declared: str = "auto") -> str:
    """自动判定菜单价格单位。

    麦当劳真实单品没有低于 ¥5 的，因此：
      全部价格最大值 <= 250 → 一定是「元」（如 21.0 / "¥21"）
      否则 → 已是「分」（如 2100）
    """
    if declared in ("fen", "yuan"):
        return declared
    vals = []
    for raw in menu or []:
        for key in ("price_fen", "price", "sellPrice"):
            v = raw.get(key)
            if isinstance(v, (int, float)) and v:
                vals.append(float(v))
                break
            if isinstance(v, str) and v.strip():
                try:
                    vals.append(float(v.replace("¥", "").replace("元", "").strip()))
                    break
                except ValueError:
                    pass
    if not vals:
        return "fen"
    return "yuan" if max(vals) <= 250 else "fen"


NUTRITION_FIELDS = {
    "energy_kj": ("energyKj", "energy_kj"),
    "energy_kcal": ("energyKcal", "energy_kcal", "kcal"),
    "fat_g": ("fat", "fat_g"),
    "protein_g": ("protein", "protein_g"),
    "carb_g": ("carbohydrate", "carb_g", "carbohydrate_g"),
    "sodium_mg": ("sodium", "sodium_mg"),
    "calcium_mg": ("calcium", "calcium_mg"),
}


def read_nutrition(row: dict) -> dict:
    """读取营养行，兼容 productName/name 与 energyKcal/energy_kcal 两套命名。"""
    out = {}
    for std, aliases in NUTRITION_FIELDS.items():
        for alias in aliases:
            if row.get(alias) not in (None, "", "null"):
                try:
                    out[std] = float(row[alias])
                except (TypeError, ValueError):
                    pass
                break
    desc = row.get("nutritionDescription") or row.get("description")
    if desc and str(desc).strip() not in ("", "null", "None"):
        out["description"] = str(desc).strip()
    for key in NUTRITION_FIELDS:
        out.setdefault(key, 0.0)
    return out


def build_nutrition_index(nutrition: list) -> dict:
    """建立 {餐品名: 营养字典}，作为 lookup_nutrition 的查询索引。"""
    idx = {}
    for row in nutrition or []:
        name = str(row.get("name", "") or row.get("productName", "")).strip()
        if name:
            idx[name] = read_nutrition(row)
    return idx


def lookup_nutrition(name: str, idx: dict):
    """先精确匹配，再做双向包含匹配（取最长命中）。"""
    if name in idx:
        return idx[name]
    best, best_len = None, 0
    for key, row in idx.items():
        if (key in name) or (name in key):
            score = len(set(key) & set(name))
            if score > best_len:
                best, best_len = row, score
    return best


def empty_nutrition():
    return {"energy_kj": 0.0, "energy_kcal": 0.0, "fat_g": 0.0, "protein_g": 0.0,
            "carb_g": 0.0, "sodium_mg": 0.0, "calcium_mg": 0.0}


# ---------------------------------------------------------------- 建模

class Item:
    __slots__ = ("code", "name", "price_fen", "original_fen", "save_fen",
                 "category", "tags", "serves", "nutrition")

    def __init__(self, raw: dict, nutrition_idx: dict, unit: str = "fen"):
        self.code = str(raw.get("code") or raw.get("productCode") or raw.get("name") or "")
        self.name = str(raw.get("name") or raw.get("productName") or "").strip()
        raw_price = raw.get("price_fen", raw.get("price", raw.get("sellPrice")))
        self.price_fen = num_to_fen(raw_price, unit)
        raw_original = raw.get("original_fen", raw.get("originalPrice", raw.get("original")))
        self.original_fen = num_to_fen(raw_original, unit) or self.price_fen
        self.save_fen = max(0, self.original_fen - self.price_fen)
        self.category = classify(
            self.name, str(raw.get("category") or raw.get("type") or ""), self.price_fen)
        raw_tags = raw.get("tags") or raw.get("labels") or []
        if isinstance(raw_tags, str):
            raw_tags = [t for t in re.split(r"[,，/、\s]+", raw_tags) if t]
        self.tags = [str(t) for t in raw_tags]
        self.serves = max(1, int(raw.get("serves") or raw.get("servings") or 1))
        nut = lookup_nutrition(self.name, nutrition_idx)
        self.nutrition = dict(nut) if nut else None

    def haystack(self) -> str:
        return self.name + " " + " ".join(self.tags)

    def base_score(self) -> float:
        s = BASE_WEIGHT.get(self.category, 2.0)
        if self.category == "套餐":
            s += 2.0 + self.save_fen / 500.0     # 套餐省得越多越优先
        return s


def filter_candidates(cfg: dict, nutrition_idx: dict, unit: str):
    """执行忌口 + 避雷过滤，返回 (可用列表, 剔除明细, 偏好词)。"""
    avoid = [str(a).strip() for a in (cfg.get("avoid") or []) if str(a).strip()]
    blacklist = [str(b).strip() for b in (cfg.get("blacklist") or []) if str(b).strip()]
    prefer = [str(p).strip() for p in (cfg.get("prefer") or []) if str(p).strip()]

    items, dropped = [], []
    for raw in cfg.get("menu") or []:
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        item = Item(raw, nutrition_idx, unit)
        if item.price_fen <= 0:
            dropped.append({"name": name, "reason": "无有效价格"})
            continue
        hit = None
        for b in blacklist:
            if b and b in item.name:
                hit = "永久避雷:%s" % b
                break
        if not hit:
            for a in avoid:
                if a and (a in item.haystack()):
                    hit = "忌口:%s" % a
                    break
        if hit:
            dropped.append({"name": name, "reason": hit, "price_fen": item.price_fen})
            continue
        item.tags = item.tags + [p for p in prefer if p in item.haystack()]
        items.append(item)
    return items, dropped, prefer


# ---------------------------------------------------------------- 评分与搜索

def make_scorer(budget_fen: int, prefer: list, headcount: int):
    # 单次点单总件数上限：保证「每人一份餐 + 少量共享加购」的真实用餐形态，
    # 避免算法为了花满预算而变成批发。
    max_items = max(headcount + 1, int(round(headcount * 1.8)))

    def score(slots: list, extras: Counter, pool: dict) -> float:
        codes = list(slots) + list(extras.elements())
        if not codes or len(codes) > max_items:
            return -1e9
        cost = sum(pool[c].price_fen for c in codes)
        if cost > budget_fen:
            return -1e9
        # 必须喂饱所有人；多出来的份额要惩罚，避免「双人餐 + 三个堡」的浪费组合
        served = sum(pool[c].serves for c in slots)
        if served < headcount:
            return -1e9
        excess = served - headcount
        total = 0.0
        for c in codes:
            total += pool[c].base_score()
        if excess > 0:
            total -= EXCESS_PENALTY * excess
        distinct = len(set(codes))
        total += DIVERSITY_BONUS * distinct
        # 重复惩罚
        cnt = Counter(codes)
        cap_extra = min(headcount, MAX_CAND_EXTRA)
        for code, k in cnt.items():
            if k > 1:
                total -= DUP_PENALTY * (k - 1)
            if pool[code].category != "主食" and k > cap_extra:
                total -= 3.0 * (k - cap_extra)
        # 偏好命中
        if prefer:
            for c, k in cnt.items():
                if any(p in pool[c].haystack() for p in prefer):
                    total += PREFER_BONUS * k
        # 预算利用率
        waste = (budget_fen - cost) / float(budget_fen)
        total -= WASTE_PENALTY * waste
        return total

    return score


def optimize(items: list, headcount: int, budget_fen: int, prefer: list):
    """主角占用 hill-climbing：先生成可行基线，再迭代改良至局部最优。"""
    meals = [i for i in items if i.category in ("主食", "套餐")]
    extras_pool = [i for i in items if i.category not in ("主食", "套餐")]
    fallback = False
    if not meals:
        # 忌口过重导致主食全被剔除 → 放开限制，用剩余餐品拆分拼单（仍保证零超支）
        fallback = True
        meals = sorted(items, key=lambda i: i.price_fen)[:MAX_CAND_MEAL]
        extras_pool = sorted(items, key=lambda i: i.price_fen)[:MAX_CAND_EXTRA]

    # 候选裁剪，控制搜索规模
    meals.sort(key=lambda i: (-(i.base_score() + (PREFER_BONUS if prefer and any(p in i.haystack() for p in prefer) else 0)), i.price_fen))
    meals = meals[:MAX_CAND_MEAL]
    extras_pool.sort(key=lambda i: (-(i.base_score()), i.price_fen))
    extras_pool = extras_pool[:MAX_CAND_EXTRA]

    pool = {i.code: i for i in items}
    score = make_scorer(budget_fen, prefer, headcount)

    # 基线成本检查：即使按最便宜主食排，也可能装不下
    cheapest = sorted(meals, key=lambda i: i.price_fen)[0]
    units = max(1, math.ceil(headcount / cheapest.serves))
    base_cost = cheapest.price_fen * units
    if base_cost > budget_fen:
        gap = base_cost - budget_fen
        return (slots_init := [cheapest.code] * units, Counter(), pool), None, (
            "预算不足以让每人一份餐：最省方案需 ¥%s / 预算 ¥%s，缺口 ¥%s"
            % (CNY(base_cost), CNY(budget_fen), CNY(gap))), fallback

    def search(start_slots: list, start_extras: Counter):
        """从一个起点开始爬山，返回局部最优。"""
        slots, extras = list(start_slots), Counter(start_extras)
        cur = score(slots, extras, pool)
        if cur <= -1e8:
            return slots, extras, cur
        for _ in range(MAX_ITER):
            best_move, best_val = None, cur + 1e-9
            # 复合动作：用一个多人份套餐替换掉任意 span 个餐位（枚举全组合，避免只换最便宜那份的偏见）
            for cand in meals:
                span = max(1, cand.serves)
                if span == 1 and cand.category != "套餐":
                    continue
                if span > len(slots):
                    continue
                for combo_idx in itertools.combinations(range(len(slots)), span):
                    keep = [slots[i] for i in range(len(slots)) if i not in combo_idx]
                    trial = keep + [cand.code]
                    v = score(trial, extras, pool)
                    if v > best_val:
                        best_move, best_val = ("combo", tuple(combo_idx), cand.code), v
            # 替换某个餐位
            for idx in range(len(slots)):
                for cand in meals:
                    if cand.code == slots[idx]:
                        continue
                    trial = list(slots)
                    trial[idx] = cand.code
                    v = score(trial, extras, pool)
                    if v > best_val:
                        best_move, best_val = ("swap", idx, cand.code), v
            # 减少一个餐位（仅在不至于喂不饱的前提下），用于消除多人份造成的冗余
            served_total = sum(pool[c].serves for c in slots)
            if served_total > headcount:
                for idx in range(len(slots)):
                    if served_total - pool[slots[idx]].serves < headcount:
                        continue
                    trial = slots[:idx] + slots[idx + 1:]
                    if not trial:
                        continue
                    v = score(trial, extras, pool)
                    if v > best_val:
                        best_move, best_val = ("drop", idx, None), v
            # 加购一件小食/饮料/甜品
            for cand in extras_pool:
                trial_extras = Counter(extras)
                trial_extras[cand.code] += 1
                v = score(slots, trial_extras, pool)
                if v > best_val:
                    best_move, best_val = ("add", None, cand.code), v
            # 换掉一件加购
            for code in list(extras.keys()):
                for cand in extras_pool:
                    if cand.code == code:
                        continue
                    trial_extras = Counter(extras)
                    trial_extras[code] -= 1
                    if trial_extras[code] == 0:
                        del trial_extras[code]
                    trial_extras[cand.code] += 1
                    v = score(slots, trial_extras, pool)
                    if v > best_val:
                        best_move, best_val = ("reswap", code, cand.code), v
            if best_move is None:
                break
            kind, a, b = best_move
            if kind == "swap":
                slots[a] = b
            elif kind == "add":
                extras[b] += 1
            elif kind == "drop":
                slots.pop(a)
            elif kind == "combo":
                keep = [slots[i] for i in range(len(slots)) if i not in a]
                slots[:] = keep + [b]
            else:
                extras[a] -= 1
                if extras[a] == 0:
                    del extras[a]
                extras[b] += 1
                cur = best_val
        return slots, extras, cur

    # ---- 多起点：单品最省 / 单人最优 / 每个套餐优先，各爬一次取全局最好
    starts = []
    cheapest = sorted(meals, key=lambda i: i.price_fen)[0]
    starts.append([cheapest.code] * max(1, math.ceil(headcount / cheapest.serves)))
    best_item = sorted(meals, key=lambda i: -(i.base_score()))[0]
    starts.append([best_item.code] * max(1, math.ceil(headcount / best_item.serves)))
    for combo in [m for m in meals if m.category == "套餐" or m.serves > 1][:5]:
        units = max(1, math.ceil(headcount / combo.serves))
        if combo.price_fen * units <= budget_fen:
            starts.append([combo.code] * units)

    best = None
    for start in starts:
        slots_t, extras_t, score_t = search(start, Counter())
        if score_t <= -1e8:
            continue
        if best is None or score_t > best[2]:
            best = (slots_t, extras_t, score_t)

    if best is None:
        return None, None, "无法在当前预算与忌口条件下生成方案", fallback
    slots, extras, cur = best
    return (slots, extras, pool), cur, None, fallback


# ---------------------------------------------------------------- 输出组装

def nutrition_sum(items: list):
    total = empty_nutrition()
    missing = []
    for it in items:
        nut = it.nutrition
        if not nut:
            missing.append(it.name)
            continue
        for k in total:
            total[k] += float(nut.get(k) or 0)
    return total, missing


def build_report(slots, extras, pool, cfg, nutrition_idx, dropped):
    budget_fen = cfg["budget_fen"]
    headcount = cfg["headcount"]
    slot_items = [pool[c] for c in slots]
    extra_items = [pool[c] for c in extras.elements()]

    subtotal = sum(i.price_fen for i in slot_items) + sum(i.price_fen for i in extra_items)
    original = sum((i.original_fen or i.price_fen) for i in slot_items + extra_items)

    coupons = cfg.get("coupons") or []
    usable = [c for c in coupons
              if not c.get("threshold_fen") or num_to_fen(c.get("threshold_fen"), "fen") <= subtotal]
    coupon_save = sum(num_to_fen(c.get("save_fen"), "fen") for c in usable)
    payable = max(0, subtotal - coupon_save)
    remain = budget_fen - payable
    per_person = payable // headcount if headcount else payable

    lines = []
    lines.append("### 💰 预算与花费")
    lines.append("")
    lines.append("| 项目 | 金额 |")
    lines.append("| --- | --- |")
    lines.append("| 总预算 | ¥%s |" % CNY(budget_fen))
    lines.append("| 原价合计 | ¥%s |" % CNY(original))
    lines.append("| 优惠后实付 | **¥%s** |" % CNY(payable))
    if coupon_save:
        lines.append("| 券省 | -¥%s |" % CNY(coupon_save))
    lines.append("| 预算结余 | ¥%s |" % CNY(max(0, remain)))
    lines.append("| 人均 | ¥%s（%d 人） |" % (CNY(per_person), headcount))
    lines.append("")

    lines.append("### 🍔 每人一个餐位")
    lines.append("")
    lines.append("| 座位 | 餐品 | 单价 | 热量 |")
    lines.append("| --- | --- | --- | --- |")
    seat = 1
    for it in slot_items:
        span = it.serves
        label = str(seat) if span == 1 else "%d-%d" % (seat, seat + span - 1)
        seat += span
        kcal = "%d kcal" % int(it.nutrition["energy_kcal"]) if it.nutrition else "—"
        lines.append("| %s | %s%s | ¥%s | %s |"
                     % (label, it.name,
                        "（%d 人份）" % span if span > 1 else "",
                        CNY(it.price_fen), kcal))
    lines.append("")

    if extra_items:
        agg = Counter(extra_items)
        lines.append("### 🍟 加购小食 / 饮品")
        lines.append("")
        lines.append("| 餐品 | 数量 | 小计 |")
        lines.append("| --- | --- | --- |")
        for it, k in agg.items():
            lines.append("| %s | %d | ¥%s |" % (it.name, k, CNY(it.price_fen * k)))
        lines.append("")

    if dropped:
        reasons = {}
        for d in dropped:
            reasons.setdefault(d["reason"], []).append(d["name"])
        lines.append("### 🚫 已自动过滤")
        lines.append("")
        for reason, names in reasons.items():
            shown = "、".join(names[:8]) + (" 等 %d 个" % len(names) if len(names) > 8 else "")
            lines.append("- **%s**：%s" % (reason, shown))

        # 已生效但菜单里没有任何匹配的忌口 / 避雷项 —— 必须告知，
        # 否则用户会以为自己的忌口被忽略了。
        matched = set()
        for reason in reasons:
            for prefix in ("忌口:", "永久避雷:"):
                if reason.startswith(prefix):
                    matched.add(reason[len(prefix):])
        no_hit_avoid = [a for a in (cfg.get("avoid") or []) if a not in matched]
        no_hit_bl = [b for b in (cfg.get("blacklist") or []) if b not in matched]
        if no_hit_avoid:
            lines.append("- **%s**：菜单中无匹配餐品，规则已生效"
                         % "、".join("忌口「%s」" % a for a in no_hit_avoid))
        if no_hit_bl:
            lines.append("- **%s**：菜单中无匹配餐品，规则已生效"
                         % "、".join("避雷「%s」" % b for b in no_hit_bl))
        lines.append("")

    if usable:
        lines.append("### 🎟️ 可用优惠券")
        lines.append("")
        for c in usable:
            lines.append("- %s（省 ¥%s）" % (c.get("name", "优惠券"), CNY(num_to_fen(c.get("save_fen"), "fen"))))
        lines.append("")

    total_nut, missing = nutrition_sum(slot_items + extra_items)
    lines.append("### 🔥 热量总览")
    lines.append("")
    lines.append("- 整套合计：**%d kcal**｜脂肪 %.1f g｜蛋白 %.1f g｜碳水 %.1f g｜钠 %d mg"
                 % (int(total_nut["energy_kcal"]), total_nut["fat_g"],
                    total_nut["protein_g"], total_nut["carb_g"], int(total_nut["sodium_mg"])))
    warnings = []
    if slot_items and slot_items[0].nutrition:
        kcal_list = [(it.name, int(it.nutrition["energy_kcal"])) for it in slot_items if it.nutrition]
        if kcal_list:
            top = max(kcal_list, key=lambda x: x[1])
            lines.append("- 单人最高：%s %d kcal" % (top[0], top[1]))
    analogies = sorted({(it.name, it.nutrition["description"])
                        for it in slot_items + extra_items
                        if it.nutrition and it.nutrition.get("description")})
    if analogies:
        for nm, desc in analogies[:4]:
            lines.append("- 💡 %s：%s" % (nm, desc))
    per_cap_kcal = total_nut["energy_kcal"] / max(1, headcount)
    if per_cap_kcal > 1000:
        warnings.append("人均 %.0f kcal 偏高，建议替换部分炸物/含糖饮料" % per_cap_kcal)
    lowfat = [it.name for it in slot_items + extra_items if it.nutrition and it.nutrition.get("fat_g", 99) < 10]
    if lowfat:
        lines.append("- 低脂友好：%s" % "、".join(sorted(set(lowfat))[:5]))
    if total_nut["sodium_mg"] / max(1, headcount) > 2000:
        warnings.append("人均钠摄入偏高，注意控盐")
    if missing:
        lines.append("- ⚠️ 缺少营养数据：%s" % "、".join(sorted(set(missing))[:6]))
    if warnings:
        lines.append("")
        for w in warnings:
            lines.append("- ⚠️ %s" % w)
    lines.append("")

    order_lines = []
    agg_all = Counter(slot_items + extra_items)
    for it, k in agg_all.items():
        order_lines.append("%s  x%d" % (it.name, k))
    lines.append("### 📋 一键复制下单清单")
    lines.append("")
    lines.append("```")
    lines.extend(order_lines)
    lines.append("合计：¥%s" % CNY(payable))
    lines.append("```")

    return {
        "payable_fen": payable,
        "subtotal_fen": subtotal,
        "original_fen": original,
        "coupon_save_fen": coupon_save,
        "remain_fen": max(0, remain),
        "per_person_fen": per_person,
        "slot_items": [{"name": i.name, "code": i.code, "price_fen": i.price_fen,
                        "category": i.category, "serves": i.serves,
                        "nutrition": i.nutrition} for i in slot_items],
        "extra_items": [{"name": i.name, "code": i.code, "price_fen": i.price_fen,
                         "qty": extras[i.code], "category": i.category, "serves": i.serves,
                         "nutrition": i.nutrition} for i in extra_items],
        "served": sum(i.serves for i in slot_items),
        "total_nutrition": total_nut,
        "report_markdown": "\n".join(lines),
    }


def run(cfg: dict) -> dict:
    headcount = max(1, int(cfg.get("headcount") or 1))
    budget_fen = int(cfg.get("budget_fen") or 0)
    if budget_fen <= 0:
        return {"ok": False, "error": "budget_fen 必填且必须为正整数（单位：分）"}
    nutrition_idx = build_nutrition_index(cfg.get("nutrition") or [])
    cfg["headcount"], cfg["budget_fen"] = headcount, budget_fen
    unit = detect_price_unit(cfg.get("menu"), cfg.get("price_unit", "auto"))

    items, dropped, prefer = filter_candidates(cfg, nutrition_idx, unit)
    if not items:
        return {"ok": False, "error": "所有餐品都被忌口/避雷规则剔除，请放宽条件",
                "dropped": dropped}

    state, best_score, err, fallback = optimize(items, headcount, budget_fen, prefer)
    if state is None:
        return {"ok": False, "error": err, "dropped": dropped}
    slots, extras, pool = state

    result = build_report(slots, extras, pool, cfg, nutrition_idx, dropped)
    result.update({
        "ok": err is None,
        "feasible": err is None,
        "fallback_mode": fallback,
        "headcount": headcount,
        "budget_fen": budget_fen,
        "score": round(best_score or 0.0, 3),
        "dropped": dropped,
        "dropped_count": len(dropped),
        "available_count": len(items),
    })
    if err:
        result["error"] = err
        result["tip"] = "建议提高预算，或允许部分人单点最便宜餐品"
    return result


# ---------------------------------------------------------------- 自检样例

SELFTEST = {
    "headcount": 3,
    "budget_fen": 10000,
    "avoid": ["辣", "洋葱"],
    "prefer": ["炸鸡"],
    "menu": [
        {"code": "A1", "name": "板烧鸡腿堡", "price": 21.0},
        {"code": "A2", "name": "麦香鸡腿堡", "price": 19.0},
        {"code": "A3", "name": "麦辣鸡腿堡", "price": 22.0},
        {"code": "A4", "name": "双层牛堡", "price": 26.0},
        {"code": "B1", "name": "麦乐鸡", "price": 12.0},
        {"code": "B2", "name": "薯条(大)", "price": 12.5},
        {"code": "B3", "name": "洋葱圈", "price": 11.0},
        {"code": "C1", "name": "可口可乐(中)", "price": 8.5},
        {"code": "C2", "name": "牛奶", "price": 7.0},
        {"code": "D1", "name": "菠萝派", "price": 8.0},
        {"code": "S1", "name": "超值午餐套餐", "price": 24.0, "original": 31.0},
    ],
    "nutrition": [
        {"name": "板烧鸡腿堡", "energy_kcal": 404, "fat_g": 18, "protein_g": 25, "carb_g": 38, "sodium_mg": 800},
        {"name": "麦香鸡腿堡", "energy_kcal": 380, "fat_g": 16, "protein_g": 22, "carb_g": 40, "sodium_mg": 760},
        {"name": "双层牛堡", "energy_kcal": 520, "fat_g": 30, "protein_g": 32, "carb_g": 36, "sodium_mg": 1100},
        {"name": "麦乐鸡", "energy_kcal": 240, "fat_g": 14, "protein_g": 13, "carb_g": 16, "sodium_mg": 520},
        {"name": "薯条(大)", "energy_kcal": 420, "fat_g": 20, "protein_g": 5, "carb_g": 55, "sodium_mg": 400},
        {"name": "可口可乐(中)", "energy_kcal": 150, "fat_g": 0, "protein_g": 0, "carb_g": 38, "sodium_mg": 20},
        {"name": "牛奶", "energy_kcal": 130, "fat_g": 7, "protein_g": 8, "carb_g": 12, "sodium_mg": 100},
        {"name": "菠萝派", "energy_kcal": 260, "fat_g": 13, "protein_g": 3, "carb_g": 33, "sodium_mg": 210},
        {"name": "超值午餐套餐", "energy_kcal": 700, "fat_g": 30, "protein_g": 32, "carb_g": 80, "sodium_mg": 1200},
    ],
}


def main():
    ap = argparse.ArgumentParser(description="麦饭搭子 · 预算锁死智能搭配算法")
    ap.add_argument("--input", help="请求 JSON 文件路径；省略则从 stdin 读取")
    ap.add_argument("--out", help="结果 JSON 输出路径；省略则打印到 stdout")
    ap.add_argument("--md", action="store_true", help="只输出面向用户的 Markdown 报告")
    ap.add_argument("--selftest", action="store_true", help="用内置样例自检")
    args = ap.parse_args()

    if args.selftest:
        cfg = SELFTEST
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = json.load(sys.stdin)

    res = run(cfg)
    if args.md:
        if not res.get("ok"):
            print("❌ %s" % res.get("error"))
            return 1
        print(res["report_markdown"])
        return 0

    text = json.dumps(res, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("✅ 已写出 %s" % args.out)
    else:
        print(text)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
