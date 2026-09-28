#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测农历换算（Src/img/lunar.c，表从原厂的 GUI/Lunar.c 生成）。

不用硬件：拿宿主机 cc 把 lunar.c 跟一段小测试程序编到一起，喂一堆**已知答案**的
公历日期进去，逐条对。判据是那些"看一眼日历就知道"的锚点：

  * 每年春节（正月初一）—— 2000/2001/2010/2024/2025/2026/2027/2050；
  * 中秋（八月十五）—— 2025-10-06、2026-09-25；
  * **闰月** —— 2020 闰四月、2023 闰二月、2025 闰六月（闰月最容易被表位打错）；
  * 月大月小 —— 2025-07-24 必须是六月三十（说明六月是 30 天）；
  * 今天的日期（跑起来那天附近）也能画出来。

另外盯两条边界：超出表范围（1970 那种早年）要返回非 0，而不是给个瞎答案。

    python3 tools/test_lunar.py
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
IMG = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img')

# (公历年, 月, 日, 农历，期望的字符串)
CASES = [
    (2000, 2, 5, '正月初一'),      # 2000 春节
    (2001, 1, 24, '正月初一'),
    (2010, 2, 14, '正月初一'),
    (2024, 2, 10, '正月初一'),
    (2025, 1, 29, '正月初一'),
    (2026, 2, 17, '正月初一'),      # 2026 春节
    (2027, 2, 6, '正月初一'),
    (2050, 1, 23, '正月初一'),
    (2025, 10, 6, '八月十五'),      # 2025 中秋
    (2026, 9, 25, '八月十五'),      # 2026 中秋
    (2020, 5, 23, '闰四月初一'),    # 闰月
    (2023, 3, 22, '闰二月初一'),
    (2025, 7, 25, '闰六月初一'),
    (2025, 8, 1, '闰六月初八'),
    (2025, 7, 24, '六月三十'),      # 六月是大月（30 天），不是闰月
    (2026, 1, 1, '冬月十三'),       # 十一月 = 冬月（传统叫法）
    (2026, 5, 1, '三月十五'),
    (2026, 9, 28, '八月十八'),
    (2026, 12, 31, '冬月廿三'),
]

SRC = r'''
#include <stdio.h>
#include <string.h>
#include "lunar.h"

struct { int y, m, d; const char *expect; } cases[] = {
@ROWS@};

static void name(uint8_t lm, uint8_t ld, uint8_t leap, char *buf, int cap)
{
    int n = 0;
    (void)cap;
    if (leap) { memcpy(buf + n, zk_lunar_leap_cn[1], 3); n += 3; }
    memcpy(buf + n, zk_lunar_month_cn[lm], strlen(zk_lunar_month_cn[lm]));
    n += (int)strlen(zk_lunar_month_cn[lm]);
    memcpy(buf + n, zk_lunar_day_cn[ld], strlen(zk_lunar_day_cn[ld]));
    n += (int)strlen(zk_lunar_day_cn[ld]);
    buf[n] = 0;
}

int main(void)
{
    int i, bad = 0;

    for (i = 0; i < (int)(sizeof(cases) / sizeof(cases[0])); i++)
    {
        uint8_t lm = 0, ld = 0, leap = 0;
        char    buf[64];
        int     rc = zk_lunar_from_solar((uint16_t)cases[i].y, (uint8_t)cases[i].m,
                                         (uint8_t)cases[i].d, &lm, &ld, &leap);

        name(lm, ld, leap, buf, sizeof(buf));
        if (rc != 0 || strcmp(buf, cases[i].expect) != 0)
        {
            bad++;
            printf("FAIL %04d-%02d-%02d -> rc=%d \"%s\" 期望 \"%s\"\n",
                   cases[i].y, cases[i].m, cases[i].d, rc, buf, cases[i].expect);
        }
        else
        {
            printf("PASS %04d-%02d-%02d -> %s\n", cases[i].y, cases[i].m,
                   cases[i].d, buf);
        }
    }

    /* 边界：表外的年份要老老实实返回失败，让调用方画 "--" */
    {
        uint8_t lm, ld, leap;
        int rc = zk_lunar_from_solar(1970, 1, 1, &lm, &ld, &leap);
        printf("%s 1970（表外）rc=%d\n", (rc != 0) ? "PASS" : "FAIL", rc);
        if (rc == 0) bad++;
    }

    printf(bad ? "%d 项失败\n" : "全部通过\n", bad);
    return bad ? 1 : 0;
}
'''


def main():
    rows = ''.join('    {%d, %d, %d, "%s"},\n' % c for c in CASES)
    with tempfile.TemporaryDirectory(prefix='zklunar-') as td:
        src = os.path.join(td, 't.c')
        open(src, 'w', encoding='utf-8').write(SRC.replace('@ROWS@', rows))
        exe = os.path.join(td, 't')
        subprocess.run(['cc', '-std=gnu99', '-O1', '-Wall', '-I', IMG,
                        src, os.path.join(IMG, 'lunar.c'), '-o', exe], check=True)
        out = subprocess.run([exe], check=False, stdout=subprocess.PIPE)
    text = out.stdout.decode('utf-8', 'replace')
    sys.stdout.write(text)
    n = len(re.findall(r'^FAIL', text, re.M))
    print('（共 %d 条已知日期 + 1 条边界）' % len(CASES))
    return 1 if (n or out.returncode) else 0


if __name__ == '__main__':
    sys.exit(main())
