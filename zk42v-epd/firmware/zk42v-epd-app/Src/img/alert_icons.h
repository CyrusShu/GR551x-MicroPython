/*
 * 天气预警图标（20x20 的 1 位点阵）—— 由 tools/gen_alert_icons.py 生成，别手改。
 *
 * 画在哪：表头那一格原本是"天气图标"的位置；**有预警时改画预警图标**
 * （见 Src/img/zkgui.c 的 draw_header），颜色跟预警名一致（≥黄色用红）。
 *
 * 谁挑哪一张：基站经 BLE 0x7D 把和风的预警编号送下来（1003 暴雨 / 1014 雷电…），
 * 固件用 zk_alert_icon() 查表；认不出的编号走下标 0 的"通用预警"。
 */
#ifndef __ZK_ALERT_ICONS_H__
#define __ZK_ALERT_ICONS_H__

#include <stdint.h>

#define ZK_ALERT_ICON_W     20
#define ZK_ALERT_ICON_H     20
#define ZK_ALERT_ICON_IDX_BASE 0        /* 0 = 通用预警（兜底） */
#define ZK_ALERT_ICON_IDX_RAIN 1        /* 1003 暴雨 */
#define ZK_ALERT_ICON_IDX_LIGHT 2       /* 1014 雷电 */
#define ZK_ALERT_ICON_IDX_TYPHOON 3     /* 1001 台风 */
#define ZK_ALERT_ICON_IDX_HAIL 4        /* 1015 冰雹 */
#define ZK_ALERT_ICON_IDX_HEAT 5        /* 1009 高温 */
#define ZK_ALERT_ICON_NUM   6

/* 和风预警编号 -> 20x20 点阵（每行 3 字节，MSB first）；认不出返回通用预警 */
const uint8_t *zk_alert_icon(int qweather_code);

#endif /* __ZK_ALERT_ICONS_H__ */
