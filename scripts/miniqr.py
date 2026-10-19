#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦饭搭子 · 零依赖二维码生成器

只用 Python 标准库实现 QR Code 编码（ISO/IEC 18004），不引入 qrcode / Pillow / numpy
等任何第三方依赖 —— 这是本项目「纯标准库」承诺的一部分。

支持范围：
    - 字节模式（Byte mode，UTF-8）
    - 版本 1~6（自动选择能装下的最小版本）
    - 纠错等级 L / M / Q / H（默认 M）
    - 输出 PNG（自带 zlib+struct 编码器）/ SVG / UTF-8 半块字符画

之所以不直接 pip 一个 qrcode：本项目作为参赛 Skill 要能在干净的 WorkBuddy
环境里跑起来，第三方依赖每多一个，可用性就掉一截。手写虽慢，但可控。

用法（作为库）：
    from miniqr import encode, to_png, to_utf8
    m = encode(b"hello")
    to_png(m, path="out.png")
    print(to_utf8(m))

命令行：
    python scripts/miniqr.py --text "MFDPAY1|..." --out code.png
    python scripts/miniqr.py --text "..." --utf8
    python scripts/miniqr.py --selftest
"""

from __future__ import annotations

import struct
import zlib

# ------------------------------------------------------------------ GF(256)

_EXP = [0] * 512
_LOG = [0] * 256


def _init_gf():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D  # 本原多项式 x^8+x^4+x^3+x^2+1
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_gf()


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _poly_mul(p, q):
    """多项式乘法，系数按次数从低到高排列。"""
    r = [0] * (len(p) + len(q) - 1)
    for j in range(len(q)):
        if q[j] == 0:
            continue
        for i in range(len(p)):
            if p[i] != 0:
                r[i + j] ^= _mul(p[i], q[j])
    return r


def _generator_highest_first(nsym: int):
    """
    生成器多项式 Π(i=0..nsym-1)(x + α^i)，返回「高次在前」排列。

    注意坑点：本文件的多项式一律按「低次在前」存储（index = 次数），
    因此单个因子 (x + α^i) 应写作 [α^i, 1]，写成 [1, α^i] 会得到 (1 + α^i·x)，
    两者完全不同，且算出来的纠错码字会让整张码盘无法解码。
    """
    g = [1]
    for i in range(nsym):
        g = _poly_mul(g, [_EXP[i], 1])
    return list(reversed(g))  # 反转成高次在前，供长除法使用


def _rs_encode(data, nsym: int):
    """对 data 追加 nsym 个 Reed-Solomon 纠错码字，返回仅纠错部分。"""
    gen = _generator_highest_first(nsym)
    res = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = res[i]
        if coef == 0:
            continue
        for j in range(1, len(gen)):
            if gen[j] != 0:
                res[i + j] ^= _mul(gen[j], coef)
    return res[len(data):]


# ------------------------------------------------------ 版本 / 纠错结构表

# (版本, 等级) -> (每块纠错码字数, [(块数, 该组每块数据码字数), ...])
# 约束：数据码字总数 + 纠错码字总数 == 该版本总码字数
# selftest 会校验这张表的算术一致性，防止手抄出错。
_SPEC = {
    1: {"L": (7,  [(1, 19)]), "M": (10, [(1, 16)]),
        "Q": (13, [(1, 13)]), "H": (17, [(1, 9)])},
    2: {"L": (10, [(1, 34)]), "M": (16, [(1, 28)]),
        "Q": (22, [(1, 22)]), "H": (28, [(1, 16)])},
    3: {"L": (15, [(1, 55)]), "M": (26, [(1, 44)]),
        "Q": (18, [(2, 17)]), "H": (22, [(2, 13)])},
    4: {"L": (20, [(1, 80)]), "M": (18, [(2, 32)]),
        "Q": (26, [(2, 24)]), "H": (16, [(4, 9)])},
    5: {"L": (26, [(1, 108)]), "M": (24, [(2, 43)]),
        "Q": (18, [(2, 15), (2, 16)]), "H": (22, [(2, 11), (2, 12)])},
    6: {"L": (18, [(2, 68)]), "M": (16, [(4, 27)]),
        "Q": (24, [(4, 19)]), "H": (28, [(4, 15)])},
}

_TOTAL_CODEWORDS = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134, 6: 172}

# 纠错等级指示符是 **2 位**：L=01, M=00, Q=11, H=10。
# 写成 0x11 / 0x10 是四位的错位写法，会让 Q/H 的格式信息整个算错
# —— 表现为 L、M 正常而 Q、H 一律扫不出来，排查时极易误判成纠错算法问题。
_LEVEL_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}


def capacity(version: int, level: str) -> int:
    """该版本的字节模式最大容量（字节数）。"""
    ec_per_block, groups = _SPEC[version][level]
    data_words = sum(cnt * words for cnt, words in groups)
    # 扣除模式指示符 4 bit 与字符计数 8 bit，再换算字节
    return (data_words * 8 - 4 - 8) // 8


def pick_version(nbytes: int, level: str = "M") -> int:
    for v in sorted(_SPEC):
        if capacity(v, level) >= nbytes:
            return v
    raise ValueError("数据过长：%d 字节超出版本 6-%s 的容量 %d"
                     % (nbytes, level, capacity(6, level)))


# ------------------------------------------------------------------ 编码

def _bitstream(payload: bytes, version: int):
    bits = []

    def push(value: int, length: int):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)            # 字节模式
    push(len(payload), 8)      # 版本 1~9 的字符计数为 8 bit
    for b in payload:
        push(b, 8)
    return bits


def _codewords(payload: bytes, version: int, level: str):
    ec_per_block, groups = _SPEC[version][level]
    total_words = _TOTAL_CODEWORDS[version]
    data_words = sum(cnt * w for cnt, w in groups)

    bits = _bitstream(payload, version)
    # 终止符 + 补齐到字节边界
    for _ in range(min(4, data_words * 8 - len(bits))):
        bits.append(0)
    while len(bits) % 8 != 0:
        bits.append(0)

    words = []
    for i in range(0, len(bits), 8):
        v = 0
        for b in bits[i:i + 8]:
            v = (v << 1) | b
        words.append(v)

    # 填充字节 0xEC / 0x11 交替
    pads = (0xEC, 0x11)
    k = 0
    while len(words) < data_words:
        words.append(pads[k % 2])
        k += 1

    # 切块
    blocks, pos = [], 0
    for cnt, wlen in groups:
        for _ in range(cnt):
            blocks.append(words[pos:pos + wlen])
            pos += wlen

    # 逐块算纠错，再交织：先数据后纠错，均为跨块轮转
    ec_blocks = [_rs_encode(blk, ec_per_block) for blk in blocks]
    result = []
    max_len = max(len(b) for b in blocks)
    for i in range(max_len):
        for blk in blocks:
            if i < len(blk):
                result.append(blk[i])
    for i in range(ec_per_block):
        for eb in ec_blocks:
            result.append(eb[i])

    assert len(result) == total_words, \
        "码字总数不匹配：%d != %d" % (len(result), total_words)
    return result


# ------------------------------------------------------------------ 矩阵

def _new_matrix(size: int):
    return [[0] * size for _ in range(size)]


def _draw_finder(m, size, r0, c0, func):
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            r, c = r0 + dr, c0 + dc
            if not (0 <= r < size and 0 <= c < size):
                continue
            if 0 <= dr <= 6 and 0 <= dc <= 6:
                v = 1 if (dr in (0, 6) or dc in (0, 6)
                          or (2 <= dr <= 4 and 2 <= dc <= 4)) else 0
            else:
                v = 0  # 分隔符
            m[r][c] = v
            func[r][c] = 1


def _draw_alignment(m, size, cr, cc, func):
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            r, c = cr + dr, cc + dc
            if not (0 <= r < size and 0 <= c < size):
                continue
            d = max(abs(dr), abs(dc))
            m[r][c] = 1 if (d == 2 or d == 0) else 0
            func[r][c] = 1


def _reserve_format(m, size, func):
    for i in range(9):
        func[8][i] = 1
        func[i][8] = 1
    for i in range(8):
        func[8][size - 1 - i] = 1
        func[size - 1 - i][8] = 1


def _place_format(m, size, fmt: int):
    def bit(i):
        return (fmt >> i) & 1

    # 第一份（左上角附近）
    for i in range(6):
        m[i][8] = bit(i)
    m[7][8] = bit(6)
    m[8][8] = bit(7)
    m[8][7] = bit(8)
    for i in range(9, 15):
        m[8][14 - i] = bit(i)
    # 第二份
    for i in range(8):
        m[size - 1 - i][8] = bit(i)
    for i in range(8, 15):
        m[8][size - 15 + i] = bit(i)
    # 固定深色模块
    m[size - 8][8] = 1


def _format_bits(level: str, mask: int) -> int:
    data = (_LEVEL_BITS[level] << 3) | mask
    rem = data
    for _ in range(10):
        rem = ((rem << 1) & 0x3FF) ^ (0x537 if rem & 0x200 else 0)
    return (((data << 10) | rem) ^ 0x5412) & 0x7FFF


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _penalty(m, size) -> int:
    p = 0
    # 规则 1：同色连续 >=5
    for line in _rows_and_cols(m, size):
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    p += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            p += 3 + (run - 5)
    # 规则 2：2x2 同色块
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                p += 3
    # 规则 3：1:1:3:1:1 图形
    pat = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    t1 = "".join(str(x) for x in pat)
    t2 = "".join(str(x) for x in reversed(pat))
    for line in _rows_and_cols(m, size):
        s = "".join(str(x) for x in line)
        for w in (t1, t2):
            idx, n = 0, 0
            while True:
                i = s.find(w, idx)
                if i < 0:
                    break
                n += 1
                idx = i + 1
            p += 40 * n
    # 规则 4：深色占比偏离 50%
    dark = sum(sum(row) for row in m)
    ratio = dark * 100.0 / (size * size)
    p += 10 * int(abs(ratio - 50) // 5)
    return p


def _rows_and_cols(m, size):
    for row in m:
        yield row
    for c in range(size):
        yield [m[r][c] for r in range(size)]


def _build_base(version: int, words, level: str, mask: int):
    size = version * 4 + 17
    m = _new_matrix(size)
    func = _new_matrix(size)

    # 绘制顺序很关键：必须先画定时图形，再画定位图形。
    # 反过来的话，第 6 行/列的定时图形会把三个定位图形的边框整格覆盖掉，
    # 定位图形被破坏，解码器连码在哪里都找不到（表现为「扫不出来」而不是「解错」）。
    for i in range(size):
        v = 1 if i % 2 == 0 else 0
        m[6][i] = v
        func[6][i] = 1
        m[i][6] = v
        func[i][6] = 1

    _draw_finder(m, size, 0, 0, func)
    _draw_finder(m, size, 0, size - 7, func)
    _draw_finder(m, size, size - 7, 0, func)

    # 版本 2~6 只有 1 个校正图形，位于右下角
    if version >= 2:
        _draw_alignment(m, size, size - 7, size - 7, func)

    _reserve_format(m, size, func)

    # 数据位按两列为一组的蛇形轨迹放置
    bits = []
    for w in words:
        for i in range(7, -1, -1):
            bits.append((w >> i) & 1)

    cols = [c for c in range(size - 1, -1, -1) if c != 6]
    idx = 0
    upward = True
    for ci in range(0, len(cols), 2):
        pair = [cols[ci]]
        if ci + 1 < len(cols):
            pair.append(cols[ci + 1])
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in pair:
                if func[r][c]:
                    continue
                m[r][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        upward = not upward

    # 应用掩码（只作用于非功能图形）
    mf = _MASKS[mask]
    for r in range(size):
        for c in range(size):
            if not func[r][c] and mf(r, c):
                m[r][c] ^= 1

    _place_format(m, size, _format_bits(level, mask))
    return m


def encode(data, level: str = "M"):
    """
    生成二维码矩阵。返回二维列表，1 表示深色模块。

    data 可以是 str（按 UTF-8 编码）或 bytes。
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    version = pick_version(len(data), level)
    words = _codewords(data, version, level)

    best, best_p = None, None
    for mask in range(8):
        mat = _build_base(version, words, level, mask)
        p = _penalty(mat, len(mat))
        if best_p is None or p < best_p:
            best, best_p = mat, p
    return best


