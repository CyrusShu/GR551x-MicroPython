/*
 * 见 zk_bat.h —— 这里干四件事：
 *   1) 开机初始化 ADC 的两个内部通道（VBAT / TMP）
 *   2) **每次读之前重新 init 自己要用的那个通道**（ADC 只有一个配置寄存器，
 *      两个通道共用；不重选就会读到别人的通道 —— 这就是 build 41 之前
 *      "电池电压"一直显示 2.59V 的原因，详见 zk_bat.h 的头注释）
 *   3) 用 1.28V 参考自己读一遍 VBAT（满量程 ~4.9V，装得下 3.0~4.2V 的锂电），
 *      顺便把**原始码值**记下来 —— 状态块里能看见，好核对换算对不对
 *   4) 限速（默认 60 秒一次）、把 double 收敛成整数
 *
 * ⚠ 用的头文件都是 SDK 里的（drivers/inc/hal/…、components/sdk/…），
 *   实现在预编译的 libble_sdk.a / ROM 里 ——**不用自己写 ADC 时序**。
 */
#include "zk_bat.h"
#include "zk_epd_svc.h"                      /* zk_tick_ms() */

#include <string.h>

#include "gr55xx_hal_adc.h"                  /* adc_handle_t / hal_adc_init / poll */
#include "gr55xx_ll_adc.h"                   /* ll_adc_enable_vbat / disable */
#include "gr55xx_sys.h"                      /* sys_adc_trim_get / adc_trim_info_t */
#include "gr55xx_hal_adc_vbat_api.h"         /* hal_adc_vbat_init/read */
#include "gr55xx_hal_adc_temp_api.h"         /* hal_adc_temp_init/read */

/* VBAT 那一路的内部换算系数：ADC 输入 = 电池 / (27/7)。
   SDK 的 vbat api 里也是这个常数（见 projects/peripheral/adc/adc_temp_vbat 那份源码）。 */
#define ZK_ADC_VBAT_DIV  (27.0 / 7.0)

static uint8_t  s_inited;
static int      s_mv   = -1;                 /* 1.28V 参考那条路（对外就是它） */
static int      s_mv_sdk = -1;               /* SDK api（0.85V 参考）读出来的，留作对比 */
static int      s_raw  = -1;                 /* 原始 ADC 平均值 0..4095 */
static int      s_t10  = INT16_MIN;
static uint32_t s_reads;
static uint32_t s_errs;
static uint32_t s_last_ms;
static uint32_t s_cfg;                       /* 读完之后的 AON->SNSADC_CFG */
static uint32_t s_trim08;
static uint32_t s_trim12;
static uint32_t s_trim_rc = 0xFFFFFFFFu;
static uint8_t  s_force;                     /* 被 zk_bat_trigger() 置 1：下一次 poll 跳过限速 */

/* 我们自己的 VBAT 通道（1.28V 参考）。SDK 的 vbat api 用的是它自己那份 handle。 */
static adc_handle_t s_bat_handle;
static double       s_bat_offset;
static double       s_bat_slope;

void zk_bat_init(void)
{
    adc_trim_info_t t;

    /* 先把两个通道各 init 一次：这一步之后 AON->SNSADC_CFG 停在"温度"通道上，
       所以后面每次读都必须重新 init（这就是那个 bug）。 */
    hal_adc_vbat_init();
    hal_adc_temp_init();

    /* 把出厂校准读出来（每颗芯片一份，存在 ROM 的 trim 区）：
       SDK 内部的算法是 V = (raw - offset) / (-slope) * (27/7)，offset/slope 按参考分档。 */
    memset(&t, 0, sizeof(t));
    s_trim_rc = (uint32_t)sys_adc_trim_get(&t);
    if (s_trim_rc == 0u)
    {
        s_trim08 = ((uint32_t)t.slope_int_0p8 << 16) | t.offset_int_0p8;
        s_trim12 = ((uint32_t)t.slope_int_1p2 << 16) | t.offset_int_1p2;
        s_bat_offset = (double)t.offset_int_1p2;
        s_bat_slope  = (double)t.slope_int_1p2;
    }

    /* 自己那份 handle：通道 = BAT，参考 = 1.28V（满量程 ≈ 1.28 * 27/7 ≈ 4.94V） */
    s_bat_handle.init.channel_n  = ADC_INPUT_SRC_BAT;
    s_bat_handle.init.channel_p  = ADC_INPUT_SRC_BAT;
    s_bat_handle.init.input_mode = ADC_INPUT_SINGLE;
    s_bat_handle.init.ref_source = ADC_REF_SRC_BUF_INT;
    s_bat_handle.init.ref_value  = ADC_REF_VALUE_1P2;
    s_bat_handle.init.clock      = ADC_CLK_1P6M;

    s_inited = 1;
}

