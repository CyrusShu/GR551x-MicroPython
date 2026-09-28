#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
农历表生成器：从**原厂**那份 GUI/Lunar.c 里把两张数据表原样抽出来，
生成我们自己的 Src/img/lunar.c（只留我们用的部分：日期换算 + 月/日中文名）。

    python3 tools/gen_lunar.py

为什么用生成而不是手抄：那两张表是二进制位域（闰月 + 每个月 29/30 天），
手抄错一位就会算错某一年——生成的话永远跟原厂一致，也顺便省掉不用的一大堆
（节气/生肖/干支/月建）代码。

表的结构（看 Lunar.c 的 LUNAR_SolarToLunar）：
    solar_1_1[i]        = 第 i 年"农历正月初一"对应的公历日期，
                          编码 (公历年 << 9) | (月 << 5) | 日；**第 0 项是基年(1997)本身**
    lunar_month_days[i] = 第 i 年的月长位图 + 闰月号（bit13..16 是闰月，0 = 不闰）
可用年份：2000 ~ 2050（原厂算法里带 `solar_year - base < 3` 的保护，所以前三年不用）
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
SRC = ('/Users/mac/Documents/Codex/2026-09-15/a/work/github/epd-nrf5-user/'
       'GUI/Lunar.c')
OUT_C = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img', 'lunar.c')
OUT_H = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img', 'lunar.h')


def read_tables():
    s = open(SRC, encoding='utf-8-sig').read()
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)     # 里面有一大段被注释掉的表
    s = re.sub(r'//[^\n]*', '', s)
    out = {}
    for name in ('solar_1_1', 'lunar_month_days'):
        m = re.search(name + r'\[\]\s*=\s*\{(.*?)\};', s, re.S)
        assert m, name
        vals = [int(t, 0) for t in re.findall(r'0x[0-9a-fA-F]+|\d+', m.group(1))]
        out[name] = vals
    # 字符串表（中文名）
    for name in ('Lunar_MonthString', 'Lunar_MonthLeapString', 'Lunar_DateString'):
        # 形如 `[13][7] = {...}`：两个维度都要吃掉
        m = re.search(name + r'\[[^\]]*\](\[[^\]]*\])?\s*=\s*\{(.*?)\};', s, re.S)
        assert m, name
        out[name] = re.findall(r'"([^"]*)"', m.group(2))
    return out


def rows(vals, indent='    '):
    """按每行 12 个排一下，好读"""
    out = []
    for i in range(0, len(vals), 12):
        out.append(indent + ', '.join('0x%08X' % v for v in vals[i:i + 12]) + ',')
    return '\n'.join(out)


