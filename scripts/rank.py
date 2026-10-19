#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦饭搭子 · 麦门段位与成就徽章系统

把「点得省、分得清、不超支」这些本来很枯燥的行为，变成一套可积累的设定：
累计战绩晋升段位（麦门萌新 → 麦门至尊），达成条件解锁徽章，最后输出一张
「个人麦门档案卡」。

数据落在本地 ~/.maifan-dazi/rank.json（可用 MAIFAN_RANK 覆盖），
与 profile.py 的档案分开存放，互不干扰，同样不需要数据库。

用法：
    python rank.py show                      # 查看当前段位与徽章
    python rank.py show --md                 # 输出 Markdown 档案卡
    python rank.py record --result r.json    # 记录一次拼单并结算解锁
    python rank.py record --result r.json --split s.json --md
    python rank.py reset                     # 清空战绩（段位归零）

设计约定：
- 金额一律用「分」做整数运算，展示时才 ÷100
- 战绩点只增不减，段位不会掉段（避免用户因一次失误受挫）
- 徽章一旦解锁永久保留
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".maifan-dazi", "rank.json")

# ---------------------------------------------------------------- 段位表

# (所需战绩点, 称号, 图标, 一句话人设)
RANKS = [
    (0,   "麦门萌新", "🎒", "刚踏进麦门，甜筒都还没吃热乎"),
    (30,  "麦门学徒", "🍟", "知道薯条要趁热，但还会在柜台前犹豫"),
    (80,  "麦门老饕", "🍔", "套餐结构了然于胸，优惠券用得比谁都溜"),
    (160, "麦门达人", "🏅", "闭着眼都能算出怎么点最省，同事开始向你请教"),
    (300, "麦门宗师", "👑", "你点的套餐已经是别人抄作业的标准答案"),
    (500, "麦门至尊", "🐉", "传说麦门的光会照进你的每一张订单"),
]

# ---------------------------------------------------------------- 徽章表

# 每个徽章：done(stats) 判定是否达成，progress(stats) 返回 (当前, 需要)
BADGES = [
    {
        "id": "zero_over", "icon": "🎯", "name": "零超支守卫",
        "rule": "累计 3 次在预算内拿下",
        "done": lambda s: s["no_over"] >= 3,
        "progress": lambda s: (min(s["no_over"], 3), 3),
    },
    {
        "id": "exact_split", "icon": "⚖️", "name": "AA 标兵",
        "rule": "分账精确闭合 1 次（各人应付之和 == 实付）",
        "done": lambda s: s["exact_split"] >= 1,
        "progress": lambda s: (min(s["exact_split"], 1), 1),
    },
    {
        "id": "big_save", "icon": "💰", "name": "省钱战神",
        "rule": "单次省下 ¥30 以上",
        "done": lambda s: s["big_save"] >= 1,
        "progress": lambda s: (min(s["big_save"], 1), 1),
    },
    {
        "id": "blacklist", "icon": "🧨", "name": "避雷大师",
        "rule": "永久拉黑 3 样东西",
        "done": lambda s: s["blacklist_n"] >= 3,
        "progress": lambda s: (min(s["blacklist_n"], 3), 3),
    },
    {
        "id": "streak", "icon": "🔥", "name": "连击王",
        "rule": "连续 3 天拼单",
        "done": lambda s: s["streak"] >= 3,
        "progress": lambda s: (min(s["streak"], 3), 3),
    },
    {
        "id": "dessert", "icon": "🍦", "name": "甜品终结者",
        "rule": "单次点 2 件以上甜品",
        "done": lambda s: s["max_dessert"] >= 2,
        "progress": lambda s: (min(s["max_dessert"], 2), 2),
    },
    {
        "id": "social", "icon": "👥", "name": "社交达人",
        "rule": "单次 6 人以上拼单",
        "done": lambda s: s["max_headcount"] >= 6,
        "progress": lambda s: (min(s["max_headcount"], 6), 6),
    },
    {
        "id": "light", "icon": "🌱", "name": "减脂卫士",
        "rule": "单次整套低于 1500 kcal（须每样都有营养数据才算）",
        "done": lambda s: (s["min_kcal"] is not None and s["min_kcal"] < 1500),
        "progress": lambda s: (0 if (s["min_kcal"] is None or s["min_kcal"] >= 1500) else 1, 1),
    },
    {
        "id": "regular", "icon": "📅", "name": "麦门常客",
        "rule": "累计拼单 10 次",
        "done": lambda s: s["orders"] >= 10,
        "progress": lambda s: (min(s["orders"], 10), 10),
    },
    {
        "id": "tight", "icon": "🎲", "name": "预算魔术师",
        "rule": "单次花到预算 95% 以上且没超支",
        "done": lambda s: s["tight"] >= 1,
        "progress": lambda s: (min(s["tight"], 1), 1),
    },
]

