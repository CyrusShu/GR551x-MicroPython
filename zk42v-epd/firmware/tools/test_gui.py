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

# 版式常量（跟 zkgui.c 里那组一致；数值是照样板量出来的，见 build 32 的说明）
CAL_HDR_H = 26
CAL_WD_Y = 26
CAL_WD_H = 22
CAL_GRID_Y = 48
CAL_GRID_PAD = 6
CAL_BOTTOM_PAD = 8
CAL_COL_W = G.W // 7
CAL_ROW_H_MIN = 38
CAL_ROW_H_MAX = 56
CAL_CONTENT_H = (19 + 14) - 3        # 农历底 - 日号顶 = 30（跟 zkgui.c 一致）


def row_h(rows_used):
    """行距是按当月几行摊开的（跟 zkgui.c 的 cal_row_h 同一套算法）"""
    top = CAL_GRID_Y + CAL_GRID_PAD
    avail = G.H - CAL_BOTTOM_PAD - CAL_CONTENT_H - top
    h = (avail // (rows_used - 1)) if rows_used > 1 else avail
    return max(CAL_ROW_H_MIN, min(CAL_ROW_H_MAX, h))


def cell_xy(col, row, pitch):
    """格子的左上角（含格子区上边距）"""
    return col * CAL_COL_W, CAL_GRID_Y + CAL_GRID_PAD + row * pitch

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


def cell_ink(rows, col, row, pitch):
    """某个格子里有多少"有内容"的像素（黑或红都算）"""
    x0, y0 = cell_xy(col, row, pitch)
    return sum(1 for y in range(y0, min(y0 + pitch, G.H))
               for x in range(x0, min(x0 + CAL_COL_W, G.W))
               if at(rows, x, y) in (BLACK, RED))


def lunar_ink(rows, col, row, pitch, is_today=False):
    """格子里那行农历小字的墨量（日号下面那一条）。

    颜色要按格子背景来数：普通格/周末格都是白底（字是黑或红），
    今天那格是红底白字（只在红块里面数，块外那圈白边不算）。"""
    x0 = col * CAL_COL_W
    w = CAL_COL_W
    y0 = CAL_GRID_Y + CAL_GRID_PAD + row * pitch + 19
    if is_today:
        x0 += 4
        w -= 8
        want = WHITE
    else:
        want = RED if col >= 5 else BLACK
    return sum(1 for y in range(y0, min(y0 + 16, G.H))
               for x in range(x0, min(x0 + w, G.W))
               if at(rows, x, y) == want)


def today_cell(rows, pitch):
    """今天那格（日号外面套着红圆）在第几列第几行；找不到返回 None。

    判据：日号那一小块里红像素特别多，别的格子最多只有红色的数字笔画。"""
    for row in range(6):
        for col in range(7):
            x0, y0 = cell_xy(col, row, pitch)
            if count(rows, RED, x0 + 6, y0 + 1, x0 + CAL_COL_W - 6, y0 + 22) > 300:
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

        # 2) 日历页版式（build 31 起照社区第 14 号那版：白底表头 + 黑星期条）
        hdr_red = count(cal_rows, RED, 0, 0, G.W, CAL_HDR_H)
        hdr_blk = count(cal_rows, BLACK, 0, 0, G.W, CAL_HDR_H)
        check('2: 表头是白底 + 红色年月/生肖（红 %d，黑 %d）'
              % (hdr_red, hdr_blk), hdr_red > 400 and hdr_blk < 3000)

        wd_blk = count(cal_rows, BLACK, 0, CAL_WD_Y, G.W, CAL_WD_Y + CAL_WD_H)
        wd_red = count(cal_rows, RED, 5 * CAL_COL_W, CAL_WD_Y,
                       7 * CAL_COL_W, CAL_WD_Y + CAL_WD_H)
        check('2: 星期条是黑底（黑 %d / %d）' % (wd_blk, G.W * CAL_WD_H),
              wd_blk > G.W * CAL_WD_H * 0.5)
        check('2: 星期条上 六/日 那两列是红底（%d 个红像素）' % wd_red, wd_red > 800)

        want = month_cells(2026, 9)
        pitch = row_h(max(r for (_c, r) in want.values()) + 1)
        empty, wrong = [], []
        for d, (col, row) in want.items():
            if cell_ink(cal_rows, col, row, pitch) < 40:
                empty.append(d)
        # 反向：不该有内容的格子（这个月只有 30 天、前面空 1 格）
        for row in range(6):
            for col in range(7):
                if (col, row) not in want.values():
                    if cell_ink(cal_rows, col, row, pitch) > 0:
                        wrong.append((col, row))
        check('2: 30 天全都画在正确的格子里（空格 %s）' % empty, not empty)
        check('2: 没排到日子的格子是空的（多画的 %s）' % wrong, not wrong)

        # 3) 今天那格：红圆 + 白字
        cell = today_cell(cal_rows, pitch)
        check('3: 今天（27 号）那格套着红圆 —— 在第 %s 格' % (cell,), cell == want[27])
        # 红圆里必须是白字：白像素太少说明日号没画上去（或被圆吃掉）
        x0, y0 = cell_xy(6, 3, pitch)
        check('3: 红圆里有白色日号（%d 个白像素）'
              % count(cal_rows, WHITE, x0 + 6, y0 + 1, x0 + CAL_COL_W - 6, y0 + 22),
              count(cal_rows, WHITE, x0 + 6, y0 + 1, x0 + CAL_COL_W - 6, y0 + 22) > 60)
        _, cal2 = render(1, TS + 5 * DAY)          # 挪 5 天 -> 1 号
        pitch2 = row_h(5)
        check('3: 今天那格会跟着日期换位置（10-02 那次在第 %s 格）'
              % (today_cell(G.to_rgb(cal2), pitch2),),
              today_cell(G.to_rgb(cal2), pitch2) != cell)

        # 4) 表针不许出圈（时钟页；build 27 那个 Bresenham 跑飞的 bug 就靠它盯）
        worst = (0, None)
        for m in range(60):
            rows, _ = render(2, TS + m * 60)
            # 时钟页表盘：圆心 (200,140) 半径 80，粗细 4 —— 外面多给 6px 余量
            bad = 0
            for y in range(G.H):
                for x in range(G.W):
                    if 40 <= y < 230 and at(rows, x, y) == BLACK:
                        dx, dy = x - 200, y - 140
                        if dx * dx + dy * dy > 86 * 86:
                            bad += 1
            if bad > worst[0]:
                worst = (bad, m)
        check('4: 时钟页 60 个刻度表针都没画到表盘外（最差 %d 像素 @%s 分）'
              % worst, worst[0] == 0)

        # 5) 农历开关：**只**影响每格那行小字（日号一点都不能动）
        rows_off, _ = render(1, TS, 0x04)
        diff = 0
        outside = []
        for y in range(G.H):
            for x in range(G.W):
                if at(cal_rows, x, y) == at(rows_off, x, y):
                    continue
                diff += 1
                # 允许两处：表头（"农历八月"那条也会跟着消失）
                #           和每格那行小字（y0+20 往下）
                if y < CAL_HDR_H:
                    continue
                if y < CAL_GRID_Y:
                    outside.append((x, y))
                    continue
                row = (y - CAL_GRID_Y - CAL_GRID_PAD) // pitch
                if not (CAL_GRID_Y + CAL_GRID_PAD + row * pitch + 18 <= y):
                    outside.append((x, y))
        check('5: 关农历只改了每格那行小字（改了 %d 个像素）' % diff, diff > 1200)
        check('5: 改动没跑到日号/表头上去（越界 %d 个）' % len(outside),
              not outside, str(outside[:4]))
        # 表头里的红色部分（年月、生肖、电池）一个像素都不该动
        check('5: 表头的红字（年月/生肖/电池）没受影响',
              count(cal_rows, RED, 0, 0, G.W, CAL_HDR_H)
              == count(rows_off, RED, 0, 0, G.W, CAL_HDR_H))

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
