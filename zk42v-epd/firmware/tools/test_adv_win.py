#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测广播窗口（Src/ble/zk_adv_win.h）—— build 90。

背景：价签原来 24 小时常开广播（100ms 间隔）。按射频占空比估 ≈0.1 mA ≈ **2.4 mAh/天**，
是"一天一次整屏刷新"（0.067 mAh）的 30 多倍。用户 2026-10-14 拍板改成窗口：
只在 ① 上电后 5 分钟 ② 每天 0 点前后 150 秒 ③ 每 6 小时 30 秒 开广播。

这个测试直接把**固件那份头文件**编进来跑（不是另写一遍 Python 版），喂 3 天时间轴：
  1  上电/复位后先开满 5 分钟（刷机、换电池、按 RST 都能连上）
  2  时间还没同步过 → 一直开着（不然基站/网页根本没机会把时间送进来）
  3  每天窗口**跨午夜**：23:59:30 开、00:02:00 关
  4  兜底窗口落在本地 00/06/12/18 点，各开 30 秒（0 点那窗跟兜底窗重叠，不重复开）
  5  **一天里开着的总时长 = 240 秒**（150 + 3×30），占空比 0.28% —— 
     这条就是省电那笔账的分子（对照：常开 = 86400 秒 / 100%）
  6  边界：起点前一秒关、起点那一秒开、跨过午夜还开、结束那一秒关

    python3 tools/test_adv_win.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
INC = os.path.join(FW, 'zk42v-epd-app', 'Src', 'ble')
WIN_H = os.path.join(INC, 'zk_adv_win.h')

FAIL = []


def check(label, ok, detail=''):
    print(('  PASS  ' if ok else '  FAIL  ') + label)
    if not ok:
        FAIL.append(label)
        if detail:
            print('        · %s' % detail)


