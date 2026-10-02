/*
 * B2-A.2：EPD 服务实现（对齐 tsl0922/EPD-nRF5）
 * =====================================================================
 *  目标：让 tsl0922 那个网页（手机 Chrome / iOS Bluefy）连上来、推一张图、
 *        我们的屏把它画出来。协议细节是从他原厂固件 EPD/EPD_service.c 里
 *        逐条读出来的，实现上刻意跟他保持一致 —— 尤其下面这三件容易踩的事：
 *
 *  1) **通知的顺序**：网页把「收到的第 0 条通知」当成配置结构体解析
 *     （pins/model），第 1 条才当文本（"mtu=.. rle=1"）。所以他原厂固件是
 *     在客户端开通知(CCCD)时立刻推一份配置，INIT 时再推 mtu/time。
 *     我们也照这个顺序来，否则网页会把 "mtu=..." 当配置解析，
 *     并且**永远不会打开 RLE**（那传输就慢十倍）。
 *
 *  2) **flags 位定义**（WRITE_IMAGE 的第二个字节）：
 *       bit0 = 0 黑白面 / 1 红面
 *       bit1 = 1 本面的第一块
 *       bit2 = 1 这一块是 RLE 压缩的
 *     只有网页处于「新流程」时才用这套位（它问了 rle=1 之后）。万一通知没
 *     送到、网页退回 v1.5 老格式（bit0-3=0x0F 表示黑白面、bit4=非首块），
 *     我们也能认出来并照老规矩解 —— 见 s_legacy。
 *
 *  3) **别把屏的重活堵在 BLE 事件回调里**：一次刷新要等 BUSY 十几秒。
 *     所以命令一到只记「要做什么」，真正的初始化/写图/刷新放到空闲循环的
 *     zk_epd_svc_poll() 里做；写完再发一条通知。
 * =====================================================================
 */
#include "zk_epd_svc.h"
#include "zk_dbg.h"
#include "epd_zk42v.h"
#include "zkgui.h"           /* 日历 / 时钟页面的绘制 */
#include "zk_opt.h"          /* 画面选项：反色 / 旋转 180°（写屏前对整帧做变换） */
#include "zk_bat.h"          /* 电池/温度：表头右上角要画 */
#include "weather.h"         /* 天气码（手机下发）：表头画图标 */

#include "gr_includes.h"
#include "ble.h"
#include "ble_att.h"
#include "ble_prf.h"          /* ble_gatts_prf_add —— 建服务必须走它，见下面注释 */

#include <string.h>
#include "gr55xx.h"     /* SystemCoreClock（build 55 的观测用） */

/* ---------------------------------------------------------------- UUID */
#define ZK_UUID128(b12, b13)                                                  \
    { 0xEC, 0x5A, 0x67, 0x1C, 0xC1, 0xB6, 0x46, 0xFB,                         \
      0x8D, 0x91, 0x28, 0xD8, (b12), (b13), 0x75, 0x62 }

static const uint8_t s_svc_uuid[16] = ZK_UUID128(0x01, 0x00);

/* 网页读这个判断固件新旧；他原厂是 0x1a，>= 0x16 才走新流程 */
#define ZK_APP_VERSION   0x1au

/* 写特征的值最大长度 = 对端 MTU - 3（ATT 写请求/通知的开销 3 字节）。
   网页拿到我们通知的 "mtu=N" 之后按 N-2 切图块（还得留 1 字节命令 + 1 字节 flags）。 */
#define ZK_WR_MAX_LEN    244u

/* ------------------------------------------------------- 属性表（一次性建库） */
enum
{
    ZK_IDX_SVC = 0,
    ZK_IDX_WR_CHAR,      /* 62750002 的特征声明 */
    ZK_IDX_WR_VAL,       /* 62750002 的值：网页往这儿写命令/图块 */
    ZK_IDX_WR_CFG,       /* 62750002 的 CCCD */
    ZK_IDX_VER_CHAR,     /* 62750003 的特征声明 */
    ZK_IDX_VER_VAL,      /* 62750003 的值：版本号（只读） */
    ZK_IDX_NB,
};

#define ZK_ATT_128_PRIMARY_SERVICE  BLE_ATT_16_TO_128_ARRAY(BLE_ATT_DECL_PRIMARY_SERVICE)
#define ZK_ATT_128_CHARACTERISTIC   BLE_ATT_16_TO_128_ARRAY(BLE_ATT_DECL_CHARACTERISTIC)
#define ZK_ATT_128_CCCD             BLE_ATT_16_TO_128_ARRAY(BLE_ATT_DESC_CLIENT_CHAR_CFG)

#define ZK_ATT_USER_128  (BLE_GATTS_ATT_VAL_LOC_USER | \
                          BLE_GATTS_ATT_UUID_TYPE_SET(BLE_GATTS_UUID_TYPE_128))

static const ble_gatts_attm_desc_128_t s_attr_tab[ZK_IDX_NB] =
{
    [ZK_IDX_SVC]      = { ZK_ATT_128_PRIMARY_SERVICE,
                          BLE_GATTS_READ_PERM_UNSEC, 0, 0 },

    [ZK_IDX_WR_CHAR]  = { ZK_ATT_128_CHARACTERISTIC,
                          BLE_GATTS_READ_PERM_UNSEC, 0, 0 },
    [ZK_IDX_WR_VAL]   = { ZK_UUID128(0x02, 0x00),
                          BLE_GATTS_WRITE_REQ_PERM_UNSEC |
                          BLE_GATTS_WRITE_CMD_PERM_UNSEC |
                          BLE_GATTS_NOTIFY_PERM_UNSEC,
                          ZK_ATT_USER_128,
                          ZK_WR_MAX_LEN },
    [ZK_IDX_WR_CFG]   = { ZK_ATT_128_CCCD,
                          BLE_GATTS_READ_PERM_UNSEC |
                          BLE_GATTS_WRITE_REQ_PERM_UNSEC, 0, 0 },

    [ZK_IDX_VER_CHAR] = { ZK_ATT_128_CHARACTERISTIC,
                          BLE_GATTS_READ_PERM_UNSEC, 0, 0 },
    [ZK_IDX_VER_VAL]  = { ZK_UUID128(0x03, 0x00),
                          BLE_GATTS_READ_PERM_UNSEC,
                          ZK_ATT_USER_128,
                          1 },
};

/* ---------------------------------------------------------------- 内部状态 */

static uint16_t s_start_hdl;
static uint16_t s_end_hdl;
static uint8_t  s_conn_idx;
static uint8_t  s_connected;
static uint16_t s_cccd;
static uint8_t  s_cfg_sent;          /* 配置通知只发一次（网页按 idx==0 解析它） */
static uint16_t s_mtu = 23;          /* 协商结果（默认 23） */

static uint8_t  s_panel_inited;      /* 屏的初始化序列跑过没有 */
static uint8_t  s_gpio_ready;        /* 屏那 7 根脚现在是不是我们占着 */
static uint8_t  s_need_init;
static uint8_t  s_need_refresh;
static uint8_t  s_refresh_ctrl = 0xC7;   /* 0x22 的控制字；0x75 可改（0xC7 全刷 / 0xD7 带温度） */
static uint8_t  s_refresh_temp = 0;      /* 1 = 刷新前先写 0x18/0x1A 温度 */
static uint8_t  s_fast_refresh = 0;      /* 0x76：1 = 走原厂那条"短延时快刷"（D7 + 查表延时） */
static uint8_t  s_partial_on = 0;        /* 0x77：1 = 只刷下面这个矩形 */
static uint8_t  s_drv = 0x55;            /* 0x78：快刷驱动强度（0x1A 的值），默认照抄原厂 0x55 */
static uint8_t  s_px0, s_py0, s_px1, s_py1;   /* 局刷窗口：x 是"字节列"(0~49)，y 是行(0~299) */
static uint8_t  s_need_sleep;
static uint8_t  s_need_cfg;          /* 该发「配置」那条通知了（见下面为什么不立刻发） */
static uint32_t s_wr_seen;           /* 这一条连接里收到第几块 WRITE_IMAGE（用来认老格式） */

static uint8_t  s_plane;             /* 0 = 黑白面, 1 = 红面 */
static uint32_t s_plane_pos[2];      /* 每个面已经收了多少字节 */
static uint8_t  s_legacy;            /* 1 = 网页在用 v1.5 老命令格式 */
static uint32_t s_ts;                /* 客户端同步过来的时间（秒） */

