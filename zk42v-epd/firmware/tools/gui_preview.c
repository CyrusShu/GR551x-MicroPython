/*
 * 在电脑上跑一遍 zkgui_draw()，把结果吐到 stdout（给 tools/gui_preview.py 转 PNG）。
 *
 * 用法： gui_preview <mode> <unix秒> [选项位] [电池毫伏] [温度x10]
 *   mode: 0=空白 1=日历 2=时钟
 *   选项位（跟固件 zk_opt.h 的 ZK_OPT_xxx 一致，这里只用到"不画农历"那位）：
 *     0x04 = 日历页不画农历那一行
 *   电池/温度：不传就按"没读到"（不画电池）；传 0 也当没读到。
 *   第 8/9 个参数：天气码（1 晴 2 多云 … 9 风）、天气温度（℃，-128=没收到过）
 *
 * 底下那 32 字节"哨兵"是故意的：缓冲前后各留一段填 0xA5，画完之后由 Python 检查
 * 有没有被越界写坏 —— 这种"画到缓冲外面"的 bug 在屏上是看不出来的。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "zkgui.h"
#include "zk_bat_curve.h"    /* 电量曲线：跟固件 zk_bat_pct() 共用同一张表 */

#define GUARD 32

int main(int argc, char **argv)
{
    static uint8_t buf[ZKGUI_BYTES + 2 * GUARD];
    zkgui_info_t   info;

    if (argc < 3)
    {
        fprintf(stderr, "usage: gui_preview <mode> <unix_ts>\n");
        return 2;
    }

    memset(buf, 0xA5, sizeof(buf));
    info.mode = (uint8_t)atoi(argv[1]);
    info.ts   = (uint32_t)strtoul(argv[2], NULL, 10);
    info.bat_mv   = -1;
    info.bat_pct  = -1;
    info.temp_c10 = ZK_TEMP_NONE;
    info.wx_code  = 0;
    info.env_temp_c = (int8_t)-128;
    info.city = (argc > 8) ? argv[8] : "";        /* build 61：温度后面那个城市名 */
    /* build 67：纪念日提醒 —— argv[9] 是 "月-日"（例 "10-05"），argv[10] 是祝福语 */
    info.memo_mon = 0;
    info.memo_day = 0;
    info.memo     = "";
    /* build 71：天气预警 —— argv[11] = 级别（1白 2蓝 3黄 4橙 5红，0/缺省 = 没有），
       argv[12] = 类型名（"暴雨"/"雷电"…）。有预警时表头那格会改画它。 */
    info.alert_level = 0;
    info.alert_type  = "";
    if (argc > 11)
    {
        info.alert_level = (int8_t)atoi(argv[11]);
        info.alert_type  = (argc > 12) ? argv[12] : "";
    }
    /* build 74：argv[13] = 和风的预警图标编号（1003 暴雨 / 1014 雷电…），0 = 不画 */
    info.alert_code = (argc > 13) ? (int16_t)atoi(argv[13]) : 0;
    if (argc > 10)
    {
        int mm = 0, dd = 0;

        if (sscanf(argv[9], "%d-%d", &mm, &dd) == 2)
        {
            info.memo_mon = (int8_t)mm;
            info.memo_day = (int8_t)dd;
            info.memo     = argv[10];
        }
    }
    if (argc > 6)
    {
        info.wx_code = (uint8_t)atoi(argv[6]);
    }
    if (argc > 7)
    {
        info.env_temp_c = (int8_t)atoi(argv[7]);
    }
    if (argc > 3)
    {
        unsigned opt = (unsigned)strtoul(argv[3], NULL, 0);

        zkgui_set_lunar((opt & 0x04u) ? 0 : 1);      /* 0x04 = ZK_OPT_NO_LUNAR */
        zkgui_set_term_bold((opt & 0x08u) ? 1 : 0);  /* 0x08 = ZK_OPT_TERM_BOLD */
    }
    if (argc > 4)
    {
        int mv = atoi(argv[4]);

        if (mv > 1000)                               /* 毫伏；0/负数 = 没读到 */
        {
            int pct = zk_bat_curve_pct(mv);          /* CR2450 的曲线，跟固件同一个函数 */

            info.bat_mv  = (int16_t)mv;
            info.bat_pct = (int8_t)(pct < 0 ? 0 : (pct > 100 ? 100 : pct));
        }
    }
    if (argc > 5)
    {
        info.temp_c10 = (int16_t)atoi(argv[5]);
    }

    zkgui_draw(buf + GUARD, &info);

    fwrite(buf, 1, sizeof(buf), stdout);
    return 0;
}