# ------------------------------------------------------------------ 渲染

def to_png(matrix, path: str, scale: int = 8, border: int = 4):
    """
    写出灰度 PNG。png 结构由本项目自行拼装（zlib + struct），无需 Pillow。
    """
    size = len(matrix)
    total = (size + border * 2) * scale
    raw = bytearray()
    for y in range(total):
        raw.append(0)  # 过滤类型：None
        my = y // scale - border
        for x in range(total):
            mx = x // scale - border
            dark = (0 <= my < size and 0 <= mx < size and matrix[my][mx])
            raw.append(0 if dark else 255)

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", total, total, 8, 0, 0, 0, 0)
    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(blob)
    return path


def to_svg(matrix, border: int = 4, scale: int = 8) -> str:
    size = len(matrix)
    total = size + border * 2
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'shape-rendering="crispEdges" viewBox="0 0 %d %d">'
             % (total * scale, total * scale, total, total)]
    parts.append('<rect width="%d" height="%d" fill="#fff"/>' % (total, total))
    for r in range(size):
        c = 0
        while c < size:
            if matrix[r][c]:
                start = c
                while c < size and matrix[r][c]:
                    c += 1
                parts.append('<rect x="%d" y="%d" width="%d" height="1" fill="#000"/>'
                             % (start + border, r + border, c - start))
            else:
                c += 1
    parts.append("</svg>")
    return "".join(parts)


