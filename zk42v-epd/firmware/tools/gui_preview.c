/*
 * 在电脑上跑一遍 zkgui_draw()，把结果吐到 stdout（给 tools/gui_preview.py 转 PNG）。
 *
 * 用法： gui_preview <mode> <unix秒> [选项位]
 *   mode: 0=空白 1=日历 2=时钟
 *   选项位（跟固件 zk_opt.h 的 ZK_OPT_xxx 一致，这里只用到"不画农历"那位）：
 *     0x04 = 日历页不画农历那一行
 *
 * 底下那 32 字节"哨兵"是故意的：缓冲前后各留一段填 0xA5，画完之后由 Python 检查
 * 有没有被越界写坏 —— 这种"画到缓冲外面"的 bug 在屏上是看不出来的。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "zkgui.h"

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
    if (argc > 3)
    {
        unsigned opt = (unsigned)strtoul(argv[3], NULL, 0);

        zkgui_set_lunar((opt & 0x04u) ? 0 : 1);      /* 0x04 = ZK_OPT_NO_LUNAR */
    }

    zkgui_draw(buf + GUARD, &info);

    fwrite(buf, 1, sizeof(buf), stdout);
    return 0;
}