/* ---- 日历 / 时钟页面（B2-A.2 第二步） --------------------------------
 * 原厂的分工：网页只发"时间戳 + 模式字节"，画是固件干的；
 * 重画节奏也照抄原厂 ble_epd_on_timer：
 *     MODE_CALENDAR：换天（00:00）重画一次
 *     MODE_CLOCK   ：每分钟重画一次（原厂说这模式就是用来除残影的，页面也提醒了）
 */
static uint8_t  s_mode;              /* 0 = 图片, 1 = 日历, 2 = 时钟 */
static uint64_t s_ts_ms;             /* 设时间那一刻的 zk_tick_ms64()，用来往后推 */
static uint32_t s_drawn_day;         /* 上次画的是哪一天（cur/86400） */
static uint32_t s_drawn_min;         /* 上次画的是哪一分钟（cur/60） */
static uint8_t  s_need_gui;          /* 立刻重画一次 */
static uint32_t s_gui_due_ms;        /* build 72：预约重画的时刻（合并窗口，见下） */
static uint8_t  s_opt;               /* 画面选项位 ZK_OPT_xxx（命令 0x70 设） */
/* 天气（手机经 0x71 下发）：这块板子上没有天气/温度传感器，天气只能从外面来 */
static uint8_t  s_wx_code;           /* 0 = 不显示 */
static int8_t   s_env_temp_c = (int8_t)(-128);   /* -128 = 还没收到过 */
/* build 54：天气温度的**十分之一度**（例如 346 = 34.6℃）。
   为什么要有它：0x71 原来只能带**整度**（int8），"不要四舍五入"就得走这条。
   ZK_TEMP_NONE = 没有；有值时表头按"一位小数"画（复用片内温度那条画法）。 */
static int16_t  s_env_temp_c10 = ZK_TEMP_NONE;
/* build 61：表头温度后面那个**城市名**（"多云 26.4℃ 深圳"）。
   基站知道自己的经纬度，所以由基站发（命令 0x79 SET_CITY <utf8>）——
   固件不联网。字模只有 tools/gen_font.py 的 CITY_CHARS 那批字，
   认不出来的字画不出来（会跳过），所以城市名尽量用常见字。 */
static char     s_city[24];
/* build 67：**纪念日提醒**（用户要的生日高亮）。基站经 0x7A 发下来：
   月、日、祝福语。日历翻到那个月时：那天套黑框 + 空白处框出这句话。 */
static int8_t   s_memo_mon;
static int8_t   s_memo_day;
static char     s_memo[40];
/* build 71：**天气预警**（基站经 0x7C 发下来，见下面 case 0x7C 的注释）。
   和风的预警按经纬度取，基站那边取回后顺手发过来；清空 = level 0。 */
static int8_t   s_alert_level;
static char     s_alert_type[24];
/* build 74：和风的预警图标编号（1003 暴雨 / 1014 雷电…），0 = 没有/不知道。
   单独一条命令（0x7D）下发 —— 这样老固件（不认识 0x7D）会**直接忽略**，
   只是画不出图标，不会把预警名弄乱。 */
static int16_t  s_alert_code;

/* --------------------------------------------------- build 72：**合并刷新窗口**

   用户 2026-10-02："价签开机刷的次数太多了，有没有办法精简。"
   实机数出来的是这样（基站 build-23 开机那一轮）：
       18s  0x79 城市名            → 整页重画 1 次
       18s  0x7A 纪念日 + 0x7B 续传 → 整页重画 2 次
       78s  0x20 时间+天气          → 整页重画 1 次
       79s  0x7C 天气预警           → 整页重画 1 次
   一共 5 次全刷 ≈ 80 秒在闪。根因是**每条命令都立刻整页重画**：

       gui_request_redraw();   ……  poll 每 5ms 看一次：if (s_need_gui) { 清标志; 画; 刷 16 秒 }

   标志在**开始重画的那一刻**就被清掉，而刷新是阻塞的十几秒 —— 所以后面来的命令
   一律变成"下一次重画"。一次连接里连发 3 条 = 刷 3 次。

   改法：命令不再"立刻画"，而是把预约时间推到 now + 2 秒；poll 里到点才画。
   2 秒内有新命令就一直往后推 —— 同一条连接里的那几条就被合并成**一次**刷新。
   ⚠ 注意这不会漏掉刷新：基站一次连接内部的命令间隔是几十毫秒到 1 秒，
     2 秒窗口足够；而"下一轮连接"（几十秒后）早过了窗口，照样各刷各的。
   代价：屏上更新最多晚 2 秒（日历页完全看不出来）。 */
#define ZK_GUI_COALESCE_MS  2000u

/* 所有"需要重画"的命令都改调它（原来是直接写 s_need_gui = 1） */
static void gui_request_redraw(void)
{
    s_need_gui   = 1;
    s_gui_due_ms = zk_tick_ms() + ZK_GUI_COALESCE_MS;
}

/* 往纪念日文案后面接一段（build 68）。为什么要"接"：BLE 的 ATT 一次只能发
   MTU-3 字节，MTU=23 时只有 20 字节 —— 而一句祝福语 24+ 字节，基站只能分几次发。
   基站用 0x7A 起头（带月日）、0x7B 续传。这里只做"追加 + 截断"，不关心分几段。 */
static void memo_append(const uint8_t *p, uint16_t n)
{
    uint16_t have = (uint16_t)strlen(s_memo);

    if ((uint32_t)have + n >= (uint32_t)sizeof(s_memo))
    {
        n = (uint16_t)(sizeof(s_memo) - 1u - have);
    }
    if (n > 0u)
    {
        memcpy(s_memo + have, p, n);
    }
    s_memo[have + n] = 0;
}
static uint8_t  s_bat_notify;        /* 0x72 之后：下一次 poll 把刚读到的电池值回报给网页 */
static int8_t   s_tz_h;              /* 网页给的时区（小时）；只用来回报/记账，见 SET_TIME */

/* 版本号的“值”放在用户空间（VAL_LOC_USER），读请求我们自己回 */
static uint8_t  s_version = ZK_APP_VERSION;

#define ZK_NONE  0xFFFFFFFFu

/* 这份建库描述**必须是常驻的**：框架里存的是指向它的指针，等协议栈来加载
   profile 的时候（ble_service_load_cb）才拿它去真正建库。 */
static ble_gatts_create_db_t s_db;

/* ---------------------------------------------------------------- 通知 */

static void zk_notify(const uint8_t *data, uint16_t len)
{
    ble_gatts_noti_ind_t ntf;
    sdk_err_t            err;

    g_dbg.ble_noti_cnt++;
    if (!s_connected || 0 == (s_cccd & 0x0001u))
    {
        return;                           /* 客户端没开通知，发也没人要 */
    }

    memset(&ntf, 0, sizeof(ntf));
    ntf.type   = BLE_GATT_NOTIFICATION;
    ntf.handle = (uint16_t)(s_start_hdl + ZK_IDX_WR_VAL);
    ntf.length = len;
    ntf.value  = (uint8_t *)data;
    err = ble_gatts_noti_ind(s_conn_idx, &ntf);
    g_dbg.ble_noti_err = err;
}

/* 手工拼十进制（不引 printf —— 那玩意儿要好几 KB flash） */
static uint8_t *zk_put_u32(uint8_t *p, uint32_t v)
{
    uint8_t t[10];
    uint8_t i = 0;

    if (0 == v)
    {
        *p++ = '0';
        return p;
    }
    while (v)
    {
        t[i++] = (uint8_t)('0' + (v % 10u));
        v /= 10u;
    }
    while (i)
    {
        *p++ = t[--i];
    }
    return p;
}

/* 「mtu=<可写长度> rle=1」—— 网页靠它设分块大小、并打开 RLE。
   注意这里报的是**可写字节数**（不是 ATT MTU）：跟他原厂固件的语义一致，
   网页拿到后按 N-2 切图块。 */
static void zk_notify_mtu(void)
{
    uint8_t buf[24];
    uint8_t *p = buf;
    uint32_t m = (s_mtu > 3u) ? (uint32_t)(s_mtu - 3u) : 20u;

    if (m > ZK_WR_MAX_LEN)
    {
        m = ZK_WR_MAX_LEN;
    }
    g_dbg.ble_mtu_rpt = m;

    memcpy(p, "mtu=", 4);  p += 4;
    p = zk_put_u32(p, m);
    memcpy(p, " rle=1", 6); p += 6;
    zk_notify(buf, (uint16_t)(p - buf));
}

/* 通知 #0：配置结构体（跟他原厂 epd_config_t 一样的 13 字节）。
   网页拿它填「引脚」「驱动型号」两个输入框 —— 型号 0x03 = 4.2 寸三色 UC8176，
   这样网页的预览就是 400x300 三色、抖动模式也对得上。
   引脚我们其实不采用（我们的脚是写死的），但填对了好读。 */
