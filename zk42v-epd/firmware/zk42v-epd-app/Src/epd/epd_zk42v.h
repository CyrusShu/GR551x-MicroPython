/*
 * ZK42V 4.2" 400x300 三色墨水屏驱动（自研固件版）
 *
 * 参数全部来自原厂固件逆向（outputs/analysis/PANEL-zk42v.md），
 * 初始化字节、缓冲区布局、刷新序列都跟原厂一字不差地重放。
 * 我们唯一新增的东西是「不关 SWD」和「往 0x3001F000 写状态」。
 */
#ifndef __EPD_ZK42V_H__
#define __EPD_ZK42V_H__

#include <stdint.h>
#include "zk42v_board.h"

/* 把 7 根脚配好（CS/RST/SCLK/SDI/DC/AUX 输出，BUSY 输入），并让它们回到空闲电平 */
void epd_gpio_init(void);

/* 屏复位：RST 低 1ms -> 高 1ms -> CMD 0x12 -> 等 BUSY */
void epd_reset(void);

/* 完整初始化序列（原厂 0x01010802） */
void epd_init_sequence(void);

/* 传一帧：buf 30000 字节，[0..14999] = 黑白面，[15000..29999] = 红面 */
void epd_write_image(const uint8_t *buf);

/* 刷新：CMD 0x22=0xC7 -> CMD 0x20 -> 等 BUSY 松开 */
void epd_refresh(void);

/* 让屏进深度睡眠（CMD 0x10=0x01）—— 测试阶段用不到 */
void epd_deep_sleep(void);

/* 等 BUSY 松开。返回 0 = 正常，-1 = 超时 */
int  epd_wait_busy(uint32_t timeout_ms);

/* 毫秒延时（能用 DWT 就用 DWT，用不了退回循环计数） */
void epd_delay_ms(uint32_t ms);
void epd_delay_us(uint32_t us);

#endif /* __EPD_ZK42V_H__ */
