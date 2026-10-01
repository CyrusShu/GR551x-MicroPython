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

from cjk_ascii import CJK_ASCII, CJK_ASCII_S, NUM_ASCII   # 我们栅格化的（补字用）
from vendor_font import CJK as V_CJK, NUM as V_NUM         # **原厂固件里的 u8g2 字形**
from wqy_font import CJK16 as WQY_CJK16, LUNAR16 as WQY_LUNAR16  # 文泉驿点阵宋体（补字 / 农历细字）

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
    '.': ["00000", "00000", "00000", "00000", "00000", "00110", "00110"],
    # build 31：表头右上角要写「3.90V」「26C」，所以补 V 和 C
    'V': ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    'C': ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    # build 61：温度要写正经的度数（26.4℃）。用一个 3x3 的小圈当"°"，
    # 放在格子的**右上半**，这样跟后面的 C 挨得近、看起来就是"26.4℃"。
    '°': ["00110", "01001", "01001", "00110", "00000", "00000", "00000"],
    ' ': ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}

# ---------------------------------------------------------------- 16x16 中文
#
# ⚠ 这一版**不再手画矩形**了。原因：手画的简单字（日/一/月/十）还行，复杂字
#   根本认不出来 —— build 28 的日历页上「初」被画成 π、「廿」成了一条横线、
#   「农/历/星/期/年」完全糊了。
#   现在这些字是拿**真黑体**（冬青黑体简体中文）在 15px 下栅格化成 16x16 点阵的，
#   结果存在 tools/cjk_ascii.py 里（ASCII 点阵，一个字符看一个字）。
#   要加字/换字体：改 tools/cjk_from_ttf.py 的 CHARS + FONT，跑一遍那个脚本，
#   再把下面的 CJK_ORDER 对齐（顺序 = C 里的索引）。
#
# （老的 CJK_RECTS 手画表删掉了；要找回来看 git 历史。）


# 生成时的顺序 = C 里的索引顺序（zkgui.c 里有对应的 CJK_xxx 常量）。
# 顺序的唯一真相在 tools/cjk_from_ttf.py 的 CHARS（cjk_ascii.py 按同样的顺序存），
# 这里直接用它，免得两处顺序对不上。
# ---- build 61：表头要按"干支"报年份（用户：把"农历"两个字换成具体的天干地支）----
#   顺序 = 甲0..癸9 / 子0..亥11；天干 = (year-4)%10、地支 = (year-4)%12
#   （2026 → (2026-4)=2022 %10=2 → 丙；%12=6 → 午 ⇒ 丙午，正好是马年 ✓）
GANZHI_CHARS = ['甲', '乙', '丙', '丁', '戊', '己', '庚', '辛', '壬', '癸',
                '子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥']

# ---- build 61：温度后面跟"经纬度所在地的城市名"（用户提的）----
#   ⚠ MCU 上没法现栅格化汉字，所以城市名只能用**这张表里有字形的字**。
#   加城市就往这里加字（每个字 32 字节），再跑 gen_wqy_bitmap.py + bash build.sh。
CITY_CHARS = list('深圳广州东莞佛山珠海惠州中山香港澳门北京上海天津重庆杭州'
                  '南京苏州成都武汉西安长沙厦门青岛大连沈阳郑州济南合肥福州'
                  '昆明贵阳南宁南昌太原石家庄海口三亚宁波无锡常州温州绍兴')

# 页面用到的全部汉字，顺序 = C 里的索引顺序（zkgui.c 的 CJK_xxx 常量依赖它，
# **改顺序必须同步改 zkgui.c**，新增的字只能往后加）。前 35 个是原来那套
# （月历/星期/农历/生肖），接着 24 个节气用字，build 61 又加了干支 + 城市名用字。
CJK_ORDER = [
    '日', '一', '二', '三', '四', '五', '六', '月',
    '七', '八', '九', '十', '冬', '腊', '正', '初', '廿', '闰',
    '年', '星', '期', '农', '历',
    '鼠', '牛', '虎', '兔', '龙', '蛇', '马', '羊', '猴', '鸡', '狗', '猪',
    '冬',
    '分',
    '处',
    '夏',
    '大',
    '寒',
    '小',
    '惊',
    '春',
    '暑',
    '水',
    '清',
    '满',
    '白',
    '秋',
    '种',
    '立',
    '至',
    '芒',
    '蛰',
    '谷',
    '降',
    '雨',
    '雪',
    '霜',
    '露',
] + GANZHI_CHARS + CITY_CHARS
LUNAR_ORDER = list(WQY_LUNAR16)      # 农历日名的细字（16x16，1px）
NUM_ORDER = '0123456789:'            # 原厂 helvB14 那套（9x13）