static void zk_notify_config(void)
{
    uint8_t cfg[13];
    uint8_t i;

    for (i = 0; i < sizeof(cfg); i++)
    {
        cfg[i] = 0xFF;
    }
    cfg[0] = ZK42V_PIN_SDI_RAW;    /* mosi */
    cfg[1] = ZK42V_PIN_SCLK_RAW;   /* sclk */
    cfg[2] = ZK42V_PIN_CS_RAW;     /* cs   */
    cfg[3] = ZK42V_PIN_DC_RAW;     /* dc   */
    cfg[4] = ZK42V_PIN_RST_RAW;    /* rst  */
    cfg[5] = ZK42V_PIN_BUSY_RAW;   /* busy */
    cfg[6] = 0xFF;                 /* bs   （没有） */
    cfg[7] = 0x03;                 /* model: 4.2" 三色 UC8176 */
    cfg[8] = 0xFF;                 /* wakeup pin */
    cfg[9] = 0xFF;                 /* led */
    cfg[10] = 0xFF;                /* en */
    cfg[11] = 0;                   /* display_mode */
    cfg[12] = 0;                   /* week_start */
    zk_notify(cfg, sizeof(cfg));
}

/* ---------------------------------------------------------------- 屏的重活 */

int zk_panel_ensure_init(void)
{
    if (s_panel_inited)
    {
        return 1;
    }

    epd_gpio_init();
    epd_reset();
    epd_init_sequence();
    s_panel_inited = 1;
    s_gpio_ready   = 1;                      /* **脚先攥着**，见下面的注释 */
    return 1;
}

/* 为什么初始化完**不能**马上放开那 7 根脚（build 22 的教训）
 * ------------------------------------------------------------------
 * 那 7 根脚里 AUX = P1_8 是屏的供电/使能。原厂固件的节奏是
 *      pins_init -> 复位 -> 初始化 -> 写图 -> 刷新 -> **pins_release**
 * 也就是"一次会话把活干完，然后放开脚=给屏断电省电"。
 *
 * build 22 我把它拆成两段（INIT 一段、REFRESH 一段），INIT 干完就松了脚，
 * 等 REFRESH 再来时屏已经掉电/丢了初始化状态 —— 写 RAM + 发 0x22/0x20 之后，
 * 屏只在 BUSY 上象征性忙了 455 毫秒（成功刷屏那几次是 8 万~47 万次轮询，
 * 17~94 秒），屏上完全没反应。
 *
 * 所以现在：初始化完脚一直攥着，直到一次"写图+刷新"做完才放开，
 * 放开的同时把 s_panel_inited 作废（下次要用就重新初始化一遍）。 */

/* ---------------------------------------------------------------- 面的极性问题
 *
 * 网页（以及他原厂固件）那套 SSD16xx/UC81xx 的约定是：
 *     黑白面 bit=1 -> 白；   **红面 bit=0 -> 红**（红面"低有效"）
 * 证据：网页 js/dithering.js 里 threeColor 那段 `redWhiteBit = 红像素 ? 0 : 1`，
 *       他原厂 SSD16xx_Clear 把两个面都填 0xFF 当"全白"。
 *
 * 但**我们这块屏实测是反的**（见 ../img/testimg.c 和 B1.5/B1.6 两次上色实验，
 * 以及 tools/img2epd.py：红像素写的是红面 bit=1）：
 *     黑白面 bit=1 -> 白；   **红面 bit=1 -> 红**
 * 所以从网页收下来的红面数据要**整片取反**再进我们的缓冲。
 *
 * 万一哪天发现颜色反了（屏上一片红底、该红的地方不红），把下面这个宏改成 0
 * 重编即可 —— 清屏那条路也会跟着一起变。
 */
#define ZK_WEB_RED_IS_ACTIVE_LOW  1

/* 把一块数据追到某个面的缓冲里（红面按上面说的取反）。返回实际写入的字节数 */
static uint32_t zk_plane_append(uint8_t plane, const uint8_t *src, uint32_t n)
{
    uint8_t *dst = (uint8_t *)ZK_IMG_BUF +
                   (plane ? (uint32_t)ZK42V_EPD_PLANE_BYTES : 0u);
    uint32_t pos = s_plane_pos[plane];
    uint32_t cap = (uint32_t)ZK42V_EPD_PLANE_BYTES - pos;
    uint32_t i;

    if (n > cap)
    {
        n = cap;
    }
#if ZK_WEB_RED_IS_ACTIVE_LOW
    if (plane)
    {
        for (i = 0; i < n; i++)
        {
            dst[pos + i] = (uint8_t)~src[i];
        }
        s_plane_pos[plane] = pos + n;
        return n;
    }
#endif
    memcpy(&dst[pos], src, n);
    s_plane_pos[plane] = pos + n;
    return n;
}

/* RLE 解压一块（网页保证每块里的 RLE 码是完整的，所以可以逐块解）。
   规则就是他原厂那套：控制字节最高位=1 -> 重复下一个字节 (c&0x7F)+3 次；
   否则 -> 后面跟 c+1 个原文字节。
   src_pos 是**进出参数**：进来时是这块里已经解到的位置，出去时是解到哪儿了
   （解不完的码留在原地，由调用方下一轮接着喂）。返回写进 dst 的字节数。 */
static uint32_t zk_rle_decode(const uint8_t *src, uint16_t len,
                              uint16_t *src_pos, uint8_t *dst, uint32_t cap)
{
    uint16_t pos = *src_pos;
    uint32_t out = 0;

    while (pos < len)
    {
        uint8_t c = src[pos];

        if (c & 0x80u)
        {
            uint32_t cnt = (uint32_t)(c & 0x7Fu) + 3u;
            uint8_t  v;

            if (pos + 1u >= len || out + cnt > cap)
            {
                break;
            }
            pos++;
            v = src[pos++];
            while (cnt--)
            {
                dst[out++] = v;
            }
        }
        else
        {
            uint32_t cnt = (uint32_t)c + 1u;

            if (pos + 1u + cnt > len || out + cnt > cap)
            {
                break;
            }
            pos++;
            memcpy(&dst[out], &src[pos], cnt);
            pos += cnt;
            out += cnt;
        }
    }
    *src_pos = pos;
    return out;
}

/* ---------------------------------------------------------------- 命令 */

static void zk_cmd_write_image(const uint8_t *d, uint16_t len)
{
    uint8_t        flags = d[1];
    uint16_t       data_len = (uint16_t)(len - 2u);
    const uint8_t *src = &d[2];
    uint8_t        plane;
    uint8_t        begin;
    uint8_t        rle;

    g_dbg.ble_img_chunks++;
    s_wr_seen++;
    g_dbg.ble_last_flags = flags;

    /* 认一下网页用的是新格式还是 v1.5 老格式（老格式 bit4 会亮，新格式不会） */
    if (1u == s_wr_seen)
    {
        if (0x0Fu == flags || 0u != (flags & 0xF0u))
        {
            s_legacy = 1;
        }
        g_dbg.ble_legacy = s_legacy;
    }

    if (s_legacy)
    {
        /* v1.5：bit0-3 = 0x0F 表示黑白面，bit4 = 非首块 */
        plane = ((flags & 0x0Fu) == 0x0Fu) ? 0u : 1u;
        begin = (0u == (flags & 0x10u)) ? 1u : 0u;
        rle   = 0u;
    }
    else
    {
        plane = (flags & 0x01u) ? 1u : 0u;
        begin = (flags & 0x02u) ? 1u : 0u;
        rle   = (flags & 0x04u) ? 1u : 0u;
    }

    s_plane = plane;
    if (begin)
    {
        s_plane_pos[plane] = 0;
    }

    if (rle)
    {
        /* 一块里可能装着好几段完整的 RLE 码：解一段、写一段、接着解 */
        static uint8_t tmp[256];
        uint16_t       pos = 0;

        while (pos < data_len)
        {
            uint32_t n = zk_rle_decode(src, data_len, &pos, tmp, sizeof(tmp));

            if (0u == n)
            {
                break;
            }
            (void)zk_plane_append(plane, tmp, n);
            g_dbg.ble_rle_out += n;
        }
    }
    else
    {
        (void)zk_plane_append(plane, src, data_len);
    }

    g_dbg.ble_img_bw  = s_plane_pos[0];
    g_dbg.ble_img_red = s_plane_pos[1];
}

