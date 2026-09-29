#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电池图标"电压 -> 长什么样"对照表（离线，不用硬件、不用刷机）。

    python3 tools/bat_sheet.py [输出.png]

每行是**表头那 27 行**（放大 2 倍）+ 行首下面用固件那套 5x7 字标上电压，
电压点默认是 CR2450 那段（2.3V~3.2V）。

⚠ 电量曲线**不是**在这脚本里另抄一份：它从 `Src/board/zk_bat.c` 的
`s_curve_mv[] / s_curve_pct[]` 两张表里抠出来（改固件那张表，这张图跟着变），
所以它天生就是"改曲线前后的对比工具"。

顺带回一张表：电压 / 固件算出来的百分比 / 图标填几格。
"""

import os
import re
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
C_SRC = os.path.join(os.path.dirname(HERE), 'zk42v-epd-app', 'Src', 'board',
                     'zk_bat_curve.h')

# CR2450 的看点全在 3.0V 附近那一小段，所以扫得密一点
VOLTS = [2300, 2400, 2500, 2550, 2600, 2650, 2700, 2750,
         2800, 2850, 2900, 2950, 3000, 3100, 3200]


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


def load_curve():
    """从 zk_bat_curve.h 里抠出那条曲线（唯一真相在固件里，这里不另抄一份）"""
    src = open(C_SRC, encoding='utf-8', errors='replace').read()

    def grab(name):
        m = re.search(name + r'\[[^\]]*\]\s*=\s*\{([^}]*)\}', src)
        if not m:
            raise SystemExit('zk_bat_curve.h 里找不到 %s' % name)
        return [int(v) for v in re.findall(r'\d+', m.group(1))]

    mv, pct = grab('zk_bat_curve_mv_tab'), grab('zk_bat_curve_pct_tab')
    if len(mv) != len(pct) or len(mv) < 2:
        raise SystemExit('曲线表长度不对：%d vs %d' % (len(mv), len(pct)))
    return mv, pct


def pct_of(curve, mv):
    """跟固件 zk_bat_pct() 同一套算法：分段线性插值"""
    mv_tab, pct_tab = curve
    if mv >= mv_tab[0]:
        return 100
    for i in range(1, len(mv_tab)):
        if mv >= mv_tab[i]:
            hi_mv, lo_mv = mv_tab[i - 1], mv_tab[i]
            hi_pc, lo_pc = pct_tab[i - 1], pct_tab[i]
            return lo_pc + (mv - lo_mv) * (hi_pc - lo_pc) // (hi_mv - lo_mv)
    return 0


def main(argv):
    out = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(HERE), 'docs', 'preview', 'preview-battery-levels.png')
    ts = 1790625600                      # 2026-09-29 12:00（UTC+8），跟别的预览同一时刻
    w = G.W * SCALE
    pad_top, pad_mid, pad_bot = 4, 2, 6
    label_h = 7 * SCALE
    row_h = HDR_H * SCALE + pad_mid + label_h + pad_bot

    rows = [[255] * w for _ in range(row_h * len(VOLTS) + pad_top)]
    curve = load_curve()
    print('曲线（从 zk_bat.c 抠出来的）：'
          + ' '.join('%dmV=%d%%' % (m, p) for m, p in zip(*curve)))
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
            pct = pct_of(curve, mv)
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
