#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资源生成器：把点阵字形（ASCII 图 / 矩形）和三角函数表变成 C 头文件。

    python3 tools/gen_font.py

产物（都在 zk42v-epd-app/Src/img/）：
  zkgui_font.h  5x7 数字字模 + 16x16 中文单字（画日历/时钟页用）
  zkgui_trig.h  表针角度表（每 6° 一项，定点 1/1024）

为什么这么绕一圈：手写十六进制点阵错了看不出来，而这里每个字形就是几行
矩形/字符画，改起来直观；生成出来的东西有预览图（tools/gui_preview.py）
和自测（tools/test_gui.py）盯着。
"""

import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMGDIR = os.path.join(os.path.dirname(HERE), 'zk42v-epd-app', 'Src', 'img')
FONT_OUT = os.path.join(IMGDIR, 'zkgui_font.h')
TRIG_OUT = os.path.join(IMGDIR, 'zkgui_trig.h')

# ---------------------------------------------------------------- 5x7 数字
ASCII5x7 = {
    '0': ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    '1': ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    '2': ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    '3': ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    '4': ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    '5': ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    '6': ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    '7': ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    '8': ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    '9': ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    '-': ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ':': ["00000", "00100", "00100", "00000", "00100", "00100", "00000"],
    ' ': ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}

# ---------------------------------------------------------------- 16x16 中文
# 每项 (x, y, w, h)，坐标 0..15，原点在左上。7 个是月历/星期用的，
# 后面 9 个是农历文字（正月/冬月/腊月、初一~三十、廿几）要用的字。
CJK_RECTS = {
    # 日 一 二 三 四 五 六 月 —— 星期表头 + 日期
    '日': [(3, 1, 10, 2), (3, 6, 10, 2), (3, 11, 10, 2),
           (3, 1, 2, 12), (11, 1, 2, 12)],
    '一': [(3, 7, 10, 2)],
    '二': [(4, 4, 8, 2), (2, 10, 12, 2)],
    '三': [(4, 3, 8, 2), (3, 7, 10, 2), (2, 11, 12, 2)],
    '四': [(2, 2, 12, 2), (2, 11, 12, 2), (2, 2, 2, 11), (12, 2, 2, 11),
           (5, 4, 2, 6), (9, 4, 2, 6), (5, 8, 6, 2)],
    '五': [(2, 2, 12, 2), (6, 3, 2, 5), (3, 7, 10, 2), (9, 8, 2, 4),
           (2, 12, 12, 2)],
    '六': [(7, 1, 2, 2), (3, 4, 10, 2), (5, 7, 2, 3), (4, 10, 2, 3),
           (9, 7, 2, 3), (10, 10, 2, 3)],
    '月': [(2, 2, 10, 2), (2, 12, 10, 2), (2, 2, 2, 12), (10, 2, 2, 12),
           (4, 6, 8, 2), (4, 9, 8, 2)],
    # 农历要用的
    '七': [(2, 3, 12, 2), (6, 5, 2, 6), (5, 11, 9, 2)],
    '八': [(5, 2, 2, 6), (4, 8, 2, 4), (3, 12, 4, 2),
           (9, 2, 2, 6), (10, 8, 2, 4), (11, 12, 4, 2)],
    '九': [(2, 3, 9, 2), (9, 3, 2, 7), (4, 10, 10, 2), (2, 12, 12, 2)],
    '十': [(7, 2, 2, 12), (3, 7, 10, 2)],
    '冬': [(4, 2, 4, 2), (7, 4, 3, 3), (3, 7, 10, 2),
           (4, 10, 3, 3), (9, 10, 3, 3)],
    '腊': [(1, 4, 5, 2), (1, 4, 2, 10), (4, 4, 2, 10), (1, 12, 5, 2),
           (9, 2, 6, 2), (11, 4, 1, 3), (13, 4, 1, 3),
           (9, 8, 6, 2), (9, 8, 2, 6), (13, 8, 2, 6), (9, 12, 6, 2)],
    '正': [(2, 2, 12, 2), (6, 4, 2, 10), (4, 7, 4, 2), (4, 13, 9, 2),
           (10, 9, 2, 5)],
    '初': [(4, 1, 3, 2), (5, 3, 2, 11), (2, 4, 3, 2), (2, 7, 3, 2),
           (9, 2, 4, 2), (11, 4, 2, 8), (9, 12, 6, 2)],
    '廿': [(2, 4, 12, 2), (5, 6, 2, 6), (9, 6, 2, 6), (2, 11, 12, 2)],
}

# 生成时的顺序 = C 里的索引顺序（zkgui.c 里有对应的 ZK_CJK_xxx 常量）
CJK_ORDER = ['日', '一', '二', '三', '四', '五', '六', '月',
             '七', '八', '九', '十', '冬', '腊', '正', '初', '廿']


def pack_rows(rows, w):
    """ASCII 点阵 -> 每行一个字节（MSB first，行末补到整字节）"""
    stride = (w + 7) // 8
    out = []
    for r in rows:
        assert len(r) == w, '一行 %d 位，实际 %d：%s' % (w, len(r), r)
        # '1' 和 '#' 都算着色
        bits = [1 if c in '#1' else 0 for c in r]
        for b in range(stride):
            v = 0
            for i in range(8):
                idx = b * 8 + i
                v = (v << 1) | (bits[idx] if idx < w else 0)
            out.append(v)
    return out


def cjk_bitmap(ch):
    g = [[0] * 16 for _ in range(16)]
    for (x, y, w, h) in CJK_RECTS[ch]:
        for j in range(y, min(16, y + h)):
            for i in range(x, min(16, x + w)):
                g[j][i] = 1
    return [''.join('#' if v else '.' for v in row) for row in g]


def gen_font():
    L = []
    a = L.append
    a('/*')
    a(' * 自动生成 —— 别手改。改字形请改 tools/gen_font.py，再跑一遍。')
    a(' *')
    a(' *   zk_font5x7  : 数字/-/:/空格，5 列 x 7 行，每字节 1 行（高位在左）')
    a(' *   zk_font_cjk : 中文单字 16x16，每字 32 字节（16 行 x 2 字节）')
    a(' */')
    a('#ifndef __ZK_GUI_FONT_H__')
    a('#define __ZK_GUI_FONT_H__')
    a('')
    a('#include <stdint.h>')
    a('')
    a('#define ZK_FONT5_W     5')
    a('#define ZK_FONT5_H     7')
    a('#define ZK_FONT_CJK_W  16')
    a('#define ZK_FONT_CJK_H  16')
    a('')
    a('#define ZK_F5_MINUS    10')
    a('#define ZK_F5_COLON    11')
    a('#define ZK_F5_SPACE    12')
    a('#define ZK_F5_NUM      13')
    a('')
    a('static const uint8_t zk_font5x7[ZK_F5_NUM][ZK_FONT5_H] = {')
    order = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '-', ':', ' ']
    for ch in order:
        a('    { %s },   /* %s */'
          % (', '.join('0x%02X' % v for v in pack_rows(ASCII5x7[ch], 5)), ch))
    a('};')
    a('')
    a('/* 中文单字；索引顺序 = tools/gen_font.py 里的 CJK_ORDER */')
    a('#define ZK_CJK_NUM    %d' % len(CJK_ORDER))
    a('static const uint8_t zk_font_cjk[ZK_CJK_NUM][ZK_FONT_CJK_H * 2] = {')
    for ch in CJK_ORDER:
        rows = pack_rows(cjk_bitmap(ch), 16)
        assert len(rows) == 32, len(rows)
        a('    { %s,' % ', '.join('0x%02X' % v for v in rows[:8]))
        a('      %s,' % ', '.join('0x%02X' % v for v in rows[8:16]))
        a('      %s,' % ', '.join('0x%02X' % v for v in rows[16:24]))
        a('      %s },   /* %s */'
          % (', '.join('0x%02X' % v for v in rows[24:32]), ch))
    a('};')
    a('')
    a('#endif /* __ZK_GUI_FONT_H__ */')
    with open(FONT_OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


def gen_trig():
    """表针角度表：zk_trig60[i] = (sin, -cos) * 1024，角度 = 6*i 度。
       屏幕 y 向下，所以竖直分量取 -cos。用定点表而不是 sinf/cosf：
       省下 libm，而且预览（宿主机）和固件算出的表针角度一模一样。"""
    L = []
    a = L.append
    a('/* 自动生成 —— 别手改。见 tools/gen_font.py。 */')
    a('#ifndef __ZK_GUI_TRIG_H__')
    a('#define __ZK_GUI_TRIG_H__')
    a('')
    a('#include <stdint.h>')
    a('')
    a('static const int16_t zk_trig60[60][2] = {')
    for i in range(60):
        ang = math.radians(6.0 * i)
        a('    { %6d, %6d },   /* %2d 度 */'
          % (round(math.sin(ang) * 1024), round(-math.cos(ang) * 1024), 6 * i))
    a('};')
    a('')
    a('#endif /* __ZK_GUI_TRIG_H__ */')
    with open(TRIG_OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


def main():
    gen_font()
    gen_trig()
    print('写出 %s（5x7 %d 字 + 中文 %d 字）' % (FONT_OUT, 13, len(CJK_ORDER)))
    print('写出 %s（60 项角度表）' % TRIG_OUT)


if __name__ == '__main__':
    main()