# 用"细笔画"（文泉驿点阵宋体 12pt、1px）的那批字 —— 表头（年月/干支/农历月/生肖）+
# 城市名。2026-10-01 用户对比后定的：表头那几个字要跟旁边的天气文字（同一套 1px）
# 一致，不要原厂那套 2px 的。
#   · 前 35 个 = 日月星期 + 农历月份用字 + 生肖 → 细
#   · 后面 24 个节气 → **不在这里**，继续用原厂 2px（样板里节气本来就粗）
#   · 干支 + 城市名用字 → 细（跟表头同一行）
# 有顺序的版本给生成器用（顺序固定 = 生成结果可复现；set 的迭代顺序跟哈希种子有关）
THIN_LIST = CJK_ORDER[:35] + GANZHI_CHARS + CITY_CHARS
THIN_SET = set(THIN_LIST)

# 星期条那 7 个字**单独一张表**（用原厂 2px 字形）：用户 2026-10-01 反馈
# "一二三四五六日 太细了" —— 但同一个"五"在表头的农历月名（农历五月）里要跟着细，
# 所以不能简单地把这几个字从 THIN_SET 里拿掉，得按**用途**分开两张表。
WEEK_ORDER = ['一', '二', '三', '四', '五', '六', '日']


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


def pad16(g):
    """把任意尺寸的位图居中补成 16x16（原厂字形里有 15x16 / 16x15 这种）"""
    h = len(g)
    w = max(len(r) for r in g)
    oy = (16 - h) // 2
    ox = (16 - w) // 2
    out = []
    for y in range(16):
        if y < oy or y >= oy + h:
            out.append('.' * 16)
        else:
            row = g[y - oy]
            out.append('.' * ox + row + '.' * (16 - ox - len(row)))
    return out


