/*
 * ZK42V 价签（GR5513BEND）自研固件 —— 第一版：把屏点亮
 * =====================================================================
 *  跑起来会发生什么（按顺序写进 0x3001F000 的状态块）：
 *    1 main 进来        2 打开 SWD        3 7 根脚配好
 *    4 屏复位发完        5 初始化序列发完   6 测试图拼好
 *    7 30000 字节传完    8 刷新完（这时屏上应该有条纹图）  9 心跳
 *
 *  跟原厂固件最大的区别：**我们不关 SWD**。
 *  原厂 APP 一接管就把调试口关了，所以以前只能抢复位后那 2 秒；
 *  刷上这版以后，调试器随时能连，随时能读 0x3001F000。
 *
 *  烧进去的位置：flash 0x0100A000（原厂 bootloader 找 APP 就在这儿）。
 *  回厂：cd outputs/pyocd && MODE=restore bash flash-write.sh
 * =====================================================================
 */
#include "zk42v_board.h"
#include "zk_dbg.h"
#include "epd_zk42v.h"
#include "testimg.h"
#include "zk_ble.h"
#include "zk_epd_svc.h"      /* B2-A.2：GATT 服务 / 推图协议 */
#include "zk_tick.h"         /* 把 CYCCNT 扩成单调毫秒（build 30 修 268 秒绕圈） */
#include "zk_bat.h"          /* 电池电压 / 片内温度（GR5513 的 ADC 内部通道） */

#include "gr55xx.h"
#include "gr55xx_sys.h"      /* sys_swd_enable() */
#include "gr55xx_pwr.h"      /* 2026-10-03 省电：pwr_mgmt_mode_set(PMR_MGMT_SLEEP_MODE) */
#include "app_timer.h"       /* 省电版的主循环节拍（建在 AON 睡眠定时器上） */
#include "zk_pwr.h"          /* 省电旋钮（ZK_PWR_SAVE / ZK_TICK_MS / ZK_ADV_INTERVAL） */

#include <string.h>          /* memset */

/* 状态块本体：链接脚本把它钉在 0x3001F000（RAM_DBG，NOLOAD，不清零） */
volatile zk_dbg_t g_dbg __attribute__((section(".dbg_status"), used));

/* 共享内存信箱（上位机用 SWD 直接写这块 RAM） */
static volatile zk_mailbox_t *const s_mb = (volatile zk_mailbox_t *)ZK_MB_ADDR;

/* 画面缓冲：直接用信箱里那块 30000 字节（见 zk_dbg.h 的 ZK_IMG_BUF 说明） */
#define s_img  ZK_IMG_BUF

/* 开机要不要先画那张「方向体检图」。
 *
 * 0（build 21 起）：**开机不碰屏**。屏的初始化改成由 BLE 命令触发
 *   （网页连上来的 INIT 0x01，或者 B2-B 那条 push-image 自己叫），
 *   好处有两个：开机到能广播只要几十毫秒；没人用屏的时候那 7 根脚
 *   一直是松开的（P1_8 夹着射频前端那条嫌疑也就顺手躲开了）。
 * 1：回到 B1.6 那样，开机先画一张体检图（调屏/极性/方向时用）。
 *
 * 墨水屏会一直留着上一次的画面，所以 0 不会把屏刷坏。 */
#define ZK_BOOT_PANEL_TEST  0

/* 让固件在 flash 里留下一个能搜到的标记（验收脚本会找它）。
   放在自己的 .zk_tag 段里，链接脚本里 KEEP 住了，不会被 --gc-sections 收走。 */
const char zk_fw_tag[] __attribute__((section(".zk_tag"), used)) = "ZK42V-EPD-CUSTOM-FW-B1";

/* 毫秒时基。
 *
 * ⚠ 这里**不能**直接 return CYCCNT / (clk/1000)：CYCCNT 是 32 位自由计数器，
 *   16 MHz 下每 268 秒绕一圈，那样算出来的"毫秒"也跟着每 268 秒掉回 0。
 *   凡是拿它当绝对时间的地方（日历页那句 `s_ts + (tick - s_ts_ms)/1000`）都会
 *   在那一下突然跳 49.7 天 —— build 29 的"屏每 4.5 分钟自己刷一次"就是这么来的。
 *   现在按差值累加成单调计数，见 board/zk_tick.h。 */