def to_utf8(matrix, border: int = 4) -> str:
    """
    渲染成 UTF-8 半块字符画：一个字符承载上下两行模块。
    这样二维码可以直接印在对话里，用户抬手就能扫。
    """
    size = len(matrix)
    total = size + border * 2
    grid = [[0] * total for _ in range(total)]
    for r in range(size):
        for c in range(size):
            grid[r + border][c + border] = matrix[r][c]

    lines = []
    for r in range(0, total, 2):
        top = grid[r]
        bot = grid[r + 1] if r + 1 < total else [0] * total
        chars = []
        for c in range(total):
            t, b = top[c], bot[c]
            if t and b:
                chars.append("█")
            elif t:
                chars.append("▀")
            elif b:
                chars.append("▄")
            else:
                chars.append(" ")
        lines.append("".join(chars))
    return "\n".join(lines)


# ------------------------------------------------------------------ 自检

def selftest() -> int:
    """校验 版本/等级 结构表的算术一致性。校验通过返回 0。"""
    bad = []
    for v, levels in _SPEC.items():
        for lv, (ec, groups) in levels.items():
            blocks = sum(cnt for cnt, _ in groups)
            data_words = sum(cnt * w for cnt, w in groups)
            if data_words + ec * blocks != _TOTAL_CODEWORDS[v]:
                bad.append("v%d-%s 码字总数不符" % (v, lv))
    if bad:
        print("❌ " + "; ".join(bad))
        return 1
    print("✅ QR 结构表一致（版本 1~6 × LMQH）")
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="零依赖二维码生成器")
    ap.add_argument("--text", help="要编码的文本")
    ap.add_argument("--level", choices=["L", "M", "Q", "H"], default="M")
    ap.add_argument("--out", help="PNG 输出路径")
    ap.add_argument("--svg", help="SVG 输出路径")
    ap.add_argument("--utf8", action="store_true", help="打印为字符画")
    ap.add_argument("--selftest", action="store_true", help="校验内部表格")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.text:
        ap.error("需要 --text 或 --selftest")

    m = encode(args.text, args.level)
    if args.out:
        to_png(m, args.out)
        print("✅ PNG -> %s" % args.out)
    if args.svg:
        with open(args.svg, "w", encoding="utf-8") as f:
            f.write(to_svg(m))
        print("✅ SVG -> %s" % args.svg)
    if args.utf8 or not (args.out or args.svg):
        print(to_utf8(m))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
