/*
 * ZK42V 价签（GR5513BEND）板级定义
 *
 * 这里每一个数都有出处，不是猜的：
 *   屏型号码、引脚表  ← 原厂固件 flash 0x01057000 的器件配置块
 *                        （见 outputs/analysis/PANEL-zk42v.md 第 1、2 节）
 *   引脚编码          ← 原厂代码里 (port << 6) | pin
 */
#ifndef __ZK42V_BOARD_H__
#define __ZK42V_BOARD_H__

#include <stdint.h>
#include "app_io.h"

/* ---- 从 flash 0x01057000 读出来的器件配置 -------------------------------
 *   01057000  43 03 00 00 00 00 00 00 02 FF 07 03 04 05 06 18
 *   01057010  08 00 00 00 5A 4B 43 34 32 56 2D 4E FF FF FF FF
 *
 *   [0]     = 0x43 = 'C'   屏型号码（串口日志里 <67 3> 的 67）
 *   [1]     = 0x03         面板类型
 *   [8..15] = 8 根脚的编号（(port << 6) | pin 编码）
 *   [16]    = 8            型号字符串长度
 *   [20..]  = "ZKC42V-N"
 * ---------------------------------------------------------------------- */
#define ZK42V_PANEL_CODE         0x43
#define ZK42V_PANEL_TYPE_CODE    0x03
#define ZK42V_MODEL              "ZKC42V-N"

/* 屏分辨率 / 缓冲区（原厂 0x0100E764 那张表里的硬编码常量） */
#define ZK42V_EPD_WIDTH          400
#define ZK42V_EPD_HEIGHT         300
#define ZK42V_EPD_ROW_BYTES      (ZK42V_EPD_WIDTH / 8)          /* 50 */
#define ZK42V_EPD_PLANE_BYTES    (ZK42V_EPD_ROW_BYTES * ZK42V_EPD_HEIGHT)  /* 15000 */
#define ZK42V_EPD_IMG_BYTES      (ZK42V_EPD_PLANE_BYTES * 2)     /* 30000 */

/* ---- 屏的 7 根脚（GPIOA = GPIO0 = P0_x）--------------------------------
 *   原厂引脚表顺序是 {CS, 空, RST, SCLK, SDI, DC, BUSY, AUX}，
 *   注意第 2 个是 0xFF「未使用」，所以别按顺序数错。
 * ---------------------------------------------------------------------- */
#define ZK42V_PIN_CS_RAW         0x02   /* P0_2  片选，每字节拉低再拉高 */
#define ZK42V_PIN_RST_RAW        0x07   /* P0_7  复位，低 1ms 高 1ms */
#define ZK42V_PIN_SCLK_RAW       0x03   /* P0_3  时钟，全程软件翻转 */
#define ZK42V_PIN_SDI_RAW        0x04   /* P0_4  数据/命令，MSB first */
#define ZK42V_PIN_DC_RAW         0x05   /* P0_5  0=命令 1=数据 */
#define ZK42V_PIN_BUSY_RAW       0x06   /* P0_6  输入，1=控制器在忙 */
#define ZK42V_PIN_AUX_RAW        0x18   /* P0_24 屏使能 —— 原厂 pins_init 里被拉高 */

/* 换算成 app_io 的「位掩码」写法 */
#define ZK42V_PORT               APP_IO_TYPE_GPIOA
#define ZK42V_PIN_CS             APP_IO_PIN_2
#define ZK42V_PIN_RST            APP_IO_PIN_7
#define ZK42V_PIN_SCLK           APP_IO_PIN_3
#define ZK42V_PIN_SDI            APP_IO_PIN_4
#define ZK42V_PIN_DC             APP_IO_PIN_5
#define ZK42V_PIN_BUSY           APP_IO_PIN_6

/* ---- 辅助脚：原厂编号 0x18 = 24，注意它是「全局编号」不是 P0_24 --------------
 *
 * SDK 的 drivers/src/app_io.c 里，GR551X 专用的 APP_IO_TYPE_NORMAL 是这么分的：
 *     编号 0..15  -> GPIO0 的 bit 0..15
 *     编号 16..31 -> GPIO1 的 bit 0..15
 * 所以 24 = GPIO1 的 bit 8 = **P1_8**，不是 P0_24。
 *
 * 之前我们按 P0_24 走 APP_IO_TYPE_GPIOA，被 app_io_init() 直接拒绝
 * （GPIOA 分支里有一句 `if (!(pin & APP_IO_PINS_0_15)) return INVALID_PARAM;`），
 * 所以这根脚**从来没被驱动过** —— status.sh 里看到的 `GPIO 错=1` / `flags bit1`
 * 就是它。原厂 pins_init 是把这根脚拉高的（面板供电/使能），少了它屏不工作。
 */
#define ZK42V_PIN_AUX_TYPE       APP_IO_TYPE_NORMAL
#define ZK42V_PIN_AUX            APP_IO_PIN_24   /* 经 APP_IO_TYPE_NORMAL 落到 GPIO1 bit8 = P1_8 */

#endif /* __ZK42V_BOARD_H__ */