/* ======================================================================
 * build 59：时基换成 **AON 定时器**
 *
 * 上面那段注释里说的第①个坑（268 秒绕圈）是老问题；这次解决的是第②个坑：
 * **CYCCNT 数的是 CPU 周期，而 GR5513 的主频会变**（空闲 16 MHz，刷屏/连 BLE
 * 时明显更高）。于是"毫秒"平均快 3~4.8 倍 —— 日历每 ~8 小时跨一天。
 * 证据：build 58 记下的那次跳变 = 1e9 个周期，按 16 MHz 算成 62 秒，
 * 而真实只过了 20.8 秒（正好一次全刷），比值 3.0。
 *
 * AON 定时器（AON->TIMER_VAL = 0xA000C594）跑在**低功耗时钟**上，与主频无关；
 * 实测 ~28 kHz 而且是**递减**计数（2026-10-01 三次 status：27.8/28.0/28.1 kHz）。
 *
 * 开机做一次标定（zk_timebase_init）：
 *   ① 用 DWT 量 4 个 ~50ms 的窗口，同时数 AON 走了多少 tick → 每秒多少 tick；
 *   ② 跟 SDK（ROM 的 sys_lpclk_get()）报的低频时钟频率交叉核对，差 20% 以内才信；
 *   ③ AON 完全不涨 / 两个频率都不可信 → 老实回退到上面那套 DWT 实现。
 * 标定和选用的每个数都写进状态块（104~111 号字），`status.sh` 会印出来。
 * ====================================================================== */
static zk_tick_t     s_tick;        /* DWT/CYCCNT 路径（build 30~58 的老实现，兜底） */
static zk_tick_aon_t s_tick_aon;    /* AON 路径（build 59） */
static uint32_t      s_aon_hz;      /* 实际用哪个频率：0 = 没在用 AON（走 DWT） */
static uint32_t      s_aon_src;     /* 1=DWT 标定 2=SDK 值 3=名义值 0=DWT 路径 */
static uint8_t       s_aon_down;    /* 1 = AON 计数器递减（本机实测就是） */
static uint8_t       s_aon_alive;   /* 标定期间看到它在动 */

#define ZK_TB_HZ_MIN  18000u        /* 低频时钟只可能是 32k 那一档（±）。 */
#define ZK_TB_HZ_MAX  45000u        /* 出了这个范围 = 标定/读数崩了，宁可不用。 */
#define ZK_TB_HZ_NOM  28000u        /* 本机名义值（DWT 和 SDK 都给不出时用这个） */

static inline uint32_t tb_aon_raw(void)
{
    return AON->TIMER_VAL;          /* 只读；低功耗时钟域，与 CPU 主频无关 */
}

/* 递减计数器归一成"递增"，这样 zk_tick_aon_step() 只认一种方向 */
static inline uint32_t tb_aon_norm(uint32_t raw)
{
    return s_aon_down ? (uint32_t)(0u - raw) : raw;
}

/* 空转 cycles 个周期（DWT 不在时按圈数兜底） */
static void tb_spin(uint32_t cycles)
{
    if (g_dbg.flags & ZK_FLAG_DWT_OK)
    {
        uint32_t c0 = DWT->CYCCNT;
        while ((uint32_t)(DWT->CYCCNT - c0) < cycles)
        {
        }
    }
    else
    {
        volatile uint32_t n = cycles / 4u + 1u;
        while (n--)
        {
        }
    }
}

/* 这个计数器往上数还是往下数？读两次就知道。
   兜底按**递减**算 —— GR551x 的 AON 休眠定时器天生递减（SDK 的
   hal_sleep_timer_get_current_value() 也是这样用的）。 */
static void tb_aon_probe_dir(void)
{
    uint32_t i;

    s_aon_down  = 1u;
    s_aon_alive = 0u;

    for (i = 0; i < 4u; i++)
    {
        uint32_t a = tb_aon_raw();

        tb_spin(2000u);             /* ~125us @16MHz */
        {
            uint32_t b = tb_aon_raw();

            if (b != a)
            {
                s_aon_alive = 1u;
                s_aon_down  = (b < a) ? 1u : 0u;
                return;
            }
        }
    }
}