static void zk_cmd_handle(const uint8_t *d, uint16_t len)
{
    uint8_t cmd = d[0];

    g_dbg.ble_cmd_cnt++;
    g_dbg.ble_last_cmd = cmd;

    switch (cmd)
    {
        case 0x00:      /* SET_PINS：我们的引脚是写死的，收下就完事 */
            break;

        case 0x01:      /* INIT：屏复位 + 初始化序列（放到 poll 里做） */
            s_need_init = 1;
            g_dbg.ble_want_init++;
            break;

        case 0x02:      /* CLEAR：整屏刷白 */
            /* 全白：黑白面全 1（1=白），红面全 0（我们这块屏 1=红，所以 0 才是"没有红"）。
               要是红面这里填 0xFF，屏会整片变红 —— 这条正好可以当极性自检：
               点"清屏"如果出来的是白的，红面极性就跟我们理解的一致。 */
            memset((void *)ZK_IMG_BUF, 0xFF, ZK42V_EPD_PLANE_BYTES);
            memset((void *)((uint8_t *)ZK_IMG_BUF + ZK42V_EPD_PLANE_BYTES),
                   0x00, ZK42V_EPD_PLANE_BYTES);
            s_plane_pos[0] = 0;
            s_plane_pos[1] = 0;
            g_dbg.ble_img_bw  = 0;
            g_dbg.ble_img_red = 0;
            s_mode = ZKGUI_MODE_PICTURE;     /* 清屏也算"图片模式"，别再自动重画时钟 */
            g_dbg.ble_gui_mode = s_mode;
            s_need_refresh = 1;
            g_dbg.ble_want_refresh++;
            break;

        case 0x03:      /* SEND_CMD：直接把一个字节发给屏（网页的调试页用） */
            if (len >= 2u)
            {
                if (!s_gpio_ready)
                {
                    epd_gpio_init();
                    s_gpio_ready = 1;
                }
                epd_cmd_raw(d[1]);
            }
            break;

        case 0x04:      /* SEND_DATA：把一串字节当数据发给屏 */
            if (len >= 2u)
            {
                if (!s_gpio_ready)
                {
                    epd_gpio_init();
                    s_gpio_ready = 1;
                }
                epd_data_raw(&d[1], (uint32_t)(len - 1u));
            }
            break;

        case 0x05:      /* REFRESH：把缓冲写进屏 + 刷新 */
            s_need_refresh = 1;
            g_dbg.ble_want_refresh++;
            break;

        case 0x06:      /* SLEEP：屏进深睡 */
            s_need_sleep = 1;
            break;

        case 0x30:      /* WRITE_IMAGE：收图块 */
            if (len >= 3u)
            {
                s_mode = ZKGUI_MODE_PICTURE;      /* 推图 = 切回图片模式，别让时钟再盖回来 */
                g_dbg.ble_gui_mode = s_mode;
                zk_cmd_write_image(d, len);
            }
            break;

        case 0x90:      /* SET_CONFIG：我们的配置是写死的，收下就完事 */
            break;

        /* 0x70 SET_OPTIONS：我们自己的扩展（网页那个「发送命令」框里直接敲就行）
         *   70 00 -> 全关（默认）      70 01 -> 反色
         *   70 02 -> 旋转 180°         70 04 -> 日历页不画农历
         *   70 08 -> 节气加粗（画两遍）
         *   位可以叠加，比如 70 06 = 旋转 + 不画农历。
         * 反色/旋转是在**写屏之前**对整帧做的（见 zk_opt.h），所以对网页推的图
         * 和固件自己画的日历/时钟页一视同仁；缓冲写完会还原，SWD 信箱不受影响。 */
        case 0x70:
            if (len >= 2u)
            {
                uint8_t buf[16];

                s_opt = (uint8_t)(d[1] & ZK_OPT_ALL);
                zkgui_set_lunar((s_opt & ZK_OPT_NO_LUNAR) ? 0 : 1);
                zkgui_set_term_bold((s_opt & ZK_OPT_TERM_BOLD) ? 1 : 0);

                g_dbg.ble_opt = s_opt;
                g_dbg.ble_opt_cmds++;

                if (ZKGUI_MODE_PICTURE == s_mode)
                {
                    s_need_refresh = 1;     /* 图片模式：重刷一帧就能看到效果 */
                }
                else
                {
                    gui_request_redraw();         /* 日历/时钟：重画一页（选项已生效） */
                }

                memcpy(buf, "opt=", 4);
                buf[4] = "0123456789ABCDEF"[(s_opt >> 4) & 0x0F];
                buf[5] = "0123456789ABCDEF"[s_opt & 0x0F];
                zk_notify(buf, 6u);
            }
            break;

        /* 0x71 SET_WEATHER：手机下发天气（我们自己定的私有命令）
         *   71 <code> <temp>      code 见 weather.h（1 晴 2 多云 … 9 风）
         *                         temp 是**有符号整度**（℃），例：71 02 1A = 多云 26℃
         *   71 00                 = 不显示天气
         * 为什么要手机下发：这板子上没有温度传感器（原厂固件里连 I2C 都没有），
         * 而且"天气"本来就只能从外部来。 */
        case 0x71:
            if (len >= 2u)
            {
                s_wx_code = (d[1] <= ZK_WX_MAX) ? d[1] : 0u;
                /* build 54：**优先**认 4 字节版（带一位小数）：71 <code> <t_hi> <t_lo>
                   t 是 int16 的**十分之一度**（34.6℃ → 346 = 0x01 0x5A）。
                   3 字节的老格式照旧（整度），网页不受影响。 */
                if (len >= 4u)
                {
                    int16_t t10 = (int16_t)(((uint16_t)d[2] << 8) | (uint16_t)d[3]);

                    s_env_temp_c10 = t10;
                    /* 兼容字段（状态块/通知里还在用它）：按"四舍五入到整度"填，
                       但显示优先用 c10，所以屏上不会再被舍入。 */
                    s_env_temp_c   = (int8_t)((t10 >= 0) ? ((t10 + 5) / 10) : ((t10 - 5) / 10));
                }
                else if (len >= 3u)
                {
                    s_env_temp_c = (int8_t)d[2];
                    s_env_temp_c10 = ZK_TEMP_NONE;
                }
                else
                {
                    s_env_temp_c = (int8_t)(-128);
                    s_env_temp_c10 = ZK_TEMP_NONE;
                }
                g_dbg.wx_code    = s_wx_code;
                g_dbg.env_temp_c = (uint32_t)(int32_t)s_env_temp_c;
                g_dbg.wx_cmds++;

                if (ZKGUI_MODE_PICTURE != s_mode)
                {
                    gui_request_redraw();          /* 日历/时钟页：重画一版 */
                }

                /* 回一条通知，网页日志里能看到设成了什么 */
                {
                    uint8_t nb[24];
                    uint8_t *q = nb;

                    memcpy(q, "wx=", 3); q += 3;
                    q = zk_put_u32(q, s_wx_code);
                    memcpy(q, " t=", 3); q += 3;
                    q = zk_put_u32(q, (uint32_t)(int32_t)s_env_temp_c);
                    zk_notify(nb, (uint16_t)(q - nb));
                }
            }
            break;

        case 0x20:      /* SET_TIME：把时间原样回给网页（我们不做日历模式） */
            if (len >= 5u)
            {
                uint8_t buf[24];
                uint8_t *p = buf;

                s_ts = ((uint32_t)d[1] << 24) | ((uint32_t)d[2] << 16) |
                       ((uint32_t)d[3] << 8) | (uint32_t)d[4];
                if (len > 5u)
                {
                    /* 时区（有符号小时数）。⚠ 网页那边报错时区的话，屏上的时间/日期就跟着错：
                       build 45 起把它记进状态块（tz_h），status.sh 会印出来 —— 之前遇到过
                       浏览器报 +9，结果 23:05 就跳到第二天。 */
                    s_tz_h = (int8_t)d[5];
                    s_ts += (uint32_t)s_tz_h * 3600u;
                    g_dbg.tz_h = (uint32_t)(int32_t)s_tz_h;
                }
                else
                {
                    s_tz_h = 0;
                    g_dbg.tz_h = 0xFFFFFFFFu;
                }

                /* 模式字节（1=日历 2=时钟，见他网页 syncTime(1)/syncTime(2)）。
                   网页只给"时间点"，页面由我们画 —— 跟原厂一致。

                   build 60：**0 = 保持当前模式（别动页面）**。
                   为什么要它：基站（ESP32 / Mac）每次推天气都会**顺手带一次时间**
                   给价签对表（用户 2026-10-01 的要求：取天气+温度时把时间一起推，
                   这样钟一直是校准的）。但基站并不知道用户现在看的是哪一页 ——
                   没有这个 0，它一推天气就会把时钟页/推的图顶掉换成日历页。 */
                if (len > 6u && (d[6] == ZKGUI_MODE_CALENDAR || d[6] == ZKGUI_MODE_CLOCK))
                {
                    s_mode = d[6];
                }
                else if (len > 6u && d[6] == 0u)
                {
                    /* 保持当前模式：s_mode 不动 */
                    g_dbg.time_keep_cnt++;
                }
                else
                {
                    s_mode = ZKGUI_MODE_CALENDAR;
                }
                g_dbg.set_time_cmds++;

                /* build 50：**可选把天气一起带上**（一条命令 = 只画一页、只刷一次）
                 *   20 <utc4> <tz> <mode> [wx] [temp]
                 * 为什么：以前时间、天气是两条命令，每条都触发一次"画图 + 刷新"，
                 * 一次同步要闪两次（各 17 秒）；合并之后只闪一次。
                 * 向后兼容：老客户端（网页、以及没更新的基站）只发 6 字节，行为不变。 */
                if (len > 7u)
                {
                    s_wx_code     = (d[7] <= ZK_WX_MAX) ? d[7] : 0u;
                    if (len > 9u)            /* build 54：带一位小数（int16 十分之一度） */
                    {
                        int16_t t10 = (int16_t)(((uint16_t)d[8] << 8) | (uint16_t)d[9]);

                        s_env_temp_c10 = t10;
                        s_env_temp_c   = (int8_t)((t10 >= 0) ? ((t10 + 5) / 10) : ((t10 - 5) / 10));
                    }
                    else
                    {
                        s_env_temp_c   = (len > 8u) ? (int8_t)d[8] : (int8_t)(-128);
                        s_env_temp_c10 = ZK_TEMP_NONE;
                    }
                    g_dbg.wx_code    = s_wx_code;
                    g_dbg.env_temp_c = (uint32_t)(int32_t)s_env_temp_c;
                    g_dbg.wx_cmds++;
                }
                /* 用 64 位单调时基：低 32 位每 49.7 天绕一次，绕的时候
                   "现在几点"会跳掉（build 29 就是这么每 4.5 分钟自刷一次的） */
                s_ts_ms    = zk_tick_ms64();
                /* 图片模式下不重画：那张图是网页推的，跟时间无关。
                   （build 60 起 0x20 可以"只对表、不换页"，所以这里必须跟 0x71
                     那条一样加护栏 —— 否则基站推一次天气就把用户的图顶掉了） */
                if (ZKGUI_MODE_PICTURE != s_mode)
                {
                    gui_request_redraw();
                }
                g_dbg.ble_gui_mode = s_mode;
                g_dbg.ble_gui_ts   = s_ts;

                memcpy(p, "t=", 2); p += 2;
                p = zk_put_u32(p, s_ts);
                zk_notify(buf, (uint16_t)(p - buf));
            }
            break;

        case 0x21:      /* SET_WEEK_START：收下就完事 */
            break;

        /* 0x72 READ_BAT：**立刻重读一次电池 + 重画一页**（挑电量图标 / 拿台电源扫电压时用）。
           为什么这里只置标志不直接读：这个回调是 pwr_mgmt_schedule() 里跑起来的，
           主循环接下来先走 zk_bat_poll()（build 43 把它挪到 zk_epd_svc_poll() 前面），
           所以"读电池 → 画页面 → 回报读数"都在下一次 poll 里完成，
           zk_bat_trigger() 就是让它跳过那 60 秒的限速。 */
        case 0x72:
            zk_bat_trigger();
            s_bat_notify = 1;
            if (ZKGUI_MODE_PICTURE != s_mode)
            {
                gui_request_redraw();
            }
            break;

        /* 0x79 SET_CITY：表头温度后面那个城市名（build 61）
         *   79 <utf8 城市名>      例：79 E6 B7 B1 E5 9C B3 = "深圳"
         *   空 payload（只发 79）= 清掉。
         *   为什么让基站发：它才知道自己的经纬度（Mac 版 --city / ESP32 版 CITY_NAME），
         *   固件不联网。字模只认 gen_font.py 的 CITY_CHARS 那批字；画不下的字会跳过，
         *   整段放不下就不画（右边是电池图标，不能压上去）。 */
        case 0x79:
            {
                uint16_t n = (uint16_t)(len - 1u);

                if (n >= (uint16_t)sizeof(s_city))
                {
                    n = (uint16_t)sizeof(s_city) - 1u;
                }
                if (n > 0u)
                {
                    memcpy(s_city, d + 1, n);
                }
                s_city[n] = 0;
                g_dbg.city_cmds++;
                if (ZKGUI_MODE_PICTURE != s_mode)
                {
                    gui_request_redraw();
                }
                /* 回一条通知 "city=深圳" —— 基站日志里能直接看到价签收下了什么
                   （排"屏上怎么没有城市名"时，这一行能立刻分清是"基站没发"还是"固件没画"） */
                {
                    uint8_t  nb[24];
                    uint8_t *q = nb;
                    uint16_t k;

                    memcpy(q, "city=", 5);
                    q += 5;
                    for (k = 0; s_city[k] != 0 && k < 16u; k++)
                    {
                        *q++ = (uint8_t)s_city[k];
                    }
                    zk_notify(nb, (uint16_t)(q - nb));
                }
            }
            break;

        /* 0x7A SET_MEMO：**纪念日提醒**（build 67）
         *   7A <mon> <day> <utf8 祝福语>    例：7A 0A 05 + "付婧文生日快乐！"
         *   只发 7A（或 day=0）= 清掉。
         *   画法（Src/img/zkgui.c 的 draw_calendar 末尾）：日历翻到那个月时，
         *   那天那格套一个黑框，并在"1 号左边那片空白"里框出这句话；
         *   空白不够就退到最后一行右边，再不够就只圈日子（不压字）。
         *   生日是"按月日重复"的，所以每年这个月都会亮。
         *   ⚠ 祝福语只能用字模里有的字（gen_font.py 的 MEMO_CHARS），认不出的会被跳过。 */
        case 0x7A:
            s_memo[0] = 0;                      /* 起头 = 重新开始攒文案 */
            if (len >= 3u)
            {
                uint16_t n = (uint16_t)(len - 3u);

                s_memo_mon = (int8_t)d[1];
                s_memo_day = (int8_t)d[2];
                memo_append(d + 3, n);
            }
            else                                /* 只发 0x7A = 清掉 */
            {
                s_memo_mon = 0;
                s_memo_day = 0;
            }
            g_dbg.memo_cmds++;
            if (ZKGUI_MODE_PICTURE != s_mode)
            {
                gui_request_redraw();
            }
            break;

        /* 0x7B MEMO_MORE：**纪念日文案的续传段**（build 68）
         *   7B <utf8 片段>
         * 为什么要它：ATT 一次只能发 MTU-3 字节（MTU=23 时 = 20），而一句
         * "付婧文生日快乐！" 是 24 字节 —— 基站按 UTF-8 字符边界切成几段，
         * 0x7A 起头、0x7B 接着发。实机（ESP32 基站 build-8）就是这么踩到的：
         * 一条 27 字节的写触发了 Bluedroid 的"长写"，价签不支持 → 卡 40 秒后失败，
         * 屏上一直没框。分片之后就跟 MTU 无关了。 */
        case 0x7B:
            if (len >= 2u && s_memo_day > 0)
            {
                memo_append(d + 1, (uint16_t)(len - 1u));
            }
            break;

        /* 0x7C SET_ALERT：**天气预警**（build 71）
         *   7C <level> <utf8 类型名>   例：7C 04 + "暴雨"（橙色预警 → level=4）
         *   只发 7C（或 level=0）= 清掉。
         *   level：1白 2蓝 3黄 4橙 5红（和风的 color.code，基站那边映射好的）。
         *   为什么这么设计（用户 2026-10-02）："手机上是局地雷暴雨而且有暴雨警报，
         *   中继获取的是小雨"。和风老预警接口已下架，新的是
         *   weatheralert/v1/current —— 由 NAS 上的中继取回，基站顺手 0x7C 发下来。
         *   画法：表头那格"天气文字"被它顶掉（详见 zkgui.c 的 header_alert_text），
         *   ≥3（黄/橙/红）用红、1~2（白/蓝）用黑。类型名只认 ALERT_CHARS 里的字。 */
        case 0x7C:
            s_alert_type[0] = 0;
            s_alert_code    = 0;            /* 预警换/清了 → 图标编号也一起作废 */
            if (len >= 2u)
            {
                uint16_t n = (uint16_t)(len - 2u);

                s_alert_level = (int8_t)d[1];
                if (n >= (uint16_t)sizeof(s_alert_type))
                {
                    n = (uint16_t)sizeof(s_alert_type) - 1u;
                }
                memcpy(s_alert_type, d + 2, n);
                s_alert_type[n] = 0;
            }
            else
            {
                s_alert_level = 0;
            }
            g_dbg.alert_cmds++;
            if (ZKGUI_MODE_PICTURE != s_mode)
            {
                gui_request_redraw();
            }
            break;

        /* 0x7D SET_ALERT_ICON：**预警图标编号**（build 74）
         *   7D <hi> <lo>    和风的预警 icon 编号，例 7D 03 EB = 1003（暴雨）
         *   7D（空）= 清掉，回到画天气图标。
         *   ⚠ 为什么单独一条命令而不是塞进 0x7C：**向后兼容** —— 老固件收到
         *   没见过的 0x7D 直接走 default 忽略，屏上只是没图标，预警名照旧；
         *   要是把两个字节塞进 0x7C，老固件会把它当成预警名的前两个字节 → 乱码。
         *   固件端拿这个编号去 alert_icons.c 查表（认不出走"通用预警"）。 */
        case 0x7D:
            s_alert_code = 0;
            if (len >= 3u)
            {
                s_alert_code = (int16_t)(((uint16_t)d[1] << 8) | d[2]);
            }
            g_dbg.alert_cmds++;
            if (ZKGUI_MODE_PICTURE != s_mode)
            {
                gui_request_redraw();
            }
            break;

        /* 0x75 SET_REFRESH_CTRL：**局刷实验开关**（build 49）
         *   75 <ctrl> [temp]   ctrl = 0x22 那个控制字：
         *                     0xC7 = 现在用的全刷；0xD7 = 原厂第二条路（带温度）
         *                     temp = 1 时先写 0x18/0x1A（温度传感器）。
         *   为什么要有它：`03/04` 那条"原始命令"通道只把字节透传给面板，
         *   **不经过 epd_flush_frame() 的整轮重试**，冷面板上第一次激活会被屏忽略
         *   （13:54 实测：`raw "03 22 | 04 C7 | 03 20"` 完全不闪）。
         *   这条则走**能工作的那条路径**（build 48 的写图+刷新整轮重试），
         *   于是可以干净地 A/B 对比 C7 / D7 到底闪多久、忙多久。
         *   发完立刻重画一页（build 72 起走合并窗口），不需要额外的刷新命令。 */
        case 0x75:
            if (len >= 2u)
            {
                s_refresh_ctrl = d[1];
            }
            s_refresh_temp = (len >= 3u) ? d[2] : 0u;
            gui_request_redraw();
            {
                uint8_t nb[20];
                uint8_t *q = nb;

                memcpy(q, "rc=", 3); q += 3;
                q = zk_put_u32(q, s_refresh_ctrl);
                memcpy(q, " t=", 3); q += 3;
                q = zk_put_u32(q, s_refresh_temp);
                zk_notify(nb, (uint16_t)(q - nb));
            }
            break;

        /* 0x76 SET_FAST_REFRESH：**快刷开关**（build 51）
         *   76 00 = 关（走 0xC7 全刷 + 整轮重试）
         *   76 01 = 开（走原厂那条：0x18/0x1A + 0x22=0xD7 + 0x20，等 0.16~0.64 秒，
         *            **不轮询 BUSY、不重试**）
         * 依据：原厂 func 0x0100FE84 + 表 0x0100D8A0（见 epd_zk42v.c 的 epd_refresh_fast）。 */
        case 0x76:
            s_fast_refresh = (len >= 2u) ? d[1] : 1u;
            gui_request_redraw();
            break;

        /* 0x77 SET_PARTIAL：**只刷一个矩形**（build 52，实验用）
         *   77 <x0> <y0> <x1> <y1>   x = 字节列 0~49（×8 = 像素列），y = 行 0~299
         *   77                        = 取消局刷（回整屏）
         * 走的是原厂那条快刷（D7 + 0.64s），外面套局部窗口 0x90/0x91/0x92。 */
        case 0x77:
            if (len >= 5u)
            {
                s_px0 = d[1]; s_py0 = d[2]; s_px1 = d[3]; s_py1 = d[4];
                s_partial_on   = 1;
                s_fast_refresh = 1;
            }
            else
            {
                s_partial_on   = 0;
                s_fast_refresh = 0;
            }
            gui_request_redraw();
            break;

        /* 0x78 SET_DRIVE：**快刷驱动强度**（build 53，局刷参数扫描）
         *   78 <param>   param 写进面板的 0x1A（就是原厂快刷里那个固定 0x55）
         * qbsg 社区给的语义（原话）：
         *   "局刷，以 16 进制输入 01 到 0f。关闭红色局刷并且校准黑色局刷，输入 10 到 f0。
         *    越大颜色越深"
         * 我们同步重画一页走快刷路径（不轮询 BUSY，等 0.64s）。 */
        case 0x78:
            if (len >= 2u)
            {
                s_drv          = d[1];
                s_fast_refresh = 1;
            }
            gui_request_redraw();
            break;

        case 0x91:      /* SYS_RESET */
            NVIC_SystemReset();
            break;

        case 0x92:      /* SYS_SLEEP：我们是不睡的基站，收下就完事 */
        case 0x99:      /* CFG_ERASE：配置文件对我们没意义 */
            break;

        default:
            break;
    }
}

