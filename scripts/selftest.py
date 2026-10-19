#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦饭搭子 · 边界用例自测

覆盖搭配算法、趣味模块、档案模块与营养解析的异常与边界输入，
用于验证「不崩溃、不静默出错、零超支不被违反」。

用法：
    python scripts/selftest.py              # 全部跑
    python scripts/selftest.py --only fun   # 只跑某一组
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print("  ✅ %s" % name)
    else:
        FAIL.append((name, detail))
        print("  ❌ %s  -> %s" % (name, detail))


def run_planner(cfg: dict) -> dict:
    """调用 planner.py。注意：方案不可行时它会以退出码 1 结束，
    这是预期行为，不能用 check_output，否则会误判为崩溃。"""
    p = subprocess.run([sys.executable, os.path.join(HERE, "planner.py")],
                       input=json.dumps(cfg), capture_output=True,
                       text=True, encoding="utf-8")
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"ok": False, "error": "planner 无 JSON 输出",
                "stderr": (p.stderr or "")[-400:]}


BASE_MENU = [
    {"code": "A1", "name": "板烧鸡腿堡", "price_fen": 2350},
    {"code": "A2", "name": "巨无霸", "price_fen": 2550},
    {"code": "B1", "name": "薯条", "price_fen": 1350},
    {"code": "B2", "name": "麦乐鸡", "price_fen": 1400},
    {"code": "C1", "name": "可乐", "price_fen": 950},
    {"code": "D1", "name": "菠萝派", "price_fen": 800},
]


