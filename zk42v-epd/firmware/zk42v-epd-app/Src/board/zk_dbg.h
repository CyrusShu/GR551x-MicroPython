/*
 * 调试状态块 —— 这块价签上最好用的「print」。
 *
 * 为什么要这个：我们暂时不知道这块板的 UART 引脚在哪，看不到 printf。
 * 但我们的固件**不关 SWD**（原厂 APP 会关，所以我们以前只能抢复位后那 2 秒）。
 * 也就是说：刷上我们自己固件以后，调试器随时都能连上，随时都能读内存。
 *
 * 于是约定一个固定地址 0x3001F000（链接脚本里那块 4KB RAM_DBG），
 * 固件每走完一步就往里写个数。之后用 pyOCD 读 48 字节，就知道
 * 「跑到哪一步了 / 卡在哪 / BUSY 到底啥电平」。
 *
 * 读法见 outputs/pyocd/status.sh。
 */
#ifndef __ZK_DBG_H__
#define __ZK_DBG_H__

#include <stdint.h>

#define ZK_DBG_ADDR   0x3001F000UL
#define ZK_DBG_MAGIC  0x5A4B3401UL      /* 'Z''K''4' + 版本 1 */

/* 固件构造号：改代码时手动 +1，状态块里能看到 */
#define ZK_BUILD_ID   4u

/* 用来判断「这个 boot_count 是不是我们写的」——上电时 RAM 是随机的 */
#define ZK_BOOT_MAGIC 0xB007C0DEu

/* main_init 里第一时间写的：证明「Reset_Handler 已经跑到我们的代码」，
   比 ZK_STAGE_MAIN 更早 —— 用来区分「没刷进去」和「刷进去了但 SDK 初始化挂了」 */
#define ZK_STAGE_BOOT        64u

/* stage：固件走到哪一步了 */
#define ZK_STAGE_MAIN        1u   /* main() 进来了 */
#define ZK_STAGE_SWD         2u   /* sys_swd_enable() 调过了 */
#define ZK_STAGE_GPIO        3u   /* 7 根脚都配好了 */
#define ZK_STAGE_RESET       4u   /* 屏复位脉冲发完（RST 低 1ms -> 高 1ms -> 0x12） */
#define ZK_STAGE_INIT_SEQ    5u   /* 整条初始化序列发完 */
#define ZK_STAGE_IMG_READY   6u   /* 测试图在 RAM 里拼好了 */
#define ZK_STAGE_IMG_SENT    7u   /* 30000 字节都传进屏里了 */
#define ZK_STAGE_REFRESHED   8u   /* 刷新命令发完、BUSY 松开 —— 这时屏上应该有图 */
#define ZK_STAGE_IDLE        9u   /* 进空闲循环（心跳在涨） */

/* flags */
#define ZK_FLAG_BUSY_TIMEOUT 0x0001u   /* 等 BUSY 超时过 */
#define ZK_FLAG_GPIO_FAIL    0x0002u   /* app_io_init 有失败 */
#define ZK_FLAG_DWT_OK       0x0004u   /* DWT 周期计数器可用（延时是准的） */
#define ZK_FLAG_SWD_ON       0x0008u   /* sys_swd_enable() 调用成功 */
#define ZK_FLAG_UDS_CLEARED  0x0010u   /* 清掉了 AON 里的「超深睡唤醒」标志 */

#define ZK_DBG_WORDS 16

typedef struct
{
    uint32_t magic;          /* 0 = ZK_DBG_MAGIC  时说明我们的固件真的跑起来了 */
    uint32_t heart;          /* 空闲循环里自增：连读两次不一样 = 还活着 */
    uint32_t stage;          /* ZK_STAGE_xxx */
    uint32_t flags;          /* ZK_FLAG_xxx */
    uint32_t busy_levels;    /* bit0 = 见过 BUSY 低；bit1 = 见过 BUSY 高 */
    uint32_t busy_polls;     /* 等 BUSY 一共轮询了多少次 */
    uint32_t busy_timeouts;  /* 等 BUSY 超时了几次 */
    uint32_t gpio_err;       /* app_io_init 返回非 0 的次数 */
    uint32_t ms_init;        /* 复位 + 初始化序列花了多少毫秒 */
    uint32_t ms_write;       /* 传 30000 字节花了多少毫秒 */
    uint32_t ms_refresh;     /* 刷新到 BUSY 松开花了多少毫秒 */
    uint32_t build_id;       /* 固件构造号（改代码时会变） */
    uint32_t boot_count;     /* 进 main_init 的次数。这块 RAM 是 NOLOAD，
                                软复位不清 —— 所以它一直涨就说明芯片在反复复位 */
    uint32_t uds_seen;       /* 见到 AON SOFTWARE_1 == 0xF175 的次数 */
    uint32_t boot_magic;     /* == ZK_BOOT_MAGIC 才说明上面两个数有效 */
    uint32_t test_step;      /* B1.3 上色测试：刚做完的第几步（1..6，按 RST 换下一步） */
    uint32_t rsv[ZK_DBG_WORDS - 16];
} zk_dbg_t;

/* 固定落在 0x3001F000（链接脚本 .dbg_status / RAM_DBG） */
extern volatile zk_dbg_t g_dbg;

static inline void zk_dbg_stage(uint32_t s)
{
    g_dbg.stage = s;
}

#endif /* __ZK_DBG_H__ */
