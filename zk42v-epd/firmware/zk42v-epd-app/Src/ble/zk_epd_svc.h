/*
 * B2-A.2：EPD 服务（对齐 tsl0922/EPD-nRF5 那套网页协议）
 *
 * 服务的形状跟他原厂固件一模一样（UUID 也一字不差）：
 *   62750001-… 主服务
 *   62750002-… 写 + 通知（网页往这儿写命令和图块）
 *   62750003-… 读版本（网页读它判断固件新旧，>= 0x16 才走新流程）
 *
 * 用法（都在 main / zk_ble 里挂钩子）：
 *   zk_epd_svc_init()          协议栈 STACK_INIT 之后调一次，建 GATT 库
 *   zk_epd_svc_on_connect(idx) GAPC 连上了
 *   zk_epd_svc_on_disconnect() 断了
 *   zk_epd_svc_on_mtu(mtu)     收到 MTU 协商结果
 *   zk_epd_svc_on_read/write() GATTS 的读写请求（转发过来就行，回包在这里做）
 *   zk_epd_svc_poll(now_ms)    空闲循环里转：屏的重活（初始化/写图/刷新）在这儿做
 *   zk_panel_ensure_init()     需要「屏已经初始化过」的地方自己叫（B2-B 那条路也用）
 */
#ifndef __ZK_EPD_SVC_H__
#define __ZK_EPD_SVC_H__

#include <stdint.h>

void zk_epd_svc_init(void);
void zk_epd_svc_poll(uint32_t now_ms);

void zk_epd_svc_on_connect(uint8_t conn_idx);
void zk_epd_svc_on_disconnect(void);
void zk_epd_svc_on_mtu(uint16_t mtu);

void zk_epd_svc_on_read(uint8_t conn_idx, uint16_t handle);
void zk_epd_svc_on_write(uint8_t conn_idx, uint16_t handle,
                         const uint8_t *value, uint16_t length);

/* 屏没初始化过就初始化一遍（做完会把屏的脚松开）。返回 1 = 现在已初始化 */
int  zk_panel_ensure_init(void);

/* 毫秒时基（由 main.c 提供，DWT 算的）。量"这次写图+刷新花了多久"用。 */
uint32_t zk_tick_ms(void);

#endif /* __ZK_EPD_SVC_H__ */
