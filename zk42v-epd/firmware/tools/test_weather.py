#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测天气表（Src/img/weather.c，由 tools/gen_weather.py 生成）。

    python3 tools/test_weather.py

盯六件事：
  1  9 个码（1 晴 … 9 风）都有图标，尺寸 = ZK_WX_ICON_W x ZK_WX_ICON_H（20x20，每行 3 字节）
  2  图标真的画了东西（墨量 > 0），也没有"整块死黑"（> 一半就太糊了）
  3  每个码的**文字**（多云/雷阵雨…）每个字都能在字形表里查到，字形是 16x16
  4  每个字形的墨量在合理区间（细字 1px：不能是空、也不能是一团黑）
  5  码非法（0 或 >9）时图标返回 0、文字返回空串（调用方据此"不画"）
  6  名字表跟码对得上（1=晴 2=多云 … 9=风）
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
IMG = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img')

NAMES = {1: '晴', 2: '多云', 3: '阴', 4: '小雨', 5: '大雨',
         6: '雷阵雨', 7: '雪', 8: '雾', 9: '风'}

SRC = r'''
#include <stdio.h>
#include "weather.h"

int main(void)
{
    const int iw = ZK_WX_ICON_W, ih = ZK_WX_ICON_H;
    const int istride = (ZK_WX_ICON_W + 7) / 8;
    int bad = 0, code;

    for (code = 1; code <= ZK_WX_MAX; code++)
    {
        const uint8_t  *ic = zk_weather_icon((uint8_t)code);
        const uint32_t *tx = zk_weather_text((uint8_t)code);
        int y, x, ink = 0, nch = 0;

        if (ic == 0) { printf("FAIL %d: no icon\n", code); bad++; continue; }
        for (y = 0; y < ih; y++)
            for (x = 0; x < iw; x++)
                if (ic[y * istride + (x >> 3)] & (0x80u >> (x & 7))) ink++;

        if (ink <= 0)               { printf("FAIL %d: icon empty\n", code); bad++; }
        else if (ink > iw * ih / 2)  { printf("FAIL %d: icon too black (%d)\n", code, ink); bad++; }
        else                         printf("PASS %d %s icon ink=%d",
                                            code, zk_weather_name((uint8_t)code), ink);

        if (tx == 0 || tx[0] == 0u) { printf("  FAIL %d: no text\n", code); bad++; continue; }
        while (tx[nch] != 0u)
        {
            const uint8_t *g = zk_weather_glyph(tx[nch]);
            int gy, gx, gink = 0;

            if (g == 0) { printf("  FAIL glyph U+%04X missing\n", tx[nch]); bad++; nch++; continue; }
            for (gy = 0; gy < ZK_WX_TEXT_H; gy++)
                for (gx = 0; gx < ZK_WX_TEXT_W; gx++)
                    if (g[gy * 2 + (gx >> 3)] & (0x80u >> (gx & 7))) gink++;
            if (gink <= 0 || gink > 130)
            {
                printf("  FAIL glyph U+%04X ink=%d\n", tx[nch], gink);
                bad++;
            }
            nch++;
        }
        printf("  text=%d chars\n", nch);
    }

    if (zk_weather_icon(0) != 0)   { printf("FAIL code 0 should draw nothing\n"); bad++; }
    else                           printf("PASS code 0 -> no icon\n");
    if (zk_weather_text(0) == 0 || zk_weather_text(0)[0] != 0u)
                                   { printf("FAIL code 0 text should be empty\n"); bad++; }
    else                           printf("PASS code 0 -> empty text\n");
    if (zk_weather_icon((uint8_t)(ZK_WX_MAX + 1)) != 0)
                                   { printf("FAIL out-of-range code should return 0\n"); bad++; }
    else                           printf("PASS out-of-range -> 0\n");
    if (zk_weather_glyph(0x4E00u) != 0)
                                   { printf("FAIL unknown codepoint should return 0\n"); bad++; }
    else                           printf("PASS unknown codepoint -> 0\n");

    printf(bad ? "%d checks failed\n" : "all passed\n", bad);
    return bad ? 1 : 0;
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='zkwx-') as td:
        src = os.path.join(td, 't.c')
        open(src, 'w', encoding='utf-8').write(SRC)
        exe = os.path.join(td, 't')
        subprocess.run(['cc', '-std=gnu99', '-O1', '-Wall', '-I', IMG,
                        src, os.path.join(IMG, 'weather.c'), '-o', exe], check=True)
        out = subprocess.run([exe], check=False, stdout=subprocess.PIPE)
    text = out.stdout.decode('utf-8', 'replace')
    sys.stdout.write(text)

    bad = text.count('FAIL')
    # 名字表对得上（C 侧已经打印了名字，这里再核对一遍）
    for code, name in NAMES.items():
        if ('%d %s' % (code, name)) not in text:
            print('FAIL 码 %d 的名字不是 %s' % (code, name))
            bad += 1
    print('（9 个图标 + 9 组文字 + 4 条边界）')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