/* 开机标定：定方向 → 量频率 → 与 SDK 交叉核对 → 选中一条时基 */
static void zk_timebase_init(void)
{
    uint32_t clk = SystemCoreClock;
    uint32_t hz_cal = 0u;
    uint32_t hz_sdk;

    if (clk < 1000000u || clk > 128000000u)
    {
        clk = 16000000u;
    }

    tb_aon_probe_dir();

    /* ① DWT 标定：4 个 ~50ms 窗口，累加完再除（一次除完，免得每次截断）。
          此刻主频就是 SystemCoreClock（开机还没跑 BLE/刷屏），
          所以量出来的就是「AON tick / 真实秒」。 */
    if ((g_dbg.flags & ZK_FLAG_DWT_OK) && s_aon_alive)
    {
        uint32_t i;
        uint32_t win   = clk / 20u;         /* ~50ms */
        uint32_t ticks = 0u;
        uint32_t cyc   = 0u;

        for (i = 0; i < 4u; i++)
        {
            uint32_t c0 = DWT->CYCCNT;
            uint32_t a0 = tb_aon_norm(tb_aon_raw());

            while ((uint32_t)(DWT->CYCCNT - c0) < win)
            {
            }
            ticks += (uint32_t)(tb_aon_norm(tb_aon_raw()) - a0);
            cyc   += win;
        }

        if (0u != cyc)
        {
            hz_cal = (uint32_t)(((uint64_t)ticks * (uint64_t)clk) / (uint64_t)cyc);
        }
        if (hz_cal < ZK_TB_HZ_MIN || hz_cal > ZK_TB_HZ_MAX)
        {
            hz_cal = 0u;                    /* 标定崩了 → 当没标出来 */
        }
        s_aon_alive = (0u != ticks) ? 1u : 0u;
    }

    /* ② SDK 报的低频时钟频率（ROM 的 sys_lpclk_get()）—— 交叉核对用 */
    hz_sdk = sys_lpclk_get();
    if (hz_sdk < ZK_TB_HZ_MIN || hz_sdk > ZK_TB_HZ_MAX)
    {
        hz_sdk = 0u;
    }

    /* ③ 选一条 */
    if (!s_aon_alive)
    {
        s_aon_hz  = 0u;                     /* AON 压根不动 → 回退 DWT */
        s_aon_src = 0u;
    }
    else if (0u != hz_cal && 0u != hz_sdk)
    {
        uint32_t lo = hz_sdk - hz_sdk / 5u;     /* ±20%：能把"标定时主频不对" */
        uint32_t hi = hz_sdk + hz_sdk / 5u;     /* （量出来会差好几倍）挡在外面 */

        if (hz_cal >= lo && hz_cal <= hi)
        {
            s_aon_hz  = hz_cal;
            s_aon_src = 1u;
        }
        else
        {
            s_aon_hz  = hz_sdk;
            s_aon_src = 2u;
        }
    }
    else if (0u != hz_cal)
    {
        s_aon_hz  = hz_cal;
        s_aon_src = 1u;
    }
    else if (0u != hz_sdk)
    {
        s_aon_hz  = hz_sdk;
        s_aon_src = 2u;
    }
    else
    {
        s_aon_hz  = ZK_TB_HZ_NOM;
        s_aon_src = 3u;
    }

    if (0u != s_aon_hz)
    {
        g_dbg.flags |= ZK_FLAG_AON_TB;
    }

    /* 标定现场留证据 */
    g_dbg.tb_src       = s_aon_src | (s_aon_down ? 0x100u : 0u);
    g_dbg.tb_hz        = s_aon_hz;
    g_dbg.tb_hz_cal    = hz_cal;
    g_dbg.tb_hz_sdk    = hz_sdk;
    g_dbg.tb_aon_ticks = tb_aon_raw();
    g_dbg.tb_ticks     = 0u;
    g_dbg.tb_bad       = 0u;
    g_dbg.tb_jumps     = 0u;
    g_dbg.test_step    = s_aon_hz;      /* build 58 那个字段：现在放"实际用的频率" */
}

static uint32_t tb_core_per_ms(void)
{
    uint32_t clk = SystemCoreClock;

    if (clk < 1000000u || clk > 128000000u)
    {
        clk = 64000000u;
    }
    return clk / 1000u;
}

/* 时基本体：AON 优先，DWT 兜底 */
static uint64_t tick_ms64_raw(void)
{
    if (0u != s_aon_hz)
    {
        return zk_tick_aon_step(&s_tick_aon, tb_aon_norm(tb_aon_raw()), s_aon_hz);
    }

    if (!(g_dbg.flags & ZK_FLAG_DWT_OK))
    {
        return 0u;
    }
    return zk_tick_step(&s_tick, DWT->CYCCNT, tb_core_per_ms());
}

static uint32_t tick_ms(void)
{
    return (uint32_t)tick_ms64_raw();
}

/* 给别的模块用（B2-A.2 的推图状态机要量「写图+刷新花了多久」——
   那个过程整个跑在 zk_epd_svc_poll() 里面，外面传进去的 now_ms 是不动的）。
   build 59：顺手把时基的现场证据刷进状态块（每轮主循环都调一次这里）。 */