/* ---------------------------------------------------------------- GATTS 读写 */

void zk_epd_svc_on_write(uint8_t conn_idx, uint16_t handle,
                         const uint8_t *value, uint16_t length)
{
    ble_gatts_write_cfm_t cfm;
    uint16_t              idx;

    cfm.handle = handle;
    cfm.status = BLE_SUCCESS;

    if (0u == s_start_hdl || handle < s_start_hdl ||
        handle >= (uint16_t)(s_start_hdl + ZK_IDX_NB))
    {
        cfm.status = BLE_ATT_ERR_INVALID_HANDLE;
        ble_gatts_write_cfm(conn_idx, &cfm);
        return;
    }
    idx = (uint16_t)(handle - s_start_hdl);

    if (ZK_IDX_WR_VAL == idx)
    {
        if (length >= 1u)
        {
            zk_cmd_handle(value, length);
        }
    }
    else if (ZK_IDX_WR_CFG == idx)
    {
        if (length >= 2u)
        {
            s_cccd = (uint16_t)(value[0] | ((uint16_t)value[1] << 8));
            g_dbg.ble_cccd = s_cccd;

            /* 通知刚被打开：**第一条**通知必须是配置结构体 —— 网页按
               「第 0 条通知」解析它（引脚/驱动型号）。顺序错了，网页就
               永远开不了 RLE。

               注意这里是**先记账、等 poll 再发**：CCCD 写请求的响应得先
               发出去，客户端（Chrome 的 startNotifications）才算订阅成功，
               抢在前面发的那条通知有被丢掉的风险。 */
            if ((s_cccd & 0x0001u) && !s_cfg_sent)
            {
                s_need_cfg = 1;
            }
        }
    }
    /* 别的句柄（比如谁往版本特征写）—— 收下就完事 */

    ble_gatts_write_cfm(conn_idx, &cfm);
}