def main():
    t = read_tables()
    base = t['solar_1_1'][0]
    n = len(t['solar_1_1'])
    assert len(t['lunar_month_days']) == n, (n, len(t['lunar_month_days']))
    print('基年 %d，共 %d 项（覆盖 %d~%d）' % (base, n, base + 3, base + n - 1))

    with open(OUT_H, 'w', encoding='utf-8') as f:
        f.write('''/*
 * 农历换算（数据表从原厂固件的 GUI/Lunar.c 生成，见 tools/gen_lunar.py）
 *
 * 只做一件事：给一个公历日期，算出农历的月/日（含闰月标记）。
 * 可用范围：%d ~ %d 年；超出范围时 mon/day 返回 0（调用方画 "--"）。
 */
#ifndef __ZK_LUNAR_H__
#define __ZK_LUNAR_H__

#include <stdint.h>

#define ZK_LUNAR_YEAR_MIN  %d
#define ZK_LUNAR_YEAR_MAX  %d

/* 农历月/日的中文名（索引即农历月号 1..12 / 日号 1..30；0 项是占位） */
extern const char zk_lunar_month_cn[13][7];
extern const char zk_lunar_leap_cn[2][4];
extern const char zk_lunar_day_cn[31][7];

/* 公历 -> 农历。返回 0 = 成功；-1 = 年份超出表的范围 */
int zk_lunar_from_solar(uint16_t solar_year, uint8_t solar_month,
                        uint8_t solar_date, uint8_t *out_mon,
                        uint8_t *out_day, uint8_t *out_is_leap);

#endif /* __ZK_LUNAR_H__ */
''' % (base + 3, base + n - 1, base + 3, base + n - 1))

    with open(OUT_C, 'w', encoding='utf-8') as f:
        f.write('/*\n')
        f.write(' * 农历数据表 + 换算 —— **自动生成，别手改**。\n')
        f.write(' * 数据来源：原厂固件那份 GUI/Lunar.c（生成脚本 tools/gen_lunar.py）。\n')
        f.write(' * 作者保留原样：只去掉我们用不到的节气/生肖/干支/月建那几段。\n')
        f.write(' */\n')
        f.write('#include "lunar.h"\n\n')
        f.write('const char zk_lunar_month_cn[13][7] = {\n')
        f.write('    %s\n};\n\n' % ', '.join('"%s"' % s for s in t['Lunar_MonthString']))
        f.write('const char zk_lunar_leap_cn[2][4] = {\n')
        f.write('    %s\n};\n\n' % ', '.join('"%s"' % s for s in t['Lunar_MonthLeapString']))
        f.write('const char zk_lunar_day_cn[31][7] = {\n')
        for i in range(0, len(t['Lunar_DateString']), 8):
            f.write('    %s,\n' % ', '.join('"%s"' % s for s in t['Lunar_DateString'][i:i + 8]))
        f.write('};\n\n')
        f.write('/* 第 i 年农历正月初一对应的公历日期：(年<<9)|(月<<5)|日。'
                '第 0 项是基年本身 */\n')
        f.write('static const uint32_t s_solar_1_1[%d] = {\n%s\n};\n\n' % (n, rows(t['solar_1_1'])))
        f.write('/* 第 i 年的月长位图 + 闰月号（bit13..16） */\n')
        f.write('static const uint32_t s_lunar_month_days[%d] = {\n%s\n};\n\n'
                % (n, rows(t['lunar_month_days'])))
        f.write('''static uint32_t bit_int(uint32_t data, uint8_t len, uint8_t shift)
{
    return (data & (((1u << len) - 1u) << shift)) >> shift;
}

/* 公历 -> 儒略日式的天数（只用来算差值，跟原厂同一套公式） */
static uint16_t solar_to_int(uint16_t y, uint8_t m, uint8_t d)
{
    m = (uint8_t)((m + 9) % 12);
    y = (uint16_t)(y - m / 10);
    return (uint16_t)(365 * y + y / 4 - y / 100 + y / 400 + (m * 306 + 5) / 10 + (d - 1));
}

int zk_lunar_from_solar(uint16_t solar_year, uint8_t solar_month,
                        uint8_t solar_date, uint8_t *out_mon,
                        uint8_t *out_day, uint8_t *out_is_leap)
{
    const uint32_t base = s_solar_1_1[0];
    uint8_t  i, lunar_m, leap, dm;
    uint16_t year_index, offset, y;
    uint32_t solar_data, solar11, days;
    uint8_t  m, d;

    *out_mon = 0;
    *out_day = 0;
    *out_is_leap = 0;

    if (solar_month < 1 || solar_month > 12 || solar_date < 1 || solar_date > 31)
    {
        return -1;
    }
    if (solar_year < base + 3 || solar_year > base + (sizeof(s_solar_1_1) / 4) - 1)
    {
        return -1;                       /* 表外的年份：让调用方画 "--" */
    }

    year_index = (uint16_t)(solar_year - base);
    solar_data = ((uint32_t)solar_year << 9) | ((uint32_t)solar_month << 5) | solar_date;
    if (s_solar_1_1[year_index] > solar_data)
    {
        year_index--;
    }
    solar11 = s_solar_1_1[year_index];
    y = (uint16_t)bit_int(solar11, 12, 9);
    m = (uint8_t)bit_int(solar11, 4, 5);
    d = (uint8_t)bit_int(solar11, 5, 0);
    offset = (uint16_t)(solar_to_int(solar_year, solar_month, solar_date) -
                        solar_to_int(y, m, d));

    days = s_lunar_month_days[year_index];
    leap = (uint8_t)bit_int(days, 4, 13);

    lunar_m = 1;
    offset++;
    for (i = 0; i < 13; i++)
    {
        dm = (uint8_t)((bit_int(days, 1, 12 - i) == 1) ? 30 : 29);
        if (offset > dm)
        {
            lunar_m++;
            offset = (uint16_t)(offset - dm);
        }
        else
        {
            break;
        }
    }

    if (leap != 0 && lunar_m > leap)
    {
        if (lunar_m == leap + 1)
        {
            *out_is_leap = 1;
        }
        lunar_m--;
    }

    *out_mon = lunar_m;
    *out_day = (uint8_t)offset;
    return 0;
}
''')
    print('写出 %s / %s' % (OUT_C, OUT_H))


if __name__ == '__main__':
    main()
