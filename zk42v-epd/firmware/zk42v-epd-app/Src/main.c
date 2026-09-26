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

#include "gr55xx.h"
#include "gr55xx_sys.h"      /* sys_swd_enable() */

#include <string.h>          /* memset */

/* 状态块本体：链接脚本把它钉在 0x3001F000（RAM_DBG，NOLOAD，不清零） */
volatile zk_dbg_t g_dbg __attribute__((section(".dbg_status"), used));

/* 一帧 30000 字节画在 RAM 里 */
static uint8_t s_img[ZK42V_EPD_IMG_BYTES];

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
     * B1.5 自动上色序列：**一次启动把 5 步全做完**，每步之间停 8 秒。
     *
     * 为什么不再"按 RST 换一步"：步号原本存在 0x3001F000 的调试块里想跨复位保留，
     * 但实测这块 RAM 每次启动都会被 ROM/bootloader 的栈踩掉 —— 证据是
     * boot_count 一直是 1、test_step 读出来是随机值（2810513150），
     * 所以每次 RST 都从第 1 步重来（现象就是"每次都一样、最后变黑"）。
     * 现在改成一次做完，不再依赖跨复位保存。
     *
     *  第1步  全黑   BW=0x00 RED=0x00   刷新 0xC7
     *  第2步  全白   BW=0xFF RED=0x00   刷新 0xC7
     *  第3步  全红   BW=0xFF RED=0xFF   刷新 0xC7
     *  第4步  四条横带（体检图）        刷新 0xC7   ← 原厂那条
     *  第5步  四条横带                  刷新 0xF7   ← EPD-nRF5/Waveshare 那条全量
     *  最后停在**第5步**的画面（横带）。
     * ------------------------------------------------------------------ */
    {
        static const uint8_t seq_bw[5]   = {0x00, 0xFF, 0xFF, 0x00, 0x00};
        static const uint8_t seq_red[5]  = {0x00, 0x00, 0xFF, 0x00, 0x00};
        static const uint8_t seq_ctrl[5] = {0xC7, 0xC7, 0xC7, 0xC7, 0xF7};
        uint32_t k;

        for (k = 0; k < 5u; k++)
        {
            g_dbg.test_step = k + 1u;         /* 记录"正在做第几步" */

            if (k >= 3u)
            {
                zk_testimg_build(s_img);      /* 第 4、5 步画体检图 */
            }
            else
            {
                memset(s_img, seq_bw[k], ZK42V_EPD_PLANE_BYTES);
                memset(s_img + ZK42V_EPD_PLANE_BYTES, seq_red[k], ZK42V_EPD_PLANE_BYTES);
            }
            zk_dbg_stage(ZK_STAGE_IMG_READY);

            t_prev = tick_ms();
            epd_write_image(s_img);
            g_dbg.ms_write = tick_ms() - t_prev;
            zk_dbg_stage(ZK_STAGE_IMG_SENT);

            t_prev = tick_ms();
            epd_refresh_ex(seq_ctrl[k], 0);
            g_dbg.ms_refresh = tick_ms() - t_prev;
            zk_dbg_stage(ZK_STAGE_REFRESHED);

            /* 停 8 秒再进下一步，方便盯着屏看这一步留下的是什么 */
            epd_delay_ms(8000);
        }
        g_dbg.test_step = 5u;
    }

    /* 不睡觉，也不关外设：就停在这儿，心跳一直涨。
       调试器随时进来都能看到「活着」的证据。 */
    zk_dbg_stage(ZK_STAGE_IDLE);
    for (;;)
    {
        g_dbg.heart++;
        epd_delay_ms(200);
    }
}
