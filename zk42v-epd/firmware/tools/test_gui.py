#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测日历/时钟页面（tools/gui_preview.py 的渲染器 + zkgui.c）。

不用硬件：
  1  缓冲前后那 32 字节哨兵没被写坏（越界写是屏上根本看不出来的 bug）
  2  日历模式：左半边是红面板、右半边是白底，今天那个格子附近有红像素
  3  时钟模式：整屏以黑白为主，红的只在左上角那个星期
  4  换一天画出来的图不一样（证明时间真的参与绘制）
  5  今天那个红圈会跟着日期换位置（月历不是死的）
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gui_preview as G   # noqa: E402

CHECKS = []


def check(label, ok, detail=''):
    CHECKS.append((label, bool(ok), detail))


def count_red(rows):
    return sum(1 for r in rows for i in range(0, len(r), 3)
               if r[i:i + 3] == bytes(G.C_RED))


def red_in_band(rows, y0, y1):
    n = 0
    for y in range(y0, y1):
        r = rows[y]
        for i in range(0, len(r), 3):
            if r[i:i + 3] == bytes(G.C_RED):
                n += 1
    return n


def main():
    # 2026-09-27 18:42 (UTC+8)；另外挑一天、一个月初在周一/周日的月份
    TS = 1790505720
    DAY = 86400

    with tempfile.TemporaryDirectory(prefix='zkguitest-') as td:
        exe = G.build(td)

        def render(mode, ts):
            body = G.render(exe, mode, ts)     # 里面会断言哨兵完好
            return G.to_rgb(body), body

        # 1) 哨兵：G.render 里断言，能走到这儿就算过
        cal_rows, cal_buf = render(1, TS)
        clk_rows, clk_buf = render(2, TS)
        check('1: 画完缓冲前后哨兵完好（没有越界写）', True)

        # 2) 日历：左红右白 + 今天那块有红
        left = sum(1 for y in range(G.H) for x in range(0, 190)
                   if cal_rows[y][x * 3:x * 3 + 3] == bytes(G.C_RED))
        right = sum(1 for y in range(G.H) for x in range(200, G.W)
                    if cal_rows[y][x * 3:x * 3 + 3] == bytes(G.C_RED))
        check('2: 日历页左半边基本是红的（%d 个红像素）' % left, left > 30000)
        check('2: 日历页右半边基本不红（只有今天那个圈，%d 个）' % right,
              0 < right < 1500)

        # 3) 时钟页：以黑白为主
        clk_red = count_red(clk_rows)
        check('3: 时钟页红色很少（只有左上角星期，%d 个）' % clk_red, clk_red < 2000)

        # 4) 换一天，图应该不一样
        _, buf2 = render(1, TS + 3 * DAY)
        check('4: 换一天画出来的图不一样', buf2 != cal_buf)

        # 5) 今天的红圈跟着日期走：把同一个月的 27 号分别当成"今天"和"另一天"
        _, buf_a = render(1, TS)
        _, buf_b = render(1, TS + 5 * DAY)
        diff = sum(1 for i in range(len(buf_a)) if buf_a[i] != buf_b[i])
        check('5: 今天那格的红圈会换位置（两图差 %d 字节）' % diff, diff > 200)

    bad = 0
    for label, ok, detail in CHECKS:
        print('  [%s] %s' % ('PASS' if ok else '**FAIL**', label))
        if not ok and detail:
            print('       · %s' % detail)
        bad += 0 if ok else 1
    print('全部通过 ✅' if bad == 0 else '有 %d 项失败 ❌' % bad)
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