EMPTY_STATS = {
    "orders": 0,          # 累计拼单次数
    "save_fen": 0,        # 累计省下（分）
    "no_over": 0,         # 零超支次数
    "max_headcount": 0,   # 单次最多人数
    "min_kcal": None,     # 单次最低总热量
    "max_dessert": 0,     # 单次最多甜品件数
    "tight": 0,           # 贴预算（花到 95%+ 且不超）次数
    "big_save": 0,        # 单次省 ≥ ¥30 的次数
    "exact_split": 0,     # 分账精确闭合次数
    "blacklist_n": 0,     # 避雷清单条数（每次结算时同步）
    "streak": 0,          # 连续拼单天数
    "last_day": "",       # 上次拼单日期 YYYY-MM-DD
    "badges": [],         # 已解锁徽章 id
}

CNY = lambda fen: "{:.2f}".format(fen / 100.0)


# ---------------------------------------------------------------- 持久化


def path() -> str:
    return os.environ.get("MAIFAN_RANK") or DEFAULT_PATH


def load() -> dict:
    p = path()
    if not os.path.exists(p):
        return dict(EMPTY_STATS)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(EMPTY_STATS)
    for k, v in EMPTY_STATS.items():
        data.setdefault(k, v)
    if not isinstance(data.get("badges"), list):
        data["badges"] = []
    return data


def save(data: dict) -> str:
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


# ---------------------------------------------------------------- 计算


def points(stats: dict) -> int:
    """战绩点 = 拼单次数×10 + 累计省下(元)×1 + 零超支×5"""
    return stats["orders"] * 10 + stats["save_fen"] // 100 + stats["no_over"] * 5


def rank_of(pts: int) -> tuple:
    """返回 (当前段位, 下一段位, 下段所需点数)。已是最高段时下一段为 None。"""
    cur = RANKS[0]
    nxt = None
    for i, r in enumerate(RANKS):
        if pts >= r[0]:
            cur = r
            nxt = RANKS[i + 1] if i + 1 < len(RANKS) else None
    return cur, nxt, (nxt[0] if nxt else None)


def unlocked(stats: dict) -> list:
    return [b for b in BADGES if b["id"] in stats["badges"]]


def locked_with_progress(stats: dict) -> list:
    out = []
    for b in BADGES:
        if b["id"] in stats["badges"]:
            continue
        cur, need = b["progress"](stats)
        out.append((b, cur, need))
    return out


def progress_bar(cur: int, total: int, width: int = 12) -> str:
    if total <= 0:
        return "▓" * width
    filled = int(round(width * min(cur, total) / total))
    return "▓" * filled + "░" * (width - filled)


# ---------------------------------------------------------------- 结算


def _blacklist_count() -> int:
    """从 profile.py 的档案里读避雷清单条数；读不到就当 0。"""
    try:
        prof = os.path.join(os.path.expanduser("~"), ".maifan-dazi", "profile.json")
        prof = os.environ.get("MAIFAN_PROFILE") or prof
        if not os.path.exists(prof):
            return 0
        with open(prof, "r", encoding="utf-8") as f:
            return len(json.load(f).get("blacklist") or [])
    except (OSError, ValueError):
        return 0


