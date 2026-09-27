/*
 * B2-A：BLE 外设（对齐 tsl0922/EPD-nRF5 那套协议）
 *
 * A.1（这一版）只做：起协议栈 + 广播（名字 ZK42V-EPD，广播数据里带
 *     62750001-d828-918d-fb46-b6c11c675aec 这个服务 UUID）
 *     —— 目标判据：Chrome 打开那个网页点"连接"，设备列表里能看到 ZK42V-EPD。
 * A.2 再接 GATT 服务与命令（INIT/CLEAR/REFRESH/WRITE_IMAGE）。
 */
#ifndef __ZK_BLE_H__
#define __ZK_BLE_H__

#include <stdint.h>

/* 起协议栈并开始广播（在 main 里调一次；空闲循环不阻塞） */
void zk_ble_start(void);

/* B2-A.2：空闲循环里每转一圈调一次。管的是「扫描几秒 -> 挨个试广播数据变体」
   这条状态机的兜底超时（万一协议栈一个事件都不回，也要能自己往下走）。

   参数 now_ms：单调递增的毫秒数（main.c 用 DWT 算的）。传 0 表示没有
   可用的时基 —— 那就退回「数圈数」的兜底。 */
void zk_ble_poll(uint32_t now_ms);

/* 当前有没有连着 */
int  zk_ble_connected(void);

#endif /* __ZK_BLE_H__ */
