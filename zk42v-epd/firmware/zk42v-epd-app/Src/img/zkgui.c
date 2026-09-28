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
#include "lunar.h"           /* 农历（表来自原厂，见 tools/gen_lunar.py） */

#include <string.h>

#define C_BLACK  0
#define C_WHITE  1
#define C_RED    2

#define ROW_BYTES (ZKGUI_W / 8)

/* 农历那一行开关（BLE 命令 0x70 的 bit2）。默认开。 */
static int s_lunar_on = 1;

void zkgui_set_lunar(int on)
{
    s_lunar_on = on ? 1 : 0;
}

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

/* 直线（Bresenham），用来画表针
 *
 * ⚠️ 这里踩过一个坑（build 27 之前一直带着）：误差项 `e` 必须"先算快照、
 * 两次判断都用同一份"。一开始写成第二次判断重新读已经减过的 `e`，斜率不是
 * 0/45/90 度时轨迹会跑过目标点 —— 而收尾条件是 `x0==x1 && y0==y1`（拿变了的
 * `x0/y0` 跟目标比），一旦跑过就永远不相等，于是画满 4000 次迭代。
 * 表现：**60 个分钟刻度里有 35 个会把表针画成一条横穿整屏的长线**（`put_px`
 * 会裁掉屏外部分，所以看不出越界，只在屏上留下一道斜线）。
 * 现在按标准写法来，并把步数上限收紧到 `dx+dy+2` —— 就算以后又写错，
 * 也不会再画出屏外。 */