/* ---- 2026-10-05：主机侧给「休 / 班」表一个桩 ---------------------------------
   真正的数据来自基站 0x7E（固件在 zk_epd_svc.c 里存、并由它实现 zk_holiday_mark）；
   预览工具只编 zkgui.c，没有那个函数，所以这里补一个，好让日历上那两个小字
   也能预览出来。
   取值：环境变量 ZK_PREVIEW_HOL="月:8字节hex"，例：
        ZK_PREVIEW_HOL=10:5515080000000000    （2026-10：1-7 休、10 班） */
static uint8_t g_prev_hol[8];
static uint8_t g_prev_hol_mm;

uint8_t zk_holiday_mark(uint8_t mon, uint8_t day)
{
    static int inited = 0;

    if (!inited)                                   /* 第一次调用时读环境变量 */
    {
        const char *e = getenv("ZK_PREVIEW_HOL");

        inited = 1;
        if (e != NULL)
        {
            const char *c = strchr(e, ':');

            if (c != NULL)
            {
                int i;

                g_prev_hol_mm = (uint8_t)atoi(e);
                for (i = 0; i < 8; i++)
                {
                    char b[3];
                    b[0] = c[1 + i * 2];
                    b[1] = c[2 + i * 2];
                    b[2] = 0;
                    g_prev_hol[i] = (uint8_t)strtol(b, NULL, 16);
                }
            }
        }
    }
    if (mon != g_prev_hol_mm || day < 1u || day > 31u)
    {
        return 0u;
    }
    return (uint8_t)((g_prev_hol[(day - 1u) >> 2] >> (((day - 1u) & 3u) * 2u)) & 3u);
}

/* ---- 2026-10-05：主机侧给「节日名」一个桩 ---------------------------------
   真数据来自基站 0x7F（固件在 zk_epd_svc.c 里）。预览用环境变量喂：
       ZK_PREVIEW_FEST="10-01:国庆节,10-18:重阳节"                        */
static struct { int mm, dd; char name[24]; } g_prev_fest[8];
static int g_prev_fest_n;

const char *zk_festival_name(uint8_t mon, uint8_t day)
{
    static int inited = 0;
    int i;

    if (!inited)
    {
        const char *e = getenv("ZK_PREVIEW_FEST");

        inited = 1;
        while (e != NULL && *e != 0 && g_prev_fest_n < 8)
        {
            int mm = atoi(e);
            const char *dash = strchr(e, '-');
            const char *colon = strchr(e, ':');
            const char *comma;

            if (dash == NULL || colon == NULL) { break; }
            g_prev_fest[g_prev_fest_n].mm = mm;
            g_prev_fest[g_prev_fest_n].dd = atoi(dash + 1);
            comma = strchr(colon + 1, ',');
            {
                size_t n = comma ? (size_t)(comma - colon - 1) : strlen(colon + 1);
                if (n > 23u) { n = 23u; }
                memcpy(g_prev_fest[g_prev_fest_n].name, colon + 1, n);
                g_prev_fest[g_prev_fest_n].name[n] = 0;
            }
            g_prev_fest_n++;
            e = comma ? comma + 1 : NULL;
        }
    }
    for (i = 0; i < g_prev_fest_n; i++)
    {
        if (g_prev_fest[i].mm == (int)mon && g_prev_fest[i].dd == (int)day)
        {
            return g_prev_fest[i].name;
        }
    }
    return 0;
}