def t_planner_edges():
    print("\n[planner] 边界与异常")

    # 1 空菜单
    r = run_planner({"headcount": 2, "budget_fen": 5000, "menu": []})
    check("空菜单 -> ok=False 且有 error", (not r["ok"]) and "error" in r, str(r)[:120])

    # 2 缺 menu 键
    r = run_planner({"headcount": 2, "budget_fen": 5000})
    check("缺 menu 键 -> ok=False", not r["ok"], str(r)[:120])

    # 3 预算为 0
    r = run_planner({"headcount": 2, "budget_fen": 0, "menu": BASE_MENU})
    check("预算 0 -> ok=False", not r["ok"], str(r)[:120])

    # 4 预算为负
    r = run_planner({"headcount": 2, "budget_fen": -100, "menu": BASE_MENU})
    check("预算负数 -> ok=False", not r["ok"], str(r)[:120])

    # 5 人数 0 / 负 -> 应被纠正为 1 而不是崩
    try:
        r = run_planner({"headcount": 0, "budget_fen": 5000, "menu": BASE_MENU})
        check("人数 0 -> 不崩溃且按 1 人处理", r.get("ok") and r.get("headcount") == 1, str(r)[:120])
    except Exception as e:
        check("人数 0 -> 不崩溃且按 1 人处理", False, repr(e))

    # 6 全部被忌口剔除
    r = run_planner({"headcount": 2, "budget_fen": 5000, "menu": BASE_MENU,
                     "avoid": ["堡", "薯", "鸡", "可乐", "派"]})
    check("全部剔除 -> ok=False", not r["ok"], str(r)[:120])

    # 7 预算不足：明确指出缺口，且绝不超支
    r = run_planner({"headcount": 5, "budget_fen": 3000, "menu": BASE_MENU})
    check("预算不足 -> 报缺口且不产出超支方案",
          (not r["ok"]) and "缺口" in r.get("error", ""), r.get("error", "")[:120])

    # 8 零超支是硬约束：随机预算下实付恒 <= 预算
    overspent = []
    for budget in [2000, 3500, 5000, 7000, 9000, 12000, 20000]:
        r = run_planner({"headcount": 3, "budget_fen": budget, "menu": BASE_MENU})
        if r.get("ok") and r.get("payable_fen", 0) > budget:
            overspent.append((budget, r["payable_fen"]))
    check("多档预算都不超支", not overspent, str(overspent))

    # 9 必须喂饱每个人：餐位份额总和 >= 人数
    under = []
    for hc in range(1, 11):
        r = run_planner({"headcount": hc, "budget_fen": hc * 4000,
                         "menu": BASE_MENU + [
                             {"code": "S1", "name": "超值双人餐", "price_fen": 4200,
                              "original_fen": 5600, "serves": 2}]})
        if r.get("ok"):
            # 注意要用 serves 求和：一个「双人餐」是 1 件但服务 2 人
            served = sum(i.get("serves", 1) for i in r.get("slot_items", []))
            if served < hc:
                under.append((hc, served))
    check("1-10 人都能喂饱（按 serves 计）", not under, str(under))

    # 9b 且不能过度供给：份额不得比人数多出一整份以上
    over = []
    for hc in range(1, 11):
        r = run_planner({"headcount": hc, "budget_fen": hc * 4000,
                         "menu": BASE_MENU + [
                             {"code": "S1", "name": "超值双人餐", "price_fen": 4200,
                              "original_fen": 5600, "serves": 2}]})
        if r.get("ok"):
            served = sum(i.get("serves", 1) for i in r.get("slot_items", []))
            if served > hc + 1:
                over.append((hc, served))
    check("1-10 人不过度供给（份额 <= 人数+1）", not over, str(over))

    # 10 价格单位混用：price 用元 与 price_fen 用分 应得到一致结果
    a = run_planner({"headcount": 2, "budget_fen": 6000, "price_unit": "yuan",
                     "menu": [{"code": "A1", "name": "板烧鸡腿堡", "price": 23.5},
                              {"code": "B1", "name": "薯条", "price": 13.5}]})
    b = run_planner({"headcount": 2, "budget_fen": 6000, "price_unit": "fen",
                     "menu": [{"code": "A1", "name": "板烧鸡腿堡", "price_fen": 2350},
                              {"code": "B1", "name": "薯条", "price_fen": 1350}]})
    check("元 / 分两种单位结果一致",
          a.get("ok") and b.get("ok") and a.get("payable_fen") == b.get("payable_fen"),
          "%s vs %s" % (a.get("payable_fen"), b.get("payable_fen")))

    # 11 优惠券门槛不满足时不计入
    r = run_planner({"headcount": 1, "budget_fen": 3000, "menu": BASE_MENU,
                     "coupons": [{"name": "满100减20", "save_fen": 2000, "threshold_fen": 10000}]})
    check("门槛未达 -> 券不计入", r.get("ok") and r.get("coupon_save_fen") == 0,
          str(r.get("coupon_save_fen")))
    r = run_planner({"headcount": 1, "budget_fen": 3000, "menu": BASE_MENU,
                     "coupons": [{"name": "满20减5", "save_fen": 500, "threshold_fen": 2000}]})
    check("门槛达标 -> 券计入且实付下降",
          r.get("ok") and r.get("coupon_save_fen") == 500, str(r.get("coupon_save_fen")))

    # 12 避雷清单生效
    r = run_planner({"headcount": 2, "budget_fen": 6000, "menu": BASE_MENU,
                     "blacklist": ["板烧", "巨无霸"]})
    names = [i["name"] for i in r.get("slot_items", []) + r.get("extra_items", [])]
    check("避雷清单生效", r.get("ok") and "板烧鸡腿堡" not in names and "巨无霸" not in names,
          str(names))

    # 13 营养数据缺失时不崩，热量记 0
    r = run_planner({"headcount": 2, "budget_fen": 6000, "menu": BASE_MENU})
    check("无营养数据 -> 不崩溃，总热量为 0",
          r.get("ok") and r["total_nutrition"]["energy_kcal"] == 0, str(r.get("total_nutrition")))

    # 14 报告 Markdown 必含关键段落
    md = r.get("report_markdown", "")
    check("报告含一键复制下单清单", "一键复制下单清单" in md, "")
    check("报告含预算与花费", "预算与花费" in md, "")

    # 15 忌口过滤结果会被回报
    r = run_planner({"headcount": 2, "budget_fen": 6000, "menu": BASE_MENU, "avoid": ["薯"]})
    check("剔除明细回报给用户",
          any(d["reason"].startswith("忌口") for d in r.get("dropped", [])),
          str(r.get("dropped")))

    # 16 单人 + 极低预算（只够一个堡）
    r = run_planner({"headcount": 1, "budget_fen": 2400, "menu": BASE_MENU})
    check("单人低预算可行", r.get("ok") and len(r.get("slot_items", [])) == 1, str(r)[:120])