static void draw_line(uint8_t *buf, int x0, int y0, int x1, int y1, int t, int color)
{
    int dx = (x1 > x0) ? (x1 - x0) : (x0 - x1);
    int dy = (y1 > y0) ? (y1 - y0) : (y0 - y1);
    int sx = (x0 < x1) ? 1 : -1;
    int sy = (y0 < y1) ? 1 : -1;
    int e = dx - dy;
    int steps = dx + dy + 2;
    int i;

    for (i = 0; i < steps; i++)
    {
        int e2 = 2 * e;

        fill_rect(buf, x0 - t / 2, y0 - t / 2, t, t, color);
        if (x0 == x1 && y0 == y1)
        {
            break;
        }
        if (e2 > -dy)
        {
            e -= dy;
            x0 += sx;
        }
        if (e2 < dx)
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
#define CJK_RUN  17   /* 闰 */
#define CJK_YEAR 18   /* 年 */
#define CJK_XING 19   /* 星 */
#define CJK_QI2  20   /* 期（注意别跟 CJK_QI=七 撞名） */
#define CJK_NONG 21   /* 农 */
#define CJK_LI2  22   /* 历 */

/* 中文串：按 UTF-8 码点在 zk_cjk_cp 里查字形（那张表由 gen_font.py 生成，
   所以不用手抄码点）。返回画完之后的 x；查不到的字符跳过。 */
static int draw_text_cjk(uint8_t *buf, int x, int y, const char *s, int scale, int color)
{
    const uint8_t *p = (const uint8_t *)s;

    while (*p)
    {
        uint32_t cp = 0;
        int      i;

        if (p[0] < 0x80)
        {
            p++;
            x += 17 * scale;
            continue;
        }
        if ((p[0] & 0xE0) == 0xC0)
        {
            cp = ((uint32_t)(p[0] & 0x1F) << 6) | (p[1] & 0x3F);
            p += 2;
        }
        else
        {
            cp = ((uint32_t)(p[0] & 0x0F) << 12) | ((uint32_t)(p[1] & 0x3F) << 6) |
                 (p[2] & 0x3F);
            p += 3;
        }

        for (i = 0; i < ZK_CJK_NUM; i++)
        {
            if (zk_cjk_cp[i] == cp)
            {
                draw_cjk(buf, x, y, i, scale, color);
                break;
            }
        }
        x += 17 * scale;                       /* 16px 字形 + 1px 间距 */
    }
    return x;
}

static int text_cjk_width(const char *s, int scale)
{
    int            n = 0;
    const uint8_t *p = (const uint8_t *)s;

    while (*p)
    {
        if (p[0] < 0x80) { p++; }
        else if ((p[0] & 0xE0) == 0xC0) { p += 2; }
        else { p += 3; }
        n++;
    }
    return (n > 0) ? (n * 17 - 1) * scale : 0;
}

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

/* ---- 日历页：整页「农历月历」（build 29 换的版式）-------------------------
 *
 * 版式照着社区那几版固件的**实屏截图**做的：qbsg.top 的固件目录里每个固件都带
 * 一张实拍图（2026-09-28 挑 4.2 寸那几版看过，出处见
 * outputs/analysis/qbsg-生态调研.md；**图没进库** —— 那是第三方资料）：
 *
 *   ┌───────────────────────────────────────────────┐
 *   │ 黑条   2026年09月      农历八月        星期一  │   白字
 *   ├───────────────────────────────────────────────┤
 *   │  一   二   三   四   五  [六]  [日]           │   六/日 红底白字
 *   ├───────────────────────────────────────────────┤
 *   │    1     2     3     4     5     6     7      │   大号日号（周末红）
 *   │   初八  初九  初十  十一  十二  十三  十四     │   小号农历（16x16 原大）
 *   │  …（一共 6 行，今天那格整块红底白字）…         │
 *   └───────────────────────────────────────────────┘
 *
 * 为什么换掉原来那版：老版左边一个红面板（表盘 + 大数字 + 大日期）、右边硬塞
 * 月历，两边都挤；而且大数字是 5x7 点阵放大 7 倍，边缘全是台阶，看着糙。
 * 现在整页让给月历 —— 农历用 16x16 原大画（一个格子一行字，一个像素都不缩放），
 * 信息量更大，也更像一本"日历"。时钟另有「时钟模式」那页。
 *
 * 农历那一行的开关（0x70 的 bit2）保留：关掉就不画每格的农历小字。
 */

#define CAL_HDR_H    40                            /* 顶部黑条 */
#define CAL_WD_Y     41                            /* 星期条 */
#define CAL_WD_H     21
#define CAL_GRID_Y   64
#define CAL_COL_W    (ZKGUI_W / 7)                 /* 57 */
#define CAL_ROW_H    ((ZKGUI_H - CAL_GRID_Y) / 6)  /* 39 */

/* 某一天的农历日名（"初八"/"廿三"/"三十"）；算不出来返回 NULL */
static const char *cal_lunar_day(int year, int mon, int day)
{
    uint8_t lm = 0, ld = 0, leap = 0;

    if (0 != zk_lunar_from_solar((uint16_t)year, (uint8_t)mon, (uint8_t)day,
                                 &lm, &ld, &leap))
    {
        return NULL;
    }
    if (ld < 1 || ld > 30)
    {
        return NULL;
    }
    return zk_lunar_day_cn[ld];
}

/* 今天那个农历月（"八月" / "闰六月"），写进 out；算不出来就写 "" */
static void cal_lunar_month(int year, int mon, int day, char *out, int cap)
{
    uint8_t lm = 0, ld = 0, leap = 0;

    out[0] = 0;
    if (0 != zk_lunar_from_solar((uint16_t)year, (uint8_t)mon, (uint8_t)day,
                                 &lm, &ld, &leap) || lm < 1 || lm > 12)
    {
        return;
    }
    if (leap && cap > 8)
    {
        memcpy(out, zk_lunar_leap_cn[1], 3);       /* "闰" 占 3 字节 */
        strcpy(out + 3, zk_lunar_month_cn[lm]);
    }
    else
    {
        strcpy(out, zk_lunar_month_cn[lm]);
    }
}

/* 顶部黑条：左边 2026年09月，中间 农历八月，右边 星期一（两页共用） */
static void draw_header_bar(uint8_t *buf, int year, int mon, int day, int wday,
                            int with_lunar)
{
    char  s[12];
    char  mbuf[16];
    int   x, n;

    fill_rect(buf, 0, 0, ZKGUI_W, CAL_HDR_H, C_BLACK);

    /* 左：2026年09月 —— 数字用 5x7 放大 2 倍（14px 高），年月用 16x16 汉字 */
    s[0] = (char)('0' + (year / 1000) % 10);
    s[1] = (char)('0' + (year / 100) % 10);
    s[2] = (char)('0' + (year / 10) % 10);
    s[3] = (char)('0' + year % 10);
    s[4] = 0;
    x = 8;
    draw_text5(buf, x, 13, s, 2, C_WHITE);
    x += text5_width(s, 2) + 3;
    draw_cjk(buf, x, 12, CJK_YEAR, 1, C_WHITE);
    x += 17 + 3;
    s[0] = (char)('0' + (mon / 10) % 10);
    s[1] = (char)('0' + mon % 10);
    s[2] = 0;
    draw_text5(buf, x, 13, s, 2, C_WHITE);
    x += text5_width(s, 2) + 3;
    draw_cjk(buf, x, 12, CJK_YUE, 1, C_WHITE);

    /* 中：农历八月（关掉农历时这条也不画，免得跟别处对不上） */
    if (with_lunar && s_lunar_on)
    {
        cal_lunar_month(year, mon, day, mbuf, (int)sizeof(mbuf));
        if (mbuf[0])
        {
            n  = text_cjk_width("农历", 1) + 1 + text_cjk_width(mbuf, 1);
            x  = (ZKGUI_W - n) / 2;
            x  = draw_text_cjk(buf, x, 12, "农历", 1, C_WHITE);
            (void)draw_text_cjk(buf, x + 1, 12, mbuf, 1, C_WHITE);
        }
    }

    /* 右：星期一 */
    n = 3 * 17 - 1;
    x = ZKGUI_W - 8 - n;
    draw_cjk(buf, x, 12, CJK_XING, 1, C_WHITE);
    draw_cjk(buf, x + 17, 12, CJK_QI2, 1, C_WHITE);   /* 期（不是 CJK_QI=七！） */
    draw_cjk(buf, x + 34, 12, s_wday_cjk[wday], 1, C_WHITE);
}

/* 一个格子：日号（5x7 放大 3 倍）+ 下面一行农历（16x16 原大）。
   今天那格整块红底、白字 —— 比画个红圈稳（圆形会被行高切掉）。 */
static void cal_cell(uint8_t *buf, int col, int row, int year, int mon, int d,
                     int is_today, int weekend)
{
    const int x0 = col * CAL_COL_W;
    const int y0 = CAL_GRID_Y + row * CAL_ROW_H;
    const int cx = x0 + CAL_COL_W / 2;
    const char *lun;
    char  s[4];
    int   tw, lw, color;

    if (d < 1)
    {
        return;
    }

    if (d < 10)
    {
        s[0] = (char)('0' + d);
        s[1] = 0;
    }
    else
    {
        s[0] = (char)('0' + d / 10);
        s[1] = (char)('0' + d % 10);
        s[2] = 0;
    }
    tw = text5_width(s, 3);
    lun = s_lunar_on ? cal_lunar_day(year, mon, d) : NULL;

    if (is_today)
    {
        fill_rect(buf, x0 + 4, y0 + 1, CAL_COL_W - 8, CAL_ROW_H - 2, C_RED);
        color = C_WHITE;
    }
    else
    {
        color = weekend ? C_RED : C_BLACK;
    }

    draw_text5(buf, cx - tw / 2, y0 + 1, s, 3, color);
    if (lun)
    {
        lw = text_cjk_width(lun, 1);
        draw_text_cjk(buf, cx - lw / 2, y0 + 22, lun, 1, color);
    }
}

static void draw_calendar(uint8_t *buf, int year, int mon, int day, int wday,
                          int hour, int min)
{
    int32_t days0;
    int     wday_sun, first_col, dim, d, col, row, i;

    (void)hour;
    (void)min;

    draw_header_bar(buf, year, mon, day, wday, 1);

    /* 星期条：一~日，六和日那两列整块红底白字（照社区版的配色） */
    for (i = 0; i < 7; i++)
    {
        const int x0 = i * CAL_COL_W;
        int       color = C_BLACK;

        if (i >= 5)
        {
            fill_rect(buf, x0, CAL_WD_Y, CAL_COL_W, CAL_WD_H, C_RED);
            color = C_WHITE;
        }
        draw_cjk(buf, x0 + (CAL_COL_W - 16) / 2, CAL_WD_Y + 2,
                 s_head_cjk[i], 1, color);
    }
    fill_rect(buf, 0, CAL_WD_Y + CAL_WD_H, ZKGUI_W, 1, C_BLACK);

    /* 这个月 1 号落在第几列（0 = 周一） */
    days0     = days_from_ymd(year, mon, 1);
    wday_sun  = (int)(((days0 % 7) + 7 + 4) % 7);
    first_col = (wday_sun + 6) % 7;
    dim       = days_in_month(year, mon);

    col = first_col;
    row = 0;
    for (d = 1; d <= dim; d++)
    {
        cal_cell(buf, col, row, year, mon, d, (d == day), (col >= 5));
        col++;
        if (col > 6)
        {
            col = 0;
            row++;
        }
    }
}
/* 时钟模式：整屏一个大表盘 + 数字时间（原厂说这模式是"每分钟全刷"，
   页面上也提醒了，主要用于除残影） */
static void draw_clock(uint8_t *buf, int year, int mon, int day, int wday,
                       int hour, int min)
{
    char s[8];
    int  tw;

    /* 跟日历页用同一个表头（2026年09月 / 农历八月 / 星期一），看着是一套东西 */
    draw_header_bar(buf, year, mon, day, wday, 1);

    draw_dial(buf, ZKGUI_W / 2, 148, 76, hour, min);

    fill_rect(buf, 100, 236, 200, 52, C_BLACK);
    s[0] = (char)('0' + hour / 10);
    s[1] = (char)('0' + hour % 10);
    s[2] = ':';
    s[3] = (char)('0' + min / 10);
    s[4] = (char)('0' + min % 10);
    s[5] = 0;
    tw = text5_width(s, 5);
    draw_text5(buf, 100 + (200 - tw) / 2, 244, s, 5, C_WHITE);
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
