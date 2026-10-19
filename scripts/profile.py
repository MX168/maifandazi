#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦饭搭子 · 本地轻量档案

保存「永久避雷清单」「常用口味/场景偏好」「历史拼单战绩」到本地 JSON，
无需数据库。默认路径 ~/.maifan-dazi/profile.json，
可用环境变量 MAIFAN_PROFILE 覆盖。

用法：
    python profile.py show
    python profile.py blacklist add 麦香鱼 --reason "上次不好吃"
    python profile.py blacklist remove 麦香鱼
    python profile.py taste add-avoid 香菜
    python profile.py taste add-prefer 炸鸡
    python profile.py taste remove 香菜
    python profile.py taste set --scene 职场干饭 --headcount 4 --budget 12000
    python profile.py history add --result planner_result.json
    python profile.py collect --headcount 3 --budget 10000   # 组装 planner 入参
    python profile.py reset
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".maifan-dazi", "profile.json")

EMPTY = {
    "version": 1,
    "scene": "",            # 宿舍拼单 / 职场干饭 / 朋友聚餐 / 单人解馋 / 亲子用餐
    "headcount": 1,
    "budget_fen": 0,
    "avoid": [],            # 常用忌口
    "prefer": [],           # 常用口味偏好
    "blacklist": [],        # 永久避雷 [(name, reason, ts)]
    "history": [],          # 历史战绩
}


def path() -> str:
    return os.environ.get("MAIFAN_PROFILE") or DEFAULT_PATH


def load() -> dict:
    p = path()
    if not os.path.exists(p):
        return dict(EMPTY)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(EMPTY)
    for k, v in EMPTY.items():
        data.setdefault(k, v)
    return data


def save(data: dict) -> str:
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


CNY = lambda fen: "{:.2f}".format(fen / 100.0)


def uniq_add(lst: list, value: str) -> bool:
    value = str(value).strip()
    if not value:
        return False
    if value in lst:
        return False
    lst.append(value)
    return True


def cmd_show(data, _args):
    print("📁 档案路径：%s" % path())
    print()
    print("- 常用场景：%s" % (data["scene"] or "（未设置）"))
    print("- 常用人数：%s" % (data["headcount"] or "（未设置）"))
    print("- 常用预算：%s" % (("¥%s" % CNY(data["budget_fen"])) if data["budget_fen"] else "（未设置）"))
    print("- 常用忌口：%s" % ("、".join(data["avoid"]) or "无"))
    print("- 口味偏好：%s" % ("、".join(data["prefer"]) or "无"))
    print()
    if data["blacklist"]:
        print("🚫 永久避雷清单（%d）：" % len(data["blacklist"]))
        for row in data["blacklist"]:
            if isinstance(row, dict):
                print("  - %s（%s）" % (row.get("name"), row.get("reason") or "无备注"))
            else:
                print("  - %s" % row)
    else:
        print("🚫 永久避雷清单：空")
    print()
    if data["history"]:
        print("🏅 历史战绩（最近 5 次）：")
        for row in data["history"][-5:]:
            print("  - %s：%d人 ¥%s / 实付 ¥%s"
                  % (row.get("time", ""), row.get("headcount", 0),
                     CNY(row.get("budget_fen", 0)), CNY(row.get("payable_fen", 0))))
    else:
        print("🏅 历史战绩：暂无")


def cmd_blacklist(data, args):
    changed = False
    if args.action == "add":
        names = [n.strip() for n in args.value.split(",") if n.strip()]
        for n in names:
            exists = [row for row in data["blacklist"]
                      if (row.get("name") if isinstance(row, dict) else row) == n]
            if exists:
                print("已在避雷清单：%s" % n)
                continue
            data["blacklist"].append({"name": n, "reason": args.reason or "",
                                      "ts": int(time.time())})
            changed = True
            print("✅ 已加入避雷清单：%s" % n)
    elif args.action == "remove":
        before = len(data["blacklist"])
        data["blacklist"] = [row for row in data["blacklist"]
                             if (row.get("name") if isinstance(row, dict) else row) != args.value.strip()]
        print("✅ 已移除 %d 项" % (before - len(data["blacklist"])))
        changed = True
    elif args.action == "list":
        for row in data["blacklist"]:
            print(row.get("name") if isinstance(row, dict) else row)
        return None
    return changed