def _day(s: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(s)
    except (TypeError, ValueError):
        return datetime.date.min


def absorb(stats: dict, result: dict, split: dict | None = None) -> list:
    """把一次拼单的结果吸收进战绩，返回本次新解锁的徽章列表。

    result 为 planner.py 的输出；split 为 split.py --json 的输出（可选）。
    两条硬性承诺在这里被转成可积累的设定：不超支算「零超支」，分账闭合算「AA 标兵」。
    """
    if not result.get("ok"):
        return []

    today = datetime.date.today()
    stats["orders"] += 1

    # 连续天数：昨天拼过则 +1，同一天不重复计，否则重置为 1
    last = _day(stats.get("last_day", ""))
    if last == today:
        pass
    elif last == today - datetime.timedelta(days=1):
        stats["streak"] = int(stats.get("streak") or 0) + 1
    else:
        stats["streak"] = 1
    stats["last_day"] = today.isoformat()

    headcount = int(result.get("headcount") or 0)
    if headcount > stats["max_headcount"]:
        stats["max_headcount"] = headcount

    budget_fen = int(result.get("budget_fen") or 0)
    payable_fen = int(result.get("payable_fen") or 0)
    if budget_fen > 0 and payable_fen <= budget_fen:
        stats["no_over"] += 1
        # 贴预算：花到 95% 以上且没超
        if payable_fen * 100 >= budget_fen * 95:
            stats["tight"] += 1

    save_fen = int(result.get("coupon_save_fen") or 0)
    original_fen = int(result.get("original_fen") or 0)
    # 省的錢优先取券省；若套餐本身就有折扣，用原价与实付的差额兜底
    gain = save_fen if save_fen > 0 else max(0, original_fen - payable_fen)
    stats["save_fen"] += gain
    if gain >= 3000:  # ¥30
        stats["big_save"] += 1

    # 甜品件数：extra_items 是逐单位重复的列表，每个元素算一件
    dessert = sum(1 for it in (result.get("extra_items") or [])
                  if (it.get("category") or "") == "甜品")
    if dessert > stats["max_dessert"]:
        stats["max_dessert"] = dessert

    # 热量只有在「每样餐品都查到营养数据」时才计入，否则宁可不判。
    # 官方 list-nutrition-foods 常返回部分缺失，用残缺热量去发「减脂卫士」
    # 会误导用户——本项目不编造数据，也不拿不完整的数据充数。
    items = list(result.get("slot_items") or []) + list(result.get("extra_items") or [])
    nut_complete = bool(items) and all(it.get("nutrition") for it in items)
    if nut_complete:
        nut = result.get("total_nutrition") or {}
        kcal = nut.get("energy_kcal")
        if isinstance(kcal, (int, float)) and kcal > 0:
            if stats["min_kcal"] is None or kcal < stats["min_kcal"]:
                stats["min_kcal"] = int(kcal)

    if split and split.get("sum_check"):
        stats["exact_split"] += 1

    stats["blacklist_n"] = _blacklist_count()

    # 结算徽章
    fresh = []
    for b in BADGES:
        if b["id"] in stats["badges"]:
            continue
        if b["done"](stats):
            stats["badges"].append(b["id"])
            fresh.append(b)
    return fresh


# ---------------------------------------------------------------- 渲染


def render_md(stats: dict, fresh: list | None = None) -> str:
    pts = points(stats)
    cur, nxt, need = rank_of(pts)
    got = unlocked(stats)

    L = []
    L.append("### 🏅 麦门档案")
    L.append("")
    L.append("**%s %s**　%s" % (cur[2], cur[1], cur[3]))
    L.append("")
    if nxt:
        L.append("段位进度 `%s`　%d / %d → 下一档 **%s**（还差 %d 点）"
                 % (progress_bar(pts - cur[0], need - cur[0]),
                    pts, need, nxt[1], need - pts))
    else:
        L.append("段位进度 `%s`　**已满级**，麦门再无更高处" % progress_bar(1, 1))
    L.append("")
    L.append("| 累计拼单 | 累计省下 | 零超支 | 连续天数 | 战绩点 |")
    L.append("| ---: | ---: | ---: | ---: | ---: |")
    L.append("| %d 次 | ¥%s | %d 次 | %d 天 | **%d** |"
             % (stats["orders"], CNY(stats["save_fen"]),
                stats["no_over"], int(stats.get("streak") or 0), pts))
    L.append("")
    L.append("**徽章 %d / %d**" % (len(got), len(BADGES)))
    L.append("")
    if got:
        L.append(" ".join("%s %s" % (b["icon"], b["name"]) for b in got))
    else:
        L.append("_还没有徽章，点一单就有了。_")
    L.append("")

    rest = locked_with_progress(stats)
    if rest:
        L.append("**待解锁**")
        L.append("")
        for b, c, n in rest:
            L.append("- %s **%s** —— %s　`%s` %d/%d"
                     % ("🔒", b["name"], b["rule"], progress_bar(c, n), c, n))
        L.append("")

    if fresh:
        L.append("🎉 **本次新解锁**：" + "、".join("%s %s" % (b["icon"], b["name"]) for b in fresh))
        L.append("")
    return "\n".join(L)


def render_text(stats: dict, fresh: list | None = None) -> str:
    pts = points(stats)
    cur, nxt, need = rank_of(pts)
    got = unlocked(stats)

    L = []
    L.append("%s %s　（%s）" % (cur[2], cur[1], cur[3]))
    L.append("战绩点 %d" % pts + ("　→ 下一档 %s 还差 %d 点" % (nxt[1], need - pts) if nxt else "　已满级"))
    L.append("累计拼单 %d 次｜累计省下 ¥%s｜零超支 %d 次｜连续 %d 天"
             % (stats["orders"], CNY(stats["save_fen"]),
                stats["no_over"], int(stats.get("streak") or 0)))
    L.append("")
    L.append("徽章 %d/%d：" % (len(got), len(BADGES)))
    if got:
        for b in got:
            L.append("  %s %s —— %s" % (b["icon"], b["name"], b["rule"]))
    else:
        L.append("  （暂无）")
    rest = locked_with_progress(stats)
    if rest:
        L.append("")
        L.append("待解锁：")
        for b, c, n in rest:
            L.append("  🔒 %s —— %s（%d/%d）" % (b["name"], b["rule"], c, n))
    if fresh:
        L.append("")
        L.append("🎉 本次新解锁：" + "、".join("%s %s" % (b["icon"], b["name"]) for b in fresh))
    return "\n".join(L)


# ---------------------------------------------------------------- CLI


def cmd_show(stats, args):
    print(render_md(stats) if args.md else render_text(stats))
    return None


def cmd_record(stats, args):
    with open(args.result, "r", encoding="utf-8") as f:
        result = json.load(f)
    split = None
    if args.split:
        with open(args.split, "r", encoding="utf-8") as f:
            split = json.load(f)
    fresh = absorb(stats, result, split)
    if not fresh and not result.get("ok"):
        print("⚠️ 方案未生成成功，不记录战绩")
        return None
    print(render_md(stats, fresh) if args.md else render_text(stats, fresh))
    return True


def cmd_reset(stats, args):
    p = path()
    if os.path.exists(p):
        os.remove(p)
    print("✅ 已清空战绩：%s" % p)
    return None


def main():
    ap = argparse.ArgumentParser(description="麦饭搭子 · 麦门段位与成就徽章")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="查看段位与徽章")
    p_show.add_argument("--md", action="store_true", help="输出 Markdown 档案卡")
    p_show.set_defaults(func=cmd_show)

    p_rec = sub.add_parser("record", help="记录一次拼单并结算解锁")
    p_rec.add_argument("--result", required=True, help="planner.py 输出 JSON")
    p_rec.add_argument("--split", help="split.py --json 输出（用于 AA 标兵徽章）")
    p_rec.add_argument("--md", action="store_true", help="输出 Markdown 档案卡")
    p_rec.set_defaults(func=cmd_record)

    p_rst = sub.add_parser("reset", help="清空战绩")
    p_rst.set_defaults(func=cmd_reset)

    args = ap.parse_args()
    stats = load()
    changed = args.func(stats, args)
    if changed:
        save(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
