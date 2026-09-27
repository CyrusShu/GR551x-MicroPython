/*
 * 日历 / 时钟页面的绘制 —— 见 zkgui.h 顶上的说明。
 *
 * 颜色约定（跟 tools/img2epd.py 一致，也是 B1.5/B1.6 实测出来的）：
 *   黑白面 bit=1 -> 白；红面 bit=1 -> 红（红盖过黑白面）
 * 所以一个像素写成：
 *   黑 -> bw=0, red=0      白 -> bw=1, red=0      红 -> bw=1, red=1
 */
#include "zkgui.h"
#include "zkgui_font.h"
#include "zkgui_trig.h"

#include <string.h>

#define C_BLACK  0
#define C_WHITE  1
#define C_RED    2

#define ROW_BYTES (ZKGUI_W / 8)

/* ---------------------------------------------------------------- 画点/方块 */

static void put_px(uint8_t *buf, int x, int y, int color)
{
    uint32_t o;
    uint8_t  m;

    if (x < 0 || x >= ZKGUI_W || y < 0 || y >= ZKGUI_H)
    {
        return;
    }
    o = (uint32_t)y * ROW_BYTES + (uint32_t)(x >> 3);
    m = (uint8_t)(0x80u >> (x & 7));

    if (C_BLACK == color)
    {
        buf[o] &= (uint8_t)~m;                      /* 黑白面 0=黑 */
    }
    else
    {
        buf[o] |= m;                                /* 黑白面 1=白 */
    }

    if (C_RED == color)
    {
        buf[ZKGUI_PLANE + o] |= m;                  /* 红面 1=红 */
    }
    else
    {
        buf[ZKGUI_PLANE + o] &= (uint8_t)~m;
    }
}

static void fill_rect(uint8_t *buf, int x, int y, int w, int h, int color)
{
    int i, j;

    for (j = 0; j < h; j++)
    {
        for (i = 0; i < w; i++)
        {
            put_px(buf, x + i, y + j, color);
        }
    }
}

static void fill_all(uint8_t *buf, int color)
{
    if (C_BLACK == color)
    {
        memset(buf, 0x00, ZKGUI_BYTES);
    }
    else if (C_WHITE == color)
    {
        memset(buf, 0xFF, ZKGUI_PLANE);
        memset(buf + ZKGUI_PLANE, 0x00, ZKGUI_PLANE);
    }
    else /* 红 */
    {
        memset(buf, 0xFF, ZKGUI_BYTES);
    }
}

/* 直线（Bresenham），用来画表针 */
static void draw_line(uint8_t *buf, int x0, int y0, int x1, int y1, int t, int color)
{
    int dx = (x1 > x0) ? (x1 - x0) : (x0 - x1);
    int dy = (y1 > y0) ? (y1 - y0) : (y0 - y1);
    int sx = (x0 < x1) ? 1 : -1;
    int sy = (y0 < y1) ? 1 : -1;
    int e = dx - dy;
    int i;

    for (i = 0; i < 4000; i++)
    {
        fill_rect(buf, x0 - t / 2, y0 - t / 2, t, t, color);
        if (x0 == x1 && y0 == y1)
        {
            break;
        }
        if ((e << 1) > -dy)
        {
            e -= dy;
            x0 += sx;
        }
        if ((e << 1) < dx)
        {
            e += dx;
            y0 += sy;
        }
    }
}

/* 空心圆（表盘外圈），中点画圆法 + 用方块加粗 */
static void draw_circle(uint8_t *buf, int cx, int cy, int r, int t, int color)
{
    int x = r, y = 0, err = 1 - r;

    while (x >= y)
    {
        fill_rect(buf, cx + x - t / 2, cy + y - t / 2, t, t, color);
        fill_rect(buf, cx + y - t / 2, cy + x - t / 2, t, t, color);
        fill_rect(buf, cx - x - t / 2, cy + y - t / 2, t, t, color);
        fill_rect(buf, cx - y - t / 2, cy + x - t / 2, t, t, color);
        fill_rect(buf, cx + x - t / 2, cy - y - t / 2, t, t, color);
        fill_rect(buf, cx + y - t / 2, cy - x - t / 2, t, t, color);
        fill_rect(buf, cx - x - t / 2, cy - y - t / 2, t, t, color);
        fill_rect(buf, cx - y - t / 2, cy - x - t / 2, t, t, color);
        y++;
        if (err < 0)
        {
            err += 2 * y + 1;
        }
        else
        {
            x--;
            err += 2 * (y - x) + 1;
        }
    }
}