uint32_t zk_tick_ms(void)
{
    uint32_t now = (uint32_t)tick_ms64_raw();

    g_dbg.tb_aon_ticks = tb_aon_raw();
    g_dbg.tb_ticks     = s_tick_aon.tk_lo;
    g_dbg.tb_bad       = s_tick_aon.bad;
    g_dbg.tb_jumps     = s_tick_aon.jumps;
    g_dbg.test_step    = s_aon_hz;
    g_dbg.ble_tick_ms  = now;       /* build 57：实时值，跟秒表对拍用 */
    return now;
}

/* 单调的 64 位毫秒：日历/时钟那句"网页时间戳 + 已经过了多久"必须用这个，
   否则低 32 位每 49.7 天绕一次，又会把"现在几点"算错。 */
uint64_t zk_tick_ms64(void)
{
    return tick_ms64_raw();
}

/* ------------------------------------------------------------------
 * B2-B：看一眼共享内存信箱，有新图就刷上去
 *
 * 上位机（outputs/pyocd 里的 pushimg 命令）干这些事：
 *   1) 用 SWD 把 30000 字节写进 ZK_MB_IMG
 *   2) 写控制块的 len / sum
 *   3) **最后**把 seq 写上去（这样固件看到 seq 变了，说明前面的都就位了）
 * 固件这边：magic 对、seq != ack_seq -> 校验长度和累加和 -> 拷贝 -> 刷屏
 * -> 回写 ack_seq（上位机就等这个）。
 * ------------------------------------------------------------------ */
static void zk_mailbox_poll(void)
{
    uint32_t i;
    uint32_t s = 0;
    const uint8_t *src = (const uint8_t *)ZK_MB_IMG;
    uint32_t t0;

    if (s_mb->magic != ZK_MB_MAGIC)
    {
        return;
    }
    if (s_mb->seq == s_mb->ack_seq)
    {
        return;
    }

    if (s_mb->len == ZK42V_EPD_IMG_BYTES)
    {
        for (i = 0; i < ZK42V_EPD_IMG_BYTES; i++)
        {
            s += src[i];
        }
        if (s == s_mb->sum)
        {
            s_mb->status = 1u;
            /* build 21 起开机不再初始化屏（改成谁用谁负责），所以这条
               SWD 推图的路子得自己保证屏已经初始化、脚已经配好。 */
            (void)zk_panel_ensure_init();
            epd_gpio_init();
            epd_write_image(s_img);
            t0 = tick_ms();
            epd_refresh_ex(0xC7, 0);
            s_mb->ms_refresh = tick_ms() - t0;
            epd_pins_release();
            s_mb->status = 3u;
            s_mb->ack_seq = s_mb->seq;
            zk_dbg_stage(ZK_STAGE_PUSHED);
            return;
        }
    }

    /* 校验不过：回个错误码，同样把 ack 推上去，免得死循环重试同一帧 */
    s_mb->status = 0xFFu;
    s_mb->ack_seq = s_mb->seq;
}

/* 自己接管 main_init()（SDK 那份是 __WEAK，我们这份强符号会顶掉它）
 *
 * 为什么必须接管：SDK 默认的实现是
 *     boot_flag = pwr_mgmt_get_wakeup_flag();
 *     if (COLD_BOOT == boot_flag) __main();     // 这才是我们要的
 *     else { warm_boot_process(); for(;;); }    // 一旦判成"深睡唤醒"就睡死
 * 这块价签的 bootloader 是**直接跳**过来的，跟 SDK 那套深睡唤醒流程不是一回事，
 * 万一那个 flag 读出来不是 COLD_BOOT，我们的 main() 就永远不会被调用，
 * 表现成"刷进去了但什么都没发生"。干脆不问，直接走冷启动那条路。
 *
 * 顺手在最早的这一刻把 magic 写进状态块：即使后面 soc_init 挂了，
 * 调试器也能一眼看出「固件确实进来了，是卡在 SDK 初始化里」，
 * 而不是「根本没刷进去」。 */
