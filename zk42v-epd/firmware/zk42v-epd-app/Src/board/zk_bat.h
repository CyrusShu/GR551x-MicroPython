/*
 * 电池电压 + 片内温度（GR5513 的 ADC 内部通道：VBAT / TMP）。
 *
 * 为什么现在能做：这颗芯片内部就把电池分压接到了 ADC 的一个内部通道
 * （`ADC_INPUT_SRC_BAT`），温度也是内部通道（`ADC_INPUT_SRC_TMP`）——
 * 不用外接任何东西。SDK 的预编译库里已经带了现成的两步接口
 * （`hal_adc_vbat_init/read`、`hal_adc_temp_init/read`，返回 double）。
 *
 * ⚠ 坑（build 42 才搞明白，之前电压一直显示 2.59V）：**ADC 只有一个配置寄存器**
 *   `AON->SNSADC_CFG`，通道（CHN_P/CHN_N）、参考（REF_VALUE）、单端使能全在里面，
 *   **谁最后调 hal_adc_init() 就听谁的**。而 `hal_adc_vbat_read()` 自己
 *   只翻一下 VBAT_EN 位、`hal_adc_poll_for_conversion()` 也不重选通道 ——
 *   于是"先 vbat_init 再 temp_init"这个顺序，会让之后每一次"读电池"其实都在
 *   读**温度二极管**（转换出来 ~0.67V 再套电池公式 ≈ 2.6V，看着还挺像那么回事）。
 *   修法：**每次读之前重新 init 自己要用的那个通道**（见 zk_bat_poll）。
 *
 * 另外 SDK 那个 vbat api 用的参考是 0.85V，配上内部 27/7 的分压比，
 * 满量程只有 ~3.28V —— 想量 3.0~4.2V 的锂电会削顶。所以这里自己开了一个
 * **1.28V 参考**的通道（满量程 ~4.9V），用同一颗芯片的出厂校准
 * （`sys_adc_trim_get()` 里 1.2V 那一对 slope/offset）。
 *
 * 单位约定：对外一律用整数，免得为了打印浮点再把 printf 拖进来。
 */
#ifndef __ZK_BAT_H__
#define __ZK_BAT_H__

#include <stdint.h>

/* 电池读数的有效范围（毫伏）。超出就当读失败——比报一个假数字强。 */
#define ZK_BAT_MV_MIN   2000
#define ZK_BAT_MV_MAX   5000

/* 多久读一次（毫秒）。ADC 读一次要几毫秒，没必要每次循环都读。 */
#define ZK_BAT_PERIOD_MS  60000u

void     zk_bat_init(void);            /* 开机调一次：初始化 ADC 的两个内部通道 */
void     zk_bat_poll(uint32_t now_ms); /* 空闲循环里调；内部自己限速 */
void     zk_bat_trigger(void);         /* 让下一次 zk_bat_poll() 跳过限速、立刻读一次
                                          （BLE 命令 0x72 用它来"扫电压/挑电量图标"） */

int      zk_bat_mv(void);              /* 电池毫伏（1.28V 参考那条路）；-1 = 还没读到 */
int      zk_bat_pct(void);             /* 0..100（按 **CR2450** 的分段曲线：3.0V=100% …
                                          2.5V=0%）；-1 = 无效。表在 zk_bat.c 里，
                                          tools/bat_sheet.py 会把它抠出来画对照图 */
int      zk_bat_temp_c10(void);        /* 温度 ×10（26.4℃ -> 264）；INT16_MIN = 无效 */

uint32_t zk_bat_reads(void);           /* 成功读了几次 */
uint32_t zk_bat_errs(void);            /* 读失败（或超范围）几次 */
uint32_t zk_bat_last_ms(void);         /* 最后一次成功读取的 zk_tick_ms() */

/* ---- 给状态块用的诊断量（build 42 加，见 README「电压为什么显示 2.59V」）---- */
int      zk_bat_raw(void);             /* 原始 ADC 平均值（0..4095）：不算公式，直接看码值 */
int      zk_bat_mv_sdk(void);          /* SDK 那个 api（0.85V 参考）读出来的毫伏 */
int      zk_bat_mv_own(void);          /* 我们自己的路（1.28V 参考）算出来的毫伏（原值） */
uint32_t zk_bat_cfg(void);             /* 读完之后的 AON->SNSADC_CFG（通道/参考都在里面） */
uint32_t zk_bat_trim08(void);          /* 出厂校准 0.85V 参考那对：slope<<16 | offset */
uint32_t zk_bat_trim12(void);          /* 出厂校准 1.28V 参考那对：slope<<16 | offset */
uint32_t zk_bat_trim_rc(void);         /* sys_adc_trim_get() 的返回值（0 = 成功读到校准） */

#endif /* __ZK_BAT_H__ */
