#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦饭搭子 · 趣味模块

三个子命令：
    personality  麦门干饭人格测试（每人一个标签 + 组队报告）
    lucky        麦门幸运菜品抽签
    poster       省钱战绩海报文案
    all          一次性跑完上面三个（推荐）

用法示例：
    python fun.py personality --input persons.json
    python fun.py lucky --items 板烧鸡腿堡,薯条,可乐
    python fun.py poster --input planner_result.json
    python fun.py all --result planner_result.json --persons persons.json

persons.json 结构（可选，缺省按一个匿名干饭人处理）：
    [{"name":"小明","prefer":["炸鸡"],"avoid":["辣"],
      "items":["麦辣鸡翅","可口可乐(中)"]}]

文案库位于本文件顶部的 TAG_RULES / LUCKY_BLESSINGS / RANK_TIERS，
需要扩展或改玩梗风格时直接改这些常量即可。
"""

from __future__ import annotations

import argparse
import json
import random
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CNY = lambda fen: "{:.2f}".format(fen / 100.0)

# ----------------------------------------------------------------- 人格文案库

# (标签, 评语模板[(%s 会代入人名)], 命中关键词, 权重)
TAG_RULES = [
    ("炸鸡狂热信徒", "%s的餐盘里没有炸物就不算吃饭，麦门炸鸡事业部终身荣誉会员。",
     ["炸鸡", "鸡翅", "鸡块", "鸡排", "鸡米花", "脆鸡", "香骨", "鸡柳"], 3.0),
    ("堡系原教旨主义者", "%s坚信「世界的本质是两片面包夹一切」，汉堡区常驻住民。",
     ["汉堡", "堡", "牛堡", "鸡腿堡"], 2.6),
    ("甜品治愈选手", "%s的血糖和心情必须有一个在高位，先把快乐吃进去再说。",
     ["派", "圣代", "甜筒", "圆筒", "冰淇淋", "麦旋风", "蛋糕", "巧克力", "甜品"], 2.8),
    ("快乐水信徒", "%s的快乐有气泡，第一口永远是最爽的那口。",
     ["可乐", "雪碧", "芬达", "汽水", "气泡", "无糖"], 2.4),
    ("咖啡续命组", "%s靠咖啡因维持运行，今日份清醒已自动到账。",
     ["咖啡", "美式", "拿铁", "澳白", " Flat White"], 2.4),
    ("卷物研究者", "%s认为卷起来的东西天生好吃，也是对 handheld 美学的致敬。",
     ["卷", "鸡肉卷", "牛肉卷"], 2.2),
    ("减脂自律战士", "%s在点麦当劳这件事上做到了极致自律，热量表比菜单还熟。",
     ["减脂", "低卡", "少油", "沙拉", "玉米杯", "玉米", "0度", "无糖"], 3.2),
    ("均衡营养委员", "%s讲究荤素搭配、干稀平衡，是拼单团里的定海神针。",
     ["均衡", "无偏好", "蔬菜", "沙拉"], 2.0),
    ("早餐起早氏", "%s起得比早餐供应还早，是最有仪式感的那一批麦门人。",
     ["早餐", "早安", "pancake", "热香饼"], 2.0),
]

# 由 avoid 数量触发的附加标签
PICKY_TAG = ("忌口贵族", "%s的忌口清单比菜单还长，点单难度直接拉满。")
TIGHT_TAG = ("极致省钱大神", "%s把预算卡到分位，优惠券在他手里能叠出花来。")

TEAM_HEADLINES = [
    "本次组队干饭均衡，全员麦门忠诚度达标！",
    "这是一支能打的组合：有人冲锋有人控预算，干饭效率拉满。",
    "口味互补、预算在线，这样的队伍建议长期固定。",
    "全员风格各异但目标一致：吃好且不超支，很难不成功。",
]

# --------------------------------------------------------------- 幸运菜品文案

LUCKY_BLESSINGS = [
    "寓意：干饭顺利，事事开挂，快乐超标！",
    "寓意：今天也要好好吃饭，好运会从第一口开始。",
    "寓意：咬掉烦恼，补充欧气，下午的会很可能会取消。",
    "寓意：热气腾腾，运势up，连 bug 都会自己修好。",
    "寓意：此物入口，霉运退散，甲方今天一定会说「挺好的」。",
    "寓意：酥脆即正义，今天适合做一件一直不敢做的事。",
    "寓意：甜度正好，烦恼减半，收工准点。",
]

# ----------------------------------------------------------------- 战绩段位

RANK_TIERS = [
    (0.30, "麦门省钱战神 🏆", "这个折扣已经接近极限操作了，请收下膝盖。"),
    (0.20, "省钱宗师 🧙", "你对优惠的理解已经超越大多数人。"),
    (0.10, "精算师 🧮", "每一分钱都花在了刀刃上。"),
    (0.02, "稳健派 👍", "优惠拿捏住了，稳。"),
    (0.00, "原始人 🍔", "没占到便宜也没关系，吃饱就是正义。"),
]


# ---------------------------------------------------------------- 人格引擎

def score_person(person: dict):
    prefer = [str(x) for x in (person.get("prefer") or [])]
    items = [str(x) for x in (person.get("items") or [])]
    avoid = [str(x) for x in (person.get("avoid") or [])]
    hay = " ".join(prefer + items).lower()

    scores = []
    for tag, tpl, kws, weight in TAG_RULES:
        hits = sum(1 for k in kws if k.lower() in hay)
        if hits:
            scores.append((weight * hits + (1.0 if any(k in " ".join(prefer).lower() for k in kws) else 0),
                           weight, tag, tpl))
    # 偏好明确表达「减脂/低卡」时权重更高
    if any(k in " ".join(prefer).lower() for k in ["减脂", "低卡", "少油"]):
        scores = [(s + 4.0, w, t, p) if t == "减脂自律战士" else (s, w, t, p) for s, w, t, p in scores]

    scores.sort(key=lambda x: (-x[0], -x[1]))
    if person.get("tight_budget") and not scores:
        scores.append((1.0, 0.0, TIGHT_TAG[0], TIGHT_TAG[1]))
    return scores, avoid, items


def render_personality(persons: list) -> str:
    lines = ["### 🎭 麦门干饭人格报告", ""]
    tag_counter = {}
    for i, p in enumerate(persons, 1):
        name = p.get("name") or ("干饭人%d" % i)
        scores, avoid, items = score_person(p)
        if scores:
            _, _, tag, tpl = scores[0]
        elif len(avoid) >= 3:
            tag, tpl = PICKY_TAG
        else:
            tag, tpl = ("均衡体验派", "%s什么都来一点，是拼单团里最好说话的人。")
        lines.append("**%s —— %s**" % (name, tag))
        lines.append("> %s" % (tpl % name))
        if avoid:
            lines.append("")
            lines.append("- 忌口：%s" % "、".join(avoid))
        if items:
            lines.append("- 餐品：%s" % "、".join(items))
        lines.append("")
        tag_counter[tag] = tag_counter.get(tag, 0) + 1
        if len(avoid) >= 3:
            lines.append("> 附加标签：%s（忌口 %d 项）" % (PICKY_TAG[0], len(avoid)))
            lines.append("")
            tag_counter[PICKY_TAG[0]] = tag_counter.get(PICKY_TAG[0], 0) + 1

    headline = TEAM_HEADLINES[sum(tag_counter.values()) % len(TEAM_HEADLINES)]
    top_tags = sorted(tag_counter.items(), key=lambda x: -x[1])
    lines.append("**组队鉴定**：%s" % headline)
    if top_tags:
        lines.append("")
        lines.append("队伍构成：%s" % " · ".join("%s×%d" % (t, c) for t, c in top_tags))
    if len(top_tags) >= 3:
        lines.append("")
        lines.append("> 口味覆盖面广，建议保持这个队伍长期开饭。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 幸运菜品

def render_lucky(names: list, seed=None) -> str:
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    pick = rng.choice(names)
    blessing = rng.choice(LUCKY_BLESSINGS)
    lines = ["### 🔮 麦门幸运菜品", "",
             "今日幸运餐品：**%s**" % pick, "",
             "> %s" % blessing]
    return "\n".join(lines)


# ---------------------------------------------------------------- 战绩海报

def render_poster(res: dict) -> str:
    n = res.get("headcount", 1)
    budget = res.get("budget_fen", 0)
    payable = res.get("payable_fen", 0)
    original = res.get("original_fen", 0) or payable
    coupon = res.get("coupon_save_fen", 0)
    remain = res.get("remain_fen", 0)
    per = payable // n if n else payable

    base_save = max(0, original - payable)
    ratio = base_save / float(original) if original else 0.0
    rank, rank_comment = RANK_TIERS[-1][1], RANK_TIERS[-1][2]
    for threshold, tier, comment in RANK_TIERS:
        if ratio >= threshold:
            rank, rank_comment = tier, comment
            break

    lines = ["### 🏅 省钱战绩海报", "",
             "```",
             "【麦饭搭子战绩】%d人拼单，预算¥%s" % (n, CNY(budget)),
             "智能搭配省 ¥%s！人均 ¥%s" % (CNY(base_save), CNY(per)),
             "剩余结余 ¥%s，%s" % (CNY(remain), "麦门省钱达人认证✅" if base_save > 0 else "麦门干饭认证✅"),
             "```", ""]
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append("| 原价合计 | ¥%s |" % CNY(original))
    if coupon:
        lines.append("| 券再加省 | -¥%s |" % CNY(coupon))
    lines.append("| 实付 | **¥%s** |" % CNY(payable))
    lines.append("| 立省 | ¥%s（%.1f%%） |" % (CNY(base_save), ratio * 100))
    lines.append("| 人均 | ¥%s |" % CNY(per))
    lines.append("")
    lines.append("**段位：%s** —— %s" % (rank, rank_comment))
    return "\n".join(lines)


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="麦饭搭子 · 趣味模块")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("personality", help="麦门干饭人格测试")
    p1.add_argument("--input", help="persons.json 路径")
    p1.add_argument("--json", help="内联 JSON 字符串")

    p2 = sub.add_parser("lucky", help="幸运菜品抽签")
    p2.add_argument("--items", help="逗号分隔的餐品名")
    p2.add_argument("--input", help="planner 结果 JSON（自动提取餐品）")
    p2.add_argument("--seed", type=int, help="随机种子，便于复现")

    p3 = sub.add_parser("poster", help="省钱战绩海报文案")
    p3.add_argument("--input", help="planner 结果 JSON 路径")
    p3.add_argument("--json", help="内联 JSON 字符串")

    p4 = sub.add_parser("all", help="一次性生成全部趣味内容")
    p4.add_argument("--result", required=True, help="planner.py 的结果 JSON")
    p4.add_argument("--persons", help="persons.json 路径")
    p4.add_argument("--seed", type=int)

    args = ap.parse_args()

    def load_json(path, inline):
        if inline:
            return json.loads(inline)
        if path:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        raise SystemExit("需要 --input 或 --json")

    if args.cmd == "personality":
        print(render_personality(load_json(args.input, args.json)))
    elif args.cmd == "lucky":
        if args.items:
            names = [x for x in args.items.split(",") if x.strip()]
        else:
            res = load_json(args.input, None)
            names = [i["name"] for i in res.get("slot_items", []) + res.get("extra_items", [])]
        if not names:
            raise SystemExit("没有可用餐品")
        print(render_lucky(names, args.seed))
    elif args.cmd == "poster":
        print(render_poster(load_json(args.input, args.json)))
    elif args.cmd == "all":
        with open(args.result, "r", encoding="utf-8") as f:
            res = json.load(f)
        if not res.get("ok"):
            raise SystemExit("搭配方案未生成成功，无法产出趣味内容")
        names = [i["name"] for i in res.get("slot_items", []) + res.get("extra_items", [])]
        if args.persons:
            with open(args.persons, "r", encoding="utf-8") as f:
                persons = json.load(f)
        else:
            persons = [{"name": "干饭人", "items": names}]
        # 人均分配给每个人，便于生成每人的餐品清单
        if len(persons) == 1 and len(res.get("slot_items", [])) > 1:
            persons = [{"name": "干饭人%d" % (i + 1), "items": [it["name"]]}
                       for i, it in enumerate(res["slot_items"])]
        print(render_personality(persons))
        print()
        print(render_lucky(names, args.seed))
        print()
        print(render_poster(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