def t_fun_edges():
    print("\n[fun] 边界与异常")
    import_fun = lambda: __import__("fun")

    sys.path.insert(0, HERE)
    import fun  # noqa: E402

    # 1 空餐品列表
    try:
        fun.render_poster({"headcount": 0, "budget_fen": 0, "payable_fen": 0})
        check("poster 人数 0 不崩溃", True)
    except Exception as e:
        check("poster 人数 0 不崩溃", False, repr(e))

    # 2 空人员列表
    try:
        fun.render_personality([])
        check("personality 空列表不崩溃", True)
    except Exception as e:
        check("personality 空列表不崩溃", False, repr(e))

    # 3 缺字段的人员
    try:
        out = fun.render_personality([{"name": "小明"}])
        check("personality 缺字段也能出标签", "小明" in out and "——" in out, out[:80])
    except Exception as e:
        check("personality 缺字段也能出标签", False, repr(e))

    # 4 lucky 单餐品
    try:
        out = fun.render_lucky(["麦香鸡"], seed=1)
        check("lucky 单餐品", "麦香鸡" in out, out[:60])
    except Exception as e:
        check("lucky 单餐品", False, repr(e))

    # 5 lucky 种子可复现
    check("lucky 同种子可复现",
          fun.render_lucky(["A", "B", "C"], seed=42) == fun.render_lucky(["A", "B", "C"], seed=42))

    # 6 段位边界：立省 0% 应落到最低段位
    out = fun.render_poster({"headcount": 2, "budget_fen": 5000,
                             "payable_fen": 5000, "original_fen": 5000})
    check("立省 0% 落到最低段位", "原始人" in out, out[-120:])

    # 7 立省 30% 应落到最高段位
    out = fun.render_poster({"headcount": 2, "budget_fen": 10000,
                             "payable_fen": 7000, "original_fen": 10000})
    check("立省 30% 落到最高段位", "战神" in out, out[-120:])


def t_parse_nutrition():
    print("\n[parse_nutrition] 边界与异常")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pn", os.path.join(HERE, "parse_nutrition.py"))
    pn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pn)

    check("空输入 -> 空列表", pn.convert("") == [])
    check("只有计数行 -> 空列表", pn.convert("[160]{a,b}:") == [])
    raw = ("[3]{productName,nutritionDescription,energyKcal,protein,fat}:\n"
           "  甲,null,100,1,2\n"
           "  乙,能量约清水1杯,200,3,4\n"
           "  甲,null,100,1,2\n")     # 重复，应去重
    rows = pn.convert(raw)
    check("重复条目去重", len(rows) == 2, str(len(rows)))
    check("字段名已映射", rows[0].get("name") == "甲" and rows[0].get("energy_kcal") == 100.0,
          str(rows[0]))
    check("通俗类比保留", rows[1].get("description") == "能量约清水1杯", str(rows[1]))
    bad = "[2]{productName,energyKcal}:\n  丙,abc\n"
    rows = pn.convert(bad)
    check("非法数值不崩溃且记 0", rows and rows[0]["energy_kcal"] == 0.0, str(rows))


def t_profile():
    print("\n[profile] 边界与异常")
    tmp = tempfile.mkdtemp()
    env = dict(os.environ, MAIFAN_PROFILE=os.path.join(tmp, "sub", "profile.json"))

    def run(*args):
        return subprocess.run([sys.executable, os.path.join(HERE, "profile.py")] + list(args),
                              capture_output=True, text=True, encoding="utf-8", env=env)

    check("首次 show 不崩溃", run("show").returncode == 0)
    check("嵌套目录自动创建", run("blacklist", "add", "麦香鱼").returncode == 0)
    check("重复添加不产生重复项",
          run("blacklist", "add", "麦香鱼").stdout.count("已加入") == 0)
    check("移除存在", run("blacklist", "remove", "麦香鱼").returncode == 0)
    check("移除不存在也不崩", run("blacklist", "remove", "不存在的菜").returncode == 0)
    r = run("taste", "add-avoid", "香菜")
    check("添加忌口", r.returncode == 0)
    r = run("collect", "--headcount", "3", "--budget", "10000")
    check("collect 输出合法 JSON", r.returncode == 0)
    try:
        data = json.loads(r.stdout)
        check("collect 带出避雷与偏好",
              data["blacklist"] == [] and "香菜" in data["avoid"], str(data))
    except Exception as e:
        check("collect 带出避雷与偏好", False, repr(e))
    shutil.rmtree(tmp, ignore_errors=True)


