/*
 * 电池电压 + 片内温度（GR5513 的 ADC 内部通道：VBAT / TMP）。
 *
 * 为什么现在能做：这颗芯片内部就把电池分压接到了 ADC 的一个内部通道
 * （`ADC_INPUT_SRC_BAT`），温度也是内部通道（`ADC_INPUT_SRC_TMP`）——
 * 不用外接任何东西。SDK 的预编译库里**已经带了现成的两步接口**
 * （`hal_adc_vbat_init/read`、`hal_adc_temp_init/read`，返回 double），
 * 所以这里只是包一层：限速、判有效范围、把结果存成整数给状态块和页面用。
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

int      zk_bat_mv(void);              /* 电池毫伏；-1 = 还没有有效读数 */
int      zk_bat_pct(void);             /* 0..100（按 3.0V=0% / 4.2V=100%）；-1 = 无效 */
int      zk_bat_temp_c10(void);        /* 温度 ×10（26.4℃ -> 264）；INT16_MIN = 无效 */

uint32_t zk_bat_reads(void);           /* 成功读了几次 */
uint32_t zk_bat_errs(void);            /* 读失败（或超范围）几次 */
uint32_t zk_bat_last_ms(void);         /* 最后一次成功读取的 zk_tick_ms() */

#endif /* __ZK_BAT_H__ */
