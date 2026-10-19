#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 MCP `list-nutrition-foods` 返回的原始文本表格，转成 planner.py 能吃的 JSON。

该接口返回的 data 是**字符串**（不是 JSON 数组），形如：

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
      猪柳麦满分,null,1288,308,16,16,24,781,213
      板烧鸡腿堡,null,1638,391,23,17,35,1041,93

用法：
    # 把 MCP 返回的 data 字符串存进 nutrition_raw.txt，然后：
    python parse_nutrition.py --input nutrition_raw.txt --out nutrition.json

    # 或直接管道：
    cat nutrition_raw.txt | python parse_nutrition.py > nutrition.json
"""

from __future__ import annotations

import argparse
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 原始列名 -> planner.py 需要的字段名
FIELD_MAP = {
    "productName": ("name", str),
    "nutritionDescription": ("description", str),
    "energyKj": ("energy_kj", float),
    "energyKcal": ("energy_kcal", float),
    "protein": ("protein_g", float),
    "fat": ("fat_g", float),
    "carbohydrate": ("carb_g", float),
    "sodium": ("sodium_mg", float),
    "calcium": ("calcium_mg", float),
}


def parse_header(line: str):
    line = line.strip()
    if "{" not in line or "}" not in line:
        return None
    cols = line[line.index("{") + 1: line.index("}")]
    return [c.strip() for c in cols.split(",")]


def num(value: str) -> float:
    """数值解析。契约：永远返回 float，解析不出就记 0.0。

    不返回 None，是为了避免下游出现 `None + float` 或 `None > 0` 这类运行时错误。
    「未知」与「0」在本项目里不做区分 —— 营养数据缺失时报告会统一提示「缺少营养数据」。
    """
    value = (value or "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def convert(text: str) -> list:
    rows, columns, seen = [], [], set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cols = parse_header(line)
        if cols:                      # 表头行
            columns = cols
            continue
        if line.startswith("[") and "]" in line and "{" not in line:
            continue                  # 计数行
        parts = [p.strip() for p in line.split(",")]
        if not columns or len(parts) < len(columns):
            continue
        record = {}
        for key, value in zip(columns, parts):
            mapped = FIELD_MAP.get(key)
            if not mapped:
                continue
            field, caster = mapped
            if caster is str:
                record[field] = None if value in ("null", "NULL", "") else value
            else:
                record[field] = num(value)
        name = record.get("name")
        if not name or name in seen:
            continue                  # 同名去重（接口里有重复项）
        seen.add(name)
        rows.append(record)
    return rows


def main():
    ap = argparse.ArgumentParser(description="解析 list-nutrition-foods 原始文本")
    ap.add_argument("--input", help="原始文本文件；省略则读 stdin")
    ap.add_argument("--out", help="输出 JSON 路径；省略则打印到 stdout")
    args = ap.parse_args()

    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    rows = convert(text)
    out = json.dumps(rows, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print("✅ 解析 %d 条营养数据 -> %s" % (len(rows), args.out))
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