def load_any_module(name: str):
    """按文件路径加载 scripts/ 下的模块（split / miniqr / paylink 均适用）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, "%s.py" % name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_split_module():
    """按文件路径加载 split.py（它和 selftest 都在 scripts/ 下，但不是包结构）。"""
    return load_any_module("split")


def t_paylink_edges():
    print("\n── 付款凭证边界用例 ──")
    qr = load_any_module("miniqr")
    pl = load_any_module("paylink")
    import tempfile

    # 1. 内部结构表的算术自洽性
    check("QR 版本/纠错结构表自洽", qr.selftest() == 0)

    # 2. 容量随版本递增，且版本选择不越界
    caps = [qr.capacity(v, "M") for v in range(1, 7)]
    check("容量随版本单调递增", all(caps[i] < caps[i + 1] for i in range(5)), str(caps))
    check("最小版本选择正确", qr.pick_version(1, "M") == 1 and qr.pick_version(17, "M") == 2,
          "%s / %s" % (qr.pick_version(1, "M"), qr.pick_version(17, "M")))

    # 3. 超长数据要报错而不是静默截断
    try:
        qr.encode("x" * (caps[-1] + 1), "M")
        check("超长数据抛出错误", False, "未抛错")
    except ValueError:
        check("超长数据抛出错误", True)

    # 4. 矩阵尺寸符合 4v+17，且编码结果确定
    mat = qr.encode("MFDPAY1|O:TEST|N:阿强|A:4300|AC", "M")
    check("矩阵尺寸符合 4v+17", len(mat) == len(mat[0]) and (len(mat) - 17) % 4 == 0,
          str(len(mat)))
    check("同一输入编码结果稳定（可复现）",
          mat == qr.encode("MFDPAY1|O:TEST|N:阿强|A:4300|AC", "M"))

    # 5. 三个定位图形必须存在：左上角 7x7 外框全为深色
    size = len(mat)
    ok_tl = all(mat[r][c] == 1 for r, c in [(0, 0), (0, 6), (6, 0), (6, 6)]) \
        and mat[3][3] == 1 and mat[1][1] == 0
    ok_tr = mat[0][size - 1] == 1 and mat[6][size - 7] == 1
    ok_bl = mat[size - 7][0] == 1 and mat[size - 1][6] == 1
    check("三个定位图形位置正确", ok_tl and ok_tr and ok_bl,
          "tl=%s tr=%s bl=%s" % (ok_tl, ok_tr, ok_bl))

    # 6. 深色比例处于合理区间（掩码生效后才可能健康）
    dark = sum(sum(row) for row in mat)
    ratio = dark * 100.0 / (size * size)
    check("深色模块占比合理（30%~70%）", 30 <= ratio <= 70, "%.1f%%" % ratio)

    # 7. 备注生成 / 校验往返
    tok = pl.make_token("20261009A", 3, 2600)
    info = pl.parse_token(tok)
    check("备注生成与解析往返一致",
          info["order_id"] == "20261009A" and info["seq"] == 3 and info["fen"] == 2600, tok)
    check("备注长度受控（<=32 字符）", len(tok) <= 32, "%d 字符: %s" % (len(tok), tok))

    # 8. 篡改金额必须被校验位检出
    tampered = tok[:-2] + ("00" if tok[-2:] != "00" else "11")
    try:
        pl.parse_token(tampered)
        check("篡改备注被检出", False, "未检出")
    except ValueError:
        check("篡改备注被检出", True)

    # 9. 不同成员的备注互不相同
    toks = {pl.make_token("X", i, 1000 + i) for i in range(1, 8)}
    check("多人备注互不重复", len(toks) == 7, str(len(toks)))

    # 10. PNG / SVG / 字符画三种渲染都不出错
    tmpdir = tempfile.mkdtemp(prefix="mfd_qr_")
    png = os.path.join(tmpdir, "t.png")
    qr.to_png(mat, png, scale=4)
    with open(png, "rb") as f:
        head = f.read(8)
    check("PNG 文件头合法", head == b"\x89PNG\r\n\x1a\x08"[:8] or
          head == b"\x89PNG\r\n\x1a\n", repr(head))
    svg = qr.to_svg(mat)
    check("SVG 输出合法", svg.startswith("<svg") and "</svg>" in svg)
    art = qr.to_utf8(mat)
    check("字符画行数正确（半块渲染）",
          len(art.splitlines()) == (size + 8 + 1) // 2, str(len(art.splitlines())))

    # 11. 端到端：由分账结果产出付款凭证
    payload = {
        "payable_fen": 9890,
        "members": [{"name": "阿强", "pay_fen": 4300, "kcal": 369},
                    {"name": "小美", "pay_fen": 2990, "kcal": 32},
                    {"name": "老王", "pay_fen": 2600, "kcal": 0}],
    }
    items = pl.build_payload(payload, "T1")
    check("付款条目金额与合计一致",
          sum(i["fen"] for i in items) == 9890 and len(items) == 3,
          str([i["fen"] for i in items]))
    notes = [pl.parse_token(i["note"])["fen"] for i in items]
    check("每条备注都能被自己解析回来", notes == [4300, 2990, 2600], str(notes))
    shutil.rmtree(tmpdir, ignore_errors=True)


def make_result(slots, extras, payable, headcount=None):
    return {
        "headcount": headcount or len(slots),
        "payable_fen": payable,
        "subtotal_fen": sum(s["price_fen"] for s in slots) + sum(e["price_fen"] for e in extras),
        "slot_items": slots,
        "extra_items": extras,
    }


def do_split(sp, result, persons, shared=None, mode="fair", payer=None):
    units = sp.build_units(result)
    owned, pool, unmatched = sp.assign_units(units, persons, shared or [])
    result["_units"] = units
    result["_owned"] = owned
    pays, _t, meta = sp.split_bill(result, persons, pool, mode=mode, payer=payer)
    return pays, meta, pool, unmatched


def t_split_edges():
    print("\n── AA 分账边界用例 ──")
    sp = load_split_module()

    # 1. 最大余额法的分配闭合性（核心承诺）
    import random
    random.seed(20261010)
    bad = 0
    for _ in range(300):
        n = random.randint(1, 9)
        total = random.randint(0, 200000)
        w = [random.randint(0, 8000) for _ in range(n)]
        r = sp.largest_remainder(total, w)
        if sum(r) != total or any(x < 0 for x in r):
            bad += 1
    check("最大余额法：300 组随机分配总和精确且非负", bad == 0, "失败 %d 组" % bad)

    # 2. 除不尽的奇数 Xin
    r = sp.largest_remainder(10, [1, 1, 1])
    check("除不尽时余额按序分摊且不丢钱", sum(r) == 10 and sorted(r) == [3, 3, 4], str(r))

    # 3. 权重全 0 / total 0 的退化
    check("权重全为 0 时退化为平摊",
          sum(sp.largest_remainder(100, [0, 0, 0])) == 100)
    check("总额为 0 时全为 0", sp.largest_remainder(0, [1, 2, 3]) == [0, 0, 0])

    # 4. 有优惠时按比例返还，且总和等于实付
    res = make_result(
        [{"name": "A", "price_fen": 5000},
         {"name": "B", "price_fen": 3000},
         {"name": "C", "price_fen": 2000}],
        [], payable=9000)
    ps = [{"name": "甲", "claims": [("A", 1)]},
          {"name": "乙", "claims": [("B", 1)]},
          {"name": "丙", "claims": [("C", 1)]}]
    pays, meta, _pool, _um = do_split(sp, res, ps)
    check("优惠按实吃比例返还：甲应付 45 元", pays[0] == 4500, str(pays))
    check("有优惠时分摊总和 == 实付", sum(pays) == 9000, str(pays))

    # 5. 平摊模式且总额除不尽
    res2 = make_result([{"name": "A", "price_fen": 9001}], [], payable=9001, headcount=3)
    pays2, _m2, _p2, _u2 = do_split(sp, res2,
                                    [{"name": "甲", "claims": [("A", 1)]},
                                     {"name": "乙", "claims": []},
                                     {"name": "丙", "claims": []}], mode="even")
    check("平摊模式除不尽：总和仍等于实付", sum(pays2) == 9001, str(pays2))

    # 6. 请客模式：名字对不上也不能崩，且有人全付
    res3 = make_result([{"name": "A", "price_fen": 4000},
                        {"name": "B", "price_fen": 4000}], [], payable=8000)
    ps3 = [{"name": "甲", "claims": [("A", 1)]}, {"name": "乙", "claims": [("B", 1)]}]
    pays3, _m3, _p3, _u3 = do_split(sp, res3, ps3, mode="treat", payer="不存在的人")
    check("请客模式 payer 不存在时兜底且不崩",
          sum(pays3) == 8000 and max(pays3) == 8000, str(pays3))

    # 7. 公共池除不尽
    res4 = make_result([{"name": "A", "price_fen": 3000}],
                       [{"name": "薯条", "price_fen": 10, "qty": 1}],
                       payable=3010, headcount=3)
    ps4 = [{"name": "甲", "claims": [("A", 1)]},
           {"name": "乙", "claims": []},
           {"name": "丙", "claims": []}]
    pays4, meta4, pool4, _u4 = do_split(sp, res4, ps4)
    check("加购小食默认进公共池", len(pool4) == 1, str(pool4))
    check("公共池除不尽：3 人分 10 分不出错",
          sum(meta4["pool_share_fen"]) == 10 and sum(pays4) == 3010,
          str(meta4["pool_share_fen"]))

    # 8. extra_items 逐单位重复，不能被 qty 重复计价
    res5 = make_result([], [{"name": "麦乐鸡", "price_fen": 1400, "qty": 2},
                            {"name": "麦乐鸡", "price_fen": 1400, "qty": 2}], payable=2800,
                       headcount=2)
    units5 = sp.build_units(res5)
    check("extra_items 单位数 == 列表长度（未按 qty 翻倍）",
          len(units5) == 2 and sum(u["price_fen"] for u in units5) == 2800,
          str(len(units5)))

    # 9. 认领订单里没有的餐品：不崩且如实回报
    res6 = make_result([{"name": "A", "price_fen": 2000}], [], payable=2000, headcount=1)
    pays6, _m6, _p6, um6 = do_split(sp, res6,
                                    [{"name": "甲", "claims": [("不存在的菜", 1)]}])
    check("认领不存在的餐品被如实记录而非静默忽略",
          um6 and um6[0][1] == "不存在的菜" and sum(pays6) == 2000, str(um6))

    # 10. CLI 冒烟：真实演示数据上 sum_check 为真
    demo_res = os.path.join(HERE, "..", "examples", "demo_result.json")
    demo_p = os.path.join(HERE, "..", "examples", "demo_persons.json")
    if os.path.exists(demo_res) and os.path.exists(demo_p):
        p = subprocess.run([sys.executable, os.path.join(HERE, "split.py"),
                            "--result", demo_res, "--persons", demo_p, "--json"],
                           capture_output=True, text=True, encoding="utf-8")
        try:
            payload = json.loads(p.stdout)
            check("CLI --json 输出的 sum_check 为真",
                  payload.get("sum_check") is True and
                  sum(m["pay_fen"] for m in payload["members"]) == payload["payable_fen"],
                  p.stdout[-200:])
        except ValueError:
            check("CLI --json 输出合法", False, (p.stderr or p.stdout)[-300:])
    else:
        print("  ⏭️  跳过 CLI 冒烟（缺演示数据）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["planner", "fun", "parse", "profile",
                                       "split", "paylink", "qr"])
    args = ap.parse_args()

    print("=" * 62)
    print("麦饭搭子 · 边界用例自测")
    print("=" * 62)

    if not args.only or args.only == "planner":
        t_planner_edges()
    if not args.only or args.only == "fun":
        t_fun_edges()
    if not args.only or args.only == "parse":
        t_parse_nutrition()
    if not args.only or args.only == "profile":
        t_profile()
    if not args.only or args.only == "split":
        t_split_edges()
    if not args.only or args.only in ("paylink", "qr"):
        t_paylink_edges()

    print("\n" + "=" * 62)
    print("通过 %d ｜ 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("\n失败明细：")
        for n, d in FAIL:
            print("  - %s : %s" % (n, d))
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