void zk_epd_svc_on_read(uint8_t conn_idx, uint16_t handle)
{
    ble_gatts_read_cfm_t cfm;

    cfm.handle = handle;
    cfm.status = BLE_SUCCESS;
    cfm.length = 0;
    cfm.value  = NULL;

    if (0u == s_start_hdl || handle < s_start_hdl ||
        handle >= (uint16_t)(s_start_hdl + ZK_IDX_NB))
    {
        cfm.status = BLE_ATT_ERR_INVALID_HANDLE;
    }
    else
    {
        uint16_t idx = (uint16_t)(handle - s_start_hdl);

        if (ZK_IDX_VER_VAL == idx)
        {
            cfm.length = 1;                   /* 网页读它判断固件新旧 */
            cfm.value  = &s_version;
        }
        else if (ZK_IDX_WR_CFG == idx)
        {
            cfm.length = 2;                   /* CCCD 由我们自己保管 */
            cfm.value  = (uint8_t *)&s_cccd;
        }
        else
        {
            cfm.status = BLE_ATT_ERR_INVALID_HANDLE;
        }
    }

    ble_gatts_read_cfm(conn_idx, &cfm);
}

/* ---------------------------------------------------------------- 连接/MTU */

void zk_epd_svc_on_connect(uint8_t conn_idx)
{
    s_conn_idx  = conn_idx;
    s_connected = 1;
    s_cfg_sent  = 0;
    s_legacy    = 0;
    s_wr_seen   = 0;
    s_need_cfg  = 0;
    s_plane_pos[0] = 0;
    s_plane_pos[1] = 0;
    g_dbg.ble_conn_cnt++;
    g_dbg.ble_conn_idx = conn_idx;
    g_dbg.ble_legacy   = 0;
}

