/*
 * 二十四节气（数据表和算法都从原厂固件的 GUI/Lunar.c 生成，见 tools/gen_jieqi.py）
 *
 * 用法：先算"这个月当前半月的节气序号"，再看那天是不是节气日：
 *      uint8_t idx  = ZK_JIEQI_IDX(mon, day);      // (mon-1)*2 + (day >= 15)
 *      uint8_t date = zk_jieqi_date(year, mon, day);
 *      if (date == day) -> 今天就是 zk_jieqi_name[idx] 这个节气
 *
 * 可用范围 2000 ~ 2050 年（超出返回 0，调用方当"不是节气"处理）。
 */
#ifndef __ZK_JIEQI_H__
#define __ZK_JIEQI_H__

#include <stdint.h>

#define ZK_JIEQI_YEAR_MIN  2000
#define ZK_JIEQI_YEAR_MAX  2050

/* 一个月两个节气：上半月是偶数序、下半月是奇数序 */
#define ZK_JIEQI_IDX(mon, day)   (uint8_t)(((mon) - 1) * 2 + ((day) >= 15 ? 1 : 0))

/* 24 个节气名（UTF-8，两个字） */
extern const char *const zk_jieqi_name[24];

/* 该月当前半月的节气日期（1..31）；年份超出范围返回 0 */
uint8_t zk_jieqi_date(uint16_t year, uint8_t mon, uint8_t day);

#endif /* __ZK_JIEQI_H__ */
