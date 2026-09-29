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

# 顺序 = zkgui.c 里的 CJK_xxx 索引顺序（gen_font.py 直接用这个顺序生成字模表）
CHARS = ['日', '一', '二', '三', '四', '五', '六', '月',
         '七', '八', '九', '十', '冬', '腊', '正', '初', '廿', '闰',
         '年', '星', '期', '农', '历',
         # build 31：表头要报生肖年（照社区第 14 号那版），要 12 个生肖字
         '鼠', '牛', '虎', '兔', '龙', '蛇', '马', '羊', '猴', '鸡', '狗', '猪']

# 月历格子里的农历那一行用**小一号**的字（12x12）。
# 为什么：样板（社区第 14 号那版）里"今天"是一个大红圆包住日号和农历两个字 ——
# 我们原来用 16x16 的字，两个字 33px 宽，39px 的行高里圆的弦长根本放不下，
# 只能把圆缩小到"只套日号"。换成 12x12 之后两个字 25px，圆能做到半径 21，
# 数字和农历都能进圆里，格子也跟着松快。
# 月历格子里的农历日名（初一..三十）：**这套要 1px 笔画**。
# 为什么：样板上农历那行的笔画量出来只有 ~1 面板像素（照片里 2px / 2.13），
# 字高也只有 9~10px；用 13px 的 wqy/Hiragino 都是 2px 笔画，显粗。
# 11px + 阈值 100 正好是 1px 且不缺笔（初/九/廿/十 逐个放大核过）。
CHARS_S = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '初', '廿']

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cjk_ascii.py')

SIZE_S = 11         # 小字（农历日名）：11px，笔画 1px
THR_S = 100

# 数字：**单独一套**，用 Arial Bold —— 样板上的日号就是这种粗黑数字（帽高约 14px、
# 笔画 2~3px）。我们原来的 5x7 点阵放大 2 倍虽然也是 14px，但形状是方块拼的，
# 细看差一截。这套只占 10 个字 x 32 字节 = 320 字节。
NUM_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
DIGITS   = "0123456789:."
SIZE_N   = 19       # 帽高约 14px，正好是样板的日号高


def raster_num(ch, size=SIZE_N, canvas=32, crop_y=8, crop_x=10, thr=110, dy=-1):
    """数字画进 12x16 的小格子（宽度留 12，够放 Arial Bold 的数字）"""
    font = ImageFont.truetype(NUM_FONT, size)
    img = Image.new('L', (canvas, canvas), 0)
    ImageDraw.Draw(img).text((canvas / 2, canvas / 2 + dy), ch,
                             font=font, fill=255, anchor='mm')
    return [[1 if img.getpixel((x, y)) >= thr else 0
             for x in range(crop_x, crop_x + 12)] for y in range(crop_y, crop_y + 16)]


def raster(ch):
    font = ImageFont.truetype(FONT, SIZE)
    img = Image.new('L', (CANVAS, CANVAS), 0)
    ImageDraw.Draw(img).text((CANVAS / 2, CANVAS / 2 + DY), ch,
                             font=font, fill=255, anchor='mm')
    return [[1 if img.getpixel((x, y)) >= THR else 0
             for x in range(CROP, CROP + 16)] for y in range(CROP, CROP + 16)]

def raster_s(ch, size=11, canvas=28, crop=8, thr=100, dy=-1):
    """小字版：画在 28x28 里、裁中间 12x12"""
    font = ImageFont.truetype(FONT, size)
    img = Image.new('L', (canvas, canvas), 0)
    ImageDraw.Draw(img).text((canvas / 2, canvas / 2 + dy), ch,
                             font=font, fill=255, anchor='mm')
    return [[1 if img.getpixel((x, y)) >= thr else 0
             for x in range(crop, crop + 12)] for y in range(crop, crop + 12)]


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

    # ---- 小字（月历格子里的农历）----
    out.append("")
    out.append("# 月历格子里那行农历用的小字（12x12）：见 cjk_from_ttf.py 的 CHARS_S")
    out.append("CJK_ASCII_S = {")
    for ch in CHARS_S:
        g = raster_s(ch, size=SIZE_S, thr=THR_S)
        out.append("    '%s': [" % ch)
        for row in g:
            out.append("        '%s'," % ''.join('#' if b else '.' for b in row))
        out.append("    ],")
        ink = sum(sum(r) for r in g)
        edge = sum(1 for y in range(12) for x in range(12)
                   if g[y][x] and (x in (0, 11) or y in (0, 11)))
        print("  [小] %s  墨=%3d  贴边=%2d" % (ch, ink, edge))
    out.append("}")
    out.append("")
    out.append("# 日号/时间用的粗体数字（Arial Bold 19px -> 12x16）：见 cjk_from_ttf.py 的 DIGITS")
    out.append("NUM_ASCII = {")
    for ch in DIGITS:
        g = raster_num(ch)
        out.append("    '%s': [" % ch)
        for row in g:
            out.append("        '%s'," % ''.join('#' if b else '.' for b in row))
        out.append("    ],")
        ink = sum(sum(r) for r in g)
        ys = [y for y in range(16) if any(g[y])]
        print("  [数字] %s  墨=%3d  高=%d 行(%d..%d)" % (ch, ink, (max(ys)-min(ys)+1) if ys else 0,
                                                     min(ys) if ys else -1, max(ys) if ys else -1))
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