/* 实心圆（今天那天的红圆点） */
static void fill_circle(uint8_t *buf, int cx, int cy, int r, int color)
{
    int x = r, y = 0, err = 1 - r;

    while (x >= y)
    {
        fill_rect(buf, cx - x, cy + y, 2 * x + 1, 1, color);
        fill_rect(buf, cx - y, cy + x, 2 * y + 1, 1, color);
        fill_rect(buf, cx - x, cy - y, 2 * x + 1, 1, color);
        fill_rect(buf, cx - y, cy - x, 2 * y + 1, 1, color);
        y++;
        if (err < 0)
        {
            err += 2 * y + 1;
        }
        else
        {
            x--;
            err += 2 * (y - x) + 1;
        }
    }
}

/* ---------------------------------------------------------------- 时间换算 */

/* 公历 Y/M/D <- 1970-01-01 起的天数（Howard Hinnant 那套，久经考验） */
static void civil_from_days(int32_t z, int *yy, int *mm, int *dd)
{
    int32_t  era;
    uint32_t doe, yoe, doy, mp, d, y;

    z += 719468;
    era = (z >= 0 ? z : z - 146096) / 146097;
    doe = (uint32_t)(z - era * 146097);
    yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    y   = (uint32_t)((int32_t)yoe + era * 400);
    doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    mp  = (5 * doy + 2) / 153;
    d   = doy - (153 * mp + 2) / 5 + 1;
    mp  = (mp < 10) ? mp + 3 : mp - 9;
    y  += (mp <= 2);

    *yy = (int)y;
    *mm = (int)mp;
    *dd = (int)d;
}

static int is_leap(int y)
{
    return (0 == (y % 4) && 0 != (y % 100)) || (0 == (y % 400));
}

static int days_in_month(int y, int m)
{
    static const uint8_t t[12] =
        { 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 };

    if (2 == m && is_leap(y))
    {
        return 29;
    }
    return t[m - 1];
}

void zkgui_civil(uint32_t ts, int *year, int *mon, int *day,
                 int *wday, int *hour, int *min, int *sec)
{
    int32_t  days = (int32_t)(ts / 86400u);
    uint32_t rem  = ts % 86400u;

    civil_from_days(days, year, mon, day);
    *wday = (int)(((days % 7) + 7 + 4) % 7);      /* 1970-01-01 是星期四，0=周日 */
    *hour = (int)(rem / 3600u);
    *min  = (int)((rem / 60u) % 60u);
    *sec  = (int)(rem % 60u);
}

/* 1970-01-01 到 y-m-d 的天数 */
static int32_t days_from_ymd(int year, int mon, int day)
{
    int32_t days = 0;
    int     y, m;

    for (y = 1970; y < year; y++)
    {
        days += is_leap(y) ? 366 : 365;
    }
    for (m = 1; m < mon; m++)
    {
        days += days_in_month(year, m);
    }
    return days + (day - 1);
}

/* ---------------------------------------------------------------- 小字模 */

static int f5_idx(char c)
{
    if (c >= '0' && c <= '9')
    {
        return c - '0';
    }
    if ('-' == c)
    {
        return ZK_F5_MINUS;
    }
    if (':' == c)
    {
        return ZK_F5_COLON;
    }
    return ZK_F5_SPACE;
}

