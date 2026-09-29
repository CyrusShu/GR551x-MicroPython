#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从**原厂固件**的字库里抽字形，生成 tools/vendor_font.py（真正的点阵，不是我们栅格化的）。

    python3 tools/gen_vendor_font.py

为什么改用它：样板（qbsg 上那张「三色纯日历」）的排版我量到最后发现，它的字是
**u8g2 的两套现成字库** ——

    u8g2_font_helvB14_tn      日号（Helvetica Bold，数字 9x13 —— 正好是量到的 13.6px 高）
    u8g2_font_wqy12_t_lunar   农历/节气/干支/生肖（文泉驿 12px，16x16，笔画细）
    u8g2_font_wqy9_t_lunar    同上的小一号（表头"农历X月"用）

而这两套字**就在原厂那份源码里**（`<本机项目>/work/github/epd-nrf5-user/GUI/fonts.c`
里是一堆 u8g2 字体数组）。所以我们不再拿系统字体去"仿"，直接把字形抽出来用 ——
跟样板同源，笔画粗细自然就对了（这就是用户说的"节气的字比普通字粗、初九的九很纤细"：
节气那两个字在他们固件里是另一套更粗的字，见 tools/ 说明）。

u8g2 字体格式（对着他们 u8g2_font.c 的实现解出来的）：
  头 23 字节：glyph_cnt / bbx / bits_per_0 / bits_per_1 / bits_per_char_{w,h,x,y,dx} /
              max_w / max_h / x_off / y_off / ascent / descent / ... / start_pos_unicode(2B)
  unicode 查找表：每条 4 字节（累计偏移 2B + 该段最后一个码点 2B）
  ASCII 段从 23 开始，每条 = 编码(1B) + 长度(1B) + 数据
  字形数据是位流（**低位在前**）：w/h/x/y/dx 各几 bit，然后
    RLE = 一组 (a 个 0, b 个 1)，后面 1 bit：是 1 就**用同一组再画一遍**；是 0 就换行
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
SRC = ('/Users/mac/Documents/Codex/2026-09-15/a/work/github/epd-nrf5-user/'
       'GUI/fonts.c')
OUT = os.path.join(HERE, 'vendor_font.py')


# ---------------------------------------------------------------- C 字符串解析
def load_font_text(path, name):
    s = open(path, encoding='utf-8', errors='replace').read()
    # ⚠ 字体数据里可能有裸的 ';' 字节，不能用 ';' 结尾来切，只认"一串字符串字面量"
    pat = (r'const\s+uint8_t\s+%s\s*\[[^\]]*\]\s*U8G2_FONT_SECTION\([^)]*\)\s*=\s*'
           r'((?:\s*"(?:\\[0-7]{1,3}|\\x[0-9a-fA-F]{1,2}|\\.|[^"\\])*"\s*)+)\s*;') % name
    m = re.search(pat, s, re.S)
    if not m:
        raise SystemExit('找不到 %s（%s）' % (name, path))
    out = bytearray()
    for tok in re.findall(r'"((?:\\[0-7]{1,3}|\\x[0-9a-fA-F]{1,2}|\\.|[^"\\])*)"',
                          m.group(1)):
        i = 0
        while i < len(tok):
            c = tok[i]
            if c != '\\':
                out.append(ord(c))
                i += 1
                continue
            nxt = tok[i + 1]
            if nxt.isdigit():
                j, oct_s = i + 1, ''
                while j < len(tok) and len(oct_s) < 3 and tok[j].isdigit():
                    oct_s += tok[j]
                    j += 1
                out.append(int(oct_s, 8) & 0xFF)
                i = j
            elif nxt == 'x':
                j, hx = i + 2, ''
                while j < len(tok) and re.match(r'[0-9a-fA-F]', tok[j]):
                    hx += tok[j]
                    j += 1
                out.append(int(hx, 16))
                i = j
            else:
                out.append(ord({'n': 10, 't': 9, 'r': 13, '\\': 92,
                                '"': 34, "'": 39}.get(nxt, nxt)))
                i += 2
    return bytes(out)