def cmd_taste(data, args):
    field = {"add-avoid": "avoid", "add-prefer": "prefer",
             "remove": None}.get(args.action)
    if args.action == "remove":
        for key in ("avoid", "prefer"):
            if args.value.strip() in data[key]:
                data[key].remove(args.value.strip())
        print("✅ 已移除：%s" % args.value)
    else:
        if uniq_add(data[field], args.value):
            print("✅ 已加入 %s：%s" % (field, args.value))
        else:
            print("已存在：%s" % args.value)
    if getattr(args, "scene", None):
        data["scene"] = args.scene
    if getattr(args, "headcount", None):
        data["headcount"] = int(args.headcount)
    if getattr(args, "budget", None):
        data["budget_fen"] = int(args.budget)
    return True


def cmd_history(data, args):
    with open(args.result, "r", encoding="utf-8") as f:
        res = json.load(f)
    if not res.get("ok"):
        print("⚠️ 方案未生成成功，不记录")
        return None
    data["history"].append({
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "headcount": res.get("headcount"),
        "budget_fen": res.get("budget_fen"),
        "payable_fen": res.get("payable_fen"),
        "save_fen": res.get("coupon_save_fen"),
    })
    data["history"] = data["history"][-50:]
    print("✅ 已记录本次战绩")
    return True


def cmd_collect(data, args):
    """把档案 + 本次参数组装成 planner.py 可直接吃的入参骨架。"""
    out = {
        "headcount": args.headcount or data["headcount"] or 1,
        "budget_fen": args.budget or data["budget_fen"] or 0,
        "avoid": list(data["avoid"]) + ([a for a in args.avoid.split(",") if a.strip()]
                                        if args.avoid else []),
        "prefer": list(data["prefer"]) + ([p for p in args.prefer.split(",") if p.strip()]
                                          if args.prefer else []),
        "blacklist": [(row.get("name") if isinstance(row, dict) else row)
                      for row in data["blacklist"]],
        "menu": [],
        "nutrition": [],
        "coupons": [],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return None


def cmd_reset(data, args):
    p = path()
    if os.path.exists(p):
        os.remove(p)
    print("✅ 已清空档案：%s" % p)
    return None


def main():
    ap = argparse.ArgumentParser(description="麦饭搭子 · 本地档案")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="查看当前档案")
    p_show.set_defaults(func=cmd_show)

    p_bl = sub.add_parser("blacklist", help="永久避雷清单")
    p_bl.add_argument("action", choices=["add", "remove", "list"])
    p_bl.add_argument("value", nargs="?", default="")
    p_bl.add_argument("--reason", help="避雷原因备注")
    p_bl.set_defaults(func=cmd_blacklist)

    p_t = sub.add_parser("taste", help="口味/忌口偏好")
    p_t.add_argument("action", choices=["add-avoid", "add-prefer", "remove"])
    p_t.add_argument("value")
    p_t.add_argument("--scene", help="常用用餐场景")
    p_t.add_argument("--headcount", type=int, help="常用人数")
    p_t.add_argument("--budget", type=int, help="常用预算（单位：分）")
    p_t.set_defaults(func=cmd_taste)

    p_h = sub.add_parser("history", help="历史战绩")
    p_h.add_argument("action", choices=["add"])
    p_h.add_argument("--result", required=True, help="planner 结果 JSON")
    p_h.set_defaults(func=cmd_history)

    p_c = sub.add_parser("collect", help="组装 planner 入参骨架")
    p_c.add_argument("--headcount", type=int)
    p_c.add_argument("--budget", type=int, help="本次预算（单位：分）")
    p_c.add_argument("--avoid", help="本次追加忌口，逗号分隔")
    p_c.add_argument("--prefer", help="本次追加偏好，逗号分隔")
    p_c.set_defaults(func=cmd_collect)

    p_r = sub.add_parser("reset", help="清空档案")
    p_r.set_defaults(func=cmd_reset)

    args = ap.parse_args()
    data = load()
    changed = args.func(data, args)
    if changed:
        save(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