/* scale 倍放大的 5x7 文字；返回画完之后的 x */
static int draw_text5(uint8_t *buf, int x, int y, const char *s, int scale, int color)
{
    int cy, cx;

    for (; *s; s++)
    {
        const uint8_t *g = zk_font5x7[f5_idx(*s)];

        for (cy = 0; cy < ZK_FONT5_H; cy++)
        {
            for (cx = 0; cx < ZK_FONT5_W; cx++)
            {
                if (g[cy] & (0x80u >> cx))
                {
                    fill_rect(buf, x + cx * scale, y + cy * scale, scale, scale, color);
                }
            }
        }
        x += (ZK_FONT5_W + 1) * scale;
    }
    return x;
}

static int text5_width(const char *s, int scale)
{
    int n = (int)strlen(s);

    return (n > 0) ? (n * (ZK_FONT5_W + 1) - 1) * scale : 0;
}

/* 中文单字（16x16，scale 倍） */
static void draw_cjk(uint8_t *buf, int x, int y, int idx, int scale, int color)
{
    const uint8_t *g;
    int cy, cx;

    if (idx < 0 || idx >= ZK_CJK_NUM)
    {
        return;
    }
    g = zk_font_cjk[idx];

    for (cy = 0; cy < ZK_FONT_CJK_H; cy++)
    {
        for (cx = 0; cx < ZK_FONT_CJK_W; cx++)
        {
            if (g[cy * 2 + (cx >> 3)] & (0x80u >> (cx & 7)))
            {
                fill_rect(buf, x + cx * scale, y + cy * scale, scale, scale, color);
            }
        }
    }
}

/* 中文单字索引（顺序必须跟 tools/gen_font.py 的 CJK_ORDER 一致） */
#define CJK_RI   0    /* 日 */
#define CJK_YI   1    /* 一 */
#define CJK_ER   2    /* 二 */
#define CJK_SAN  3    /* 三 */
#define CJK_SI   4    /* 四 */
#define CJK_WU   5    /* 五 */
#define CJK_LIU  6    /* 六 */
#define CJK_YUE  7    /* 月 */
#define CJK_QI   8    /* 七 */
#define CJK_BA   9    /* 八 */
#define CJK_JIU  10   /* 九 */
#define CJK_SHI  11   /* 十 */
#define CJK_DONG 12   /* 冬 */
#define CJK_LA   13   /* 腊 */
#define CJK_ZHENG 14  /* 正 */
#define CJK_CHU  15   /* 初 */
#define CJK_NIAN 16   /* 廿 */

/* 星期：0=周日 … 6=周六（跟 zkgui_civil 的 wday 一致） */
static const uint8_t s_wday_cjk[7] =
    { CJK_RI, CJK_YI, CJK_ER, CJK_SAN, CJK_SI, CJK_WU, CJK_LIU };

/* 月历表头：周一开头（跟国内日历、以及 4.2 寸那块屏的做法一致） */
static const uint8_t s_head_cjk[7] =
    { CJK_YI, CJK_ER, CJK_SAN, CJK_SI, CJK_WU, CJK_LIU, CJK_RI };

/* ---------------------------------------------------------------- 表针 */

/* 画一根针：idx 是 0..59（每格 6 度）。角度表见 zkgui_trig.h */
static void draw_hand(uint8_t *buf, int cx, int cy, int len, int idx, int t, int color)
{
    int x, y;

    idx %= 60;
    if (idx < 0)
    {
        idx += 60;
    }
    x = cx + (int)(((int32_t)len * zk_trig60[idx][0]) / 1024);
    y = cy + (int)(((int32_t)len * zk_trig60[idx][1]) / 1024);
    draw_line(buf, cx, cy, x, y, t, color);
}

/* 表盘：外圈 + 四个刻度 + 时针/分针 + 圆心 */
static void draw_dial(uint8_t *buf, int cx, int cy, int r, int hour, int min)
{
    int i;

    draw_circle(buf, cx, cy, r, 4, C_BLACK);
    for (i = 0; i < 4; i++)
    {
        /* 12/3/6/9 点刻度：短一点、细一点，别看着像表针 */
        draw_hand(buf, cx, cy, r * 18 / 100, i * 15, 2, C_BLACK);
    }
    /* 时针：每小时 5 格，另外按分钟再挪一点 */
    draw_hand(buf, cx, cy, r * 55 / 100, (hour % 12) * 5 + min / 12, 5, C_BLACK);
    draw_hand(buf, cx, cy, r * 80 / 100, min, 4, C_BLACK);
    fill_circle(buf, cx, cy, 5, C_BLACK);
}

