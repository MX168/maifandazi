#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦饭搭子 · 拼单 AA 分账引擎

解决拼单真正的吵架源头：不是「点什么」，而是「谁付多少」。
本模块把一笔订单的实付金额，按每个人实际拿到的餐品精确拆分到「分」，
并给出公平度体检和可直接发群的转账文案。

三种分账模式：
    fair  按实吃分摊（默认）—— 谁拿得多谁付得多，优惠也按比例返还
    even  完全平摊        —— 人情局 / 懒得算
    treat 某人请客        —— 配合 --payer 使用

核心保证：
    所有人分摊金额之和 == 订单实付金额，分毫不差。
    采用最大余额法（Hare quota）处理尾数，绝不出现「少收 1 分」或「多收 1 分」。

用法：
    python scripts/split.py --result examples/demo_result.json \
        --persons examples/demo_persons.json

    python scripts/split.py --result r.json --persons p.json --mode even
    python scripts/split.py --result r.json --persons p.json --mode treat --payer 阿强
    python scripts/split.py --result r.json --persons p.json --shared 麦乐鸡 --round-yuan
    python scripts/split.py --result r.json --persons p.json --json

退出码：0 成功；2 输入文件缺失或格式错误。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- 基础工具

def fmt(fen) -> str:
    """分 -> ¥xx.xx"""
    try:
        v = int(round(float(fen)))
    except (TypeError, ValueError):
        v = 0
    return "¥%.2f" % (v / 100.0)


def largest_remainder(total: int, weights) -> list:
    """
    最大余额法（Hare quota）分配整数。

    把 total 按 weights 的权重拆成整数列表，保证 sum(result) == total。
    这是 AA 分账最关键的一步：低于销售单位「分」的余额按小数部分从大到小补齐，
    因此既不会因为四舍五入丢失金额，也不会凭空多出一分钱。
    """
    n = len(weights)
    if n == 0:
        return []
    total = int(total or 0)
    if total == 0:
        return [0] * n

    wsum = float(sum(float(w) for w in weights))
    if wsum <= 0:
        # 权重全为 0（例如所有人都没吃到东西）：退化为平摊
        weights = [1.0] * n
        wsum = float(n)

    exact = [total * float(w) / wsum for w in weights]
    base = [int(math.floor(e)) for e in exact]
    frac = [exact[i] - base[i] for i in range(n)]

    remainder = total - sum(base)
    # 按（小数部分 降序, 原权重 降序, 索引 升序）决定余额归属，保证结果稳定可复现
    order = sorted(range(n), key=lambda i: (-frac[i], -float(weights[i]), i))

    k = 0
    while remainder > 0:
        base[order[k % n]] += 1
        remainder -= 1
        k += 1
    # 理论上 floor 之后 remainder >= 0，这里仅作防御
    while remainder < 0:
        idx = order[k % n]
        if base[idx] > 0:
            base[idx] -= 1
            remainder += 1
        k += 1
    return base


# ---------------------------------------------------------------- 输入解析