def cjk_bitmap(ch):
    """16x16 点阵，优先级：
         1. **表头/星期条**用的那批（THIN_SET）：文泉驿点阵宋体 12pt、1px 细笔画
        3. 我们栅格化的那份（兜底，现在应该用不到了）

    2026-10-01（用户提的）：表头的「2026年10月 农历八月」原来用原厂 u8g2 那套
    （wqy12，2px 笔画），跟紧挨着的天气文字（文泉驿 12pt，1px）不是一个字重，
    一行里两种粗细。现在表头/星期条这批统一走 1px；**节气那 24 个字仍用原厂
    2px**（样板里节气本就比农历日名粗，是刻意留的层次）。
    """
    if ch in THIN_SET and ch in WQY_CJK16:
        return pad16(WQY_CJK16[ch])
    if ch in V_CJK:
        return pad16(V_CJK[ch])
    if ch in WQY_CJK16:
        return pad16(WQY_CJK16[ch])
    if ch in CJK_ASCII:
        g = CJK_ASCII[ch]
        assert len(g) == 16 and all(len(r) == 16 for r in g), '字形 %s 不是 16x16' % ch
        return g
    raise KeyError('没有 %s 的字形' % ch)


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
    # build 31：表头右上角要写「3.90V」「26C」，补了点号/V/C
    a('#define ZK_F5_DOT      13')
    a('#define ZK_F5_V        14')
    a('#define ZK_F5_C        15')
    # build 61：温度的度数符号（"26.4℃"）
    a('#define ZK_F5_DEG      16')
    a('#define ZK_F5_NUM      17')
    a('')
    a('static const uint8_t zk_font5x7[ZK_F5_NUM][ZK_FONT5_H] = {')
    order = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '-', ':', ' ',
             '.', 'V', 'C', '°']
    for ch in order:
        a('    { %s },   /* %s */'
          % (', '.join('0x%02X' % v for v in pack_rows(ASCII5x7[ch], 5)), ch))
    a('};')
    a('')
    a('/* 中文单字；索引顺序 = tools/gen_font.py 里的 CJK_ORDER */')
    a('#define ZK_CJK_NUM    %d' % len(CJK_ORDER))
    # 字形索引 <-> 字符码点 的对照表：画中文串时按码点查字形（顺序同 CJK_ORDER）
    a('static const uint32_t zk_cjk_cp[ZK_CJK_NUM] = {')
    a('    ' + ', '.join('0x%04X' % ord(c) for c in CJK_ORDER) + ',')
    a('};')
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
    # ---- 星期条那 7 个字（**原厂 2px 字形**，单独一张表）----
    a('/* 星期条的「一二三四五六日」：**原厂 u8g2 wqy12（2px 笔画）**，单独一张表。')
    a('   为什么单独放：用户 2026-10-01 说这几个字"太细了"要改回粗的，但同一个"五"')
    a('   在表头的农历月名（农历五月）里要跟着表头走细字 —— 按用途分表，互不影响。 */')
    a('#define ZK_WEEK_NUM    7')
    a('static const uint8_t zk_font_week[ZK_WEEK_NUM][ZK_FONT_CJK_H * 2] = {')
    for ch in WEEK_ORDER:
        rows = pack_rows(pad16(V_CJK[ch]), 16)
        assert len(rows) == 32, len(rows)
        a('    { %s,' % ', '.join('0x%02X' % v for v in rows[:8]))
        a('      %s,' % ', '.join('0x%02X' % v for v in rows[8:16]))
        a('      %s,' % ', '.join('0x%02X' % v for v in rows[16:24]))
        a('      %s },   /* %s */'
          % (', '.join('0x%02X' % v for v in rows[24:32]), ch))
    a('};')
    a('')
    # ---- 数字（10x13）：日号 / 时间 ----
    a('/* 日号和时间用的数字：**原厂固件里的 u8g2_font_helvB14_tn**（Helvetica Bold，')
    a('   数字 9x13 —— 正好是样板量到的 13.6px 高）。见 tools/gen_vendor_font.py。 */')
    a('#define ZK_FONT_NUM_W   10')
    a('#define ZK_FONT_NUM_H   13')
    a('#define ZK_NUM_LEN      %d' % len(NUM_ORDER))
    a('static const uint8_t zk_font_num[ZK_NUM_LEN][ZK_FONT_NUM_H * 2] = {')
    for ch in NUM_ORDER:
        g = V_NUM[ch]
        w = max(len(r) for r in g)
        ox = (10 - w) // 2
        rows = ['.' * ox + r + '.' * (10 - ox - len(r)) for r in g]
        rows += ['.' * 10] * (13 - len(rows))
        packed = pack_rows(rows, 10)
        assert len(packed) == 26, len(packed)      # 13 行 x 2 字节
        a('    { %s,' % ', '.join('0x%02X' % v for v in packed[:13]))
        a('      %s },   /* %s */'
          % (', '.join('0x%02X' % v for v in packed[13:26]), ch))
    a('};')
    a('')
    # ---- 农历日名的"细字"（16x16、笔画 1px）----
    a('/* 日历格子里农历日名的**细字**：文泉驿点阵宋体 12pt（16x16，笔画 1px）——')
    a('   跟节气（原厂 wqy12，2px 笔画）同为 16px、只差笔画粗细，正好是样板那层次。')
    a('   数据见 tools/wqy_font.py 的 LUNAR16（生成器 tools/gen_wqy_bitmap.py）。 */')
    a('#define ZK_LUNAR_NUM   %d' % len(LUNAR_ORDER))
    a('static const uint32_t zk_lunar_cp[ZK_LUNAR_NUM] = {')
    a('    ' + ', '.join('0x%04X' % ord(c) for c in LUNAR_ORDER) + ',')
    a('};')
    a('static const uint8_t zk_font_lunar[ZK_LUNAR_NUM][ZK_FONT_CJK_H * 2] = {')
    for ch in LUNAR_ORDER:
        rows = pack_rows(WQY_LUNAR16[ch], 16)
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