/* ---------------------------------------------------------------- 两个页面 */

/* 左半边：红底 + 表盘 + 数字时间 + 大日期 + 星期/月份（照你给的那张图） */
static void draw_left_panel(uint8_t *buf, int mon, int day, int wday, int hour, int min)
{
    char s[8];
    int  tw;

    fill_rect(buf, 4, 4, 192, 292, C_RED);

    /* 表盘 */
    draw_dial(buf, 100, 80, 48, hour, min);

    /* 数字时间：黑底白字 */
    fill_rect(buf, 26, 140, 148, 44, C_BLACK);
    s[0] = (char)('0' + hour / 10);
    s[1] = (char)('0' + hour % 10);
    s[2] = ':';
    s[3] = (char)('0' + min / 10);
    s[4] = (char)('0' + min % 10);
    s[5] = 0;
    tw = text5_width(s, 4);
    draw_text5(buf, 26 + (148 - tw) / 2, 148, s, 4, C_WHITE);

    /* 大日期 */
    s[0] = (char)('0' + (day / 10) % 10);
    s[1] = (char)('0' + day % 10);
    s[2] = 0;
    draw_text5(buf, 34, 196, s, 7, C_BLACK);

    /* 右边：上面是星期（大），下面是月份 */
    draw_cjk(buf, 124, 192, s_wday_cjk[wday], 3, C_BLACK);
    s[0] = (char)('0' + mon / 10);
    if ('0' == s[0])
    {
        s[0] = (char)('0' + mon % 10);
        s[1] = 0;
    }
    else
    {
        s[1] = (char)('0' + mon % 10);
        s[2] = 0;
    }
    tw = text5_width(s, 3);
    draw_text5(buf, 124, 252, s, 3, C_BLACK);
    draw_cjk(buf, 124 + tw + 4, 246, CJK_YUE, 2, C_BLACK);
}

/* 右半边：白底黑框 + 年月 + 月历（今天红圈） */
static void draw_month_grid(uint8_t *buf, int year, int mon, int day)
{
    const int px = 200, py = 4, pw = 196, ph = 292;
    const int yy = 54, row_h = 39;
    const int col_w = (pw - 4) / 7;        /* 27（边框各 2px 先扣掉） */
    int32_t   days0;
    int       wday_sun, first_col, dim, d, col, row, i;
    char      s[8];
    int       tw;

    /* 外框 */
    fill_rect(buf, px, py, pw, 2, C_BLACK);
    fill_rect(buf, px, py + ph - 2, pw, 2, C_BLACK);
    fill_rect(buf, px, py, 2, ph, C_BLACK);
    fill_rect(buf, px + pw - 2, py, 2, ph, C_BLACK);

    /* 年月 "2026-09" */
    s[0] = (char)('0' + (year / 1000) % 10);
    s[1] = (char)('0' + (year / 100) % 10);
    s[2] = (char)('0' + (year / 10) % 10);
    s[3] = (char)('0' + year % 10);
    s[4] = '-';
    s[5] = (char)('0' + (mon / 10) % 10);
    s[6] = (char)('0' + mon % 10);
    s[7] = 0;
    draw_text5(buf, px + 10, 14, s, 2, C_BLACK);

    /* 右上角一个月牙（黑圆 - 偏移的白圆） */
    fill_circle(buf, px + pw - 22, 22, 11, C_BLACK);
    fill_circle(buf, px + pw - 26, 18, 10, C_WHITE);

    /* 表头：一 二 三 四 五 六 日 */
    for (i = 0; i < 7; i++)
    {
        draw_cjk(buf, px + 2 + i * col_w + (col_w - 16) / 2, 34,
                 s_head_cjk[i], 1, C_BLACK);
    }
    fill_rect(buf, px + 6, 52, pw - 12, 1, C_BLACK);

    /* 这个月 1 号落在第几列（0 = 周一） */
    days0 = days_from_ymd(year, mon, 1);
    wday_sun = (int)(((days0 % 7) + 7 + 4) % 7);
    first_col = (wday_sun + 6) % 7;
    dim = days_in_month(year, mon);

    col = first_col;
    row = 0;
    for (d = 1; d <= dim; d++)
    {
        int  cx = px + 2 + col * col_w + col_w / 2;                  /* 格子中心 */
        int  cy = yy + row * row_h + row_h / 2 - 4;
        int  x, y;

        s[0] = (char)('0' + (d / 10) % 10);
        if ('0' == s[0])
        {
            s[0] = (char)('0' + d % 10);
            s[1] = 0;
        }
        else
        {
            s[1] = (char)('0' + d % 10);
            s[2] = 0;
        }
        tw = text5_width(s, 2);
        x  = cx - tw / 2;
        y  = cy - 7;

        if (d == day)
        {
            fill_circle(buf, cx, cy, 15, C_RED);
            draw_text5(buf, x, y, s, 2, C_WHITE);
        }
        else
        {
            draw_text5(buf, x, y, s, 2, C_BLACK);
        }

        col++;
        if (col > 6)
        {
            col = 0;
            row++;
        }
    }
}

