#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
节气表生成器：从**原厂**那份 GUI/Lunar.c 里把 24 节气的表原样抽出来，
生成我们自己的 Src/img/jieqi.c / jieqi.h。

    python3 tools/gen_jieqi.py

原厂那套是"每年一张位表 + 每月两个基准日"的算法（作者赖皮，2007）：

    YearMonthBit[160]  2000~2050 每年 3 个字节，24 个节气各 1 bit
                       （bit=1 表示这一天要 +1/-1 修正）
    days[24]           每个节气所在半月的基准日
    GetJieQi(y,m,d)    返回**该月最近那个节气**的日期（d<15 → 上半月的那个）

它跟农历一样是硬件无关的纯数据/纯算术，所以直接搬（只改写成我们的命名和风格）。
有效范围 2000~2050，跟农历表一致。
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
SRC = ('/Users/mac/Documents/Codex/2026-09-15/a/work/github/epd-nrf5-user/'
       'GUI/Lunar.c')
OUT_C = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img', 'jieqi.c')
OUT_H = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img', 'jieqi.h')


def read_tables():
    s = open(SRC, encoding='utf-8-sig').read()
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)
    s = re.sub(r'//[^\n]*', '', s)
    out = {}

    m = re.search(r'YearMonthBit\[\s*\d*\s*\]\s*=\s*\{(.*?)\};', s, re.S)
    assert m, 'YearMonthBit'
    out['bit'] = [int(t, 0) for t in re.findall(r'0x[0-9a-fA-F]+|\d+', m.group(1))]

    m = re.search(r'days\[\s*24\s*\]\s*=\s*\{(.*?)\};', s, re.S)
    assert m, 'days'
    out['days'] = [int(t, 0) for t in re.findall(r'0x[0-9a-fA-F]+|\d+', m.group(1))]

    m = re.search(r'JieQiStr\[\s*24\s*\]\s*\[[^\]]*\]\s*=\s*\{(.*?)\};', s, re.S)
    assert m, 'JieQiStr'
    out['names'] = re.findall(r'"([^"]*)"', m.group(1))

    return out


def main():
    t = read_tables()
    # 原厂那只数组声明成 [160]，实际只初始化了 153 个（2000~2050 共 51 年 x 3 字节）——
    # 我们就用这 153 个，年份范围按它算
    assert len(t['bit']) % 3 == 0, len(t['bit'])
    assert len(t['days']) == 24, len(t['days'])
    assert len(t['names']) == 24, len(t['names'])
    print('年份位表 %d 字节（2000~%d）、基准日 %d 个、节气名 %d 个'
          % (len(t['bit']), 2000 + len(t['bit']) // 3 - 1, len(t['days']),
             len(t['names'])))

    with open(OUT_H, 'w', encoding='utf-8') as f:
        f.write('''/*
 * 二十四节气（数据表和算法都从原厂固件的 GUI/Lunar.c 生成，见 tools/gen_jieqi.py）
 *
 * 用法：先算"这个月当前半月的节气序号"，再看那天是不是节气日：
 *      uint8_t idx  = ZK_JIEQI_IDX(mon, day);      // (mon-1)*2 + (day >= 15)
 *      uint8_t date = zk_jieqi_date(year, mon, day);
 *      if (date == day) -> 今天就是 zk_jieqi_name[idx] 这个节气
 *
 * 可用范围 %d ~ %d 年（超出返回 0，调用方当"不是节气"处理）。
 */
#ifndef __ZK_JIEQI_H__
#define __ZK_JIEQI_H__

#include <stdint.h>

#define ZK_JIEQI_YEAR_MIN  %d
#define ZK_JIEQI_YEAR_MAX  %d

/* 一个月两个节气：上半月是偶数序、下半月是奇数序 */
#define ZK_JIEQI_IDX(mon, day)   (uint8_t)(((mon) - 1) * 2 + ((day) >= 15 ? 1 : 0))

/* 24 个节气名（UTF-8，两个字） */
extern const char *const zk_jieqi_name[24];

/* 该月当前半月的节气日期（1..31）；年份超出范围返回 0 */
uint8_t zk_jieqi_date(uint16_t year, uint8_t mon, uint8_t day);

#endif /* __ZK_JIEQI_H__ */
''' % (2000, 2000 + len(t['bit']) // 3 - 1, 2000,
       2000 + len(t['bit']) // 3 - 1))

    rows = []
    for i in range(0, len(t['bit']), 12):
        rows.append('    ' + ', '.join('0x%02X' % v for v in t['bit'][i:i + 12]) + ',')
    days = []
    for i in range(0, 24, 6):
        days.append('    ' + ', '.join('%d' % v for v in t['days'][i:i + 6]) + ',')

    with open(OUT_C, 'w', encoding='utf-8') as f:
        f.write('''/*
 * 二十四节气 —— 见 jieqi.h。表从原厂 GUI/Lunar.c 生成，别手改。
 */
#include "jieqi.h"

/* 2000~%d 年，每年 3 个字节：24 个节气各 1 bit（1 = 基准日要修正） */
static const uint8_t s_year_bit[%d] = {
%s
};

/* 每个月两个节气的基准日（上半月、下半月） */
static const uint8_t s_day_base[24] = {
%s
};

const char *const zk_jieqi_name[24] = {
%s
};

uint8_t zk_jieqi_date(uint16_t year, uint8_t mon, uint8_t day)
{
    uint8_t bak, value, jq, date;

    if (year < ZK_JIEQI_YEAR_MIN || year > ZK_JIEQI_YEAR_MAX)
    {
        return 0;
    }
    if (mon < 1 || mon > 12)
    {
        return 0;
    }

    jq = ZK_JIEQI_IDX(mon, day);
    bak = s_year_bit[(uint32_t)(year - ZK_JIEQI_YEAR_MIN) * 3u + (jq >> 3)];
    value = (uint8_t)((bak << (jq & 7)) & 0x80u);

    date = s_day_base[jq];
    if (value != 0)
    {
        /* 这四个节气在 2044 年之前是"加一天"，之后改成"减一天"
           （原厂代码里就是这么写的，照搬） */
        if ((jq == 1 || jq == 11 || jq == 18 || jq == 21) && year < 2044)
        {
            date++;
        }
        else
        {
            date--;
        }
    }
    return date;
}
''' % (2000 + len(t['bit']) // 3 - 1, len(t['bit']), '\n'.join(rows),
       '\n'.join(days),
       '\n'.join('    "%s",' % n for n in t['names'])))

    print('写出 %s' % OUT_H)
    print('写出 %s' % OUT_C)


if __name__ == '__main__':
    main()