void main_init(void)
{
    extern void __main(void);
    uint32_t sw1;

    /* ---- 1) 启动计数：这块 RAM 是 NOLOAD、软复位不清 ------------------
     * 上电时 RAM 是随机的，所以先用一个 boot_magic 认领这块 RAM；
     * 之后这个数一直涨 = 芯片在反复复位（status.sh 里的 boot_count）。 */
    if (g_dbg.boot_magic != ZK_BOOT_MAGIC)
    {
        g_dbg.boot_magic = ZK_BOOT_MAGIC;
        g_dbg.boot_count = 0;
        g_dbg.uds_seen = 0;
    }
    g_dbg.boot_count++;

    /* ---- 2) 处理 AON 里的「超深睡唤醒」标志 ----------------------------
     * 平台的 soc_init() 里有这么一段（platform/soc/src/gr_soc.c）：
     *     if (0xF175 == (AON->SOFTWARE_1 & 0xFFFF))
     *         hal_nvic_system_reset();        // 复位整个系统
     * 它的意思是「刚从超深睡醒过来，RAM 上下文没了，重新来过」。
     * 但这个标志在 AON 域（SOFTWARE_1 = 0xA000C560），**软复位不会清**，
     * 所以只要它还在，每次启动都会再复位一次 —— 无限重启循环。
     * 我们是从 bootloader 直接跳进来的，永远该当冷启动，所以直接清掉。
     * （2026-09-27 那次「刷完 APP 屏不亮、3 秒后调试口消失」就是照这个查的。） */
    sw1 = AON->SOFTWARE_1;
    if ((sw1 & 0xFFFFu) == 0xF175u)
    {
        AON->SOFTWARE_1 = sw1 & ~0xFFFFu;
        g_dbg.uds_seen++;
        g_dbg.flags |= ZK_FLAG_UDS_CLEARED;
    }

    g_dbg.magic    = ZK_DBG_MAGIC;
    g_dbg.stage    = ZK_STAGE_BOOT;
    g_dbg.build_id = ZK_BUILD_ID;

    __main();
}

#if ZK_PWR_SAVE
/* 省电版的节拍 —— 2026-10-03（用户："价签是 2450 电池，需要省电"）。

   为什么换成 app_timer：它建在 **AON 睡眠定时器**上，电源管理（pwr_mgmt_schedule）
   知道"下一个定时器什么时候到"，于是能**精确地把 CPU 睡到那一刻**；而原来的
   `epd_delay_ms(5)` 是忙等 —— CPU 一直在空转（200Hz），这是之前最大的耗电项。
   回调跑在**中断**里，所以这里只置标志，真正的活留给主循环（跟 BLE 回调同一个约定）。 */
static volatile uint8_t s_tick_flag;
static app_timer_id_t   s_tick_timer;
static uint8_t          s_psave_ready;      /* 节拍定时器起来了没（没起来就退回忙等） */

static void zk_tick_cb(void *p_ctx)
{
    (void)p_ctx;
    s_tick_flag = 1;
}
#endif

