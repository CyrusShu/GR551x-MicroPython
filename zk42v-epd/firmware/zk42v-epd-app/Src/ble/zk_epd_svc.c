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

#include "gr_includes.h"
#include "ble.h"
#include "ble_att.h"
#include "ble_prf.h"          /* ble_gatts_prf_add —— 建服务必须走它，见下面注释 */

#include <string.h>

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
static uint8_t  s_opt;               /* 画面选项位 ZK_OPT_xxx（命令 0x70 设） */

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
                    s_need_gui = 1;         /* 日历/时钟：重画一页（选项已生效） */
                }

                memcpy(buf, "opt=", 4);
                buf[4] = "0123456789ABCDEF"[(s_opt >> 4) & 0x0F];
                buf[5] = "0123456789ABCDEF"[s_opt & 0x0F];
                zk_notify(buf, 6u);
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
                    s_ts += (uint32_t)((int8_t)d[5]) * 3600u;   /* 时区 */
                }

                /* 模式字节（1=日历 2=时钟，见他网页 syncTime(1)/syncTime(2)）。
                   网页只给"时间点"，页面由我们画 —— 跟原厂一致。 */
                if (len > 6u && (d[6] == ZKGUI_MODE_CALENDAR || d[6] == ZKGUI_MODE_CLOCK))
                {
                    s_mode = d[6];
                }
                else
                {
                    s_mode = ZKGUI_MODE_CALENDAR;
                }
                /* 用 64 位单调时基：低 32 位每 49.7 天绕一次，绕的时候
                   "现在几点"会跳掉（build 29 就是这么每 4.5 分钟自刷一次的） */
                s_ts_ms    = zk_tick_ms64();
                s_need_gui = 1;
                g_dbg.ble_gui_mode = s_mode;
                g_dbg.ble_gui_ts   = s_ts;

                memcpy(p, "t=", 2); p += 2;
                p = zk_put_u32(p, s_ts);
                zk_notify(buf, (uint16_t)(p - buf));
            }
            break;

        case 0x21:      /* SET_WEEK_START：收下就完事 */
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

    /* ---- 日历 / 时钟：该画了就画进缓冲，然后走同一条"写图 + 刷新"的路 ---- */
    if (s_mode != ZKGUI_MODE_PICTURE && s_ts_ms != 0u)
    {
        uint64_t el   = (zk_tick_ms64() - s_ts_ms) / 1000u;   /* 距 SET_TIME 过了几秒 */
        uint32_t cur  = (uint32_t)((uint64_t)s_ts + el);
        int      why  = 0;
        int      redraw = 0;

        if (s_need_gui)
        {
            redraw = 1;                        /* 刚设完时间，立刻画一页 */
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
            info.temp_c10 = (int16_t)zk_bat_temp_c10();
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

        s_need_refresh = 0;
        if (!zk_panel_ensure_init())
        {
            g_dbg.ble_panel_state = 3;                   /* 3 = 出错 */
            return;
        }

        /* 到这里脚是攥着的（ensure_init 或上一次 INIT 留下的），直接写图。
           万一没有（比如客户端没发 INIT 就推图，而我们刚被断开重置过），
           就先补一次初始化。 */
        if (!s_gpio_ready)
        {
            epd_gpio_init();
            epd_reset();
            epd_init_sequence();
            s_gpio_ready = 1;
        }

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
        epd_write_image((const uint8_t *)ZK_IMG_BUF);
        zk_opt_transform((uint8_t *)ZK_IMG_BUF, ZK42V_EPD_ROW_BYTES,
                         ZK42V_EPD_HEIGHT, s_opt);
        epd_refresh_ex(0xC7, 0);

        /* 这一轮屏到底忙了多久 —— 全刷时是几万次轮询（17 秒以上）。
           要是只有几百次，说明屏压根没做全刷（build 22 就是 2277 次）。 */
        g_dbg.ble_busy_delta = g_dbg.busy_polls - p0;

        epd_pins_release();
        s_gpio_ready = 0;
        s_panel_inited = 0;                              /* 脚放开了 = 初始化状态作废 */

        g_dbg.ble_panel_state = 2;                       /* 2 = 刷完一帧 */
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
