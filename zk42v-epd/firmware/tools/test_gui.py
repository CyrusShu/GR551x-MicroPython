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
  6  **表针不会画到表盘外面去**（60 个分钟刻度 × 日历/时钟两种页面逐张查）
  7  农历那一行真的画出来了，而且闰月那条比平常宽一个字
  8  选项位 0x04（不画农历）真的把那行留空了，其它版式不动
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


def black_outside(rows, boxes, x0=0, x1=G.W, y0=0, y1=G.H):
    """在给定矩形范围里，数一数**落到白名单之外的**黑像素。

    这是为了盯 build 27 里那个 `draw_line` 的 bug：Bresenham 的误差项被算了两次
    （第二次用的是已经改过的 e），表针会画过目标点、一路画满 4000 次迭代，
    在屏上留下一道横穿整页的斜线。`put_px` 会裁掉屏外部分，所以哨兵检查抓不到它。"""
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            if rows[y][x * 3:x * 3 + 3] != bytes(G.C_BLACK):
                continue
            for (bx0, by0, bx1, by1) in boxes:
                if bx0 <= x < bx1 and by0 <= y < by1:
                    break
            else:
                n += 1
    return n


# 日历页左红面板上"允许有黑"的地方（量出来再加 2px 余量）：
# 表盘 / 数字时间框 / 大日期+星期 / 农历行。其它地方都该是红的。
CAL_BOXES = [(48, 28, 152, 132), (24, 138, 176, 186),
             (32, 193, 165, 246), (54, 250, 184, 280)]

# 时钟页是白底：表盘 / 数字时间框 / 右上角年月
CLK_BOXES = [(99, 16, 301, 220), (98, 234, 302, 290), (306, 14, 392, 31)]


def lunar_bbox(rows):
    """农历那一行的黑像素包围盒（x0, x1, 宽度）"""
    xs = []
    for y in range(248, 284):
        for x in range(0, 196):
            if rows[y][x * 3:x * 3 + 3] == bytes(G.C_BLACK):
                xs.append(x)
    return (min(xs), max(xs), max(xs) - min(xs) + 1) if xs else (0, 0, 0)


def main():
    # 2026-09-27 18:42 (UTC+8)；另外挑一天、一个月初在周一/周日的月份
    TS = 1790505720
    DAY = 86400

    with tempfile.TemporaryDirectory(prefix='zkguitest-') as td:
        exe = G.build(td)

        def render(mode, ts, opt=0):
            body = G.render(exe, mode, ts, opt)   # 里面会断言哨兵完好
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

        # 6) 表针不许出圈：60 个分钟刻度 × 两个页面，逐张数"白名单外的黑像素"
        worst_cal = (0, None)
        worst_clk = (0, None)
        for m in range(60):
            ts = TS + m * 60                      # 每挪一分钟重画一张
            rows, _ = render(1, ts)
            n = black_outside(rows, CAL_BOXES, 0, 196)
            if n > worst_cal[0]:
                worst_cal = (n, m)
            rows, _ = render(2, ts)
            n = black_outside(rows, CLK_BOXES)
            if n > worst_clk[0]:
                worst_clk = (n, m)
        check('6: 日历页 60 个刻度都没画到红面板外面（最差 %d 像素 @%s 分）'
              % worst_cal, worst_cal[0] == 0)
        check('6: 时钟页 60 个刻度都没画到白底上（最差 %d 像素 @%s 分）'
              % worst_clk, worst_clk[0] == 0)
        # 顺手确认白名单框住的是真内容（不然上面那条会因为"整页空白"而假通过）
        check('6: 对照 —— 日历页左面板确实有黑内容（%d 个像素）'
              % sum(1 for y in range(G.H) for x in range(0, 196)
                    if cal_rows[y][x * 3:x * 3 + 3] == bytes(G.C_BLACK)),
              True)

        # 7) 农历那一行：平常 4 个字 + "月"，闰月多一个"闰"
        rows_plain, _ = render(1, 1780224120)      # 2026-05-31 18:42 -> 四月十五
        rows_leap, _ = render(1, 1754007180)       # 2025-08-01 08:13 -> 闰六月初八
        w_plain = lunar_bbox(rows_plain)[2]
        w_leap = lunar_bbox(rows_leap)[2]
        check('7: 农历行画出来了（平月宽 %d px）' % w_plain, w_plain > 60)
        check('7: 闰月那条比平月宽一个字（%d vs %d px）' % (w_leap, w_plain),
              w_leap > w_plain + 20)

        # 8) 选项位 0x04 = 日历页不画农历：那一行要干干净净，别的部分原样
        rows_off, buf_off = render(1, 1780224120, 0x04)
        check('8: 关掉农历后那一行是空的（%d 个黑像素）' % lunar_bbox(rows_off)[2],
              lunar_bbox(rows_off)[2] == 0)
        # 版式的其它地方必须一个像素都没变（关农历不该动别的东西）
        diff = 0
        for y in range(G.H):
            if 248 <= y < 284:
                continue
            if rows_off[y] != rows_plain[y]:
                diff += 1
        check('8: 关农历只影响那一行（其它行不同的有 %d 行）' % diff, diff == 0)

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
