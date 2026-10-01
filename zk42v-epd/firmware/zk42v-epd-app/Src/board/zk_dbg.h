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

/* 固件构造号：改代码时手动 +1，状态块里能看到。
   ⚠ 这个数一直停在 31 —— build 32~41 忘了跟着 +1，结果状态块里的 `build = 31`
   跟 README 的「build 4x」对不上，刷机后没法一眼确认"新固件到底跑起来没有"。
   build 42 起跟 README 的里程碑号对齐（这一版就是 42）。 */
#define ZK_BUILD_ID   59u

/* B2-A：BLE 状态（写进状态块，status.sh 能读） */
#define ZK_BLE_ST_OFF        0u
#define ZK_BLE_ST_ADV        1u   /* 在广播，等连接 */
#define ZK_BLE_ST_CONNECTED  2u   /* 已连接 */
#define ZK_BLE_ST_SCANNING   3u   /* B2-A.2：先扫描（数周围有几个设备） */
#define ZK_BLE_ST_ADV_FAILED 4u   /* B2-A.2：所有广播数据变体都被拒了 */

/* B2-A.2：扫描实验（ble_gap_scan_param_set/scan_start + ADV_REPORT）状态机 */
#define ZK_SCAN_ST_OFF       0u   /* 还没发起 */
#define ZK_SCAN_ST_STARTED   1u   /* 叫过 scan_param_set + scan_start 了（还没等到事件） */
#define ZK_SCAN_ST_START_EVT 2u   /* 收到 SCAN_START 事件、status=0 */
#define ZK_SCAN_ST_REPORTED  3u   /* 已经听到过至少一条广播 */
#define ZK_SCAN_ST_STOPPED   4u   /* 扫描结束（超时 / 我们自己强停） */

/* 状态块里的「这个值没被写过」哨兵。0xFFFFFFFF 不可能是一个合法错误码，
   也不可能是一条事件的 status，所以拿它当「没收到」很安全。 */
#define ZK_NONE_U32          0xFFFFFFFFu

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
#define ZK_STAGE_PUSHED      10u  /* 由上位机通过共享内存推来的一帧，刷完了 */
#define ZK_STAGE_BLE         11u  /* B2-A：BLE 协议栈起来了、开始广播 */

/* ---- B2-B：共享内存信箱 ----------------------------------------------------
 * 位置：0x30010000（.bss 之后、栈之前的空档，见 GCC 的 .map）
 *   +0x000  控制块 64 字节
 *   +0x100  图像数据 30000 字节
 * 上位机（pyOCD 脚本）先用 SWD 把图写进图像区，再写控制块（**seq 最后写**），
 * 固件在空闲循环里轮询：magic 对、seq 变了 -> 校验 -> 拷贝 -> 刷屏 -> 回 ack。 */
#define ZK_MB_MAGIC   0x5A4B4D42u   /* 'ZKMB' */
#define ZK_MB_ADDR    0x30014000u
#define ZK_MB_IMG     (ZK_MB_ADDR + 0x100u)

/* 图像缓冲就用信箱里那块 —— 不再另开一个 30000 字节的 s_img。
 * 理由：加了 BLE 协议栈之后 RAM 紧（它要自己的堆），而这块 30KB 本来就
 * 白白多拷一次。现在：
 *   B2-B（SWD）：上位机直接把图写进来，固件读的就是它；
 *   B2-A（BLE） ：协议收下来的数据也往这里写。
 * 于是少一份 30KB 的 .bss，正好把 BLE 的堆腾出来。 */
#define ZK_IMG_BUF    ((uint8_t *)ZK_MB_IMG)

typedef struct
{
    uint32_t magic;       /* 上位机写 ZK_MB_MAGIC 才算数 */
    uint32_t seq;         /* 上位机每推一帧 +1（**最后写**） */
    uint32_t len;         /* 期望 30000 */
    uint32_t sum;         /* 图像 30000 字节的累加和 */
    uint32_t status;      /* 固件回写：1=收到 3=刷完 0xFF=校验失败 */
    uint32_t ack_seq;     /* 固件回写：已经处理到哪个 seq */
    uint32_t ms_refresh;  /* 固件回写：这一帧刷了多久 */
    uint32_t rsv[9];
} zk_mailbox_t;