int main(void)
{
    uint32_t t_prev;

    /* 把状态块里那些"计数器"清零 —— 这块 RAM 是 NOLOAD，上电不清零，
       上一次刷到的那几个数（heart、flags、busy_xxx、ms_xxx）全是随机值，
       不清的话读出来就是天文数字，没法看。
       注意：boot_count / uds_seen / boot_magic / test_step 不清 ——
       它们是跨复位要保留的。 */
    g_dbg.heart = 0;
    g_dbg.flags = 0;
    g_dbg.busy_levels = 0;
    g_dbg.busy_polls = 0;
    g_dbg.busy_timeouts = 0;
    g_dbg.gpio_err = 0;
    g_dbg.ms_init = 0;
    g_dbg.ms_write = 0;
    g_dbg.ms_refresh = 0;
    /* B2-A：这三个也要清 —— 不清的话「BLE 还没启动」会读成随机大数，
       跟"启动失败"分不出来。（这版之前就是靠这个坑浪费了一轮） */
    g_dbg.ble_state = 0;
    g_dbg.ble_err = 0;
    g_dbg.ble_mtu = 0;

    /* B2-A.2：这些全是"本轮新写的值"，同样必须显式清零。
       上一版就是漏清了 ble_evt_count（NOLOAD 的 RAM 上电是随机值），
       build 18 的状态块里读出来是个天文数字，把"事件有没有回来"这个
       最关键的判断给带偏了。注意这条：**用到的字段一个都不能漏清**。 */
    g_dbg.ble_addr0 = 0;
    g_dbg.ble_addr1 = 0;
    g_dbg.ble_evt_id = 0;
    g_dbg.ble_evt_status = 0;
    g_dbg.ble_evt_count = 0;

    g_dbg.ble_scan_state = ZK_SCAN_ST_OFF;
    g_dbg.ble_scan_param_err = 0;
    g_dbg.ble_scan_start_err = 0;
    g_dbg.ble_scan_start_st = ZK_NONE_U32;
    g_dbg.ble_scan_stop_rsn = ZK_NONE_U32;
    g_dbg.ble_scan_rpts = 0;
    g_dbg.ble_scan_devs = 0;
    g_dbg.ble_scan_ovf = 0;
    g_dbg.ble_scan_rssi_last = 0;
    g_dbg.ble_scan_rssi_best = 0;
    g_dbg.ble_scan_addr0 = 0;
    g_dbg.ble_scan_addr1 = 0;
    g_dbg.ble_scan_last_len = 0;
    g_dbg.ble_scan_data0 = 0;
    g_dbg.ble_scan_data1 = 0;
    g_dbg.ble_scan_data2 = 0;
    g_dbg.ble_scan_data3 = 0;

    g_dbg.ble_adv_try = 0;
    g_dbg.ble_adv_try_status = ZK_NONE_U32;
    g_dbg.ble_adv_ok_variant = ZK_NONE_U32;
    g_dbg.ble_adv_ds_err = 0;
    g_dbg.ble_adv_ds2_err = 0;
    g_dbg.ble_adv_start_err = 0;
    /* build 20：广播停了几次 / 我们重开了几次 */
    g_dbg.ble_adv_stop_cnt = 0;
    g_dbg.ble_adv_stop_rsn = ZK_NONE_U32;
    g_dbg.ble_adv_restart_cnt = 0;

    /* build 21：B2-A.2 的 GATT 服务 / 推图那条线的账 */
    g_dbg.ble_svc_err = 0;
    g_dbg.ble_svc_hdl = 0;
    g_dbg.ble_conn_cnt = 0;
    g_dbg.ble_conn_idx = ZK_NONE_U32;
    g_dbg.ble_cccd = 0;
    g_dbg.ble_cmd_cnt = 0;
    g_dbg.ble_last_cmd = ZK_NONE_U32;
    g_dbg.ble_img_chunks = 0;
    g_dbg.ble_last_flags = ZK_NONE_U32;
    g_dbg.ble_img_bw = 0;
    g_dbg.ble_img_red = 0;
    g_dbg.ble_rle_out = 0;
    g_dbg.ble_legacy = 0;
    g_dbg.ble_panel_state = 0;
    g_dbg.ble_panel_ms = 0;
    g_dbg.ble_init_ms = 0;
    g_dbg.ble_noti_cnt = 0;
    g_dbg.ble_noti_err = 0;
    g_dbg.ble_want_init = 0;
    g_dbg.ble_want_refresh = 0;
    g_dbg.ble_mtu_rpt = 0;
    g_dbg.ble_svc_end_hdl = 0;
    g_dbg.ble_svc_db_err = 0;
    g_dbg.ble_busy_delta = 0;
    g_dbg.ble_gui_mode = 0;
    g_dbg.ble_gui_ts = 0;
    g_dbg.ble_gui_draws = 0;
    /* build 28：画面选项（反色 / 旋转 180° / 不画农历）—— 同样得清，
       否则 status.sh 里那行"没设过"会读成随机数，跟"设过但没生效"分不出来 */
    g_dbg.ble_opt = 0;
    g_dbg.ble_opt_cmds = 0;
    g_dbg.ble_opt_frames = 0;
    /* build 30：日历/时钟"为什么重画"+ 时基自证 */
    g_dbg.ble_gui_why = 0;
    g_dbg.ble_gui_elapsed = 0;
    g_dbg.ble_tick_ms = 0;
    /* build 31：电池/温度 */
    g_dbg.bat_mv = ZK_NONE_U32;
    g_dbg.bat_pct = ZK_NONE_U32;
    g_dbg.bat_temp_c10 = ZK_NONE_U32;
    g_dbg.bat_errs = 0;
    /* build 41：天气（手机下发） */
    g_dbg.wx_code = 0;
    g_dbg.env_temp_c = (uint32_t)(int32_t)(-128);
    g_dbg.wx_cmds = 0;
    /* build 42：ADC 通道/参考/校准（查"电压显示 2.59V"用的） */
    g_dbg.bat_raw = ZK_NONE_U32;
    g_dbg.bat_mv_sdk = ZK_NONE_U32;
    g_dbg.bat_mv_own = ZK_NONE_U32;
    g_dbg.adc_trim08 = 0;
    g_dbg.adc_trim12 = 0;
    g_dbg.adc_cfg = 0;
    g_dbg.adc_trim_rc = ZK_NONE_U32;
    /* build 45：网页给的时区（屏上时间/日期不对时先看它） */
    g_dbg.tz_h = ZK_NONE_U32;
    /* build 60：基站"顺手带时间"的两个计数（推天气时把时间一起发下来） */
    g_dbg.set_time_cmds = 0;
    g_dbg.time_keep_cnt = 0;
    /* build 61：基站下发的城市名（命令 0x79）收到了几次 */
    g_dbg.city_cmds = 0;
    /* build 67：纪念日提醒（命令 0x7A，生日高亮）收到了几次 */
    g_dbg.memo_cmds = 0;
    /* build 71：天气预警（命令 0x7C）收到了几次 */
    g_dbg.alert_cmds = 0;
    /* build 74：预警图标编号（命令 0x7D）收到了几次 */
    g_dbg.alert_icon_cmds = 0;
    {
        volatile uint32_t *st = &g_dbg.ble_adv_st0;
        uint32_t           i;
        for (i = 0; i < 6u; i++)
        {
            st[i] = ZK_NONE_U32;
        }
    }

    g_dbg.magic    = ZK_DBG_MAGIC;
    g_dbg.build_id = ZK_BUILD_ID;
    zk_dbg_stage(ZK_STAGE_MAIN);

    /* 第一件事：把调试口打开。原厂 APP 会关掉它，我们偏要留着 ——
       这样出问题时随时能连上读状态块，不用再抢复位窗口。 */
    sys_swd_enable();
    g_dbg.flags |= ZK_FLAG_SWD_ON;
    zk_dbg_stage(ZK_STAGE_SWD);

    /* 单独把 DWT 时基打开（不碰屏的引脚）。
       BLE 实验这条路上原来没人调 delay_init，于是 flags 的 bit2 一直没置位、
       tick_ms() 恒为 0，空闲循环里的超时只能用"数圈数"。现在有真毫秒了。 */
    epd_timer_init();

    /* build 59：标定 AON 定时器、把毫秒时基从 CYCCNT 换过去（标不出来就继续用
       CYCCNT）。必须在 epd_timer_init() 之后 —— 标定要借 DWT 当尺子。
       也要在任何用 tick 的代码之前（zk_bat_init / zk_ble_start 都在下面）。 */
    zk_timebase_init();

    /* build 31：把 ADC 的两个内部通道（VBAT / TMP）准备好。
       表头右上角的电池和温度就是它读的 —— 不用外接任何东西。 */
    zk_bat_init();

#if ZK_BOOT_PANEL_TEST
    epd_gpio_init();
    zk_dbg_stage(ZK_STAGE_GPIO);

    t_prev = tick_ms();
    epd_reset();
    epd_init_sequence();
    g_dbg.ms_init = tick_ms() - t_prev;
    zk_dbg_stage(ZK_STAGE_INIT_SEQ);

    zk_dbg_stage(ZK_STAGE_IMG_READY);

    /* ------------------------------------------------------------------
     * B1.6 方向体检图（一帧，刷完就停）
     *
     * 极性和数据通路已经由 B1.5 的上色序列实测确认：
     *   黑白面 0=黑 1=白；红面 1=红，且红盖过黑白面。
     * 这一版就画一张自解释的图，把**方向**钉死：
     *   白底 + 上边一条黑横条 + 左边一条红竖条 + 正中间一个黑方块。
     * 两条条子互相垂直、颜色不同，所以不管屏上是旋转还是镜像，
     * 看它们落在哪一边就能反推出对应关系。
     * ------------------------------------------------------------------ */
    zk_testimg_orient(s_img);
    zk_dbg_stage(ZK_STAGE_IMG_READY);

    t_prev = tick_ms();
    epd_write_image(s_img);
    g_dbg.ms_write = tick_ms() - t_prev;
    zk_dbg_stage(ZK_STAGE_IMG_SENT);

    t_prev = tick_ms();
    epd_refresh_ex(0xC7, 0);
    g_dbg.ms_refresh = tick_ms() - t_prev;
    g_dbg.test_step = 1u;
    zk_dbg_stage(ZK_STAGE_REFRESHED);
    epd_pins_release();       /* 刷完松开屏的脚（尤其 P1_8），别影响 BLE */
#endif

    /* B2-A：起 BLE（广播 + 之后的服务）。
       放在刷完第一帧之后：屏先亮，再起无线；协议栈初始化不阻塞主循环。 */
    zk_ble_start();
    zk_dbg_stage(ZK_STAGE_BLE);

#if ZK_PWR_SAVE
    /* ① 主循环节拍（AON 睡眠定时器）。建不起来就**退回老行为**，不能让价签卡死：
          那样 s_psave_ready 保持 0，主循环里照旧每圈 sleep 5ms 忙等。 */
    if ((SDK_SUCCESS == app_timer_create(&s_tick_timer, ATIMER_REPEAT, zk_tick_cb)) &&
        (SDK_SUCCESS == app_timer_start_api(&s_tick_timer, ZK_TICK_MS, NULL)))
    {
        s_psave_ready = 1;
        g_dbg.flags |= ZK_FLAG_PSAVE;
    }

    /* ② 允许深睡。SDK 里的 PMR_MGMT_SLEEP_MODE 就是 "Deep sleep state"：
          **RAM 保持**（s_ts、上一屏内容、连接状态都还在）、AON 域继续供电 ——
          所以毫秒时基 zk_tick_ms64() 睡着的这几秒照样在涨，醒来算一下就知道
          有没有换天。
          ⚠ 刻意**不用 UDS（超深睡）**：原厂那种模式醒来等于**整个系统复位**，
          必须先把"时间持久化"（写 flash）做了，否则每次醒来都是空日历。 */
    pwr_mgmt_mode_set(PMR_MGMT_SLEEP_MODE);
#endif

    /* 停在这儿，心跳一直涨 —— 调试器随时进来都能看到「活着」的证据。
       （2026-10-04：睡眠那一路在这颗芯片上会卡死，见 board/zk_pwr.h 里的记录，
         所以 ZK_PWR_SAVE=0，走回"忙等"；广播间隔仍然放宽到 1 秒。） */
    zk_dbg_stage(ZK_STAGE_IDLE);
    for (;;)
    {
        g_dbg.heart++;
        /* 协议栈的事件靠这个泵出来（SDK 例程主循环里都有它）。
           少了它，BLE 的 BLE_COMMON_EVT_STACK_INIT 之类的事件永远递不上来，
           表现就是"固件在跑，但一直不广播"。
           ⚠ 省电版里它**还是睡觉的入口**：没事可干时就停在这里
           —— 要么等到 AON 睡眠定时器（我们的节拍）、要么被 BLE 事件叫醒。 */
        pwr_mgmt_schedule();

#if ZK_PWR_SAVE
        if (s_psave_ready)
        {
            if (!s_tick_flag)
            {
                continue;               /* 还没到节拍 → 接着睡，别干活 */
            }
            s_tick_flag = 0;
        }
#endif

        /* B2-A.2：扫描/广播实验的状态机节拍 + 兜底超时。
           传进去的是 DWT 算的毫秒数（拿不到时基就是 0，函数里会退回数圈数）。 */
        zk_ble_poll(tick_ms());

        /* build 31：电池/温度（内部自己限速，一分钟一次）。
           ⚠ 顺序有讲究：build 43 把它**挪到 zk_epd_svc_poll() 前面** ——
           BLE 命令 0x72（立刻读电池 + 重画）在 pwr_mgmt_schedule() 里置好标志，
           这一轮先在这儿把新电压读进来，紧接着画页面时用的就是新值（否则会慢一轮）。 */
        zk_bat_poll(tick_ms());
        g_dbg.bat_mv      = (zk_bat_mv() < 0) ? ZK_NONE_U32 : (uint32_t)zk_bat_mv();
        g_dbg.bat_pct     = (zk_bat_pct() < 0) ? ZK_NONE_U32 : (uint32_t)zk_bat_pct();
        g_dbg.bat_temp_c10 = (zk_bat_temp_c10() == (int)(-32768))
                             ? ZK_NONE_U32 : (uint32_t)(int32_t)zk_bat_temp_c10();
        g_dbg.bat_errs    = zk_bat_errs();
        /* build 42：ADC 诊断——原始码值、两条路的读数、通道/参考寄存器、出厂校准 */
        g_dbg.bat_raw     = (zk_bat_raw() < 0) ? ZK_NONE_U32 : (uint32_t)zk_bat_raw();
        g_dbg.bat_mv_sdk  = (zk_bat_mv_sdk() < 0) ? ZK_NONE_U32 : (uint32_t)zk_bat_mv_sdk();
        g_dbg.bat_mv_own  = (zk_bat_mv_own() < 0) ? ZK_NONE_U32 : (uint32_t)zk_bat_mv_own();
        g_dbg.adc_cfg     = zk_bat_cfg();
        g_dbg.adc_trim08  = zk_bat_trim08();
        g_dbg.adc_trim12  = zk_bat_trim12();
        g_dbg.adc_trim_rc = zk_bat_trim_rc();

        /* B2-A.2：网页推图这条线。命令在事件回调里只做记账，屏的重活
           （初始化、写图、刷新十几秒）在这个 poll 里做，别堵住协议栈。 */
        zk_epd_svc_poll(tick_ms());

        zk_mailbox_poll();      /* B2-B：有新图就刷 */
#if ZK_PWR_SAVE
        if (!s_psave_ready)
        {
            epd_delay_ms(5);    /* 节拍定时器没起来 → 退回老行为（5ms 忙等） */
        }
        /* 起来了就什么都不用做：下一圈的 pwr_mgmt_schedule() 会一直睡到节拍到点 */
#else
        epd_delay_ms(5);        /* 5ms 一圈 ≈ 200Hz：协议栈的活干得快一点 */
#endif
    }
}