/* 自己读一次 VBAT：**先选通道/参考**，再转换。成功返回原始平均值（0..4095），失败 -1 */
static int bat_read_raw(void)
{
    uint16_t buf[16];
    uint32_t sum = 0;
    int      i;

    hal_adc_init(&s_bat_handle);             /* 把 BAT + 1.28V 写进 AON->SNSADC_CFG */
    ll_adc_enable_vbat();                    /* SNSADC_CFG 的 VBAT_EN */
    hal_adc_poll_for_conversion(&s_bat_handle, buf, 16);
    ll_adc_disable_vbat();

    for (i = 8; i < 16; i++)                 /* 前 8 个是切换通道后的建立期，扔掉 */
    {
        sum += buf[i];
    }
    return (int)((sum >> 3) & 0xFFFFu);
}

void zk_bat_poll(uint32_t now_ms)
{
    double v;
    double t;

    if (!s_inited)
    {
        return;
    }
    /* 限速：第一次读也要等够一个周期，免得开机就跟 BLE 初始化抢总线。
       s_force 是 BLE 命令 0x72 要求"马上读一次"（扫电压 / 看电量图标时用）。 */
    if (!s_force && s_last_ms == 0u && now_ms < 2000u)
    {
        return;
    }
    if (!s_force && s_last_ms != 0u && (uint32_t)(now_ms - s_last_ms) < ZK_BAT_PERIOD_MS)
    {
        return;
    }
    s_force = 0;

    /* ---- 1) SDK 那个 api（0.85V 参考）—— 读之前**必须**重新 init -------------- */
    hal_adc_vbat_init();
    v = hal_adc_vbat_read();
    if (v > 1.0 && v < 6.0)
    {
        s_mv_sdk = (int)(v * 1000.0 + 0.5);
    }

    /* ---- 2) 我们自己的（1.28V 参考）+ 原始码值 --------------------------------- */
    s_raw = bat_read_raw();
    if (s_raw >= 0 && s_bat_slope != 0.0)
    {
        double volts = (((double)s_raw - s_bat_offset) / (-s_bat_slope)) * ZK_ADC_VBAT_DIV;
        int    mv    = (int)(volts * 1000.0 + 0.5);

        if (mv >= ZK_BAT_MV_MIN && mv <= ZK_BAT_MV_MAX)
        {
            s_mv = mv;
            s_reads++;
        }
        else
        {
            s_errs++;                        /* 换算出来不像电池：留个痕，状态块里能看到 */
        }
    }
    else
    {
        s_errs++;
    }

    /* ---- 3) 温度：同样先重新 init 温度通道（不然读到的还是上一次那个通道）------ */
    hal_adc_temp_init();
    t = hal_adc_temp_read();
    if (t > -40.0 && t < 125.0)
    {
        s_t10 = (int)(t * 10.0 + (t < 0 ? -0.5 : 0.5));
    }
    else
    {
        s_errs++;
    }

    /* ---- 4) 把此刻的通道/参考寄存器记下来，status.sh 会译出来 ------------------ */
    s_cfg = AON->SNSADC_CFG;

    s_last_ms = now_ms ? now_ms : 1u;
}

int zk_bat_mv(void)
{
    /* 自己那条路要是算出个离谱值（参考档/校准档选错时就会），
       就把 SDK 那个数拿出来顶着 —— 屏幕上宁可用旧公式，也别空着。 */
    if (s_mv > 0)
    {
        return s_mv;
    }
    return s_mv_sdk;
}

int zk_bat_mv_own(void)
{
    return s_mv;                             /* 自己那条路（1.28V 参考）算出来的原值 */
}

int zk_bat_pct(void)
{
    int mv = zk_bat_mv();

    if (mv < 0)
    {
        return -1;
    }
    /* 锂电很粗的线性近似：3.0V -> 0%，4.2V -> 100%。
       我们只是画个图标，不做电量计。 */
    {
        int pct = (mv - 3000) * 100 / 1200;

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

int zk_bat_raw(void)
{
    return s_raw;
}

int zk_bat_mv_sdk(void)
{
    return s_mv_sdk;
}

uint32_t zk_bat_cfg(void)
{
    return s_cfg;
}

uint32_t zk_bat_trim08(void)
{
    return s_trim08;
}

uint32_t zk_bat_trim12(void)
{
    return s_trim12;
}

uint32_t zk_bat_trim_rc(void)
{
    return s_trim_rc;
}

void zk_bat_trigger(void)
{
    s_force = 1;
}
