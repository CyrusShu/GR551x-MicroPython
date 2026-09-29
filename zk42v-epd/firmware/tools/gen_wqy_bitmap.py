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

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(FW))     # .../a（outputs/firmware 往上两级）
BDF   = os.path.join(ROOT, 'work', 'fonts', 'wenquanyi_9pt.bdf')
BDF12 = os.path.join(ROOT, 'work', 'fonts', 'wenquanyi_12pt.bdf')
OUT = os.path.join(HERE, 'wqy_font.py')

# 月历格子里农历日名要用的字（初一..三十）
WANT = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '初', '廿']

# 表头"农历"这两个字：原厂字库（u8g2 子集）里**没有**，12pt 点阵里 16x16 但只有 1px
# 笔画，而原厂那套 wqy12 是 2px —— 所以取 12pt 的字形再**加粗 1px**（形态学膨胀，
# 纯点阵操作，不会像 TTF 栅格化那样"发虚"），字重就跟旁边对上了。
WANT16 = ['农', '历']


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
    lines.append('# 表头"农历"用的 16x16：文泉驿点阵宋体 12pt + 加粗 1px（原厂字库缺这两个字）')
    lines.append('CJK16 = {')
    f12 = load_bdf(BDF12)
    for ch in WANT16:
        g = f12.get(ord(ch))
        if g is None:
            print('  %s 在 12pt 里也没有' % ch)
            continue
        w, h, xo, yo = g['bbx']
        rows = dilate(pad16(rows_of(g, w)), 1, horiz_only=True)   # 只左右加粗（中等字重）
        lines.append("    '%s': [" % ch)
        for r in rows:
            lines.append("        '%s'," % r)
        lines.append('    ],')
        ink = sum(r.count('#') for r in rows)
        print('  %s  16x16 加粗后墨=%d' % (ch, ink))
    lines.append('}')
    lines.append('')

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('写出 %s（%d 个字）' % (OUT, len(WANT) - len(miss)))
    if miss:
        print('BDF 里没有：%s' % ' '.join(miss))


if __name__ == '__main__':
    main()
