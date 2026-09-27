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

#include "gr55xx.h"
#include "gr55xx_sys.h"      /* sys_swd_enable() */

#include <string.h>          /* memset */

/* 状态块本体：链接脚本把它钉在 0x3001F000（RAM_DBG，NOLOAD，不清零） */
volatile zk_dbg_t g_dbg __attribute__((section(".dbg_status"), used));

/* 共享内存信箱（上位机用 SWD 直接写这块 RAM） */
static volatile zk_mailbox_t *const s_mb = (volatile zk_mailbox_t *)ZK_MB_ADDR;

/* 画面缓冲：直接用信箱里那块 30000 字节（见 zk_dbg.h 的 ZK_IMG_BUF 说明） */
#define s_img  ZK_IMG_BUF

/* 让固件在 flash 里留下一个能搜到的标记（验收脚本会找它）。
   放在自己的 .zk_tag 段里，链接脚本里 KEEP 住了，不会被 --gc-sections 收走。 */
const char zk_fw_tag[] __attribute__((section(".zk_tag"), used)) = "ZK42V-EPD-CUSTOM-FW-B1";

/* 粗粒度毫秒计时，只用来往状态块里填「这步花了多久」 */
static uint32_t tick_ms(void)
{
    uint32_t clk;

    if (!(g_dbg.flags & ZK_FLAG_DWT_OK))
    {
        return 0;
    }

    clk = SystemCoreClock;
    if (clk < 1000000u || clk > 128000000u)
    {
        clk = 64000000u;
    }

    return (uint32_t)(DWT->CYCCNT / (clk / 1000u));
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
            epd_write_image(s_img);
            t0 = tick_ms();
            epd_refresh_ex(0xC7, 0);
            s_mb->ms_refresh = tick_ms() - t0;
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

    g_dbg.magic    = ZK_DBG_MAGIC;
    g_dbg.build_id = ZK_BUILD_ID;
    zk_dbg_stage(ZK_STAGE_MAIN);

    /* 第一件事：把调试口打开。原厂 APP 会关掉它，我们偏要留着 ——
       这样出问题时随时能连上读状态块，不用再抢复位窗口。 */
    sys_swd_enable();
    g_dbg.flags |= ZK_FLAG_SWD_ON;
    zk_dbg_stage(ZK_STAGE_SWD);

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
        zk_mailbox_poll();      /* B2-B：有新图就刷 */
        epd_delay_ms(20);
    }
}
