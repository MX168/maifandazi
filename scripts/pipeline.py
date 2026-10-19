#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦饭搭子 · 端到端编排

把「营养解析 → 搭配算法 → 趣味彩蛋 → AA 分账 → 最终 Markdown」串成一条命令，
用于验证整条链路从输入到输出可跑通，也可作为演示生成脚本。

用法：
    python scripts/pipeline.py \
        --request examples/demo_request.json \
        --nutrition-raw examples/nutrition_raw_2026-10-09.txt \
        --persons examples/demo_persons.json \
        --seed 20261009 \
        --out examples/final_output.md

参数说明：
    --request        搭配请求 JSON（必填）
    --nutrition-raw  官方 list-nutrition-foods 的原始文本（选填，会自动解析并合并进请求）
    --persons        成员偏好 JSON（选填，缺省按餐位自动分配）
    --seed           幸运菜品随机种子（选填，不传则每次不同）
    --mode           AA 分账模式：fair 按实吃（默认）/ even 平摊 / treat 请客
    --payer          请客模式下的买单者姓名
    --shared         公共餐品名（全员平分的那些），可重复传参
    --out            最终 Markdown 输出路径（选填，不传则打印到 stdout）
    --quiet          只输出结果，不打印进度

退出码：0 成功；1 搭配方案未生成成功；2 输入文件缺失。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def run(cmd: list, stdin_data: str = None) -> str:
    p = subprocess.run([sys.executable] + cmd, input=stdin_data,
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0 and not p.stdout:
        raise RuntimeError("%s 失败：%s" % (" ".join(cmd), (p.stderr or "")[-500:]))
    return p.stdout


def step(msg: str, quiet: bool):
    if not quiet:
        print("▶ %s" % msg)


def main():
    ap = argparse.ArgumentParser(description="麦饭搭子 · 端到端编排")
    ap.add_argument("--request", required=True, help="搭配请求 JSON")
    ap.add_argument("--nutrition-raw", help="官方营养接口原始文本")
    ap.add_argument("--persons", help="成员偏好 JSON")
    ap.add_argument("--seed", type=int, help="幸运菜品随机种子")
    ap.add_argument("--out", help="最终 Markdown 输出路径")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--mode", choices=["fair", "even", "treat"], default="fair",
                    help="AA 分账模式：按实吃 / 平摊 / 请客")
    ap.add_argument("--shared", action="append", default=[],
                    help="公共餐品名（全员平分的那些），可重复传参")
    ap.add_argument("--payer", help="请客模式下的买单者姓名")
    ap.add_argument("--round-yuan", action="store_true",
                    help="AA 分账额外输出抹零到整元的版本")
    args = ap.parse_args()

    for f in [args.request, args.nutrition_raw, args.persons]:
        if f and not os.path.exists(f):
            print("❌ 输入文件不存在：%s" % f)
            return 2

    # ---- 1. 营养数据解析
    nutrition_json = None
    if args.nutrition_raw:
        step("解析营养数据 ...", args.quiet)
        nutrition_json = os.path.join(
            os.path.dirname(os.path.abspath(args.nutrition_raw)) or ".",
            "_nutrition_parsed.json")
        run([os.path.join(HERE, "parse_nutrition.py"),
             "--input", args.nutrition_raw, "--out", nutrition_json])

    # ---- 2. 组装请求
    step("组装请求 ...", args.quiet)
    with open(args.request, "r", encoding="utf-8") as f:
        req = json.load(f)
    if nutrition_json:
        with open(nutrition_json, "r", encoding="utf-8") as f:
            req["nutrition"] = json.load(f)

    # ---- 3. 搭配算法
    step("运行搭配算法 ...", args.quiet)
    raw = run([os.path.join(HERE, "planner.py")], json.dumps(req))
    result = json.loads(raw)
    if not result.get("ok"):
        print("❌ 搭配方案未生成成功：%s" % result.get("error", ""))
        return 1

    if result.get("fallback_mode"):
        step("⚠️ 忌口过重，已降级为拆单品组合", args.quiet)

    # ---- 4. 趣味彩蛋
    step("生成趣味彩蛋 ...", args.quiet)
    tmp_result = os.path.join(HERE, "_pipeline_result.json")
    with open(tmp_result, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    cmd = [os.path.join(HERE, "fun.py"), "all", "--result", tmp_result]
    if args.persons:
        cmd += ["--persons", args.persons]
    if args.seed is not None:
        cmd += ["--seed", str(args.seed)]
    fun_md = run(cmd)

    # ---- 5. AA 分账
    step("计算 AA 分账 ...", args.quiet)
    split_cmd = [os.path.join(HERE, "split.py"), "--result", tmp_result]
    if args.persons:
        split_cmd += ["--persons", args.persons]
    if args.mode != "fair":
        split_cmd += ["--mode", args.mode]
    if args.payer:
        split_cmd += ["--payer", args.payer]
    for s in (args.shared or []):
        split_cmd += ["--shared", s]
    if args.round_yuan:
        split_cmd += ["--round-yuan"]
    split_md = run(split_cmd)

    # ---- 6. 组装最终输出
    step("组装最终输出 ...", args.quiet)
    parts = [result["report_markdown"], "", "---", "", fun_md, "",
             "---", "", split_md, "",
             "> 由 `scripts/pipeline.py` 端到端生成｜数据源：麦当劳官方 MCP"]
    final = "\n".join(parts)

    # 清理中间文件
    for f in [tmp_result, nutrition_json]:
        if f and os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(final)
        print("✅ 已生成：%s" % args.out)
    else:
        print(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