def load_result(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_persons(path: str, headcount: int):
    """
    读取成员文件，返回 (persons, declared_shared)

    兼容两种格式：
      1) 数组：[{name, prefer, avoid, items:[...]}, ...]
      2) 对象：{"shared": ["大薯条"], "persons": [ ... ]}
    items 元素支持字符串（整份归该人）或 {"name": ..., "qty": n}（只认领 n 份）。
    """
    if not path or not os.path.exists(path):
        return [], []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    declared_shared = []
    if isinstance(data, dict):
        declared_shared = list(data.get("shared") or [])
        raw_persons = list(data.get("persons") or [])
    else:
        raw_persons = list(data or [])

    persons = []
    for idx, p in enumerate(raw_persons):
        name = str(p.get("name") or "成员%d" % (idx + 1))
        claims = []
        for raw in (p.get("items") or []):
            if isinstance(raw, dict):
                claims.append((str(raw.get("name") or ""), int(raw.get("qty") or 1)))
            else:
                claims.append((str(raw), 1))
        persons.append({
            "name": name,
            "prefer": list(p.get("prefer") or []),
            "avoid": list(p.get("avoid") or []),
            "claims": claims,
        })
    if not persons and headcount > 0:
        persons = [{"name": "成员%d" % (i + 1), "prefer": [], "avoid": [], "claims": []}
                   for i in range(headcount)]
    return persons, declared_shared


def build_units(result: dict) -> list:
    """
    把 planner 的输出摊平成「餐品单位」列表。

    重要：planner 的 extra_items 已经是「逐单位重复」的列表
    （同一 code 出现 n 次就代表 n 份），因此这里每个元素就是一个单位，
    绝不能再乘 extra_items 里的 qty 字段，否则会重复计价。
    """
    units = []
    for it in (result.get("slot_items") or []):
        units.append({
            "name": str(it.get("name") or ""),
            "price_fen": int(it.get("price_fen") or 0),
            "category": it.get("category") or "",
            "kcal": ((it.get("nutrition") or {}).get("energy_kcal") or 0),
            "kind": "slot",
            "owner": None,
        })
    for it in (result.get("extra_items") or []):
        units.append({
            "name": str(it.get("name") or ""),
            "price_fen": int(it.get("price_fen") or 0),
            "category": it.get("category") or "",
            "kcal": ((it.get("nutrition") or {}).get("energy_kcal") or 0),
            "kind": "extra",
            "owner": None,
        })
    return units


def assign_units(units: list, persons: list, shared_names: list):
    """
    认领分配。返回 (owned, pool, unmatched)

    owned[i]  下标 i 的人认领到的单位索引列表
    pool      无人认领 / 被声明为公共的单位索引列表
    unmatched 认领了但订单里找不到的名字（用于友好提示）
    """
    shared_set = set(shared_names or [])
    owned = [[] for _ in persons]
    pool = []  # 存单位索引，与 owned 口径保持一致
    unmatched = []

    # 先处理公共餐品声明
    for ui, u in enumerate(units):
        if u["name"] in shared_set:
            pool.append(ui)

    leftovers = list(range(len(units)))
    for pi, p in enumerate(persons):
        for claim_name, qty in p["claims"]:
            got = 0
            for ui in list(leftovers):
                if got >= qty:
                    break
                u = units[ui]
                if u["name"] in shared_set:
                    continue  # 已归入公共池
                if u["name"] == claim_name:
                    u["owner"] = pi
                    owned[pi].append(ui)
                    leftovers.remove(ui)
                    got += 1
            if got == 0:
                unmatched.append((p["name"], claim_name))

    # 剩余的：slot 按座位顺序兜底分配，extra 默认进公共池（加购小食本来就是大家分）
    seat_i = 0
    for ui in list(leftovers):
        u = units[ui]
        if u["name"] in shared_set:
            continue
        if u["kind"] == "slot":
            target = None
            for off in range(len(persons)):
                cand = (seat_i + off) % len(persons)
                if not owned[cand]:
                    target = cand
                    break
            if target is None:
                target = seat_i % len(persons)
            u["owner"] = target
            owned[target].append(ui)
            seat_i = target + 1 if target + 1 < len(persons) else 0
        else:
            pool.append(ui)
    return owned, pool, unmatched


# ---------------------------------------------------------------- 分账核心

def split_bill(result: dict, persons: list, unowned_idx: list,
               mode: str = "fair", payer: str = None):
    """
    计算每个人应付金额（单位：分）。

    mode:
      fair  —— 认领金额 + 公共池平摊，再把「实付/原价」的折扣按比例返还
      even  —— 完全平摊
      treat —— payer 全付，其余为 0
    """
    n = len(persons)
    if n == 0:
        return [], 0, {}

    units = result["_units"]
    subtotal = sum(u["price_fen"] for u in units)
    payable = int(result.get("payable_fen") or 0) or subtotal

    owned_raw = []
    for idxs in result["_owned"]:
        owned_raw.append(sum(units[i]["price_fen"] for i in idxs))

    pool_fen = sum(units[i]["price_fen"] for i in unowned_idx)
    pool_share = largest_remainder(pool_fen, [1.0] * n) if n else []

    raw = [owned_raw[i] + pool_share[i] for i in range(n)]

    if mode == "even":
        pays = largest_remainder(payable, [1.0] * n)
    elif mode == "treat":
        pays = [0] * n
        hit = False
        for i, p in enumerate(persons):
            if payer and p["name"] == payer:
                pays[i] = payable
                hit = True
        if not hit:
            pays[0] = payable  # 未指定 / 名字对不上，默认第一位请客
    else:
        # fair：以「认领 + 公共池份额」为权重，把实付总额精确拆开
        pays = largest_remainder(payable, [float(r) for r in raw])

    meta = {
        "payable_fen": payable,
        "subtotal_fen": subtotal,
        "pool_fen": pool_fen,
        "discount_fen": max(0, subtotal - payable),
        "raw_fen": raw,
        "owned_fen": owned_raw,
        "pool_share_fen": pool_share,
    }
    return pays, payable, meta


# ---------------------------------------------------------------- 趣味层

PAY_TIERS = [
    (25.0, "💸", "今日散财童子", "你一个人的账单能顶两个人，建议下次先声明预算上限。"),
    (10.0, "🤝", "AA 界老实人", "多付的那部分都被大家默默记在心里了。"),
    (-10.0, "⚖️", "精准 AA 标兵", "不多不少刚刚好，你就是群收款的天选结算单位。"),
    (-25.0, "😏", "白嫖界新秀", "吃得含蓄付得从容，这波操作很丝滑。"),
    (-1e18, "🎯", "占便宜王者", "建议下次主动提出去买单，以正队风。"),
]

HARMONY_TIERS = [
    (85, "🕊️ 和气生财", "差距极小，这顿吃完关系只会更好。"),
    (70, "🍵 小吵怡情", "略有出入，转账时不要太精确，留点人情味。"),
    (50, "🗣️ 需要谈判", "差距已经肉眼可见，建议席间先把话说开。"),
    (0, "🎲 建议摇骰子", "这差距不 AA 了，直接摇骰子定胜负比较体面。"),
]


def pay_tier(dev: float):
    for thr, emoji, title, desc in PAY_TIERS:
        if dev >= thr:
            return emoji, title, desc
    return PAY_TIERS[-1][1:]


def harmony(max_abs_dev: float):
    score = max(0, min(100, int(round(100 - max_abs_dev))))
    for thr, title, desc in HARMONY_TIERS:
        if score >= thr:
            return score, title, desc
    return score, HARMONY_TIERS[-1][1], HARMONY_TIERS[-1][2]


def round_yuan_plan(pays: list, persons: list, collector: str):
    """
    抹零友好版：把每人金额抹到整元，差额由收钱的人吸收。
    群里转账时大家更愿意转整数，这个版本就是给真人用的。
    """
    rounded = [int(round(p / 100.0)) * 100 for p in pays]
    diff = sum(rounded) - sum(pays)
    idx = 0
    for i, p in enumerate(persons):
        if collector and p["name"] == collector:
            idx = i
            break
    rounded[idx] -= diff
    return rounded, diff, idx


# ---------------------------------------------------------------- 输出

def build_report(result: dict, persons: list, pays: list, meta: dict,
                 pool_idx: list, unmatched: list, mode: str,
                 payer_name: str, do_round: bool, collector: str) -> str:
    L = []
    units = result["_units"]
    n = len(persons)
    payable = meta["payable_fen"]
    avg = payable / n if n else 0

    L.append("### 💸 拼单 AA 分账")
    L.append("")

    mode_label = {"fair": "按实吃分摊", "even": "完全平摊", "treat": "一人请客"}[mode]
    extra = ""
    if mode == "treat":
        extra = "（买单：%s）" % (payer_name or persons[0]["name"])
    L.append("**分账模式**：%s%s｜实付 **%s**｜%d 人" % (mode_label, extra, fmt(payable), n))
    L.append("")

    # ---- 明细表
    L.append("| 成员 | 认领餐品 | 公共均摊 | 结算差额 | **应付** | 与人均差 | 段位 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    devs = []
    for i, p in enumerate(persons):
        own = meta["owned_fen"][i]
        ps = meta["pool_share_fen"][i]
        raw_i = meta["raw_fen"][i]
        diff = pays[i] - raw_i
        dev = ((pays[i] - avg) / avg * 100) if avg else 0.0
        # 避免出现「-0.0%」这种观感问题
        if abs(dev) < 0.05:
            dev = 0.0
        devs.append(dev)
        emoji, title, _ = pay_tier(dev)
        pool_cell = fmt(ps) if ps else "—"
        if diff == 0:
            diff_cell = "—"
        elif diff < 0:
            diff_cell = "省 %s" % fmt(-diff)
        else:
            diff_cell = "多摊 %s" % fmt(diff)
        L.append("| **%s** | %s | %s | %s | **%s** | %+.1f%% | %s %s |"
                 % (p["name"], fmt(own), pool_cell, diff_cell, fmt(pays[i]), dev, emoji, title))
    L.append("")
    L.append("> 结算差额 = 应付 − 实吃原价。**省**表示优惠已按比例返还到你头上；"
             "**多摊**表示你替别人补了差额。")
    L.append("")

    # ---- 公共池
    if pool_idx:
        names = ["%s %s" % (units[i]["name"], fmt(units[i]["price_fen"])) for i in pool_idx]
        shares = meta["pool_share_fen"]
        lo, hi = (min(shares), max(shares)) if shares else (0, 0)
        L.append("**🍟 公共 AA 池**（全员均分）：%s" % "、".join(names))
        L.append("")
        if lo == hi:
            L.append("> 每人承担 %s，合计 %s" % (fmt(lo), fmt(meta["pool_fen"])))
        else:
            L.append("> 每人承担 %s ~ %s，合计 %s；整数除不尽的 %d 分"
                     "已按最大余额法分给部分成员，总和分毫不差。"
                     % (fmt(lo), fmt(hi), fmt(meta["pool_fen"]), hi - lo))
        L.append("")

    # ---- 每人实吃明细
    L.append("<details><summary>📦 每人实吃明细（点击展开）</summary>")
    L.append("")
    for i, p in enumerate(persons):
        idxs = result["_owned"][i]
        if not idxs:
            continue
        L.append("**%s** —— 应付 %s" % (p["name"], fmt(pays[i])))
        for ui in idxs:
            u = units[ui]
            kcal = " %d kcal" % u["kcal"] if u["kcal"] else ""
            L.append("- %s %s%s" % (u["name"], fmt(u["price_fen"]), kcal))
        L.append("")
    L.append("</details>")
    L.append("")

    # ---- 公平度体检（请客模式下无意义，跳过）
    if mode != "treat":
        max_abs = max((abs(d) for d in devs), default=0.0)
        score, h_title, h_desc = harmony(max_abs)
        L.append("### ⚖️ 公平度体检")
        L.append("")
        L.append("| 指标 | 数值 |")
        L.append("| --- | --- |")
        L.append("| 人均 | %s |" % fmt(avg))
        L.append("| 最高 | %s（%s） |" % (fmt(max(pays) if pays else 0),
                                         persons[pays.index(max(pays))]["name"] if pays else "—"))
        L.append("| 最低 | %s（%s） |" % (fmt(min(pays) if pays else 0),
                                         persons[pays.index(min(pays))]["name"] if pays else "—"))
        L.append("| 极差 | %s |" % fmt((max(pays) - min(pays)) if pays else 0))
        L.append("| 标准差 | %s |" % fmt(statistics.pstdev(pays) if len(pays) > 1 else 0))
        L.append("")
        L.append("**和气指数 %d/100 —— %s**：%s" % (score, h_title, h_desc))
        L.append("")

    # ---- 群收款文案
    L.append("### 💬 群收款文案（可直接复制）")
    L.append("")
    L.append("```")
    L.append("【麦饭搭子 AA】本次共 %s，%d 人" % (fmt(payable), n))
    for i, p in enumerate(persons):
        L.append("%s %s" % (p["name"], fmt(pays[i])))
    L.append("合计 %s ✅ 已算平，AA 不吵架" % fmt(sum(pays)))
    L.append("```")
    L.append("")

    if mode == "treat":
        who = payer_name or (persons[0]["name"] if persons else "")
        L.append("> 本次由 **%s** 请客。记账建议：下次轮到下一位。" % who)
        L.append("")

    # ---- 抹零版
    if do_round and n:
        rounded, diff, cidx = round_yuan_plan(pays, persons, collector)
        L.append("### 🧮 抹零友好版（转整数）")
        L.append("")
        L.append("| 成员 | 转账金额 |")
        L.append("| --- | --- |")
        for i, p in enumerate(persons):
            mark = "（收钱方，吸收差额）" if i == cidx else ""
            L.append("| %s | %s %s |" % (p["name"], fmt(rounded[i]), mark))
        L.append("")
        if diff == 0:
            tail = "抹零后金额刚好闭合，无需调整。"
        elif diff < 0:
            tail = ("抹零后合计少了 %s，由收钱方 **%s** 补齐。"
                    % (fmt(-diff), persons[cidx]["name"]))
        else:
            tail = ("抹零后合计多了 %s，由收钱方 **%s** 少收。"
                    % (fmt(diff), persons[cidx]["name"]))
        L.append("> 让人转整数更方便；%s实际权益请以精确版为准。" % tail)
        L.append("")

    # ---- 提示
    if unmatched:
        shown = "、".join("%s→%s" % (a, b) for a, b in unmatched[:5])
        L.append("> ⚠️ 认领了但订单中没有：%s（已忽略）" % shown)
        L.append("")
    if meta["discount_fen"] > 0:
        L.append("> 🎫 本单优惠 %s，已按各人实吃金额比例返还，吃得多省得多。"
                 % fmt(meta["discount_fen"]))
        L.append("")
    L.append("> 金额精确到分，各人应付之和 = %s，与实付完全一致。"
             % fmt(sum(pays)))
    return "\n".join(L)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="麦饭搭子 · 拼单 AA 分账引擎")
    ap.add_argument("--result", required=True, help="planner 输出的结果 JSON")
    ap.add_argument("--persons", help="成员偏好 JSON")
    ap.add_argument("--shared", action="append", default=[],
                    help="公共餐品名（大家平分的那些），逗号分隔，可重复传参")
    ap.add_argument("--mode", choices=["fair", "even", "treat"], default="fair",
                    help="分账模式：按实吃 / 平摊 / 请客")
    ap.add_argument("--payer", help="请客模式下的买单者姓名")
    ap.add_argument("--collector", help="抹零差额承担者，默认第一位")
    ap.add_argument("--round-yuan", action="store_true", help="额外输出抹零到整元的版本")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    ap.add_argument("--out", help="写入文件路径")
    args = ap.parse_args()

    if not os.path.exists(args.result):
        print("❌ 输入文件不存在：%s" % args.result)
        return 2
    if args.persons and not os.path.exists(args.persons):
        print("❌ 成员文件不存在：%s" % args.persons)
        return 2

    result = load_result(args.result)
    headcount = int(result.get("headcount") or 0) or len(result.get("slot_items") or []) or 1
    persons, declared_shared = load_persons(args.persons, headcount)
    if not persons:
        print("❌ 没有可用成员信息")
        return 2
    # 保证人数 == 餐位数，避免出现「有人没饭吃也付钱」或「多出来的人」
    if len(persons) < headcount:
        for i in range(len(persons), headcount):
            persons.append({"name": "成员%d" % (i + 1), "prefer": [], "avoid": [],
                            "claims": []})
    persons = persons[:headcount]

    shared = list(declared_shared)
    for chunk in (args.shared or []):
        shared += [s.strip() for s in str(chunk).split(",") if s.strip()]

    units = build_units(result)
    owned, pool_idx, unmatched = assign_units(units, persons, shared)
    result["_units"] = units
    result["_owned"] = owned

    pays, _payable, meta = split_bill(result, persons, pool_idx,
                                      mode=args.mode, payer=args.payer)

    if args.json:
        payload = {
            "mode": args.mode,
            "payer": args.payer if args.mode == "treat" else None,
            "payable_fen": meta["payable_fen"],
            "members": [
                {
                    "name": p["name"],
                    "owned_fen": meta["owned_fen"][i],
                    "pool_share_fen": meta["pool_share_fen"][i],
                    "pay_fen": pays[i],
                    "items": [units[ui]["name"] for ui in owned[i]],
                    "kcal": int(sum(units[ui]["kcal"] for ui in owned[i])),
                } for i, p in enumerate(persons)
            ],
            "pool": [{"name": units[i]["name"], "price_fen": units[i]["price_fen"]}
                     for i in pool_idx],
            "unmatched_claims": [{"person": a, "item": b} for a, b in unmatched],
            "sum_check": sum(pays) == meta["payable_fen"],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = build_report(result, persons, pays, meta, pool_idx, unmatched,
                            args.mode, args.payer, args.round_yuan, args.collector)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("✅ 已生成：%s" % args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