void zk_epd_svc_on_disconnect(void)
{
    s_connected = 0;
    s_cccd      = 0;
    s_cfg_sent  = 0;
    g_dbg.ble_conn_idx = ZK_NONE;
    g_dbg.ble_cccd     = 0;

    /* 把屏的脚松开：万一 P1_8 那种脚跟射频前端有关系，断开了就别占着 */
    if (s_gpio_ready)
    {
        epd_pins_release();
        s_gpio_ready = 0;
    }
}

void zk_epd_svc_on_mtu(uint16_t mtu)
{
    if (mtu > 23u)
    {
        s_mtu = mtu;
    }
    g_dbg.ble_mtu = mtu;
}

/* ---------------------------------------------------------------- 建服务 */

/* 我们这个服务自己的事件回调。
 *
 * 为什么 GATTS 的事件在这儿收、不在 zk_ble.c 的全局回调里收：
 * SDK 的分发是这样的（ble_event_handle 反汇编）：
 *      先问各个 profile -> 谁认领了就不再往下传 -> 最后才轮到全局回调
 * 所以服务注册成 profile 之后，我们这个范围的读写请求只会递到这儿来。
 * 全局回调那两个 GATTS case 因此是死的，已经删掉。 */
static void zk_epd_svc_evt_handler(const ble_evt_t *p_evt)
{
    switch (p_evt->evt_id)
    {
        case BLE_GATTS_EVT_DATABASE_INITED_IND:
            /* 协议栈刚拿着我们那份描述建完库，把句柄范围告诉我们；
               evt_status 就是建库那一步的返回码（0 = 成功）。
               —— 这就是 build 21 缺的那一环：光调 ROM 的建库函数、
                 不告诉协议栈，手机侧压根看不到这个服务。 */
            s_start_hdl = p_evt->evt.gatts_evt.params.inited_ind.start_hdl;
            s_end_hdl   = p_evt->evt.gatts_evt.params.inited_ind.end_hdl;
            g_dbg.ble_svc_hdl     = s_start_hdl;
            g_dbg.ble_svc_end_hdl = s_end_hdl;
            g_dbg.ble_svc_db_err  = p_evt->evt_status;
            break;

        case BLE_GATTS_EVT_READ_REQUEST:
            zk_epd_svc_on_read(p_evt->evt.gatts_evt.index,
                               p_evt->evt.gatts_evt.params.read_req.handle);
            break;

        case BLE_GATTS_EVT_WRITE_REQUEST:
            zk_epd_svc_on_write(p_evt->evt.gatts_evt.index,
                                p_evt->evt.gatts_evt.params.write_req.handle,
                                p_evt->evt.gatts_evt.params.write_req.value,
                                p_evt->evt.gatts_evt.params.write_req.length);
            break;

        case BLE_GATTS_EVT_CCCD_RECOVERY:
            /* 我们不做配对/绑定，正常不会来；真来了就当"通知被关掉"处理 */
            s_cccd = p_evt->evt.gatts_evt.params.cccd_recovery.cccd_val;
            g_dbg.ble_cccd = s_cccd;
            break;

        default:
            break;
    }
}

void zk_epd_svc_init(void)
{
    sdk_err_t err;

    /* build 46：**开机默认就是日历模式**（以前是 0 = 图片模式）。
       为什么：这块价签是当"电子日历"用的，装电池/上电之后就该等着显示日历，
       而不是停在"图片模式"等手机来推图。改完的行为：
         · 上电 → 模式 = 日历（状态块里也这么记），屏上还是上一次的画面（墨水屏不掉电就不变）；
         · 手机连上一点「日历模式」/「时钟模式」→ 立刻按对应页面重画（本来就是这个流程）；
         · 只要有人用网页**推图**（WRITE_IMAGE/CLEAR），模式会自动切回"图片"
           （下面 case 里那两处 s_mode = ZKGUI_MODE_PICTURE），不会被日历盖回来。
       ⚠ 注意：时间还没同步过的时候（s_ts_ms == 0）不会自动画 ——
       也就是说"装电池就自己显示日历"还差一步：得把时间也存进 flash（见 README 的待办）。 */
    s_mode = ZKGUI_MODE_CALENDAR;
    g_dbg.ble_gui_mode = s_mode;

    s_start_hdl = 0;                  /* 0 = PRF_INVALID_HANDLE："让栈自己分配" */
    s_end_hdl   = 0;
    memset(&s_db, 0, sizeof(s_db));
    s_db.shdl          = &s_start_hdl;
    s_db.uuid          = s_svc_uuid;
    s_db.attr_tab_cfg  = NULL;        /* NULL = 整张属性表都加 */
    s_db.max_nb_attr   = ZK_IDX_NB;
    s_db.srvc_perm     = BLE_GATTS_SRVC_UUID_TYPE_SET(BLE_GATTS_UUID_TYPE_128);
    s_db.attr_tab_type = BLE_GATTS_SERVICE_TABLE_TYPE_128;
    s_db.attr_tab.attr_tab_128 = s_attr_tab;

    /* 必须走 ble_gatts_prf_add（SDK 里每个 profile 都是这么建的）。
       它做三件事：在协议栈里登记一个 profile 槽、把这份描述和回调存进框架、
       然后告诉 GAPM「profile 加好了」—— 协议栈随后回来加载 profile
       （ble_service_load_cb）时才真正调 ble_gatts_srvc_db_create 建库，
       建完给我们发 BLE_GATTS_EVT_DATABASE_INITED_IND。

       ⚠ build 21 的坑：直接调 ROM 的 ble_gatts_srvc_db_create() 是"成功"的
         （返回 0、还给了句柄 0x0001），但**没人告诉协议栈**，ATT 库从来没建，
         手机一看：No Services matching UUID … found in Device。 */
    err = ble_gatts_prf_add(&s_db, zk_epd_svc_evt_handler);
    g_dbg.ble_svc_err = err;
}

/* ---------------------------------------------------------------- 空闲循环 */

