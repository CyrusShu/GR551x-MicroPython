#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把一套**真黑体**栅格化成 16x16 点阵，写出 tools/cjk_ascii.py。

为什么要有这个：`gen_font.py` 原来那 18 个汉字是**手画的矩形**（`CJK_RECTS`），
简单的字（日/一/月/十）还凑合，一上复杂字就露馅 —— build 28 的日历页上
「初/廿」被画成 π 和一条横线，「农/历/星/期/年」更是认不出来。
16x16 是点阵字库的经典尺寸，直接拿黑体栅格化就清楚多了。

它不是每次构建都要跑的：栅格化结果已经作为 **ASCII 点阵**存进 `cjk_ascii.py`
（跟 `zkgui_font.h` 一样是"生成物"，代码库里直接存着，看一眼就知道每个字长什么样）。
只有在**要加字/换字体**的时候才需要重新跑这个脚本。

    python3 -m venv /tmp/zkvenv && /tmp/zkvenv/bin/pip install pillow
    /tmp/zkvenv/bin/python tools/cjk_from_ttf.py            # 写出 tools/cjk_ascii.py

字体换成别的：改下面的 FONT / SIZE / THR。脚本最后会自检每个字的"墨量"和
"有没有碰到边框"（碰到了说明被裁了，把 SIZE 调小或者改 dy）。
"""

import os

from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"   # 冬青黑体简体中文
SIZE = 15          # 字号；16px 的格子留 1px 边距，边缘不会糊在一起
CANVAS = 24        # 先画在 24x24 里（anchor=mm 居中），再裁中间 16x16
CROP = 4
THR = 110          # 二值化阈值（抗锯齿灰度 >= 这个值算着色）
DY = -1            # 汉字在 em 框里偏下，往上挪一格像素更居中

# 顺序 = zkgui.c 里的 ZK_CJK_xxx 索引顺序 = CJK_ORDER
CHARS = ['日', '一', '二', '三', '四', '五', '六', '月',
         '七', '八', '九', '十', '冬', '腊', '正', '初', '廿', '闰',
         '年', '星', '期', '农', '历']

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cjk_ascii.py')


def raster(ch):
    font = ImageFont.truetype(FONT, SIZE)
    img = Image.new('L', (CANVAS, CANVAS), 0)
    ImageDraw.Draw(img).text((CANVAS / 2, CANVAS / 2 + DY), ch,
                             font=font, fill=255, anchor='mm')
    return [[1 if img.getpixel((x, y)) >= THR else 0
             for x in range(CROP, CROP + 16)] for y in range(CROP, CROP + 16)]


def main():
    out = ["# 自动生成 —— 别手改。见 tools/cjk_from_ttf.py（真黑体栅格化的结果）。",
           "#",
           "# 每个字 16 行 x 16 列，'#' = 着色。gen_font.py 把它打包成 zkgui_font.h。",
           "",
           "CJK_ASCII = {"]
    bad = []
    for ch in CHARS:
        try:
            g = raster(ch)
        except ValueError as e:                      # 字体里没这个字
            print('!! 字体里没有 %s：%s' % (ch, e))
            bad.append(ch)
            continue
        out.append("    '%s': [" % ch)
        for row in g:
            out.append("        '%s'," % ''.join('#' if b else '.' for b in row))
        out.append("    ],")

        ink = sum(sum(r) for r in g)
        edge = sum(1 for y in range(16) for x in range(16)
                   if g[y][x] and (x in (0, 15) or y in (0, 15)))
        flag = ''
        if ink == 0:
            flag = '  <<< 一个点都没有'
            bad.append(ch)
        elif edge > 12:
            flag = '  <<< 有笔画贴边，可能被裁了'
        print("  %s  墨=%3d  贴边=%2d%s" % (ch, ink, edge, flag))

    out.append("}")
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    print('')
    print('写出 %s（%d 个字）' % (OUT, len(CHARS)))
    if bad:
        print('有问题的字：%s' % ' '.join(bad))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
