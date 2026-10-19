#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦饭搭子 · 付款凭证生成器（把 AA 分账结果变成可扫、可对账的东西）

## 先说清楚它不是什么

微信、支付宝**均未开放**第三方可签发的群收款口令或收款链接：

- 微信群收款是 App 内功能（单日上限 10 次、最多 500 人），无对外 API；
- 支付宝 `https://qr.alipay.com/xxx` 由 `alipay.trade.precreate` 在**服务端 RSA2 签名**
  后签发，官方明确说明不可自行拼接，否则返回无效二维码。

因此本模块**不生成、也不伪造任何能唤起支付的链接**。它做的是另一件务实的事：

> 把「谁该付多少」压成一枚**可扫的付款凭证**，并给出**带订单号的转账备注**。
> 付款人扫码即可拿到金额与备注，收款方凭备注就能对账 —— 群里一串转账，
> 谁付了、谁没付、付多了还是付少了，不再靠记忆。

## 用法

    # 基于 split.py 的 JSON 输出生成
    python scripts/paylink.py gen --split split.json --order-id 20261009A

    # 直接基于搭配结果与成员文件（内部会跑一遍分账）
    python scripts/paylink.py gen --result result.json --persons persons.json \
        --order-id 20261009A --outdir examples/pay

    # 校验某人回传的转账备注是不是被改过
    python scripts/paylink.py verify --token "MFD|20261009A|03|3212|8F"

备注格式：`MFD|<订单号>|<序号>|<金额分>|<校验位>`
校验位取 CRC32 低 8 位的十六进制，用于发现手改备注导致的错账。

退出码：0 成功；2 输入缺失；3 备注校验失败。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PREFIX = "MFD"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, "%s.py" % name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_DISCLAIMER = (
    "> ⚠️ **这是什么 / 不是什么**：这是**付款信息凭证**，不是支付指令。\n"
    "> 微信、支付宝均未向第三方开放可签发的收款链接或群收款口令\n"
    "> （支付宝收款码须由商户服务端 RSA2 签名签发，自行拼接一律无效），\n"
    "> 因此本码**不会自动唤起支付** —— 扫码后仍需你在 App 内完成转账。\n"
    "> 本项目不伪造这类链接。"
)


def checksum(parts) -> str:
    """CRC32 低 8 位，两人十六进制大写。"""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return "%02X" % (zlib.crc32(raw) & 0xFF)


def make_token(oid: str, seq: int, fen: int) -> str:
    parts = [PREFIX, oid, "%02d" % seq, "%d" % fen]
    return "|".join(parts) + "|" + checksum(parts)


def parse_token(token: str):
    """
    解析转账备注。返回 dict 或抛出 ValueError。
    同时校验校验位，防止备注被手改后错账。
    """
    token = (token or "").strip()
    bits = [b for b in token.split("|") if b != ""]
    if len(bits) != 5 or bits[0].upper() != PREFIX:
        raise ValueError("不是麦饭搭子备注：格式应为 MFD|订单号|序号|金额分|校验位")
    oid, seq_s, fen_s, chk = bits[1], bits[2], bits[3], bits[4].upper()
    if not (seq_s.isdigit() and fen_s.isdigit()):
        raise ValueError("序号或金额不是数字")
    parts = [PREFIX, oid, seq_s, fen_s]
    if checksum(parts) != chk:
        raise ValueError("校验位不匹配（%s ≠ %s），备注可能被改动过"
                         % (chk, checksum(parts)))
    return {"order_id": oid, "seq": int(seq_s), "fen": int(fen_s), "note": token}


# ------------------------------------------------------------------ 生成

def build_payload(split_data: dict, oid: str):
    """把分账 JSON 转成每人一条付款条目。"""
    items = []
    for i, m in enumerate(split_data.get("members") or [], start=1):
        fen = int(m.get("pay_fen") or 0)
        items.append({
            "seq": i,
            "name": m.get("name") or "成员%d" % i,
            "fen": fen,
            "amount": "¥%.2f" % (fen / 100.0),
            "note": make_token(oid, i, fen),
            "kcal": int(m.get("kcal") or 0),
        })
    return items


def render_markdown(items, oid: str, payable_fen: int, qr_art, outdir=None) -> str:
    L = []
    L.append("### 📲 付款凭证（扫一下就拿到转账备注）")
    L.append("")
    L.append(_DISCLAIMER)
    L.append("")
    L.append("**用法**：每人扫自己的码 → 把得到的备注附在转账里 → "
             "收款方凭备注自动对账。")
    L.append("")
    L.append("| 序号 | 成员 | 应付 | 转账备注 |")
    L.append("| --- | --- | --- | --- |")
    for it in items:
        L.append("| %02d | %s | **%s** | `%s` |" % (it["seq"], it["name"],
                                                    it["amount"], it["note"]))
    L.append("| | **合计** | **%s** | |"
             % ("¥%.2f" % (payable_fen / 100.0)))
    L.append("")
    if outdir:
        L.append("图片版：%s" % ", ".join(
            "`%s`" % os.path.join(outdir, "pay_%02d.png" % it["seq"])
            for it in items))
        L.append("")
    for idx, it in enumerate(items):
        L.append("**%02d · %s · %s**" % (it["seq"], it["name"], it["amount"]))
        L.append("")
        L.append("```text")
        L.append(qr_art[idx])
        L.append("```")
        L.append("")
        L.append("备注：`%s`" % it["note"])
        L.append("")
    L.append("> 备注中的金额以「分」为单位，避免小数点造成歧义；"
             "校验位由 CRC32 低 8 位生成，改一个字符就会被 `verify` 检出。")
    return "\n".join(L)


