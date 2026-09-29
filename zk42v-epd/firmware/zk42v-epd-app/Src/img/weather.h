/*
 * 天气图标 + 天气文字 —— 自动生成，别手改（改 tools/gen_weather.py 再跑）。
 *
 * 图标：20x20 的 1 位点阵，方案 "mix" —— 从开源图标字体渲染，不是手画的。
 *       Font Awesome 6 Free (solid)（SIL OFL 1.1（字体）/ CC BY 4.0（图标））
 *       Material Symbols (Google)（Apache License 2.0）
 *       来源见 tools/gen_weather.py 与 README 的「第三方素材」。
 * 文字：文泉驿点阵宋体 12pt（16x16、笔画 1px），单独一张表。
 *
 * 天气码（BLE 命令 0x71，跟网页对齐）：
 *   0 不显示  1 晴  2 多云  3 阴  4 小雨  5 大雨  6 雷阵雨  7 雪  8 雾  9 风
 *
 * 为什么天气要手机下发：这块板子上没有温度/天气传感器（原厂固件里连 I2C
 * 都没有），屏上要显示"天气"就只能从外部来。
 */
#ifndef __ZK_WEATHER_H__
#define __ZK_WEATHER_H__

#include <stdint.h>

#define ZK_WX_NONE      0u    /* 不显示 */
#define ZK_WX_SUNNY       1u
#define ZK_WX_CLOUDY      2u
#define ZK_WX_OVERCAST    3u
#define ZK_WX_LIGHTRAIN   4u
#define ZK_WX_HEAVYRAIN   5u
#define ZK_WX_THUNDER     6u
#define ZK_WX_SNOW        7u
#define ZK_WX_FOG         8u
#define ZK_WX_WIND        9u
#define ZK_WX_MAX       9u

#define ZK_WX_ICON_W    20
#define ZK_WX_ICON_H    20
#define ZK_WX_TEXT_W    16
#define ZK_WX_TEXT_H    16

/* 天气码 -> 中文名（UTF-8，给日志/通知用；未知码返回 "?"） */
const char *zk_weather_name(uint8_t code);

/* 天气码 -> 文字（unicode 码点数组，0 结尾；码 0 = 空） */
const uint32_t *zk_weather_text(uint8_t code);

/* 天气码 -> 20x20 的 1 位图标（每行 3 字节，MSB first）；码非法返回 0 */
const uint8_t *zk_weather_icon(uint8_t code);

/* 码点 -> 16x16 的文字点阵（每行 2 字节）；表里没有返回 0 */
const uint8_t *zk_weather_glyph(uint32_t cp);

#endif /* __ZK_WEATHER_H__ */
