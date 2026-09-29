#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电池图标"电压 -> 长什么样"对照表（离线，不用硬件、不用刷机）。

    python3 tools/bat_sheet.py [输出.png]

每行是**表头那 27 行**（放大 2 倍）+ 行首下面用固件那套 5x7 字标上电压，
从 2.0V 一路排到 4.2V（每 200mV 一行）。图标里填几格来自固件里的
`zk_bat_pct()`（现在是 3.0V=0% / 4.2V=100% 的锂电直线），所以这张图也是
**改电量曲线前后的对比工具**：改完 Src/board/zk_bat.c 再跑一遍就知道图标变没变。

顺带回一张表：电压 / 固件算出来的百分比 / 图标填几格。
"""

import os
import struct
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gui_preview as G          # noqa: E402
from gen_font import ASCII5x7    # noqa: E402  用固件里同一套 5x7 字画标签

HDR_H = 27          # 表头 26 行 + 底下那条黑线
SCALE = 2           # 整张图放大 2 倍（表格 400px 宽 -> 800px）
VOLTS = [2000, 2200, 2400, 2600, 2800, 3000, 3200, 3400, 3600, 3800, 4000, 4200]


def draw_text5(img, x, y, s, scale, color=0):
    """把 5x7 点阵字画到灰度图 img 上（跟固件 draw_text5() 一个形状）"""
    for ch in s:
        for cy, row in enumerate(ASCII5x7.get(ch, ASCII5x7[' '])):
            for cx, bit in enumerate(row):
                if bit != '1':
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        yy, xx = y + cy * scale + dy, x + cx * scale + dx
                        if 0 <= yy < len(img) and 0 <= xx < len(img[0]):
                            img[yy][xx] = color
        x += (5 + 1) * scale
    return x


def write_png(path, rows):
    h, w = len(rows), len(rows[0])
    raw = b''.join(b'\x00' + bytes(r) for r in rows)

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    hdr = struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', hdr) +
                chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def pct_of(mv):
    """跟固件 zk_bat_pct() 同一条公式：3.0V=0% / 4.2V=100%"""
    p = (mv - 3000) * 100 // 1200
    return max(0, min(100, p))


def main(argv):
    out = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(HERE), 'docs', 'preview', 'preview-battery-levels.png')
    ts = 1790625600                      # 2026-09-29 12:00（UTC+8），跟别的预览同一时刻
    w = G.W * SCALE
    pad_top, pad_mid, pad_bot = 4, 2, 6
    label_h = 7 * SCALE
    row_h = HDR_H * SCALE + pad_mid + label_h + pad_bot

    rows = [[255] * w for _ in range(row_h * len(VOLTS) + pad_top)]
    print('%-8s %-8s %-6s %s' % ('电压', '毫伏', '百分比', '图标格数'))
    with tempfile.TemporaryDirectory(prefix='zkbat-') as td:
        exe = G.build(td)
        for i, mv in enumerate(VOLTS):
            hdr = G.to_rgb(G.render(exe, 1, ts, 0, mv, 264, 2, 26))[:HDR_H]
            y0 = pad_top + i * row_h
            for y, line in enumerate(hdr):
                for x in range(G.W):
                    px = line[x * 3:x * 3 + 3]
                    v = 0 if px == bytes(G.C_BLACK) else (128 if px == bytes(G.C_RED) else 255)
                    for dy in range(SCALE):
                        for dx in range(SCALE):
                            rows[y0 + y * SCALE + dy][x * SCALE + dx] = v
            pct = pct_of(mv)
            bars = (pct + 24) // 25
            draw_text5(rows, 6, y0 + HDR_H * SCALE + pad_mid,
                       '%.2fV' % (mv / 1000.0), SCALE)
            print('%-8s %-8d %-6d %s' % ('%.2fV' % (mv / 1000.0), mv, pct,
                                          '■' * bars + '□' * (4 - bars)))
    write_png(out, rows)
    print('写出 %s  %dx%d（放大 %d 倍；每行 = 表头 + 电压标签）'
          % (out, w, len(rows), SCALE))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
