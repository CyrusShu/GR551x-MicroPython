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
#include "jieqi.h"           /* 二十四节气（表和算法也来自原厂，见 tools/gen_jieqi.py） */
#include "weather.h"         /* 天气图标（手机经 BLE 下发天气码，见 tools/gen_weather.py） */

#include <string.h>

#define C_BLACK  0
#define C_WHITE  1
#define C_RED    2

#define ROW_BYTES (ZKGUI_W / 8)

/* 农历那一行开关（BLE 命令 0x70 的 bit2）。默认开。 */
static int s_lunar_on = 1;
/* 节气加粗（BLE 命令 0x70 的 bit3）。默认关：同样的字错开 1px 再画一遍。 */
static int s_term_bold = 0;

void zkgui_set_lunar(int on)
{
    s_lunar_on = on ? 1 : 0;
}

void zkgui_set_term_bold(int on)
{
    s_term_bold = on ? 1 : 0;
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
    if ('.' == c)
    {
        return ZK_F5_DOT;
    }
    if ('V' == c)
    {
        return ZK_F5_V;
    }
    if ('C' == c)
    {
        return ZK_F5_C;
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

/* 16x16 点阵（每行 2 字节，MSB first）—— 天气文字用 weather.c 里那张表 */
static void draw_glyph16(uint8_t *buf, int x, int y, const uint8_t *g, int scale, int color)
{
    int cy, cx;

    for (cy = 0; cy < ZK_WX_TEXT_H; cy++)
    {
        for (cx = 0; cx < ZK_WX_TEXT_W; cx++)
        {
            if (g[cy * 2 + (cx >> 3)] & (0x80u >> (cx & 7)))
            {
                fill_rect(buf, x + cx * scale, y + cy * scale, scale, scale, color);
            }
        }
    }
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

/* ---------------------------------------------------------------- 农历细字（16x16、1px）
 *
 * 格子里那行农历日名（初一..三十）用这套：**文泉驿点阵宋体 12pt**，
 * 16x16、笔画 1px —— 跟节气（原厂 wqy12，2px 笔画）同高同宽、只差笔画粗细，
 * 于是"节气比农历粗"这个层次不用换字号就出来了（样板就是这样）。
 */
static void draw_cjk_lunar(uint8_t *buf, int x, int y, int idx, int scale, int color)
{
    const uint8_t *g;
    int cy, cx;

    if (idx < 0 || idx >= ZK_LUNAR_NUM)
    {
        return;
    }
    g = zk_font_lunar[idx];

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

static int draw_text_lunar(uint8_t *buf, int x, int y, const char *s, int scale, int color)
{
    const uint8_t *p = (const uint8_t *)s;

    while (*p)
    {
        uint32_t cp = 0;
        int      i;

        if (p[0] < 0x80) { p++; x += 17 * scale; continue; }
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
        for (i = 0; i < ZK_LUNAR_NUM; i++)
        {
            if (zk_lunar_cp[i] == cp)
            {
                draw_cjk_lunar(buf, x, y, i, scale, color);
                break;
            }
        }
        x += 17 * scale;
    }
    return x;
}

static int text_lunar_width(const char *s, int scale)
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

/* ---------------------------------------------------------------- 数字
 *
 * 日号和时间用的数字表来自**原厂固件里的 u8g2_font_helvB14_tn**
 * （Helvetica Bold，数字 9x13 —— 正好是样板量到的 13.6px 高），见 tools/gen_vendor_font.py。
 * 每个字形在表里补成 10px 宽、居中对齐，步进就是 10px。
 */
static int num_idx(char c)
{
    if (c >= '0' && c <= '9')
    {
        return c - '0';
    }
    if (':' == c)
    {
        return 10;
    }
    return -1;
}

static int num_width(const char *s, int scale)
{
    int n = (int)strlen(s);

    return (n > 0) ? (n * ZK_FONT_NUM_W) * scale : 0;
}

static int draw_num(uint8_t *buf, int x, int y, const char *s, int scale, int color)
{
    for (; *s; s++)
    {
        int idx = num_idx(*s);
        int cy, cx;

        if (idx >= 0)
        {
            const uint8_t *g = zk_font_num[idx];

            for (cy = 0; cy < ZK_FONT_NUM_H; cy++)
            {
                for (cx = 0; cx < ZK_FONT_NUM_W; cx++)
                {
                    if (g[cy * 2 + (cx >> 3)] & (0x80u >> (cx & 7)))
                    {
                        fill_rect(buf, x + cx * scale, y + cy * scale,
                                  scale, scale, color);
                    }
                }
            }
        }
        x += ZK_FONT_NUM_W * scale;
    }
    return x;
}

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

/* ---- 日历页：整页「农历月历」（build 29 起，build 31 换成社区第 14 号那版的样子）
 *
 * 版式照着社区那几版固件的**实屏截图**做的（qbsg.top 的固件目录里每个固件都带
 * 一张实拍图；2026-09-28 挑出 4.2 寸那几版，出处见 outputs/analysis/qbsg-生态调研.md；
 * **图没进库** —— 那是第三方资料）：
 *
 *   ┌───────────────────────────────────────────────┐
 *   │ 2026年09月  农历八月  马年       [电池] 3.97V │  白底：年月红、农历黑、生肖红
 *   │                                        26.4C  │  电池/温度是芯片内部通道真读出来的
 *   ├───────────────────────────────────────────────┤
 *   │  一   二   三   四   五  [六]  [日]           │  黑底白字，六/日 红底白字
 *   ├───────────────────────────────────────────────┤
 *   │    1     2     3     4     5     6     7      │  大号日号（周末红）
 *   │   初八  初九  初十  十一  十二  十三  十四     │  小号农历（16x16 原大）
 *   │  …（一共 6 行；今天那格是红圆白字 + 红农历）…  │
 *   └───────────────────────────────────────────────┘
 *
 * 为什么换掉原来那版：老版左边一个红面板（表盘 + 大数字 + 大日期）、右边硬塞
 * 月历，两边都挤；而且大数字是 5x7 点阵放大 7 倍，边缘全是台阶，看着糙。
 * 现在整页让给月历 —— 农历用 16x16 原大画（一个格子一行字，一个像素都不缩放），
 * 信息量更大，也更像一本"日历"。时钟另有「时钟模式」那页。
 *
 * 农历那一行的开关（0x70 的 bit2）保留：关掉就不画每格的农历小字。
 */

/* 版式数值是**照着样板量出来的**（见 docs/feature-backlog.md 里的量测笔记）：
   样板（社区第 14 号那版）：表头 ~29px、黑条 21.6px、格子从 51.6 开始、
   行距 48.7px（5 行月）/ 约 43px（6 行月）、日号高 13.6px、农历高 ~14px、
   今天那个红圆直径 48px（把日号和农历两个字都圈住）。
   我们按 6 行排：表头 26 + 黑条 22 + 格子 252 → 行距 42。 */
#define CAL_HDR_H    26                            /* 表头（白底） */
#define CAL_HDR_ICON_Y 3                           /* 表头里天气图标的上边距（20px 图标 -> 3..22） */
#define CAL_HDR_TEMP_Y 10                          /* 表头里温度那串小字的上边距（5x7 字，跟 16px 的天气文字对齐居中） */
#define CAL_WD_Y     26                            /* 黑星期条 */
#define CAL_WD_H     22
#define CAL_GRID_Y   48                            /* 格子区从黑条下沿开始 */
#define CAL_GRID_PAD 6                             /* 第一行日号离黑条再留 6px */
#define CAL_BOTTOM_PAD 8                           /* 最后一行离下边框（样板几乎贴边，我们留 8px） */
#define CAL_COL_W    (ZKGUI_W / 7)                 /* 57 */
/* 格子里那两行的位置（相对行的上沿）：
   日号 3..17（scale 2 的 5x7 = 14px 高，跟样板的 13.6px 对得上），
   农历 19..35（16x16 原大，墨迹约 13px）。中间留 2px，行底还剩 6px 空。 */
#define CAL_NUM_Y    3
#define CAL_LUN_Y    20

#define CAL_ROW_H_MIN 38                           /* 行距下限（6 行月） */
#define CAL_ROW_H_MAX 56                           /* 行距上限（4 行月别拉太散） */

/* 行距**按这个月要几行摊开** —— 样板就是这么做的：
 *   它 5 行月的行距 48.4、最后一行农历离下边框只有 ~6px（我放大看过，
 *   下面几乎贴着边）；6 行月按同一个公式压到 ~41。
 *   算法：第一行内容顶(格子区顶) → 最后一行内容底(离底 CAL_BOTTOM_PAD)
 *   这一段平分给 (行数-1) 个间隔。 */
static int cal_row_h(int rows_used)
{
    const int content_h = (CAL_LUN_Y + ZK_FONT_CJK_H) - CAL_NUM_Y;    /* 31 */
    const int top       = CAL_GRID_Y + CAL_GRID_PAD;
    int       avail     = ZKGUI_H - CAL_BOTTOM_PAD - content_h - top;
    int       h         = (rows_used > 1) ? (avail / (rows_used - 1)) : avail;

    if (h < CAL_ROW_H_MIN)
    {
        h = CAL_ROW_H_MIN;
    }
    if (h > CAL_ROW_H_MAX)
    {
        h = CAL_ROW_H_MAX;
    }
    return h;
}

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

/* 电池图标：外框 + 正极凸点 + 里面按电量填格。
   pct < 0（还没读到）就只画个空框 —— 宁可不画，也不画假电量。 */
static void draw_battery(uint8_t *buf, int x, int y, int pct)
{
    const int w = 26, h = 13;
    int       bars = 0, i;

    fill_rect(buf, x, y, w - 2, h, C_BLACK);                  /* 外框 */
    fill_rect(buf, x + 1, y + 1, w - 4, h - 2, C_WHITE);      /* 掏空 */
    fill_rect(buf, x + w - 2, y + h / 3, 2, h / 3, C_BLACK);  /* 正极凸点 */

    if (pct >= 0)
    {
        bars = (pct + 24) / 25;                               /* 0..4 格 */
        for (i = 0; i < bars; i++)
        {
            fill_rect(buf, x + 3 + i * 5, y + 3, 4, h - 6, C_BLACK);
        }
    }
}

/*
 * 表头（两页共用）—— 版式照社区第 14 号那版（三色纯日历）：
 *
 *   红 2026年09月   黑 农历八月   红 马年   [图标]多云 26C    [电池] 3.90V
 *   ─────────────────────────────────────────────────────────────
 *
 * 跟 build 29 那版的区别：**不再是黑条白字**，改成白底 —— 年月用红色、农历黑色、
 * 生肖年红色；右上角放电池图标 + 电压 + 温度（社区那版最显眼的特征）。
 *
 * build 41：表头这块给"天气"腾了地方 ——
 *   · 星期（星期X）**不画了**：下面那条黑星期条已经写着一~日，表头重复一遍是浪费；
 *   · 天气画在"马年"右边：**图标 + 文字**（多云 / 雷阵雨…），
 *     天气码 + 天气温度都是手机经 BLE 命令 0x71 下发的（这块板子没有天气传感器）。
 *
 * build 43：**温度跟着天气走**（原来挤在右上角电池下面那行小字的位置）——
 *   现在挨着天气文字画，"多云 26C"读起来是一句话；右上角**只留电池图标**
 *   （不写 "3.97V" 那几个字了，电量多少看图标里那几格）。
 */
/* 表头里那串温度（"26C" / "-2C" / "26.4C"）—— build 43 起画在**天气后面**。
   手机下发的天气温度优先（整度）；没收到过才退回片内温度（一位小数）。
   返回画完之后的 x（没温度可画就原样返回）。 */
static int draw_header_temp(uint8_t *buf, int x, const zkgui_info_t *info)
{
    char s[8];
    int  neg = 0;

    if (info->env_temp_c != (int8_t)(-128))
    {
        int t  = info->env_temp_c;
        int av = (t < 0) ? -t : t;
        int k  = 0;

        neg = (t < 0);
        if (av >= 10)
        {
            s[k++] = (char)('0' + (av / 10) % 10);
        }
        s[k++] = (char)('0' + av % 10);
        s[k++] = 'C';
        s[k]   = 0;
    }
    else if (info->temp_c10 != ZK_TEMP_NONE)
    {
        int t  = info->temp_c10;
        int av = (t < 0) ? -t : t;

        neg = (t < 0);
        s[0] = (char)('0' + (av / 100) % 10);
        s[1] = (char)('0' + (av / 10) % 10);
        s[2] = '.';
        s[3] = (char)('0' + av % 10);
        s[4] = 'C';
        s[5] = 0;
        if (av < 100)                        /* 26.4 -> "26.4C" */
        {
            s[0] = s[1];
            s[1] = s[2];
            s[2] = s[3];
            s[3] = 'C';
            s[4] = 0;
        }
    }
    else
    {
        return x;                            /* 两个温度都没有：这一段留空 */
    }

    x += 3;                                  /* 跟天气文字拉开 3px */
    if (neg)
    {
        draw_text5(buf, x, CAL_HDR_TEMP_Y, "-", 1, C_BLACK);
        x += 7;
    }
    draw_text5(buf, x, CAL_HDR_TEMP_Y, s, 1, C_BLACK);
    return x + text5_width(s, 1);
}

static void draw_header(uint8_t *buf, int year, int mon, int day,
                        const zkgui_info_t *info, int with_lunar)
{
    char  s[12];
    char  mbuf[16];
    int   x, zi;

    fill_rect(buf, 0, 0, ZKGUI_W, CAL_HDR_H - 1, C_WHITE);

    /* 左：2026年09月（红）—— 数字 scale 2（14px 高，样板年月是 ~24px，
       但那是在 29px 高的表头里；我们压到 26px 后取 14px 更稳） */
    s[0] = (char)('0' + (year / 1000) % 10);
    s[1] = (char)('0' + (year / 100) % 10);
    s[2] = (char)('0' + (year / 10) % 10);
    s[3] = (char)('0' + year % 10);
    s[4] = 0;
    x = 6;
    draw_num(buf, x, 5, s, 1, C_RED);
    x += num_width(s, 1) + 2;
    draw_cjk(buf, x, 5, CJK_YEAR, 1, C_RED);
    x += 17 + 2;
    s[0] = (char)('0' + (mon / 10) % 10);
    s[1] = (char)('0' + mon % 10);
    s[2] = 0;
    draw_num(buf, x, 5, s, 1, C_RED);
    x += num_width(s, 1) + 2;
    draw_cjk(buf, x, 5, CJK_YUE, 1, C_RED);
    x += 17 + 5;

    /* 中：农历八月（黑）—— 用**小一号**的 12x12 字（样板里表头农历就比年月小一档） */
    if (with_lunar && s_lunar_on)
    {
        cal_lunar_month(year, mon, day, mbuf, (int)sizeof(mbuf));
        if (mbuf[0])
        {
            x = draw_text_cjk(buf, x, 5, "农历", 1, C_BLACK);
            x = draw_text_cjk(buf, x + 1, 5, mbuf, 1, C_BLACK);
            x += 6;
        }
    }

    /* 中右：生肖年（红） */
    zi = (int)(((year - 4) % 12 + 12) % 12);
    draw_cjk(buf, x, 5, 23 + zi, 1, C_RED);
    draw_cjk(buf, x + 17, 5, CJK_YEAR, 1, C_RED);
    x += 17 + 17 + 6;

    /* 再右：天气 = **图标 + 文字**（手机经 BLE 0x71 下发；见 weather.h）。
       图标 20x20（表头 26px 高，上下各留 3px），文字是 16x16 的 1px 点阵，
       跟旁边农历那行同一个字源，所以不会"一个粗一个细"。 */
    {
        const uint8_t *ic = zk_weather_icon(info->wx_code);
        const uint32_t *tx = zk_weather_text(info->wx_code);

        if (ic != 0)
        {
            const int stride = (ZK_WX_ICON_W + 7) / 8;
            int cy, cx;

            for (cy = 0; cy < ZK_WX_ICON_H; cy++)
            {
                for (cx = 0; cx < ZK_WX_ICON_W; cx++)
                {
                    if (ic[cy * stride + (cx >> 3)] & (0x80u >> (cx & 7)))
                    {
                        fill_rect(buf, x + cx, CAL_HDR_ICON_Y + cy, 1, 1, C_BLACK);
                    }
                }
            }
            x += ZK_WX_ICON_W + 3;
        }
        if (tx != 0)
        {
            int i;

            for (i = 0; tx[i] != 0u; i++)
            {
                const uint8_t *g = zk_weather_glyph(tx[i]);

                if (g != 0)
                {
                    draw_glyph16(buf, x, 5, g, 1, C_BLACK);
                }
                x += 17;                        /* 16px 字形 + 1px 间距 */
            }
        }
    }

    /* 再右：温度 —— **手机给的天气温度优先**，没有才退回片内温度。
       build 43 起从右上角挪到这里，紧跟天气（"多云 26C"是一句话） */
    x = draw_header_temp(buf, x, info);

    /* 右上：**只画电池图标**（build 43：不写"3.97V"那几个字了，格子不够好看；
       电量多少直接看图标里那几格）。图标竖直居中，跟左边那行字对齐。 */
    static const int bat_w = 26;                 /* draw_battery 里那个 26x13 的外形 */
    if (info->bat_mv >= 0)
    {
        draw_battery(buf, ZKGUI_W - 6 - bat_w, (CAL_HDR_H - 1 - 13) / 2, info->bat_pct);
    }

    /* 表头下面一条黑线 == 星期条的上边框 */
    fill_rect(buf, 0, CAL_HDR_H - 1, ZKGUI_W, 1, C_BLACK);
}

/* 一个格子：日号（5x7 放大 3 倍）+ 下面一行农历（16x16 原大）。
   今天那格整块红底、白字 —— 比画个红圈稳（圆形会被行高切掉）。 */
static void cal_cell(uint8_t *buf, int col, int row, int row_h, int year, int mon,
                     int d, int is_today, int weekend)
{
    const int x0 = col * CAL_COL_W;
    const int y0 = CAL_GRID_Y + CAL_GRID_PAD + row * row_h;
    const int cx = x0 + CAL_COL_W / 2;
    const char *lun;
    const char *jq;          /* 这天是节气的话 = 节气名，否则 NULL */
    char  s[4];
    int   tw, lw, color;

    if (d < 1)
    {
        return;
    }

    /* 日号是 1~2 位数字（原厂 helvB14，10px 宽一格） */
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
    tw = num_width(s, 1);

    /* 下面那行：**节气优先**，没有节气才画农历日名 */
    {
        uint8_t jq_date = zk_jieqi_date((uint16_t)year, (uint8_t)mon, (uint8_t)d);

        jq  = (jq_date != 0 && jq_date == d)
              ? zk_jieqi_name[ZK_JIEQI_IDX(mon, d)] : NULL;
    }
    lun = (!jq && s_lunar_on) ? cal_lunar_day(year, mon, d) : NULL;

    /* ---- 日号 ---- */
    if (is_today)
    {
        /* 今天：红圆把日号和下面那行字一起圈住（照样板）。
         * 半径按"能装下日号 + 两个字"算：今天那行文字最宽 25px（农历细字），
         * 最外角离圆心 sqrt(12.5² + 12²) ≈ 17.3 —— r=22 余量很足；
         * 行距大的月份（≥46px）用 r=25，跟样板的 48px 圆更接近。 */
        fill_circle(buf, cx, y0 + 17, (row_h >= 46) ? 25 : 23, C_RED);
        draw_num(buf, cx - tw / 2, y0 + CAL_NUM_Y, s, 1, C_WHITE);
        color = C_WHITE;
    }
    else
    {
        color = weekend ? C_RED : C_BLACK;
        draw_num(buf, cx - tw / 2, y0 + CAL_NUM_Y, s, 1, color);
    }

    /* ---- 下面那行 ---- */
    if (jq)
    {
        /* 节气：**红字**（照样板）。要不要再"加粗"由 ZK_TERM_BOLD 决定：
           置 1 就把同样的字**错开 1px 再画一遍**（视觉上粗 1px，
           比换字体省事，也不用额外的字模）。 */
        lw = text_cjk_width(jq, 1);
        draw_text_cjk(buf, cx - lw / 2, y0 + CAL_LUN_Y, jq, 1, C_RED);
        if (s_term_bold)
        {
            /* 错开 1px 再画一遍 = 视觉上加粗 1px（照样板里"节气比农历粗"） */
            draw_text_cjk(buf, cx - lw / 2 + 1, y0 + CAL_LUN_Y, jq, 1, C_RED);
        }
    }
    else if (lun)
    {
        /* 农历日名：16x16 的**细字**（文泉驿点阵宋体 12pt，1px 笔画）
           —— 跟旁边 2px 的节气拉开层次（样板就是这样） */
        lw = text_lunar_width(lun, 1);
        draw_text_lunar(buf, cx - lw / 2, y0 + CAL_LUN_Y, lun, 1, color);
    }
}

static void draw_calendar(uint8_t *buf, int year, int mon, int day, int wday,
                          int hour, int min, const zkgui_info_t *info)
{
    int32_t days0;
    int     wday_sun, first_col, dim, d, col, row, i;

    (void)hour;
    (void)min;
    (void)wday;             /* 表头不画星期了（build 41：那格改成天气），改成下面那条星期条 */

    draw_header(buf, year, mon, day, info, 1);

    /* 星期条（照社区第 14 号那版）：整条**黑底白字**，六/日那两列是**红底白字** */
    fill_rect(buf, 0, CAL_WD_Y, ZKGUI_W, CAL_WD_H, C_BLACK);
    for (i = 0; i < 7; i++)
    {
        const int x0 = i * CAL_COL_W;
        int       color = C_WHITE;

        if (i >= 5)
        {
            /* 红块比黑条略窄一点，右边那两列之间留 2px 黑缝（社区版就这样） */
            fill_rect(buf, x0 + (i == 5 ? 2 : 0), CAL_WD_Y, CAL_COL_W - 2,
                      CAL_WD_H, C_RED);
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

    {
        const int rows_used = (first_col + dim + 6) / 7;
        const int row_h     = cal_row_h(rows_used);

        col = first_col;
        row = 0;
        for (d = 1; d <= dim; d++)
        {
            cal_cell(buf, col, row, row_h, year, mon, d, (d == day), (col >= 5));
            col++;
            if (col > 6)
            {
                col = 0;
                row++;
            }
        }
    }
}
/* 时钟模式：整屏一个大表盘 + 数字时间（原厂说这模式是"每分钟全刷"，
   页面上也提醒了，主要用于除残影） */
static void draw_clock(uint8_t *buf, int year, int mon, int day, int wday,
                       int hour, int min, const zkgui_info_t *info)
{
    char s[8];
    int  tw;

    (void)wday;             /* 表头不画星期了，见 draw_header */

    /* 跟日历页用同一个表头（2026年09月 / 农历八月 / 生肖 / 电池），一套东西 */
    draw_header(buf, year, mon, day, info, 1);

    draw_dial(buf, ZKGUI_W / 2, 140, 80, hour, min);

    fill_rect(buf, 100, 236, 200, 52, C_BLACK);
    s[0] = (char)('0' + hour / 10);
    s[1] = (char)('0' + hour % 10);
    s[2] = ':';
    s[3] = (char)('0' + min / 10);
    s[4] = (char)('0' + min % 10);
    s[5] = 0;
    tw = num_width(s, 3);
    draw_num(buf, 100 + (200 - tw) / 2, 240, s, 3, C_WHITE);
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
            draw_calendar(buf, year, mon, day, wday, hour, min, info);
            break;

        case ZKGUI_MODE_CLOCK:
            draw_clock(buf, year, mon, day, wday, hour, min, info);
            break;

        default:
            break;                          /* 其它模式：留白 */
    }
}