SRC = r'''
#include <stdio.h>
#include "zk_adv_win.h"

int main(void)
{
    zk_adv_win_cfg_t c;
    uint32_t base;

    c.boot_ms     = 300u * 1000u;     /* 同 zk_pwr.h 的 ZK_ADV_BOOT_WINDOW_S */
    c.day_start_s = 85800u;           /* 23:50:00（同 ZK_ADV_DAY_WIN_START_S） */
    c.day_len_s   = 1200u;            /* 20 分钟（同 ZK_ADV_DAY_WIN_LEN_S） */
    c.fb_every_s  = 21600u;           /* 6 小时 */
    c.fb_len_s    = 30u;

    base = 1781188800u;
    base -= base % 86400u;            /* 归到"本地当天 00:00" */

    /* 1：上电后的长窗口（墙钟挑 12:30 —— 不落在任何窗口里，
          不然"上电窗口过了"和"这会儿正好是兜底窗口"两件事会混在一起） */
    printf("A %d %d %d\n",
           zk_adv_win_open(0u,             base + 12u * 3600u + 1800u, 1, &c),
           zk_adv_win_open(299u * 1000u,   base + 12u * 3600u + 1800u, 1, &c),
           zk_adv_win_open(301u * 1000u,   base + 12u * 3600u + 1800u, 1, &c));

    /* 2：没同步过（synced=0）→ 一直开 */
    printf("B %d %d %d\n",
           zk_adv_win_open(600u * 1000u, 0u,     0, &c),
           zk_adv_win_open(600u * 1000u, 43200u, 0, &c),
           zk_adv_win_open(600u * 1000u, 90000u, 0, &c));

    /* 3：每天那一窗跨午夜 —— 逐秒扫一天（**把兜底窗关掉**，只看"0 点那窗"）*/
    {
        zk_adv_win_cfg_t d = c;
        uint32_t s, cnt = 0u, first = 0xFFFFFFFFu, last = 0u;

        d.fb_every_s = 0u;
        for (s = 0u; s < 86400u; s++)
        {
            if (zk_adv_win_open(600u * 1000u, base + s, 1, &d))
            {
                if (first == 0xFFFFFFFFu) { first = s; }
                last = s;
                cnt++;
            }
        }
        printf("C %u %u %u\n", cnt, first, last);
    }

    /* 4/5：24 小时里的"段"：段数 + 每段时长 + 总秒数 */
    {
        uint32_t s, seg = 0u, runs = 0u, tot = 0u, i;
        uint32_t segs[16];
        int      on = 0;

        for (s = 0u; s < 86400u; s++)
        {
            int w = zk_adv_win_open(600u * 1000u, base + s, 1, &c);

            if (w)
            {
                if (!on) { on = 1; seg = 1u; runs++; }
                else     { seg++; }
                tot++;
            }
            else if (on)
            {
                on = 0;
                if (runs <= 16u) { segs[runs - 1u] = seg; }
            }
        }
        if (on && runs <= 16u) { segs[runs - 1u] = seg; }
        printf("D %u %u", runs, tot);
        for (i = 0u; i < runs && i < 16u; i++) { printf(" %u", segs[i]); }
        printf("\n");
    }

    /* 6：边界 */
    {
        uint32_t d0 = base;

        printf("E %d %d %d %d\n",
               zk_adv_win_open(600000u, d0 + 85799u, 1, &c),
               zk_adv_win_open(600000u, d0 + 85800u, 1, &c),
               zk_adv_win_open(600000u, d0 + 86400u, 1, &c),
               zk_adv_win_open(600000u, d0 + 86400u + 600u, 1, &c));
    }

    return 0;
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='advwin-') as td:
        csrc = os.path.join(td, 't.c')
        exe = os.path.join(td, 't')
        with open(csrc, 'w', encoding='utf-8') as f:
            f.write(SRC)
        r = subprocess.run(['cc', '-std=gnu99', '-O1', '-Wall', '-I', INC, csrc, '-o', exe],
                           capture_output=True)
        if r.returncode != 0:
            print(r.stderr.decode('utf-8', 'replace'))
            return 1
        out = subprocess.run([exe], capture_output=True).stdout.decode('utf-8')

    A = B = C = D = E = None
    for line in out.splitlines():
        f = line.split()
        if not f:
            continue
        if f[0] == 'A':
            A = [int(x) for x in f[1:]]
        elif f[0] == 'B':
            B = [int(x) for x in f[1:]]
        elif f[0] == 'C':
            C = [int(x) for x in f[1:]]
        elif f[0] == 'D':
            D = [int(x) for x in f[1:]]
        elif f[0] == 'E':
            E = [int(x) for x in f[1:]]

    check('1: 上电/复位后先开 5 分钟（0s 开 / 299s 开 / 301s 关）：%s' % A,
          A == [1, 1, 0])
    check('2: 时间没同步过就一直开着（三个时刻都该是 1）：%s' % B, B == [1, 1, 1])
    # 扫描轴是"从 00:00 开始的 86400 秒"，而这一窗是 23:59:30 → 00:02:00，
    # 所以它在扫描轴上被日界切成两头：**开头的 120 秒**（00:00–00:02）+ **结尾的 30 秒**
    # （23:59:30–24:00），合计 150 秒 —— 这正是"跨午夜"的证据（第一段在 0，最后一段在 86399）。
    check('3: 每天那窗跨午夜、合计 1200 秒（被日界切成开头 600 秒 + 结尾 600 秒）：%s' % C,
          C is not None and C[0] == 1200 and C[1] == 0 and C[2] == 86399,
          '实际 %s' % C)
    if D:
        runs, total = D[0], D[1]
        segs = D[2:]
        # 扫描是从"当天 00:00"开始的，所以 0 点那窗被**日界线切成两段**：
        #   开头的 120 秒（00:00–00:02，属于这一窗的后半）+ 结尾的 30 秒（23:59:30–24:00），
        # 中间夹着 06/12/18 三个各 30 秒的兜底窗 —— 一共 5 段、240 秒。
        check('4: 24 小时里开 %d 段、每段 %s 秒（0 点那窗被日界切成 600+600，'
              '中间 06/12/18 各 30 秒）' % (runs, segs),
              runs == 5 and segs == [600, 30, 30, 30, 600], '实际 段=%s' % segs)
        check('5: 一天合计开 %d 秒 = 1200+3×30，占空比 %.2f%%（常开是 100%%）'
              % (total, total / 864.0), total == 1290, '实际 %d 秒' % total)
    else:
        check('4: 没拿到 D 那组数据', False)
    check('6: 边界对：23:49:59 关 / 23:50:00 开 / 00:00:00 开 / 00:10:00 关（%s）' % E,
          E == [0, 1, 1, 0])

    # 7) 源码守卫：固件那边真的用上了这套判断，而且**窗口关着时不许自动重开广播**
    try:
        with open(WIN_H, encoding='utf-8') as f:
            win_h = f.read()
        with open(os.path.join(INC, 'zk_ble.c'), encoding='utf-8') as f:
            ble_c = f.read()
        with open(os.path.join(FW, 'zk42v-epd-app', 'Src', 'board', 'zk_pwr.h'),
                  encoding='utf-8') as f:
            pwr_h = f.read()
        check('7: zk_ble.c 每圈都问一次窗口（zk_adv_win_open + zk_epd_now）',
              'zk_adv_win_open' in ble_c and 'zk_epd_now' in ble_c)
        check('7: 窗口关着时不会自作主张重开广播（ADV_STOP/断开那条都看 s_adv_open）',
              ble_c.count('s_adv_open &&') >= 2)
        check('7: 旋钮在 zk_pwr.h 里、默认开（ZK_ADV_WINDOW 1）',
              'ZK_ADV_WINDOW           1' in pwr_h and 'ZK_ADV_BOOT_WINDOW_S' in pwr_h)
        # 4 个默认值**两边都钉住**：这个测试里的数是从 zk_pwr.h 抄来的，
        # 谁改了旋钮没同步改测试，这里就该红。
        check('7: zk_pwr.h 的四个窗口默认值跟本测试一致（300s / 23:50 起 1200s / 6h 30s）',
              'ZK_ADV_BOOT_WINDOW_S    300u' in pwr_h and
              'ZK_ADV_DAY_WIN_START_S  85800u' in pwr_h and
              'ZK_ADV_DAY_WIN_LEN_S    1200u' in pwr_h and
              'ZK_ADV_FB_EVERY_S       21600u' in pwr_h and
              'ZK_ADV_FB_LEN_S         30u' in pwr_h)
        check('7: zk_adv_win.h 是"纯逻辑"头文件（主机端测的就是固件那一份）',
              'zk_adv_win_open' in win_h and '#include "gr_includes.h"' not in win_h)
    except OSError as e:
        check('7: 读源码失败：%s' % e, False)

    print()
    if FAIL:
        print('有 %d 项失败 ❌' % len(FAIL))
        return 1
    print('全部通过 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