class U8g2Font:
    def __init__(self, data):
        self.d = data
        self.glyph_cnt     = data[0]
        self.bits_per_0    = data[2]
        self.bits_per_1    = data[3]
        self.bpw, self.bph = data[4], data[5]
        self.bpx, self.bpy = data[6], data[7]
        self.bpdx          = data[8]
        self.max_w, self.max_h = data[9], data[10]
        self.ascent        = data[13]
        self.start_upper   = (data[17] << 8) | data[18]
        self.start_lower   = (data[19] << 8) | data[20]
        self.start_unicode = (data[21] << 8) | data[22]

    def glyph_data(self, cp):
        d, n = self.d, len(self.d)
        if cp <= 255:
            p = self.start_lower if cp >= ord('a') else (
                self.start_upper if cp >= ord('A') else 0)
            while 23 + p + 1 < n:
                if d[23 + p + 1] == 0:
                    return None
                if d[23 + p] == cp:
                    return 23 + p + 2
                p += d[23 + p + 1]
            return None
        base = 23 + self.start_unicode
        if base >= n:
            return None
        p, tbl = 0, base
        while True:
            if tbl + 4 > n:
                return None
            p += (d[tbl] << 8) | d[tbl + 1]
            e = (d[tbl + 2] << 8) | d[tbl + 3]
            tbl += 4
            if e >= cp:
                break
        cur = base + p
        while True:
            if cur + 3 > n:
                return None
            e = (d[cur] << 8) | d[cur + 1]
            if e == 0:
                return None
            if e == cp:
                return cur + 3
            cur += d[cur + 2]

    class Bits:
        """u8g2 的位流：**低位在前**，先读到的位落在结果低位"""
        def __init__(self, data, bitpos):
            self.d, self.pos = data, bitpos

        def u(self, cnt):
            v = 0
            for k in range(cnt):
                byte = self.d[self.pos >> 3]
                v |= ((byte >> (self.pos & 7)) & 1) << k
                self.pos += 1
            return v

        def s(self, cnt):
            if cnt == 0:
                return 0
            v = self.u(cnt)
            return v - (1 << cnt) if v & (1 << (cnt - 1)) else v

    def bitmap(self, cp):
        gd = self.glyph_data(cp)
        if gd is None:
            return None
        b = self.Bits(self.d, gd * 8)
        w, h = b.u(self.bpw), b.u(self.bph)
        x, y, dx = b.s(self.bpx), b.s(self.bpy), b.s(self.bpdx)
        if w == 0 or h == 0:
            return None
        px = [[0] * w for _ in range(h)]
        lx = ly = 0
        while ly < h:
            a, c = b.u(self.bits_per_0), b.u(self.bits_per_1)
            while True:                       # 同一组 (a,b) 可以重复画
                for _ in range(a):
                    lx += 1
                    if lx >= w:
                        lx, ly = 0, ly + 1
                for _ in range(c):
                    if ly < h and lx < w:
                        px[ly][lx] = 1
                    lx += 1
                    if lx >= w:
                        lx, ly = 0, ly + 1
                if b.u(1) == 0:
                    break
        return w, h, x, y, dx, px


# 要抽哪些字：我们页面用到的全部汉字 + 24 个节气名（48 个字）
CJK_WANT = ('日一二三四五六七八九十冬腊正初廿闰年星期农历'
            '鼠牛虎兔龙蛇马羊猴鸡狗猪'
            '冬分处夏大寒小惊春暑水清满白秋种立至芒蛰谷降雨雪霜露')
NUM_WANT = '0123456789:'


def main():
    cjk = U8g2Font(load_font_text(SRC, 'u8g2_font_wqy12_t_lunar'))
    num = U8g2Font(load_font_text(SRC, 'u8g2_font_helvB14_tn'))

    lines = ['# 自动生成 —— 别手改。见 tools/gen_vendor_font.py。',
             '#',
             '# 字形直接来自**原厂固件带的 u8g2 字库**（GUI/fonts.c），跟样板同源：',
             '#   CJK  <- u8g2_font_wqy12_t_lunar  （文泉驿 12px，16x16，笔画细）',
             '#   NUM  <- u8g2_font_helvB14_tn     （Helvetica Bold，数字 9x13）',
             '',
             'CJK = {']
    miss = []
    for ch in CJK_WANT:
        r = cjk.bitmap(ord(ch))
        if r is None:
            miss.append(ch)
            continue
        w, h, x, y, dx, px = r
        lines.append("    '%s': [" % ch)
        for row in px:
            lines.append("        '%s'," % ''.join('#' if v else '.' for v in row))
        lines.append('    ],')
    lines.append('}')
    lines.append('')
    lines.append('NUM = {')
    for ch in NUM_WANT:
        r = num.bitmap(ord(ch))
        if r is None:
            miss.append(ch)
            continue
        w, h, x, y, dx, px = r
        lines.append("    '%s': [" % ch)
        for row in px:
            lines.append("        '%s'," % ''.join('#' if v else '.' for v in row))
        lines.append('    ],')
    lines.append('}')
    lines.append('')

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print('CJK 抽到 %d 个字（宽高最多 %dx%d），NUM 抽到 %d 个'
          % (len(CJK_WANT) - len(miss), cjk.max_w, cjk.max_h, len(NUM_WANT)))
    if miss:
        print('原厂字库里没有这些：%s' % ' '.join(miss))
    print('写出 %s' % OUT)


if __name__ == '__main__':
    main()
