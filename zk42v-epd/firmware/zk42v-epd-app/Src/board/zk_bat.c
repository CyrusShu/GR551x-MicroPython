/*
 * 见 zk_bat.h。这里只干三件事：初始化、限速读、把 double 收敛成整数。
 *
 * ⚠ 用的两个头文件是 SDK 里就有的（drivers/inc/hal/gr55xx_hal_adc_*_api.h），
 *   实现在预编译的 libble_sdk.a 里（对象名 gr55xx_hal_adc_vbat_api.o /
 *   gr55xx_hal_adc_temp_api.o）——**不用自己写 ADC 时序**。
 */
#include "zk_bat.h"
#include "zk_epd_svc.h"                      /* zk_tick_ms() */

#include "gr55xx_hal_adc_vbat_api.h"         /* hal_adc_vbat_init/read */
#include "gr55xx_hal_adc_temp_api.h"         /* hal_adc_temp_init/read */

static uint8_t  s_inited;
static int      s_mv   = -1;
static int      s_t10  = INT16_MIN;
static uint32_t s_reads;
static uint32_t s_errs;
static uint32_t s_last_ms;

void zk_bat_init(void)
{
    hal_adc_vbat_init();
    hal_adc_temp_init();
    s_inited = 1;
}

void zk_bat_poll(uint32_t now_ms)
{
    double v, t;

    if (!s_inited)
    {
        return;
    }
    /* 限速：第一次读也要等够一个周期，免得开机就跟 BLE 初始化抢总线 */
    if (s_last_ms == 0u && now_ms < 2000u)
    {
        return;
    }
    if (s_last_ms != 0u && (uint32_t)(now_ms - s_last_ms) < ZK_BAT_PERIOD_MS)
    {
        return;
    }

    v = hal_adc_vbat_read();
    if (v > 1.0 && v < 6.0)
    {
        int mv = (int)(v * 1000.0 + 0.5);

        if (mv >= ZK_BAT_MV_MIN && mv <= ZK_BAT_MV_MAX)
        {
            s_mv = mv;
            s_reads++;
        }
        else
        {
            s_errs++;                        /* 读到了但不像电池：多半是通道没接 */
        }
    }
    else
    {
        s_errs++;                            /* NaN / 0 / 离谱值都算失败 */
    }

    t = hal_adc_temp_read();
    if (t > -40.0 && t < 125.0)
    {
        s_t10 = (int)(t * 10.0 + (t < 0 ? -0.5 : 0.5));
    }
    else
    {
        s_errs++;
    }

    s_last_ms = now_ms ? now_ms : 1u;
}

int zk_bat_mv(void)
{
    return s_mv;
}

int zk_bat_pct(void)
{
    if (s_mv < 0)
    {
        return -1;
    }
    /* 锂电很粗的线性近似：3.0V -> 0%，4.2V -> 100%。
       我们只是画个图标，不做电量计。 */
    {
        int pct = (s_mv - 3000) * 100 / 1200;

        if (pct < 0)
        {
            pct = 0;
        }
        if (pct > 100)
        {
            pct = 100;
        }
        return pct;
    }
}

int zk_bat_temp_c10(void)
{
    return s_t10;
}

uint32_t zk_bat_reads(void)
{
    return s_reads;
}

uint32_t zk_bat_errs(void)
{
    return s_errs;
}

uint32_t zk_bat_last_ms(void)
{
    return s_last_ms;
}
