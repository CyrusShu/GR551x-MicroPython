#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从**文泉驿点阵宋体**（wqy-bitmapsong）里抽月历格子用的农历细字，生成 tools/wqy_font.py。

    python3 tools/gen_wqy_bitmap.py

为什么要它：样板（qbsg 那张「三色纯日历」）上农历那行**笔画只有 1 面板像素**
（照片 2px ÷ 2.13），字高 9~10px —— 而原厂固件带的那两套 u8g2 文泉驿子集
（wqy9/wqy12）解出来都是 **2px 笔画**，u8g2 官方最小也只有 12px。
真正 1px 笔画的是**文泉驿点阵宋体**（就是当年给 X11 用的那套点阵字，
9/10/11/12pt 各有 bitmap strike）。

数据来源（Debian 的 xfonts-wqy 包，本地路径见下面 BDF）：
    https://deb.debian.org/debian/pool/main/x/xfonts-wqy/xfonts-wqy_1.0.0~rc1.orig.tar.gz
    -> wqy-bitmapsong/wenquanyi_9pt.bdf

⚠ BDF 本身不进口袋（几 MB），进库的是抽出来的 ASCII 点阵（tools/wqy_font.py）。
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(FW))     # .../a（outputs/firmware 往上两级）
BDF   = os.path.join(ROOT, 'work', 'fonts', 'wenquanyi_9pt.bdf')
BDF12 = os.path.join(ROOT, 'work', 'fonts', 'wenquanyi_12pt.bdf')
OUT = os.path.join(HERE, 'wqy_font.py')

# 月历格子里农历日名要用的字（初一..三十）
WANT = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '初', '廿']

# 表头 / 星期条 / 月历页用到的汉字（= gen_font.CJK_ORDER 里前 35 个：日月星期 +
# 农历月份用字 + 生肖）。
#
# ⚠ 2026-10-01 改动（用户提的）：这些字**改成不加粗**（纯 12pt 1px）。
#   原来为了跟原厂那套 u8g2 wqy12（2px）对齐字重，取了 12pt 再左右膨胀 1px；
#   但用户对比后说：表头的「2026年10月 农历八月」比旁边的天气文字（同一套
#   文泉驿点阵宋体、1px）难看 —— 同一行里两种字重本来就别扭。
#   现在这 35 个字全部用 1px，跟天气文字/农历日名一家；**节气那 24 个字保持
#   原厂 2px**（样板里节气本来就比农历日名粗，那是刻意留的层次，见 docs）。
# 这份表 = gen_font.py 的 THIN_SET（那里是唯一真相）—— 直接拿过来，免得两处对不上。
# 内容是：表头/农历月份用字 + 生肖（前 35 个）+ 干支（22）+ 城市名用字。
def thin_chars():
    sys.path.insert(0, HERE)
    import gen_font
    return list(gen_font.THIN_LIST)


WANT16 = thin_chars()


def small_chars():
    """表头里"次要信息"（农历月份 / 生肖 / 城市名）用的 11x11 小字 —— 名单在
    gen_font.py 的 SMALL_CHARS（唯一真相），从 9pt 的 BDF 里取。"""
    sys.path.insert(0, HERE)
    import gen_font
    return list(gen_font.SMALL_CHARS)


WANT_SMALL = small_chars()

# 农历日名的 16px 版：**同一套文泉驿点阵宋体、但用 12pt**（16x16、笔画 1px）。
# 想让"日历格子里的农历"保持 16px、但比节气（原厂 wqy12，2px 笔画）细一档时用它。
WANT_L16 = WANT


def load_bdf(path):
    """BDF -> {码点: (w, h, xoff, yoff, [每行的 '#'/'.' 字符串])}"""
    out = {}
    cur = None
    in_bitmap = False
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            if line.startswith('STARTCHAR'):
                cur, in_bitmap = {}, False
            elif cur is not None and line.startswith('ENCODING'):
                cur['cp'] = int(line.split()[1])
            elif cur is not None and line.startswith('BBX'):
                cur['bbx'] = tuple(int(x) for x in line.split()[1:5])
            elif cur is not None and line.startswith('BITMAP'):
                in_bitmap = True
            elif cur is not None and line.startswith('ENDCHAR'):
                if 'cp' in cur and 'bbx' in cur and cur.get('rows'):
                    out[cur['cp']] = cur
                cur, in_bitmap = None, False
            elif cur is not None and in_bitmap and re.fullmatch(r'[0-9A-Fa-f]+', line.strip()):
                cur.setdefault('rows', []).append(line.strip())
    return out


def rows_of(g, w):
    """BDF 的十六进制行 -> '#'/'.'（每行左边补到整字节，取前 w 位）"""
    out = []
    for r in g['rows']:
        bits = bin(int(r, 16))[2:].zfill(len(r) * 4)[:w]
        out.append(bits.replace('0', '.').replace('1', '#'))
    return out


