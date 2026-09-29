#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测电量曲线（Src/board/zk_bat_curve.h）—— 不用硬件。

    python3 tools/test_bat.py

为什么要这个：这块价签是 **2×CR2450**（3.0V 标称），曲线要是还照锂电写
（3.0V=0% / 4.2V=100%），**满电的电池会显示成空格** —— 屏上看着像"该换了"。
这条判据就是盯着这件事的：新电池（3.0V）必须 4 格。

顺带钉死：
  1  表里的电压从高到低、百分比从高到低（单调），不能出现"越充越少"
  2  端点：>=3.00V 算满、<2.50V 算空
  3  关键电压对应的格数（3.00V=4、2.90V=3、2.80V=2、2.70V=1、2.50V=0）
  4  中间是线性插值（拿 2.925V 对一下）
  5  固件（zk_bat.c）和主机预览（gui_preview.c）都 include 同一张表 ——
     谁要是又抄了一份，这条会红
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
IMG = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img')
BOARD = os.path.join(FW, 'zk42v-epd-app', 'Src', 'board')
CURVE = os.path.join(BOARD, 'zk_bat_curve.h')

CHECKS = []


def check(label, ok):
    CHECKS.append((label, bool(ok)))


def read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def load_curve():
    src = read(CURVE)

    def grab(name):
        m = re.search(name + r'\[[^\]]*\]\s*=\s*\{([^}]*)\}', src)
        return [int(v) for v in re.findall(r'\d+', m.group(1))] if m else []

    return grab('zk_bat_curve_mv_tab'), grab('zk_bat_curve_pct_tab')


def pct_of(mv_tab, pct_tab, mv):
    """跟 zk_bat_curve_pct() 同一套算法"""
    if mv >= mv_tab[0]:
        return 100
    for i in range(1, len(mv_tab)):
        if mv >= mv_tab[i]:
            hi_mv, lo_mv = mv_tab[i - 1], mv_tab[i]
            hi_pc, lo_pc = pct_tab[i - 1], pct_tab[i]
            return lo_pc + (mv - lo_mv) * (hi_pc - lo_pc) // (hi_mv - lo_mv)
    return 0


def bars(pct):
    """跟 zkgui.c 的 draw_battery() 同一条：0..4 格"""
    return (pct + 24) // 25


def main():
    mv_tab, pct_tab = load_curve()
    check('1: 曲线表读到了（%d 个点）' % len(mv_tab),
          len(mv_tab) >= 5 and len(mv_tab) == len(pct_tab))
    if len(mv_tab) != len(pct_tab) or len(mv_tab) < 5:
        for label, ok in CHECKS:
            print('  [%s] %s' % ('PASS' if ok else 'FAIL', label))
        return 1

    check('1: 电压从高到低（%d -> %d）' % (mv_tab[0], mv_tab[-1]),
          all(mv_tab[i] > mv_tab[i + 1] for i in range(len(mv_tab) - 1)))
    check('1: 百分比从高到低、不超过 100',
          all(pct_tab[i] >= pct_tab[i + 1] for i in range(len(pct_tab) - 1))
          and max(pct_tab) <= 100 and min(pct_tab) >= 0)

    check('2: >=3.00V 算满（3.30V -> %d%%）' % pct_of(mv_tab, pct_tab, 3300),
          pct_of(mv_tab, pct_tab, 3300) == 100)
    check('2: <2.50V 算空（2.30V -> %d%%）' % pct_of(mv_tab, pct_tab, 2300),
          pct_of(mv_tab, pct_tab, 2300) == 0)

    want = [(3000, 4), (2950, 4), (2900, 3), (2850, 3), (2800, 2),
            (2700, 1), (2500, 0)]
    for mv, n in want:
        p = pct_of(mv_tab, pct_tab, mv)
        check('3: %d.%02dV -> %d 格（pct=%d）' % (mv / 1000, mv % 1000 // 10, n, p),
              bars(p) == n)

    # 4：插值（2.925V 夹在 2900/2950 之间，应在 70~85 之间）
    mid = pct_of(mv_tab, pct_tab, 2925)
    check('4: 2.925V 线性插值 = %d%%（应在 70~85 之间）' % mid, 70 <= mid <= 85)

    # 5：固件和主机预览共用同一张表（别再各抄一份）
    check('5: zk_bat.c include 了 zk_bat_curve.h',
          'zk_bat_curve.h' in read(os.path.join(BOARD, 'zk_bat.c')))
    check('5: gui_preview.c include 了 zk_bat_curve.h',
          'zk_bat_curve.h' in read(os.path.join(HERE, 'gui_preview.c')))
    check('5: zk_bat.c 里没有另抄一份曲线表',
          'zk_bat_curve_mv_tab' not in read(os.path.join(BOARD, 'zk_bat.c')))

    bad = 0
    for label, ok in CHECKS:
        print('  [%s] %s' % ('PASS' if ok else 'FAIL', label))
        if not ok:
            bad += 1
    print('全部通过 ✅' if not bad else '%d 项失败 ❌' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
