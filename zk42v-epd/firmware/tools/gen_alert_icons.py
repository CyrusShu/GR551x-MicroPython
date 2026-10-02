#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成**天气预警图标**表（Src/img/alert_icons.c / .h）—— 6 张 20×20 的 1 位点阵。

    /tmp/zkvenv/bin/python3 tools/gen_alert_icons.py

为什么要这几张（2026-10-02 用户："预警上图标"）：
  表头那格现在只写预警名（"暴雨"），加一张图标更醒目 —— 而三色屏只有黑/白/红，
  所以图标必须是**实心、形状大**的 1 位点阵，20×20。

数据来源（都跟 weather.c 同一条渲染路径：8 倍超采样 + 阈值 128 + 去孤立点）：
  · 和风官方图标库（**MIT**，github.com/qwd/Icons，编号 = API 返回的 icon 字段）
    —— 只挑了在 20×20 下**一眼能认**的 5 个：1003 暴雨 / 1014 雷电 / 1001 台风 /
       1015 冰雹 / 1009 高温。
  · 其余类型（1006 大风、1005 寒潮、1017 大雾、1007 沙尘、1021 道路结冰…）在 20×20
    下糊成一块（见 docs/preview/preview-wx-alert-icons.png），统一用 **Material
    Symbols 的 warning**（实心三角 + 感叹号，Apache-2.0）当"通用预警"兜底。

⚠ 固件里只认这张表里的编号；认不出的编号走"通用预警"那张，不会画成空白。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gen_weather as G          # noqa: E402   复用它的 render_icon / ICON_DIR

OUT_C = os.path.join(G.FW, 'zk42v-epd-app', 'Src', 'img', 'alert_icons.c')
OUT_H = os.path.join(G.FW, 'zk42v-epd-app', 'Src', 'img', 'alert_icons.h')

# 顺序 = 固件里的下标（0 固定是"通用预警"兜底）
#   (和风编号, 名字, 用哪套字体, 码点)
ITEMS = [
    (0,    '通用预警', 'ms', 0xE002),      # Material Symbols: warning（实心三角+感叹号）
    (1003, '暴雨',     'qw', 0xF10D),      # 1003 rainstorm
    (1014, '雷电',     'qw', 0xF118),      # 1014 lightning
    (1001, '台风',     'qw', 0xF105),      # 1001 typhoon
    (1015, '冰雹',     'qw', 0xF119),      # 1015 hail
    (1009, '高温',     'qw', 0xF10B),      # 1009 high-temperature
]


def main():
    import json

    cp_table = json.load(open(os.path.join(G.ICON_DIR,
                                           'qweather-icons.codepoints.json'),
                              encoding='utf-8'))
    # 和风那边的码点以**官方码点表**为准（上面 ITEMS 里写的仅供参考）
    rows = []
    for code, name, setname, cp in ITEMS:
        if setname == 'qw':
            cp = cp_table[str(code)]
        ttf = G.icon_ttf(setname)
        g = G.render_icon(ttf, cp, axes=G.SETS[setname].get('axes'))
        ink = ''.join(g).count('#')
        rows.append((code, name, setname, cp, g))
        print('  %-10s 编号 %-6s %s 墨=%3d' % (name, code or '-', setname, ink))

    L = []
    a = L.append
    a('/*')
    a(' * 天气预警图标 —— 见 alert_icons.h。点阵由 tools/gen_alert_icons.py 生成，别手改。')
    a(' *')
    a(' * 来源（跟 weather.c 同一条渲染路径：TTF → 8 倍超采样 → 20x20 二值化，阈值 128）：')
    for code, name, setname, cp, _ in rows:
        st = G.SETS[setname]
        a(' *   %-8s %-6s = %s U+%04X（%s）'
          % (name, code or '通用', st['name'], cp, st['license']))
    a(' */')
    a('#include "alert_icons.h"')
    a('')
    a('/* 每行 3 字节（20 px），MSB first；下标见 alert_icons.h 的 ZK_ALERT_ICON_xxx */')
    a('static const uint8_t s_alert_icon[ZK_ALERT_ICON_NUM][60] = {')
    for code, name, setname, cp, g in rows:
        a('    {   /* %s%s */' % (name, ('（和风编号 %d）' % code) if code else ''))
        body = []
        for r in g:
            v = 0
            out = []
            for i, ch in enumerate(r):
                v = (v << 1) | (1 if ch == '#' else 0)
                if i % 8 == 7:
                    out.append(v)
                    v = 0
            if len(r) % 8:
                out.append(v << (8 - len(r) % 8))
            body.append(out)
        for y in range(20):
            a('        ' + ' '.join('0x%02X,' % b for b in body[y]))
        a('    },')
    a('};')
    a('')
    a('/* 和风预警编号 -> 图标下标；认不出的返回 0（通用预警） */')
    a('static const struct { uint16_t code; uint8_t idx; } s_alert_map[] = {')
    for i, (code, name, _, _, _) in enumerate(rows):
        if code:
            a('    { %4d, %d },   /* %s */' % (code, i, name))
    a('};')
    a('')
    a('const uint8_t *zk_alert_icon(int qweather_code)')
    a('{')
    a('    unsigned i;')
    a('')
    a('    for (i = 0; i < sizeof(s_alert_map) / sizeof(s_alert_map[0]); i++)')
    a('    {')
    a('        if ((int)s_alert_map[i].code == qweather_code)')
    a('        {')
    a('            return s_alert_icon[s_alert_map[i].idx];')
    a('        }')
    a('    }')
    a('    return s_alert_icon[0];        /* 通用预警兜底 */')
    a('}')
    with open(OUT_C, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')

    h = '''/*
 * 天气预警图标（20x20 的 1 位点阵）—— 由 tools/gen_alert_icons.py 生成，别手改。
 *
 * 画在哪：表头那一格原本是"天气图标"的位置；**有预警时改画预警图标**
 * （见 Src/img/zkgui.c 的 draw_header），颜色跟预警名一致（≥黄色用红）。
 *
 * 谁挑哪一张：基站经 BLE 0x7D 把和风的预警编号送下来（1003 暴雨 / 1014 雷电…），
 * 固件用 zk_alert_icon() 查表；认不出的编号走下标 0 的"通用预警"。
 */
#ifndef __ZK_ALERT_ICONS_H__
#define __ZK_ALERT_ICONS_H__

#include <stdint.h>

#define ZK_ALERT_ICON_W     20
#define ZK_ALERT_ICON_H     20
#define ZK_ALERT_ICON_IDX_BASE 0        /* 0 = 通用预警（兜底） */
#define ZK_ALERT_ICON_IDX_RAIN 1        /* 1003 暴雨 */
#define ZK_ALERT_ICON_IDX_LIGHT 2       /* 1014 雷电 */
#define ZK_ALERT_ICON_IDX_TYPHOON 3     /* 1001 台风 */
#define ZK_ALERT_ICON_IDX_HAIL 4        /* 1015 冰雹 */
#define ZK_ALERT_ICON_IDX_HEAT 5        /* 1009 高温 */
#define ZK_ALERT_ICON_NUM   6

/* 和风预警编号 -> 20x20 点阵（每行 3 字节，MSB first）；认不出返回通用预警 */
const uint8_t *zk_alert_icon(int qweather_code);

#endif /* __ZK_ALERT_ICONS_H__ */
'''
    with open(OUT_H, 'w', encoding='utf-8') as f:
        f.write(h)
    print('写出 %s / %s（%d 张）' % (OUT_C, OUT_H, len(rows)))


if __name__ == '__main__':
    main()
