#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测日历/时钟页面（tools/gui_preview.py 的渲染器 + zkgui.c）。

不用硬件。版式在 build 29 换成了「整页农历月历」，判据也跟着换了：

  1  缓冲前后那 32 字节哨兵没被写坏（越界写是屏上根本看不出来的 bug）
  2  日历页：顶部黑条 + 星期条（六/日红底）+ **每天一格都画出来了**，
     而且格子位置对不对（用 Python 自己算一遍月历，逐格对）
  3  今天那格是整块红底白字，而且会跟着日期换格子
  4  **表针不会画到表盘外面去**（时钟页 60 个分钟刻度逐张查）
  5  农历开关（0x04）真的只影响每格的农历小字，日号不动
  6  换一天画出来的图不一样（证明时间真的参与绘制）
  7  时钟页：一个大表盘 + 底部时间框
"""

import datetime
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gui_preview as G   # noqa: E402

# 版式常量（跟 zkgui.c 里那组一致）
CAL_HDR_H = 40
CAL_WD_Y = 41
CAL_WD_H = 21
CAL_GRID_Y = 64
CAL_COL_W = G.W // 7                      # 57
CAL_ROW_H = (G.H - CAL_GRID_Y) // 6       # 39

BLACK = bytes(G.C_BLACK)
WHITE = bytes(G.C_WHITE)
RED = bytes(G.C_RED)

CHECKS = []


def check(label, ok, detail=''):
    CHECKS.append((label, bool(ok), detail))


def at(rows, x, y):
    return rows[y][x * 3:x * 3 + 3]


def count(rows, color, x0=0, y0=0, x1=G.W, y1=G.H):
    return sum(1 for y in range(y0, y1) for x in range(x0, x1)
               if at(rows, x, y) == color)


def cell_ink(rows, col, row):
    """某个格子里有多少"有内容"的像素（黑或红都算）"""
    x0 = col * CAL_COL_W
    y0 = CAL_GRID_Y + row * CAL_ROW_H
    return sum(1 for y in range(y0, min(y0 + CAL_ROW_H, G.H))
               for x in range(x0, min(x0 + CAL_COL_W, G.W))
               if at(rows, x, y) in (BLACK, RED))


def lunar_ink(rows, col, row, is_today=False):
    """格子里那行农历小字的墨量（日号下面那一条）。

    颜色要按格子背景来数：普通格/周末格都是白底（字是黑或红），
    今天那格是红底白字（只在红块里面数，块外那圈白边不算）。"""
    x0 = col * CAL_COL_W
    w = CAL_COL_W
    y0 = CAL_GRID_Y + row * CAL_ROW_H + 22
    if is_today:
        x0 += 4
        w -= 8
        want = WHITE
    else:
        want = RED if col >= 5 else BLACK
    return sum(1 for y in range(y0, min(y0 + 16, G.H))
               for x in range(x0, min(x0 + w, G.W))
               if at(rows, x, y) == want)


def today_cell(rows):
    """今天那格（整块红底）在第几列第几行；找不到返回 None"""
    for row in range(6):
        for col in range(7):
            x0 = col * CAL_COL_W + 4
            y0 = CAL_GRID_Y + row * CAL_ROW_H + 1
            if at(rows, x0 + 2, y0 + 2) == RED:
                return (col, row)
    return None


def month_cells(year, mon):
    """用 Python 自己排一遍月历 -> {日: (列, 行)}，用来核对固件排得对不对
    （周一开头，跟固件里 s_head_cjk 的顺序一致）"""
    first = datetime.date(year, mon, 1)
    col = first.weekday()          # 0 = 周一
    out = {}
    for d in range(1, 32):
        try:
            datetime.date(year, mon, d)
        except ValueError:
            break
        out[d] = (col % 7, col // 7)
        col += 1
    return out


def main():
    # 2026-09-27 18:42（UTC+8）；预览程序按 UTC 秒算，所以这里直接给 UTC 值
    TS = 1790505720
    DAY = 86400

    with tempfile.TemporaryDirectory(prefix='zkguitest-') as td:
        exe = G.build(td)

        def render(mode, ts, opt=0):
            body = G.render(exe, mode, ts, opt)     # 里面会断言哨兵完好
            return G.to_rgb(body), body

        # 1) 哨兵：G.render 里断言，能走到这儿就算过
        cal_rows, cal_buf = render(1, TS)
        check('1: 画完缓冲前后哨兵完好（没有越界写）', True)

        # 2) 日历页版式
        hdr_black = count(cal_rows, BLACK, 0, 0, G.W, CAL_HDR_H)
        check('2: 顶部是黑条（黑 %d / %d 像素）'
              % (hdr_black, G.W * CAL_HDR_H), hdr_black > G.W * CAL_HDR_H * 0.6)

        wd_red = count(cal_rows, RED, 5 * CAL_COL_W, CAL_WD_Y,
                       7 * CAL_COL_W, CAL_WD_Y + CAL_WD_H)
        check('2: 星期条上 六/日 那两列是红底（%d 个红像素）' % wd_red, wd_red > 800)

        want = month_cells(2026, 9)
        empty, wrong = [], []
        for d, (col, row) in want.items():
            if cell_ink(cal_rows, col, row) < 40:
                empty.append(d)
        # 反向：不该有内容的格子（这个月只有 30 天、前面空 1 格）
        for row in range(6):
            for col in range(7):
                if (col, row) not in want.values():
                    if cell_ink(cal_rows, col, row) > 0:
                        wrong.append((col, row))
        check('2: 30 天全都画在正确的格子里（空格 %s）' % empty, not empty)
        check('2: 没排到日子的格子是空的（多画的 %s）' % wrong, not wrong)

        # 3) 今天那格
        cell = today_cell(cal_rows)
        check('3: 今天（27 号）那格是整块红底 —— 在第 %s 格'
              % (cell,), cell == want[27])
        # 红块里必须是白字（白 = 已经是"红底"了；纯红块说明日号没画上去）
        x0 = 6 * CAL_COL_W
        y0 = CAL_GRID_Y + 4 * CAL_ROW_H
        check('3: 今天那格的红底里有白字（%d 个白像素）'
              % count(cal_rows, WHITE, x0 + 8, y0 + 6, x0 + CAL_COL_W - 4,
                      y0 + CAL_ROW_H - 4),
              count(cal_rows, WHITE, x0 + 8, y0 + 6, x0 + CAL_COL_W - 4,
                    y0 + CAL_ROW_H - 4) > 60)
        _, cal2 = render(1, TS + 5 * DAY)          # 挪 5 天 -> 1 号
        check('3: 今天那格会跟着日期换位置（10-02 那次在第 %s 格）'
              % (today_cell(G.to_rgb(cal2)),), today_cell(G.to_rgb(cal2)) != cell)

        # 4) 表针不许出圈（时钟页；build 27 那个 Bresenham 跑飞的 bug 就靠它盯）
        worst = (0, None)
        for m in range(60):
            rows, _ = render(2, TS + m * 60)
            # 时钟页表盘：圆心 (200,148) 半径 76，粗细 4 —— 外面多给 6px 余量
            bad = 0
            for y in range(G.H):
                for x in range(G.W):
                    if 40 <= y < 230 and at(rows, x, y) == BLACK:
                        dx, dy = x - 200, y - 148
                        if dx * dx + dy * dy > 84 * 84:
                            bad += 1
            if bad > worst[0]:
                worst = (bad, m)
        check('4: 时钟页 60 个刻度表针都没画到表盘外（最差 %d 像素 @%s 分）'
              % worst, worst[0] == 0)

        # 5) 农历开关：只影响每格那行小字
        rows_off, _ = render(1, TS, 0x04)
        lun_on = sum(lunar_ink(cal_rows, c, r, d == 27) for d, (c, r) in want.items())
        lun_off = sum(lunar_ink(rows_off, c, r, d == 27) for d, (c, r) in want.items())
        check('5: 关掉农历后小字全没了（%d -> %d）' % (lun_on, lun_off),
              lun_on > 1200 and lun_off == 0)
        day_on = sum(1 for c, r in want.values() if cell_ink(cal_rows, c, r) > 40)
        day_off = sum(1 for c, r in want.values() if cell_ink(rows_off, c, r) > 20)
        check('5: 关农历不影响日号（%d/%d 格还有内容）' % (day_off, day_on),
              day_off == day_on)

        # 6) 换一天，图不一样
        check('6: 换一天画出来的图不一样',
              G.render(exe, 1, TS + 3 * DAY) != cal_buf)

        # 7) 时钟页：表盘 + 底部时间框
        clk_rows, _ = render(2, TS)
        check('7: 时钟页有表盘（圆心那圈有黑像素）',
              count(clk_rows, BLACK, 108, 60, 292, 236) > 1500)
        check('7: 时钟页底部是黑底白字的时间框（%d 个白像素）'
              % count(clk_rows, WHITE, 100, 236, 300, 288),
              count(clk_rows, WHITE, 100, 236, 300, 288) > 500)

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
