/*
 * 农历换算（数据表从原厂固件的 GUI/Lunar.c 生成，见 tools/gen_lunar.py）
 *
 * 只做一件事：给一个公历日期，算出农历的月/日（含闰月标记）。
 * 可用范围：2000 ~ 2051 年；超出范围时 mon/day 返回 0（调用方画 "--"）。
 */
#ifndef __ZK_LUNAR_H__
#define __ZK_LUNAR_H__

#include <stdint.h>

#define ZK_LUNAR_YEAR_MIN  2000
#define ZK_LUNAR_YEAR_MAX  2051

/* 农历月/日的中文名（索引即农历月号 1..12 / 日号 1..30；0 项是占位） */
extern const char zk_lunar_month_cn[13][7];
extern const char zk_lunar_leap_cn[2][4];
extern const char zk_lunar_day_cn[31][7];

/* 公历 -> 农历。返回 0 = 成功；-1 = 年份超出表的范围 */
int zk_lunar_from_solar(uint16_t solar_year, uint8_t solar_month,
                        uint8_t solar_date, uint8_t *out_mon,
                        uint8_t *out_day, uint8_t *out_is_leap);

#endif /* __ZK_LUNAR_H__ */