/* flags */
#define ZK_FLAG_BUSY_TIMEOUT 0x0001u   /* 等 BUSY 超时过 */
#define ZK_FLAG_GPIO_FAIL    0x0002u   /* app_io_init 有失败 */
#define ZK_FLAG_DWT_OK       0x0004u   /* DWT 周期计数器可用（延时是准的） */
#define ZK_FLAG_SWD_ON       0x0008u   /* sys_swd_enable() 调用成功 */
#define ZK_FLAG_UDS_CLEARED  0x0010u   /* 清掉了 AON 里的「超深睡唤醒」标志 */
#define ZK_FLAG_AON_TB       0x0020u   /* build 59：毫秒时基在跑 AON 定时器（不是 CYCCNT） */

#define ZK_DBG_WORDS 112

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
    uint32_t ble_state;      /* B2-A：ZK_BLE_ST_xxx */
    uint32_t ble_err;        /* B2-A：协议栈/广播 API 返回的错误码（0 = 没出错） */
    uint32_t ble_mtu;        /* B2-A：协商出来的 MTU */
    uint32_t ble_addr0;      /* B2-A：本机 BLE 地址低 4 字节 */
    uint32_t ble_addr1;      /* B2-A：本机 BLE 地址高 2 字节（低 16 位有效） */
    uint32_t ble_evt_id;     /* B2-A：协议栈最后递上来的事件 id */
    uint32_t ble_evt_status; /* B2-A：那个事件带的状态码（0 = 成功） */
    uint32_t ble_evt_count;  /* B2-A：一共收到多少事件 */

    /* ---- B2-A.2：扫描实验 + 广播数据变体实验（build 19 起） --------------
     * 这一组是为了回答「射频到底活没活」和「广播到底被什么拒了」。
     * 索引（word 号）跟 led-window-user.py 里的解码一一对应，别乱序。 */
    uint32_t ble_scan_state;      /* 24: ZK_SCAN_ST_xxx */
    uint32_t ble_scan_param_err;  /* 25: ble_gap_scan_param_set 返回码 */
    uint32_t ble_scan_start_err;  /* 26: ble_gap_scan_start 返回码 */
    uint32_t ble_scan_start_st;   /* 27: SCAN_START 事件的 status（ZK_NONE_U32 = 没收到） */
    uint32_t ble_scan_stop_rsn;   /* 28: SCAN_STOP 事件的 reason（0=超时 1=自己停 2=连上了）
                                     0xFFFFFFFE = 我们的兜底强停；ZK_NONE_U32 = 没收到 */
    uint32_t ble_scan_rpts;       /* 29: ADV_REPORT 一共几条（含重复设备） */
    uint32_t ble_scan_devs;       /* 30: 去重后听到几个设备（上限 16） */
    uint32_t ble_scan_ovf;        /* 31: 去重表满了以后又冒出来的新地址次数 */
    uint32_t ble_scan_rssi_last;  /* 32: 最后一条上报的 RSSI（有符号 32 位，Python 那边还原） */
    uint32_t ble_scan_rssi_best;  /* 33: 听到过的最强 RSSI */
    uint32_t ble_scan_addr0;      /* 34: 最后一条上报的地址低 4 字节（小端） */
    uint32_t ble_scan_addr1;      /* 35: 最后一条上报的地址高 2 字节 */
    uint32_t ble_scan_last_len;   /* 36: 第一条广播数据的长度 */
    uint32_t ble_scan_data0;      /* 37: 第一条广播数据 0..3 字节 —— 拿它看别人怎么
                                     写 Flags(0x01)/名字(0x09)，照着抄最保险 */
    uint32_t ble_scan_data1;      /* 38: 4..7 */
    uint32_t ble_scan_data2;      /* 39: 8..11 */
    uint32_t ble_scan_data3;      /* 40: 12..15 */

    uint32_t ble_adv_try;         /* 41: 当前试到第几个广播数据变体（0 基） */
    uint32_t ble_adv_try_status;  /* 42: 最近一次 ADV_START 事件的 status
                                     （ZK_NONE_U32 = 这个变体压根没等到事件） */
    uint32_t ble_adv_ok_variant;  /* 43: 第一个成功的变体号；ZK_NONE_U32 = 都失败 */
    uint32_t ble_adv_ds_err;      /* 44: 最近一次 ble_gap_adv_data_set(DATA) 的返回码 */
    uint32_t ble_adv_ds2_err;     /* 45: 最近一次 ble_gap_adv_data_set(SCAN_RSP) 的返回码 */
    uint32_t ble_adv_start_err;   /* 46: 最近一次 ble_gap_adv_start 的返回码 */
    uint32_t ble_adv_st0;         /* 47: 变体 0 的 ADV_START status */
    uint32_t ble_adv_st1;         /* 48 */
    uint32_t ble_adv_st2;         /* 49 */
    uint32_t ble_adv_st3;         /* 50 */
    uint32_t ble_adv_st4;         /* 51 */
    uint32_t ble_adv_st5;         /* 52 */

    /* ---- build 20：广播「自己停了」也要看得见（B2-A.2 的教训）----------
       手机截图那一轮能确认空中真有包，很靠运气 —— 因为当时**没有任何地方
       记录 ADV_STOP**：广播要是中途停了，状态块里一个数都不变，我们还在说
       "在广播"。现在每停一次都记一笔，非连接原因还会自动重开。 */
    uint32_t ble_adv_stop_cnt;    /* 53: ADV_STOP 事件来了几次 */
    uint32_t ble_adv_stop_rsn;    /* 54: 最后一次停的原因（0超时/1主机停/2被连接打断） */
    uint32_t ble_adv_restart_cnt; /* 55: 我们主动重开广播成功了几次 */

    /* ---- build 21：B2-A.2 的 GATT 服务和推图（对齐 tsl0922/EPD-nRF5）------
       这一组是「网页连上来之后到底发生了什么」的全部证据：服务建没建起来、
       命令收到几条、图片两个面各收了多少字节、屏有没有真的刷完。 */
    uint32_t ble_svc_err;         /* 56: ble_gatts_srvc_db_create 的返回码 */
    uint32_t ble_svc_hdl;         /* 57: 服务起始句柄（=0 说明没建起来） */
    uint32_t ble_conn_cnt;        /* 58: 连接上来的次数 */
    uint32_t ble_conn_idx;        /* 59: 最近一次连接的 conn_idx（ZK_NONE_U32 = 没连） */
    uint32_t ble_cccd;            /* 60: CCCD 值（bit0=1 客户端开了通知） */
    uint32_t ble_cmd_cnt;         /* 61: 收到多少条写（命令 + 图块） */
    uint32_t ble_last_cmd;        /* 62: 最后一个命令字节（ZK_NONE_U32 = 还没收到过） */
    uint32_t ble_img_chunks;      /* 63: WRITE_IMAGE(0x30) 收到多少块 */
    uint32_t ble_last_flags;      /* 64: 最后一个 WRITE_IMAGE 的 flags 字节 */
    uint32_t ble_img_bw;          /* 65: 黑白面（RAM 上半）收了多少字节，满 = 15000 */
    uint32_t ble_img_red;         /* 66: 红面收了多少字节 */
    uint32_t ble_rle_out;         /* 67: RLE 解出来多少字节（0 = 网页走的没压缩那条路） */
    uint32_t ble_legacy;          /* 68: 1 = 网页用的是 v1.5 老命令格式（没通知成功时） */
    uint32_t ble_panel_state;     /* 69: 0 没动过 / 1 初始化完 / 2 刷完一帧 / 3 出错 */
    uint32_t ble_panel_ms;        /* 70: 最近一次「写图 + 刷新」用了多少毫秒 */
    uint32_t ble_init_ms;         /* 71: 最近一次屏初始化用了多少毫秒 */
    uint32_t ble_noti_cnt;        /* 72: 一共发出去几条通知 */
    uint32_t ble_noti_err;        /* 73: 最近一次通知 API 的返回码 */
    uint32_t ble_want_init;       /* 74: 收到过几次 INIT(0x01) */
    uint32_t ble_want_refresh;    /* 75: 收到过几次 REFRESH(0x05)/CLEAR(0x02) */
    uint32_t ble_mtu_rpt;         /* 76: 我们告诉网页的可写长度（= min(MTU-3, 244)） */
    uint32_t ble_svc_end_hdl;     /* 77: 服务结束句柄（建库成功后才有） */
    uint32_t ble_svc_db_err;      /* 78: 真正建库那一步的返回码
                                     （build 21 的教训：ble_gatts_srvc_db_create 得由
                                      协议栈的 profile 加载回调去调，直接调 ROM 那个
                                      函数只会建出"描述"、栈里没有，手机就看不到服务） */
    uint32_t ble_busy_delta;      /* 79: 最近一次「写图+刷新」期间 BUSY 被轮询了多少次。
                                     全刷时是几万~几十万次（每次 200us，17 秒以上）；
                                     只有几百 = 屏根本没做全刷（build 22 就是 2277 次，
                                     原因是那次我把"初始化"和"写图+刷新"拆成两段，
                                     中间把屏的供电脚放开了 —— 屏掉电，白写）。 */
    uint32_t ble_gui_mode;        /* 80: 显示模式 0=图片 1=日历 2=时钟 */
    uint32_t ble_gui_ts;          /* 81: 网页同步过来的时间戳（已经加过时区） */
    uint32_t ble_gui_draws;       /* 82: 日历/时钟页一共画过几次 */

    /* ---- build 28：画面选项（反色 / 旋转 180° / 农历开关）------
       命令 0x70 SET_OPTIONS，网页那个「发送命令」框里敲 "7002" 就是旋转。
       这两个变换是写屏之前对整帧做的，所以要做没做、做过几帧，得能看见 ——
       不然"屏上怎么反了"这种问题只能靠猜。 */
    uint32_t ble_opt;             /* 83: 选项位掩码（ZK_OPT_xxx：1反色 2旋转 4不画农历） */
    uint32_t ble_opt_cmds;        /* 84: 收到过几次 0x70 SET_OPTIONS */
    uint32_t ble_opt_frames;      /* 85: 有几帧在写屏时真的做过变换（选项=0 时不涨） */

    /* ---- build 30：日历/时钟那边"为什么重画"（以及时基有没有又跳）------
       build 29 的现象：**什么都不干，屏每 4.5 分钟自己刷一次**。
       根因是 tick_ms() 直接拿 32 位 CYCCNT 除，16MHz 下每 268 秒绕一圈，
       日历页算出来的"现在"会突然跳 49.7 天 -> 判定换天 -> 重画 + 全刷。
       现在时基改成单调的 64 位（board/zk_tick.h），并把这几个数记下来盯着。 */
    uint32_t ble_gui_why;         /* 86: 最近一次重画的原因 1=收到时间/命令 2=换天 3=换分钟 */
    uint32_t ble_gui_elapsed;     /* 87: 那次重画时"距同步时间过了几秒"。正常是几十~几万；
                                     要是看到几百万（≈49.7 天），就是时基又绕了 */
    uint32_t ble_tick_ms;         /* 88: 当前 zk_tick_ms()（低 32 位），看它有没有掉回 0 */

    /* ---- build 31：电池 + 片内温度（表头右上角那两个数）---------------------
       GR5513 的 ADC 内部就有 VBAT / TMP 两个通道，SDK 库里也带了现成接口
       （hal_adc_vbat_read / hal_adc_temp_read），所以这两个数是**真读出来的**。
       读失败时 mv = 0xFFFFFFFF（页面上就不画电池，宁可不画也不画假的）。 */
    uint32_t bat_mv;              /* 89: 电池毫伏（ZK_NONE_U32 = 还没有效读数） */
    uint32_t bat_pct;             /* 90: 电量百分比 0..100（算不出来时 ZK_NONE_U32） */
    uint32_t bat_temp_c10;        /* 91: 片内温度 ×10（26.4℃ -> 264；无效 = ZK_NONE_U32） */
    uint32_t bat_errs;            /* 92: ADC 读失败/超范围了几次（一直在涨 = 通道不对） */

    /* ---- build 41：天气（手机经 BLE 0x71 下发）--------------------------------
       这块板子没有天气/温度传感器（原厂固件里连 I2C 都没有），所以"天气"只能
       从外面来：网页把天气码 + 天气温度发给固件，固件画在表头（"马年"右边）。
       ⚠ ADC 那个"温度"是**芯片结温**，不是环境温度，别拿它当天气。 */
    uint32_t wx_code;             /* 93: 天气码（0 不显示 / 1 晴 / 2 多云 … / 9 风） */
    uint32_t env_temp_c;          /* 94: 手机给的天气温度（有符号 ℃；0xFFFFFF80 = 没收到过） */
    uint32_t wx_cmds;             /* 95: 收到过几次 0x71 SET_WEATHER */

    /* ---- build 42：电压为什么显示 2.59V（ADC 通道/参考/校准，全在这里）----------
       起因：喂 3.3V，表头却写 2.59V。查下来是 ADC 只有一个配置寄存器
       （AON->SNSADC_CFG：通道 + 参考 + 使能都在这一个寄存器里），而 SDK 的
       hal_adc_vbat_read() 只翻 VBAT_EN、**不重选通道** —— 我们开机时先
       vbat_init 再 temp_init，于是"读电池"其实一直在读温度二极管。
       修法：每次读之前重新 init 自己要用的通道（Src/board/zk_bat.c）。
       下面这几个数是给 status.sh 对账用的：原始码值 + 两条路的读数 + 出厂校准。 */
    uint32_t bat_raw;             /* 96: 原始 ADC 平均值 0..4095（没套公式的码值） */
    uint32_t bat_mv_sdk;          /* 97: SDK 的 vbat api（0.85V 参考）读出来的毫伏 */
    uint32_t bat_mv_own;          /* 98: 我们自己的（1.28V 参考）读出来的毫伏 */
    uint32_t adc_trim08;          /* 99: 出厂校准 0.85V 档：slope<<16 | offset */
    uint32_t adc_trim12;          /* 100: 出厂校准 1.28V 档：slope<<16 | offset */
    uint32_t adc_cfg;             /* 101: 读完之后的 AON->SNSADC_CFG（通道/参考） */
    uint32_t adc_trim_rc;         /* 102: sys_adc_trim_get() 的返回值（0 = 读到校准了） */
    uint32_t tz_h;                /* 103: 网页给的时区（有符号小时数，例 8 = 北京；0xFFFFFFFF = 还没同步过）
                                      —— 屏上时间/日期不对时先看这个：浏览器报错时区就会这样 */

    /* ---- build 59：毫秒时基从 CYCCNT 换成 **AON 定时器**（低功耗时钟域）------
       背景：CYCCNT 数的是 CPU 周期，而主频会变（空闲 16 MHz、刷屏/连 BLE 时更高），
       于是"毫秒"快 3~4.8 倍 —— 日历每 ~8 小时跨一天就是这么来的。
       AON 定时器（AON->TIMER_VAL）与主频无关，但**频率要标定**（实测 ~28 kHz，
       不是教科书的 32.768 kHz），所以下面这几个数把"标定 — 选用 — 现场"全记下来，
       status.sh 会把它们译成人话。 */
    uint32_t tb_src;              /* 104: 时基来源：bit0-7 = 1 DWT标定 / 2 SDK值 / 3 名义值 /
                                     0 没用 AON（回退老 DWT 做法）；bit8 = 1 表示计数器**递减** */
    uint32_t tb_hz;               /* 105: 实际用的「每秒多少 tick」（0 = 没在用 AON） */
    uint32_t tb_hz_cal;           /* 106: 开机用 DWT 标定出来的值（不管采不采用；0 = 没标出来） */
    uint32_t tb_hz_sdk;           /* 107: SDK（ROM 的 sys_lpclk_get()）报的 LP 时钟频率（0 = 不可信） */
    uint32_t tb_aon_ticks;        /* 108: AON 计数器的**原始**读数（每轮都刷；在动就说明时钟活着） */
    uint32_t tb_ticks;            /* 109: 累计原始 tick 的低 32 位（÷ tb_hz 就是秒） */
    uint32_t tb_bad;              /* 110: 计数器"倒退/被复位"的异常次数（正常一直是 0） */
    uint32_t tb_jumps;            /* 111: 两次 tick 之间隔了 >1 秒的次数（长阻塞的补记，正常几次） */
    uint32_t rsv[ZK_DBG_WORDS - 112];
} zk_dbg_t;

/* 固定落在 0x3001F000（链接脚本 .dbg_status / RAM_DBG） */
extern volatile zk_dbg_t g_dbg;

static inline void zk_dbg_stage(uint32_t s)
{
    g_dbg.stage = s;
}

#endif /* __ZK_DBG_H__ */
