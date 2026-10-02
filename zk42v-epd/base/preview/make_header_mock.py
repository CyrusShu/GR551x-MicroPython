#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""表头加"位置"的预览 mock（不改仓库里的任何东西）：
   拿工具链真渲染出表头，再把 Hiragino 栅格化的城市名贴到几个候选位置上。

   /tmp/zkvenv/bin/python3 /tmp/zk_mock_place.py
"""
import os, struct, sys, tempfile, zlib

sys.path.insert(0, '/Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware/tools')
import gui_preview as G
from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
SCALE = 4


def glyph16(ch):
    """跟 tools/cjk_from_ttf.py 同一套参数（SIZE=15 / THR=110 / DY=-1 / 裁中间 16x16）"""
    im = Image.new('L', (24, 24), 0)
    ImageDraw.Draw(im).text((12, 11), ch, fill=255,
                            font=ImageFont.truetype(FONT, 15), anchor='mm')
    px = im.load()
    bm = [[1 if px[4 + x, 4 + y] >= 110 else 0 for x in range(16)] for y in range(16)]
    print("   [debug] glyph %s 墨点 %d" % (ch, sum(sum(r) for r in bm)))
    return bm


def paste(buf, rows, x, y, bitmap, rgb):
    n = 0
    for dy, line in enumerate(bitmap):
        for dx, v in enumerate(line):
            if not v:
                continue
            px = (x + dx, y + dy)
            if 0 <= px[0] < G.W and 0 <= px[1] < len(rows):
                # ⚠ rows 是"每行一个 bytes"，所以行内偏移只跟列有关 ——
                #    早先写成 px[1]*G.W*3 + px[0]*3，等于把字贴到了每行尾巴外面（不报错、也看不见）
                i = px[0] * 3
                rows[px[1]] = rows[px[1]][:i] + bytes(rgb) + rows[px[1]][i + 3:]
                n += 1
    print("   [debug] paste 到 (%d,%d) 写了 %d 个像素" % (x, y, n))


def save(path, rows, hdr_h=27):
    crop = [r[:G.W * 3] for r in rows[:hdr_h]]
    big = []
    for r in crop:
        px = [r[i:i + 3] for i in range(0, len(r), 3)]
        line = b''.join(p * SCALE for p in px)
        for _ in range(SCALE):
            big.append(line)
    h, w = len(big), G.W * SCALE
    raw = b''.join(b'\x00' + r for r in big)
    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
    print(path, "%dx%d" % (w, h))


def rightmost(rows, x0, x1, y0=3, y1=22):
    CB, CR = bytes(G.C_BLACK), bytes(G.C_RED)
    xs = [x for x in range(x0, x1 + 1)
          if any(rows[y][x * 3:x * 3 + 3] in (CB, CR) for y in range(y0, y1 + 1))]
    return max(xs) if xs else None


def render(mode, ts, opt, bat, t10, wx, wxt):
    with tempfile.TemporaryDirectory(prefix='zkmock-') as td:
        return G.to_rgb(G.render(G.build(td), mode, ts, opt, bat, t10, wx, wxt))


TS = 1790737436 + 8 * 3600          # 2026-09-30 11:03:56 本地
PLACE = "上海"

# ---- 基线：不画位置 ----
rows = render(1, TS, 0, 3326, 293, 3, 26)
end = rightmost(rows, 0, 366)
print("基线：内容最右 =", end, " 电池左边界 =", rightmost(rows, 360, 399, 8, 16))
save('/tmp/hdr_a_base.png', rows)

# ---- 方案 A：温度后面接 16px 位置（"阴 26C 上海"）----
rows = render(1, TS, 0, 3326, 293, 3, 26)
x = end + 6
for ch in PLACE:
    paste(None, rows, x, 5, glyph16(ch), G.C_BLACK)
    x += 17
print("方案A：位置 x = %d ~ %d" % (end + 6, x - 1))
save('/tmp/hdr_b_place16.png', rows)

# ---- 方案 B：右对齐到电池前 6px（最长 4 字也不会跑偏）----
rows = render(1, TS, 0, 3326, 293, 3, 26)
width = 17 * len(PLACE) - 1
x0 = 362 - width
for i, ch in enumerate(PLACE):
    paste(None, rows, x0 + i * 17, 5, glyph16(ch), G.C_BLACK)
print("方案B(右对齐)：位置 x = %d ~ %d" % (x0, x0 + width - 1))
save('/tmp/hdr_c_place_right.png', rows)

# ---- 最挤的情况：三字天气 + 零下温度 ----
rows = render(1, TS, 0, 3326, 293, 6, -26)      # 6 = 雷阵雨
end2 = rightmost(rows, 0, 366)
print("最挤：雷阵雨 -26C 时内容最右 =", end2, " 剩给位置的 =", 368 - end2 - 1, "px")
x = end2 + 6
for ch in PLACE[:1]:                             # 只剩得下一个字
    paste(None, rows, x, 5, glyph16(ch), G.C_BLACK)
    x += 17
save('/tmp/hdr_d_place_worst.png', rows)
