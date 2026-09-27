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

/* 像原厂 pins_release 那样把脚交还（设回默认态，等于松开）。
 * 原厂每次刷完屏都做这一步；我们之前一直把 P1_8 钉在高电平 ——
 * 怀疑 P1_8 还兼着射频前端/天线开关，钉着它 BLE 就发不出去。 */
void epd_pins_release(void);

/* 屏复位：RST 低 1ms -> 高 1ms -> CMD 0x12 -> 等 BUSY */
void epd_reset(void);

/* 完整初始化序列（原厂 0x01010802） */
void epd_init_sequence(void);

/* 传一帧：buf 30000 字节，[0..14999] = 黑白面，[15000..29999] = 红面 */
void epd_write_image(const uint8_t *buf);

/* 刷新：CMD 0x22=0xC7 -> CMD 0x20 -> 等 BUSY 松开 */
void epd_refresh(void);

/* 同上，但可以指定 0x22 的更新控制字节，并可选先发温度（0x18/0x1A）
 *
 * 为什么要这个：原厂里有两条更新路径 ——
 *   简单那条：0x22 = 0xC7（不重载温度/LUT）
 *   另一条  ：0x18=0x80 -> 0x1A=0x55 -> 0x22 = 0xD7（重载温度/LUT）
 * 而 EPD-nRF5 / Waveshare 那套 SSD16xx 驱动用的是 0x22 = 0xF7（全量）。
 * 三种都试一遍，看屏认哪一条。 */
void epd_refresh_ex(uint8_t ctrl, int with_temp);

/* 让屏进深度睡眠（CMD 0x10=0x01）—— 测试阶段用不到 */
void epd_deep_sleep(void);

/* 等 BUSY 松开。返回 0 = 正常，-1 = 超时 */
int  epd_wait_busy(uint32_t timeout_ms);

/* 毫秒延时（能用 DWT 就用 DWT，用不了退回循环计数） */
void epd_delay_ms(uint32_t ms);
void epd_delay_us(uint32_t us);

/* 把延时/计时基准（DWT）单独初始化一次。
   不碰屏的引脚，所以 BLE-only 的固件也能有准的毫秒时基。 */
void epd_timer_init(void);

/* 裸命令/数据（网页的 SEND_CMD 0x03 / SEND_DATA 0x04 用的）。
   调用前先 epd_gpio_init()。 */
void epd_cmd_raw(uint8_t c);
void epd_data_raw(const uint8_t *d, uint32_t n);

#endif /* __EPD_ZK42V_H__ */
