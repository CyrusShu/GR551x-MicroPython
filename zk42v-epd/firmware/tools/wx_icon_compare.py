#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把"现在固件里那 9 个天气图标"和"和风官方图标"渲染成**同一张对比图**，给眼睛看。

    /tmp/zkvenv/bin/python3 tools/wx_icon_compare.py [输出.png]

为什么要有它（2026-10-02 用户："我 iPhone 上的天气图标都很好看，你从和风天气
服务器上能获取图标吗"）：
  · 和风确实有一套**开源（MIT）图标库**：github.com/qwd/Icons，507 个 SVG，
    文件名就是 API 返回的那个 `icon` 编号（100 晴 / 305 小雨 / 1003 暴雨预警…），
    而且**同时提供 TTF** —— 正好跟我们 build 41 那套渲染流水线（gen_weather.py，
    TTF → 8 倍超采样 → 20×20 二值化）是同一个路子，不用另造轮子。
  · 但 iOS 天气里那套是 **Apple 自己画的**（SF Symbols 的许可只限 Apple 平台 App），
    拿不到 —— 所以这里比的是"和风那套 vs 我们现在那套"。

行顺序（每行 9 个 = 内部天气码 1..9：晴 多云 阴 小雨 大雨 雷阵雨 雪 雾 风）：
    now      现在固件里用的（Material Symbols + Font Awesome 混搭，= gen_weather.PROFILES['mix']）
    qw-line  和风官方图标（线条版）
    qw-fill  和风官方图标（实心版；"风"没有 fill 版本，退回线条版）

⚠ 图和固件是**同一条渲染路径**（同一个 render_icon、同样的 20×20、同样的
   8 倍超采样 + 阈值 128），所以"图上什么鬼样，屏上就是什么鬼样"。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gen_weather as GEN          # noqa: E402
import wx_sheet as SHEET           # noqa: E402

ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9]
NAME = {1: '晴', 2: '多云', 3: '阴', 4: '小雨', 5: '大雨',
        6: '雷阵雨', 7: '雪', 8: '雾', 9: '风'}

# 内部天气码 -> 和风的 icon 编号（跟 API 返回的 icon 字段是同一套编号）
#   100 sunny / 101 cloudy / 104 overcast / 305 light-rain / 307 heavy-rain
#   302 thundershower / 400 light-snow / 501 foggy / 2051 wind
QW = {1: '100', 2: '101', 3: '104', 4: '305', 5: '307',
      6: '302', 7: '400', 8: '501', 9: '2051'}


def qw_codepoints():
    """和风图标编号 -> 字体里的码点（表来自官方仓库 font/qweather-icons.json）"""
    import json

    p = os.path.join(GEN.ICON_DIR, 'qweather-icons.codepoints.json')
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def qw_ttf():
    return os.path.join(GEN.ICON_DIR, 'qweather-icons.ttf')


def render_qweather(cp_table, fill):
    from PIL import ImageFont          # noqa: F401  （render_icon 内部会 import）

    icons = {}
    for code in ORDER:
        key = QW[code] + ('-fill' if fill else '')
        if key not in cp_table:        # "风"没有 fill，退回线条版
            key = QW[code]
        icons[code] = GEN.render_icon(qw_ttf(), cp_table[key])
    return icons


def render_now():
    icons = {}
    for code in ORDER:
        setname, cp = GEN.PROFILES['mix'][code]
        icons[code] = GEN.render_icon(GEN.icon_ttf(setname), cp,
                                      axes=GEN.SETS[setname].get('axes'))
    return icons


def main(argv):
    out = argv[1] if len(argv) > 1 else os.path.join(
        GEN.FW, 'docs', 'preview', 'preview-wx-icons-qweather.png')

    cp_table = qw_codepoints()
    groups = [
        ('now     （现在：Material Symbols + Font Awesome 混搭）', render_now()),
        ('qw-line （和风官方图标 · 线条版）', render_qweather(cp_table, False)),
        ('qw-fill （和风官方图标 · 实心版）', render_qweather(cp_table, True)),
    ]

    # 每行先自检：20×20、有墨、别糊成一团
    print('天气码：' + '  '.join('%d=%s' % (c, NAME[c]) for c in ORDER))
    for title, icons in groups:
        ink = ' '.join('%3d' % icons[c].__str__().count('#') for c in ORDER)
        print('%-46s 墨量: %s' % (title.split('（')[0].strip(), ink))

    # 拼图：行之间留一条灰线；左边用 PIL 默认字体写 ASCII 标签
    scale, pad = 8, 8
    from PIL import Image, ImageDraw

    body = [SHEET.sheet(icons, scale, pad) for _, icons in groups]
    cw, ch = len(body[0][0]), len(body[0])
    label_h = 16
    width = cw + 150
    rows = []
    for i, ((title, _), img_rows) in enumerate(zip(groups, body)):
        band = [[255] * width for _ in range(label_h)]
        rows += band + [r + [255] * 150 for r in img_rows]
        if i != len(groups) - 1:
            rows += [[170] * width]
    img = Image.new('L', (width, len(rows)), 255)
    img.putdata([v for r in rows for v in r])
    d = ImageDraw.Draw(img)

    def label(x, y, text):
        for k, ch_ in enumerate(text):
            if 32 <= ord(ch_) < 127:
                d.text((x + k * 6, y + 3), ch_, fill=0)

    y = 0
    for i, (title, _) in enumerate(groups):
        label(8, y, title.encode('ascii', 'ignore').decode() or 'row %d' % i)
        y += label_h + ch
        if i != len(groups) - 1:
            y += 1
    for i, code in enumerate(ORDER):
        label(width - 146 + i * ((cw) // 9), len(rows) - 0, '')   # 占位
    img.save(out)
    print('写出 %s  %dx%d' % (out, img.size[0], img.size[1]))
    print('行顺序：now / qw-line / qw-fill；列顺序：%s'
          % ' '.join(NAME[c] for c in ORDER))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
