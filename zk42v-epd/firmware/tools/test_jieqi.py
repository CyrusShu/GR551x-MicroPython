#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测二十四节气（Src/img/jieqi.c，表从原厂的 GUI/Lunar.c 生成）。

    python3 tools/test_jieqi.py

判据是**一眼能查证的锚点**：
  * 样板那张图上的两个 —— **2025-08-07 立秋、2025-08-23 处暑**（我们从照片上量出来的）；
  * 每年的春分/秋分（3/20、9/23 前后）、冬至（12/21-22）、立春（2/3-4）；
  * 不是节气的日子必须判成"不是"（2026-09-27 就是普通一天）；
  * 边界：1970 / 2060 年表外，必须返回 0，不许瞎给日期。
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
IMG = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img')

# (年, 月, 日, 期望的节气名；空串 = 不是节气)
CASES = [
    (2025, 8, 7, '立秋'),      # 样板那张图上的
    (2025, 8, 23, '处暑'),     # 同上
    (2025, 8, 8, ''),
    (2026, 9, 7, '白露'),
    (2026, 9, 23, '秋分'),
    (2026, 9, 27, ''),
    (2026, 2, 4, '立春'),
    (2026, 3, 20, '春分'),
    (2026, 6, 21, '夏至'),
    (2026, 12, 22, '冬至'),   # 2026 的冬至是 22 号（表里算出来的也是 22）
    (2025, 12, 21, '冬至'),
    (2024, 2, 4, '立春'),
    (2020, 4, 4, '清明'),
    (2050, 3, 20, '春分'),
    (1970, 3, 20, ''),         # 表外
    (2060, 3, 20, ''),         # 表外
]

SRC = r'''
#include <stdio.h>
#include <string.h>
#include "jieqi.h"

struct { int y, m, d; const char *want; } cases[] = {
@ROWS@};

int main(void)
{
    int i, bad = 0;

    for (i = 0; i < (int)(sizeof(cases) / sizeof(cases[0])); i++)
    {
        uint8_t idx  = ZK_JIEQI_IDX(cases[i].m, cases[i].d);
        uint8_t date = zk_jieqi_date((uint16_t)cases[i].y, (uint8_t)cases[i].m,
                                     (uint8_t)cases[i].d);
        const char *got = (date == cases[i].d) ? zk_jieqi_name[idx] : "";

        if (strcmp(got, cases[i].want) != 0)
        {
            bad++;
            printf("FAIL %04d-%02d-%02d -> \"%s\"（该月节气日=%d）期望 \"%s\"\n",
                   cases[i].y, cases[i].m, cases[i].d, got, date, cases[i].want);
        }
        else
        {
            printf("PASS %04d-%02d-%02d -> %s\n", cases[i].y, cases[i].m, cases[i].d,
                   got[0] ? got : "（不是节气）");
        }
    }
    printf(bad ? "%d 项失败\n" : "全部通过\n", bad);
    return bad ? 1 : 0;
}
'''


def main():
    rows = ''.join('    {%d, %d, %d, "%s"},\n' % c for c in CASES)
    with tempfile.TemporaryDirectory(prefix='zkjq-') as td:
        src = os.path.join(td, 't.c')
        open(src, 'w', encoding='utf-8').write(SRC.replace('@ROWS@', rows))
        exe = os.path.join(td, 't')
        subprocess.run(['cc', '-std=gnu99', '-O1', '-Wall', '-I', IMG,
                        src, os.path.join(IMG, 'jieqi.c'), '-o', exe], check=True)
        out = subprocess.run([exe], check=False, stdout=subprocess.PIPE)
    text = out.stdout.decode('utf-8', 'replace')
    sys.stdout.write(text)
    n = len(re.findall(r'^FAIL', text, re.M))
    print('（共 %d 条）' % len(CASES))
    return 1 if (n or out.returncode) else 0


if __name__ == '__main__':
    sys.exit(main())