def cmd_gen(args) -> int:
    split_data = None
    if args.split:
        if not os.path.exists(args.split):
            print("❌ 分账文件不存在：%s" % args.split)
            return 2
        with open(args.split, "r", encoding="utf-8") as f:
            split_data = json.load(f)
    else:
        if not args.result or not os.path.exists(args.result):
            print("❌ 需要 --split 或 --result")
            return 2
        sp = _load("split")
        with open(args.result, "r", encoding="utf-8") as f:
            result = json.load(f)
        headcount = int(result.get("headcount") or 0) or len(result.get("slot_items") or []) or 1
        persons, shared = sp.load_persons(args.persons, headcount)
        if len(persons) < headcount:
            for i in range(len(persons), headcount):
                persons.append({"name": "成员%d" % (i + 1), "prefer": [],
                                "avoid": [], "claims": []})
        persons = persons[:headcount]
        units = sp.build_units(result)
        owned, pool, _um = sp.assign_units(units, persons, shared)
        result["_units"] = units
        result["_owned"] = owned
        pays, _t, meta = sp.split_bill(result, persons, pool,
                                       mode=args.mode, payer=args.payer)
        split_data = {
            "payable_fen": meta["payable_fen"],
            "members": [
                {"name": p["name"], "pay_fen": pays[i],
                 "items": [units[u]["name"] for u in owned[i]],
                 "kcal": int(sum(units[u]["kcal"] for u in owned[i]))}
                for i, p in enumerate(persons)
            ],
        }

    items = build_payload(split_data, args.order_id)
    if not items:
        print("❌ 分账结果里没有成员")
        return 2

    qr = _load("miniqr")
    outdir = args.outdir
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    arts, paths = [], []
    for it in items:
        mat = qr.encode(it["note"], args.level)
        arts.append(qr.to_utf8(mat))
        if outdir:
            p = os.path.join(outdir, "pay_%02d.png" % it["seq"])
            qr.to_png(mat, p, scale=8)
            paths.append(p)

    if args.json:
        out = {
            "order_id": args.order_id,
            "payable_fen": int(split_data.get("payable_fen") or 0),
            "levels": args.level,
            "members": [
                {"seq": it["seq"], "name": it["name"], "fen": it["fen"],
                 "amount": it["amount"], "note": it["note"],
                 "png": paths[it["seq"] - 1] if paths else None}
                for it in items
            ],
        }
        text = json.dumps(out, ensure_ascii=False, indent=2)
    else:
        text = render_markdown(items, args.order_id,
                               int(split_data.get("payable_fen") or 0),
                               arts, outdir)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("✅ 已生成：%s" % args.out)
        if paths:
            print("✅ 付款码图片 %d 张 -> %s" % (len(paths), outdir))
    else:
        print(text)
    return 0


def cmd_verify(args) -> int:
    try:
        info = parse_token(args.token)
    except ValueError as e:
        print("❌ 校验失败：%s" % e)
        return 3
    print("✅ 备注有效")
    print("   订单号：%s" % info["order_id"])
    print("   序　号：%02d" % info["seq"])
    print("   金　额：¥%.2f（%d 分）" % (info["fen"] / 100.0, info["fen"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description="麦饭搭子 · 付款凭证生成器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="生成付款码与转账备注")
    g.add_argument("--split", help="split.py --json 的输出文件")
    g.add_argument("--result", help="搭配结果 JSON（不传 --split 时使用）")
    g.add_argument("--persons", help="成员 JSON")
    g.add_argument("--order-id", default="MFD", help="订单号，用于备注对账")
    g.add_argument("--outdir", help="PNG 输出目录，不传则只输出字符画")
    g.add_argument("--level", choices=["L", "M", "Q", "H"], default="M",
                   help="二维码纠错等级")
    g.add_argument("--mode", choices=["fair", "even", "treat"], default="fair")
    g.add_argument("--payer", help="请客模式下的买单者")
    g.add_argument("--json", action="store_true")
    g.add_argument("--out", help="写入文件路径")
    g.set_defaults(func=cmd_gen)

    v = sub.add_parser("verify", help="校验一条转账备注")
    v.add_argument("--token", required=True, help="要校验的备注字符串")
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