void zk_epd_svc_poll(uint32_t now_ms)
{
    /* build 57 起 zk_tick_ms() 是**实时**的（以前只在"画一页"时写一次，两次 status
       读到同一个数是正常的，不是时基停了）。
       build 59 起这一刷连"时基现场证据"（tb_* 那几个字段）一起写 —— 见 main.c 的
       zk_tick_ms()。test_step / ble_tick_ms 都由它维护，这里不再自己写。 */
    g_dbg.ble_tick_ms = zk_tick_ms();
    /* 客户端刚打开通知：先把「配置」这条通知补上（等 CCCD 的响应发完了再发，
       免得被客户端当成"还没订阅"丢掉）。网页按「第 0 条通知」解析它。 */
    if (s_need_cfg)
    {
        s_need_cfg = 0;
        if (!s_cfg_sent)
        {
            zk_notify_config();
            s_cfg_sent = 1;
        }
    }

    /* 0x72 的回报：电池刚被强制读了一次（主循环里 zk_bat_poll() 跑在我们前面），
       把结果发回网页 —— 扫电压的时候盯着网页日志就知道读到多少。 */
    if (s_bat_notify)
    {
        uint8_t  nb[24];
        uint8_t *q = nb;

        s_bat_notify = 0;
        memcpy(q, "bat=", 4); q += 4;
        q = zk_put_u32(q, (uint32_t)(int32_t)zk_bat_mv());
        memcpy(q, " pct=", 5); q += 5;
        q = zk_put_u32(q, (uint32_t)(int32_t)zk_bat_pct());
        zk_notify(nb, (uint16_t)(q - nb));
    }

    /* ---- 日历 / 时钟：该画了就画进缓冲，然后走同一条"写图 + 刷新"的路 ---- */
    if (s_mode != ZKGUI_MODE_PICTURE && s_ts_ms != 0u)
    {
        uint64_t el   = (zk_tick_ms64() - s_ts_ms) / 1000u;   /* 距 SET_TIME 过了几秒 */
        uint32_t cur  = (uint32_t)((uint64_t)s_ts + el);
        int      why  = 0;
        int      redraw = 0;

        /* build 72：**到点才画**（合并窗口）—— 命令只把预约时间往后推，
           这里等 2 秒内没有新命令了才真画。一次连接里连发的几条会被并成一次全刷。
           比较用减法再转 int32：毫秒计数器是 32 位、会绕圈，直接比大小会出错。

           ⚠⚠ 有挂起的重画请求时，**下面那两个"换天/每分钟"的分支一律不许插队**
           （2026-10-02 实测踩到）：价签刚上电时 `s_drawn_day = 0`，第一条命令
           （0x20 时间+天气）一到，"换天"判定立刻成立 → 当场先画一版 —— 那会儿
           城市名/纪念日/预警**还没发到**，于是屏上只有"阴"；剩下的命令只能等下一轮，
           再刷一次才有"暴雨"。用户看到的就是"刷两次，第一次阴、第二次暴雨"。
           所以这里改成：只要 s_need_gui 挂着，就**只看窗口到没到点**，
           换天/每分钟的判断等这次画完再说（它们下一轮照样会成立，不会丢）。 */
        if (s_need_gui)
        {
            redraw = ((int32_t)(zk_tick_ms() - s_gui_due_ms) >= 0);
            why    = 1;
        }
        else if (ZKGUI_MODE_CALENDAR == s_mode)
        {
            redraw = (cur / 86400u != s_drawn_day);    /* 换天了（原厂也是 00:00 重画） */
            why    = 2;
        }
        else
        {
            redraw = (cur / 60u != s_drawn_min);       /* 时钟模式：每分钟一张 */
            why    = 3;
        }

        if (redraw)
        {
            zkgui_info_t info;

            s_need_gui   = 0;
            s_drawn_day  = cur / 86400u;
            s_drawn_min  = cur / 60u;
            info.ts      = cur;
            info.mode    = s_mode;
            info.bat_mv  = (int16_t)zk_bat_mv();
            info.bat_pct = (int8_t)zk_bat_pct();
            /* build 54：**天气温度有位小数就用它**（不当整度画）——
               用户明确要求"温度不要四舍五入"。表头那条"带一位小数"的画法本来就有
               （片内温度在用），这里复用：把 c10 填天气值、env_temp_c 置 -128 让它走小数分支。 */
            if (s_env_temp_c10 != ZK_TEMP_NONE)
            {
                info.temp_c10   = s_env_temp_c10;
                info.env_temp_c = (int8_t)(-128);
            }
            else
            {
                info.temp_c10   = (int16_t)zk_bat_temp_c10();
                info.env_temp_c = s_env_temp_c;
            }
            info.wx_code  = s_wx_code;
            info.city     = s_city;          /* build 61：温度后面那个城市名（基站 0x79 下发） */
            info.memo_mon = s_memo_mon;      /* build 67：纪念日高亮（基站 0x7A 下发） */
            info.memo_day = s_memo_day;
            info.memo     = s_memo;
            info.alert_level = s_alert_level;   /* build 71：天气预警（基站 0x7C 下发） */
            info.alert_type  = s_alert_type;
            info.alert_code  = s_alert_code;    /* build 74：预警图标编号（基站 0x7D 下发） */
            zkgui_draw((uint8_t *)ZK_IMG_BUF, &info);

            s_need_refresh = 1;                /* 交给下面的刷新分支去写屏 */
            s_plane_pos[0] = 0;
            s_plane_pos[1] = 0;
            g_dbg.ble_gui_draws++;
            /* 记下"为什么画"和"这一画距同步时间过了多久" —— 时基要是又出问题
               （比如哪次改动退回到用 32 位/CYCCNT 直除），这个秒数会突然变成
               几十万、几百万那种离谱值，一眼就能看出来。 */
            g_dbg.ble_gui_why     = (uint32_t)why;
            g_dbg.ble_gui_elapsed = (uint32_t)el;
            g_dbg.ble_tick_ms     = zk_tick_ms();
        }
    }

    if (s_need_init)
    {
        /* 注意：这里要用 zk_tick_ms() 现场读，不能用外面传进来的 now_ms ——
           屏的初始化/刷新整个跑在这次调用里，now_ms 中途是不会变的，
           拿它算差值永远是 0（build 22 的日志里那两个 0 ms 就是这么来的）。 */
        uint32_t t0 = zk_tick_ms();

        s_need_init = 0;
        zk_panel_ensure_init();
        g_dbg.ble_panel_state = 1;                       /* 1 = 初始化完 */
        g_dbg.ble_init_ms = zk_tick_ms() - t0;
        zk_notify_mtu();                                 /* 网页靠它开 RLE */
    }

    if (s_need_refresh)
    {
        uint32_t t0 = zk_tick_ms();
        uint32_t p0 = g_dbg.busy_polls;
        int      pass;

        s_need_refresh = 0;

        /* 画面选项（反色/旋转）在写屏这一步统一生效 —— 网页推的图和固件画的
           日历/时钟页都走这里，两条路不用各写一遍。
           两个变换都是自逆的，写完再变换一次就等于把缓冲还原，
           免得 0x70 的效果"粘"在 ZK_IMG_BUF 里影响后面（比如 SWD 信箱那条路）。 */
        zk_opt_transform((uint8_t *)ZK_IMG_BUF, ZK42V_EPD_ROW_BYTES,
                         ZK42V_EPD_HEIGHT, s_opt);
        if (s_opt & (ZK_OPT_INVERT | ZK_OPT_ROT180))
        {
            g_dbg.ble_opt_frames++;
        }

        /* **写图 + 刷新整轮重试**（build 48）：地址、复位、初始化、写图、激活
           全在 epd_flush_frame() 里；BUSY 没忙够 1 秒（= 屏没真刷）就断电重来，
           最多 3 轮。为什么不是只重发激活：build 47 那么干过，3 次全被屏忽略
           （status 13:30:18：BUSY 合计才 1.4 秒），屏上什么都没变。
           返回 0 表示三轮都没真刷 —— 状态块记成 panel_state=3，一眼能看出来。 */
        if (s_partial_on)
        {
            /* 局刷指定矩形（build 52）：设局部窗口 → partial in → 写图 → D7 快刷 → partial out。
               x 传的是"字节列"，这里 ×8 换算成像素列。 */
            epd_gpio_init();
            epd_reset();
            epd_init_sequence();
            epd_write_image((const uint8_t *)ZK_IMG_BUF);
            epd_refresh_fast_window(s_drv, (int)s_px0 * 8, (int)s_py0,
                                    (int)s_px1 * 8 + 7, (int)s_py1);
            pass = 1;
        }
        else if (s_fast_refresh)
        {
            /* 原厂快刷路径（build 51）：复位 → 初始化 → 写图 → D7 + 短延时。
               **不轮询 BUSY、不重试** —— 原厂就是这么干的（见 epd_refresh_fast 的注释）。
               上一版把这条路塞进"BUSY 没忙够就整轮重来"，反而把它判成失败、连做 3 轮，
               结果 172 秒 + 画面错 —— 所以这里必须完全照原厂语义走。 */
            epd_gpio_init();
            epd_reset();
            epd_init_sequence();
            epd_write_image((const uint8_t *)ZK_IMG_BUF);
            epd_refresh_fast(s_drv);
            pass = 1;
        }
        else
        {
            pass = epd_flush_frame((const uint8_t *)ZK_IMG_BUF, s_refresh_ctrl,
                                   (int)s_refresh_temp);
        }

        zk_opt_transform((uint8_t *)ZK_IMG_BUF, ZK42V_EPD_ROW_BYTES,
                         ZK42V_EPD_HEIGHT, s_opt);

        /* 这一轮屏到底忙了多久 —— 全刷时是几万次轮询（17 秒以上）。
           要是只有几百/几千次，说明屏压根没做全刷。 */
        g_dbg.ble_busy_delta = g_dbg.busy_polls - p0;

        /* 三轮都没真刷的话，panel_state 会是 3、busy_delta 会很小 —— 这两个字段
           已经够定位（新加字段要动状态块布局 + status 工具的偏移表，不值得）。 */
        epd_pins_release();
        s_gpio_ready = 0;
        s_panel_inited = 0;                              /* 脚放开了 = 初始化状态作废 */

        g_dbg.ble_panel_state = (pass > 0) ? 2 : 3;      /* 2 = 刷完一帧  3 = 没刷成 */
        g_dbg.ble_panel_ms = zk_tick_ms() - t0;
    }

    if (s_need_sleep)
    {
        s_need_sleep = 0;
        if (s_panel_inited)
        {
            epd_gpio_init();
            epd_deep_sleep();
            epd_pins_release();
            s_gpio_ready  = 0;
            s_panel_inited = 0;                          /* 睡下去就要重新初始化 */
        }
    }
}