static void draw_calendar(uint8_t *buf, int year, int mon, int day, int wday,
                          int hour, int min)
{
    draw_left_panel(buf, mon, day, wday, hour, min);
    draw_month_grid(buf, year, mon, day);
}

/* 时钟模式：整屏一个大表盘 + 数字时间（原厂说这模式是"每分钟全刷"，
   页面上也提醒了，主要用于除残影） */
static void draw_clock(uint8_t *buf, int year, int mon, int day, int wday,
                       int hour, int min)
{
    char s[8];
    int  tw;

    draw_dial(buf, ZKGUI_W / 2, 118, 96, hour, min);

    fill_rect(buf, 100, 236, 200, 52, C_BLACK);
    s[0] = (char)('0' + hour / 10);
    s[1] = (char)('0' + hour % 10);
    s[2] = ':';
    s[3] = (char)('0' + min / 10);
    s[4] = (char)('0' + min % 10);
    s[5] = 0;
    tw = text5_width(s, 5);
    draw_text5(buf, 100 + (200 - tw) / 2, 244, s, 5, C_WHITE);

    /* 左上角星期（红），右上角日期 */
    draw_cjk(buf, 10, 8, s_wday_cjk[wday], 2, C_RED);
    s[0] = (char)('0' + (year / 1000) % 10);
    s[1] = (char)('0' + (year / 100) % 10);
    s[2] = (char)('0' + (year / 10) % 10);
    s[3] = (char)('0' + year % 10);
    s[4] = '-';
    s[5] = (char)('0' + (mon / 10) % 10);
    s[6] = (char)('0' + mon % 10);
    s[7] = 0;
    draw_text5(buf, ZKGUI_W - 10 - text5_width(s, 2), 16, s, 2, C_BLACK);
}

/* ---------------------------------------------------------------- 入口 */

void zkgui_draw(uint8_t *buf, const zkgui_info_t *info)
{
    int year, mon, day, wday, hour, min, sec;

    zkgui_civil(info->ts, &year, &mon, &day, &wday, &hour, &min, &sec);

    fill_all(buf, C_WHITE);                 /* 三色屏的白底 */

    switch (info->mode)
    {
        case ZKGUI_MODE_CALENDAR:
            draw_calendar(buf, year, mon, day, wday, hour, min);
            break;

        case ZKGUI_MODE_CLOCK:
            draw_clock(buf, year, mon, day, wday, hour, min);
            break;

        default:
            break;                          /* 其它模式：留白 */
    }
}
