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
static zk_tick_t s_tick;

static uint32_t tick_ms(void)
{
    uint32_t clk, per_ms;

    if (!(g_dbg.flags & ZK_FLAG_DWT_OK))
    {
        return 0;
    }

    clk = SystemCoreClock;
    if (clk < 1000000u || clk > 128000000u)
    {
        clk = 64000000u;
    }
    per_ms = clk / 1000u;

    return (uint32_t)zk_tick_step(&s_tick, DWT->CYCCNT, per_ms);
}

/* 给别的模块用（B2-A.2 的推图状态机要量「写图+刷新花了多久」——
   那个过程整个跑在 zk_epd_svc_poll() 里面，外面传进去的 now_ms 是不动的） */
uint32_t zk_tick_ms(void)
{
    return tick_ms();
}

/* 单调的 64 位毫秒：日历/时钟那句"网页时间戳 + 已经过了多久"必须用这个，
   否则低 32 位每 49.7 天绕一次，又会把"现在几点"算错。 */
uint64_t zk_tick_ms64(void)
{
    uint32_t clk, per_ms;

    if (!(g_dbg.flags & ZK_FLAG_DWT_OK))
    {
        return 0;
    }

    clk = SystemCoreClock;
    if (clk < 1000000u || clk > 128000000u)
    {
        clk = 64000000u;
    }
    per_ms = clk / 1000u;

    return zk_tick_step(&s_tick, DWT->CYCCNT, per_ms);
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

    /* 不睡觉，也不关外设：就停在这儿，心跳一直涨。
       调试器随时进来都能看到「活着」的证据。 */
    zk_dbg_stage(ZK_STAGE_IDLE);
    for (;;)
    {
        g_dbg.heart++;
        /* 协议栈的事件靠这个泵出来（SDK 例程主循环里都有它）。
           少了它，BLE 的 BLE_COMMON_EVT_STACK_INIT 之类的事件永远递不上来，
           表现就是"固件在跑，但一直不广播"。 */
        pwr_mgmt_schedule();

        /* B2-A.2：扫描/广播实验的状态机节拍 + 兜底超时。
           传进去的是 DWT 算的毫秒数（拿不到时基就是 0，函数里会退回数圈数）。 */
        zk_ble_poll(tick_ms());

        /* B2-A.2：网页推图这条线。命令在事件回调里只做记账，屏的重活
           （初始化、写图、刷新十几秒）在这个 poll 里做，别堵住协议栈。 */
        zk_epd_svc_poll(tick_ms());

        /* build 31：电池/温度（内部自己限速，一分钟一次） */
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

        zk_mailbox_poll();      /* B2-B：有新图就刷 */
        epd_delay_ms(5);        /* 5ms 一圈 ≈ 200Hz：协议栈的活干得快一点 */
    }
}
