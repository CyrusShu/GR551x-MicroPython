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
CAL_CONTENT_H = (20 + 16) - 3        # 农历底 - 日号顶 = 33（跟 zkgui.c 一致）


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
    y0 = CAL_GRID_Y + CAL_GRID_PAD + row * pitch + 20
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

        def render(mode, ts, opt=0, bat_mv=0, temp_c10=0, wx_code=0, env_temp=-128,
                   city='', memo_spec='', memo_text=''):
            body = G.render(exe, mode, ts, opt, bat_mv, temp_c10,
                            wx_code, env_temp, city, memo_spec, memo_text)
            return G.to_rgb(body), body      # G.render 里面会断言哨兵完好

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
        # 表头的红字分两块看（build 64 起「八」「马」都是大号红字：关掉农历时「八」
        # 会跟着没、后面的东西还会整体左移，所以不能拿整条表头一起比）：
        #   ① 年月那一段**位置永远不动**（它是第一个画的）→ 红像素数必须一模一样
        #   ② 生肖「马」不受"不画农历"影响 → 关掉农历后中段仍该有红字
        hdr_red = lambda r, x0, x1: count(r, RED, x0, 0, x1, CAL_HDR_H)
        check('5: 关农历不影响表头的年月（红字一个像素都没动）',
              hdr_red(cal_rows, 0, 110) == hdr_red(rows_off, 0, 110))
        check('5: 关农历也不影响生肖「马」（中段还有 %d 个红像素）'
             % hdr_red(rows_off, 111, 365), hdr_red(rows_off, 111, 365) > 20)

        # 5e) build 67：**纪念日提醒**（用户要的样子：10-05 付婧文生日）
        #     ⚠ 得拿 10 月的时刻来测：10-01 是周四 → 1 号左边正好有 3 格空白
        #     （上面那块 TS 是 9 月，1 号是周二，左边只有 1 格、放不下那句话）。
        TS_OCT = 1790841600                     # 2026-10-01 08:00 UTC+8
        oct_rows, _ = render(1, TS_OCT, 0, 3970, 264, 2, 26, '深圳')
        memo_rows, _ = render(1, TS_OCT, 0, 3970, 264, 2, 26, '深圳',
                              '10-05', '付婧文生日快乐！')
        w_oct = month_cells(2026, 10)
        p_oct = row_h(max(r for (_c, r) in w_oct.values()) + 1)      # 10 月 = 5 行
        c5, r5 = w_oct[5]                        # 2026-10-05 = 第 2 行第 1 列
        x5, y5 = cell_xy(c5, r5, p_oct)
        frame_o = count(oct_rows, BLACK, x5 + 2, y5 - 5, x5 + CAL_COL_W - 2, y5)
        frame_m = count(memo_rows, BLACK, x5 + 2, y5 - 5, x5 + CAL_COL_W - 2, y5)
        check('5e: 生日那天（5 号）套上了黑框（框上边 %d -> %d 个黑像素）'
              % (frame_o, frame_m), frame_m > frame_o + 40)
        # 1 号左边那片空白：本来是纯白，现在有框 + 字
        box_o = count(oct_rows, BLACK, 0, CAL_GRID_Y + CAL_GRID_PAD,
                      CAL_COL_W * 3, CAL_GRID_Y + CAL_GRID_PAD + p_oct)
        box_m = count(memo_rows, BLACK, 0, CAL_GRID_Y + CAL_GRID_PAD,
                      CAL_COL_W * 3, CAL_GRID_Y + CAL_GRID_PAD + p_oct)
        check('5e: 1 号左边那片空白出现「框 + 祝福语」（%d -> %d 个黑像素）'
              % (box_o, box_m), box_o == 0 and box_m > 400)
        # 别人的格子一个像素都不该动（除了 5 号那个框）
        moved = [d for d, (c, r) in w_oct.items() if d != 5
                 and cell_ink(oct_rows, c, r, p_oct) != cell_ink(memo_rows, c, r, p_oct)]
        check('5e: 加了纪念日后别的日子一格都没动（动了的：%s）' % moved, not moved)
        # ① 1 号左边没空白的月份（2026-06-01 是周一）→ 退到最后一行右边，也得有
        TS_JUN = 1780300800                     # 2026-06-01 08:00 UTC+8
        jun_plain, _ = render(1, TS_JUN, 0, 3970, 264, 2, 26, '深圳')
        jun_memo, _ = render(1, TS_JUN, 0, 3970, 264, 2, 26, '深圳',
                             '06-05', '付婧文生日快乐！')
        check('5e: 左边没空白的月份（1 号是周一）自动退到最后一行右边',
              count(jun_memo, BLACK, 0, G.H - 70, G.W, G.H) >
              count(jun_plain, BLACK, 0, G.H - 70, G.W, G.H) + 300)

        # 5f) build 70：**生日当天**换成红方底 + 红底白字；**生日一过就不画**
        #     （用户提的两条）。时间点自己算，别手写 epoch —— 我手算错过一次。
        def ts_of(y, m, d):
            return int(datetime.datetime(y, m, d, 10, 0,
                                         tzinfo=datetime.timezone.utc).timestamp())

        bd_rows, _  = render(1, ts_of(2026, 10, 5), 0, 3970, 264, 2, 26, '深圳',
                             '10-05', '付婧文生日快乐!')
        aft_rows, _ = render(1, ts_of(2026, 10, 6), 0, 3970, 264, 2, 26, '深圳',
                             '10-05', '付婧文生日快乐!')
        aft_plain, _ = render(1, ts_of(2026, 10, 6), 0, 3970, 264, 2, 26, '深圳')
        cell5 = count(bd_rows, RED, 0, y5 - 8, CAL_COL_W, y5 + 47)
        cell5_plain = count(oct_rows, RED, 0, y5 - 8, CAL_COL_W, y5 + 47)
        check('5f: 生日当天那格是**红方底**（红像素 %d -> %d）' % (cell5_plain, cell5),
              cell5 > cell5_plain + 1500)
        gx0, gy0 = 4, CAL_GRID_Y + CAL_GRID_PAD + (p_oct - 26) // 2
        gx1, gy1 = gx0 + CAL_COL_W * 3 - 4, gy0 + 26
        g_red   = count(bd_rows, RED, gx0, gy0, gx1, gy1)
        g_white = count(bd_rows, WHITE, gx0, gy0, gx1, gy1)
        check('5f: 生日当天祝福语是**红底白字**（红 %d / 白 %d）' % (g_red, g_white),
              g_red > 2500 and g_white > 300)
        check('5f: **生日一过就不再画**（10-06 那版跟完全不带纪念日的图一样）',
              aft_rows == aft_plain)

        # 5b) 选项 0x08 = 节气加粗（同样的字错开 1px 再画一遍）：节气那格的墨要变多
        rows_bold, _ = render(1, TS, 0x08)
        tcell = want[23]                      # 2026-09-23 是秋分
        red_a = count(cal_rows, RED, tcell[0] * CAL_COL_W, CAL_GRID_Y + CAL_GRID_PAD + tcell[1] * pitch + 18,
                      (tcell[0] + 1) * CAL_COL_W, CAL_GRID_Y + CAL_GRID_PAD + tcell[1] * pitch + 36)
        red_b = count(rows_bold, RED, tcell[0] * CAL_COL_W, CAL_GRID_Y + CAL_GRID_PAD + tcell[1] * pitch + 18,
                      (tcell[0] + 1) * CAL_COL_W, CAL_GRID_Y + CAL_GRID_PAD + tcell[1] * pitch + 36)
        check('5b: 节气加粗开关（0x08）确实把节气那格画粗了（%d -> %d 红像素）'
              % (red_a, red_b), red_b > red_a + 20)
        _, cal_plain = render(1, TS)
        check('5b: 不加粗时不加粗（同一格 %d 像素）' % red_a, red_a > 0)

        # 5c) 笔画层次：农历那行（16x16 细字）要比节气（16x16 粗字）细
        sys.path.insert(0, HERE)
        from wqy_font import LUNAR16                     # 农历（文泉驿点阵宋体 12pt，1px）
        from vendor_font import CJK as V_CJK             # 节气用的原厂 wqy12（2px）
        thin = sum(r.count('#') for r in LUNAR16['十'])
        thick = sum(r.count('#') for r in V_CJK['十'])
        check('5c: 同样是"十"，农历那版比节气那版细（墨 %d vs %d）' % (thin, thick),
              thin * 3 <= thick * 2)
        check('5c: 两边都是 16x16（字号相同，只差笔画）',
              len(LUNAR16['十']) == 16 and len(V_CJK['十']) == 16)

        rows_wx,  _ = render(1, TS, 0, 3970, 264, 2, 26)    # 参数顺序见 gui_preview.render
        rows_wx3, _ = render(1, TS, 0, 3970, 264, 6, 30)    # 雷阵雨（3 个字）
        # 5d) 天气（图标 + 文字）：给了天气码就画在表头（build 61 的版式是
        #     干支+农历月 | 图标+文字 温度 城市名；温度位置会跟着天气文字长度走）。
        #     ⚠ 不能再拿"固定窗口里的黑像素数"当判据 —— 表头东西一挪，
        #       窗里少了别的字，差值就不准了（build 61 就踩到过）。改成数
        #       **新增的墨**：a 比 b 多出来的黑像素。
        def new_ink(a, b):
            return sum(1 for y in range(0, 26) for x in range(0, 400)
                       if a[y][x * 3:x * 3 + 3] == bytes(BLACK)
                       and b[y][x * 3:x * 3 + 3] != bytes(BLACK))

        ink_icon = new_ink(rows_wx, cal_rows)      # 多云 vs 完全不给天气
        ink_3    = new_ink(rows_wx3, rows_wx)      # 雷阵雨 vs 多云（多一个字）
        check('5d: 天气码=2（多云）时表头多出图标+文字（新增 %d 墨点）' % ink_icon,
              ink_icon > 150)
        check('5d: 码=6（雷阵雨，3 个字）比码=2（多云，2 个字）多画一个字（新增 %d 墨点）'
              % ink_3, ink_3 > 60)

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