def pad16(rows):
    """把任意宽度补成 16x16（左上对齐）"""
    out = []
    for r in rows:
        out.append(r + '.' * (16 - len(r)))
    while len(out) < 16:
        out.append('.' * 16)
    return out


def dilate(rows, n=1, horiz_only=False):
    """形态学膨胀：每个点向外扩 1px（就是"加粗"）。
    horiz_only=True 时只在左右扩 —— 竖笔画变 2px、横笔画还是 1px，看着像"中等字重"，
    比四向加粗更接近原厂那套 wqy12（免得一个字太重、旁边太轻）。"""
    g = [[1 if c == '#' else 0 for c in r] for r in rows]
    for _ in range(n):
        h = [[0] * 16 for _ in range(16)]
        for y in range(16):
            for x in range(16):
                if (g[y][x] or (x > 0 and g[y][x - 1]) or (x < 15 and g[y][x + 1]) or
                        (not horiz_only and ((y > 0 and g[y - 1][x]) or
                                             (y < 15 and g[y + 1][x])))):
                    h[y][x] = 1
        g = h
    return [''.join('#' if v else '.' for v in r) for r in g]


def main():
    if not os.path.exists(BDF):
        raise SystemExit('找不到 %s\n（从 xfonts-wqy 包里解出来放到那儿，命令见脚本开头注释）' % BDF)
    font = load_bdf(BDF)
    print('BDF 里共 %d 个字形' % len(font))

    lines = ['# 自动生成 —— 别手改。见 tools/gen_wqy_bitmap.py。',
             '#',
             '# 月历格子里那行农历日名的**细字**：文泉驿点阵宋体 9pt（11x11，笔画 1px）。',
             '# 这是样板（qbsg 三色纯日历）上农历那行的观感 —— 1 面板像素的细笔画。',
             '',
             'LUNAR = {']
    miss = []
    for ch in WANT:
        g = font.get(ord(ch))
        if g is None:
            miss.append(ch)
            continue
        w, h, xo, yo = g['bbx']
        lines.append("    '%s': [" % ch)
        for r in rows_of(g, w):
            lines.append("        '%s'," % r)
        lines.append('    ],')
        ink = sum(r.count('#') for r in rows_of(g, w))
        edge = sum(1 for r in rows_of(g, w) if r[0] == '#' or r[-1] == '#')
        print('  %s  BBX %2dx%-2d 墨=%3d 贴边行=%d' % (ch, w, h, ink, edge))
    lines.append('}')
    lines.append('')
    f12 = load_bdf(BDF12)
    lines.append('# 农历日名的 16px 版：文泉驿点阵宋体 12pt（16x16、笔画 1px，不加粗）')
    lines.append('LUNAR16 = {')
    for ch in WANT_L16:
        g = f12.get(ord(ch))
        if g is None:
            continue
        w, h, xo, yo = g['bbx']
        rows = pad16(rows_of(g, w))
        lines.append("    '%s': [" % ch)
        for r in rows:
            lines.append("        '%s'," % r)
        lines.append('    ],')
    lines.append('}')
    lines.append('')
    lines.append('# 表头 / 星期条用的 16x16：文泉驿点阵宋体 12pt（**1px 细笔画，不加粗**）')
    lines.append('# 跟天气文字（Src/img/weather.c）同源同字重 —— 用户要的就是这个观感。')
    lines.append('CJK16 = {')
    for ch in WANT16:
        g = f12.get(ord(ch))
        if g is None:
            print('  %s 在 12pt 里也没有' % ch)
            continue
        w, h, xo, yo = g['bbx']
        rows = pad16(rows_of(g, w))
        lines.append("    '%s': [" % ch)
        for r in rows:
            lines.append("        '%s'," % r)
        lines.append('    ],')
        ink = sum(r.count('#') for r in rows)
        print('  %s  16x16 墨=%d' % (ch, ink))
    lines.append('}')
    lines.append('')
    # ---- build 62b：表头的"小号字"（11x11，文泉驿点阵宋体 9pt）----
    lines.append('# 表头里"次要信息"的小字（农历月份 / 生肖 / 城市名）：文泉驿点阵宋体 9pt，')
    lines.append('# 11x11、笔画 1px。表头一行要塞下 年月|干支农历月|生肖|天气温度城市，')
    lines.append('# 16px 排不下，所以这几项降一号（样板里"农历X月"也是小一号的 wqy9）。')
    lines.append('SMALL = {')
    for ch in WANT_SMALL:
        g = font.get(ord(ch))          # ⚠ 9pt 的那份（font）不是 12pt（f12）
        if g is None:
            print('  %s 在 9pt 里没有' % ch)
            continue
        w, h, xo, yo = g['bbx']
        rows = rows_of(g, w)
        lines.append("    '%s': [" % ch)
        for r in rows:
            lines.append("        '%s'," % r)
        lines.append('    ],')
    lines.append('}')
    lines.append('')

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('写出 %s（%d 个字）' % (OUT, len(WANT) - len(miss)))
    if miss:
        print('BDF 里没有：%s' % ' '.join(miss))


if __name__ == '__main__':
    main()
