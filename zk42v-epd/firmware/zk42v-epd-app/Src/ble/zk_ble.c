/*
 * B2-A：BLE 外设（对齐 tsl0922/EPD-nRF5 那套协议）
 *
 * =====================================================================
 *  B2-A.2（build 19）：两个实验，一次 flash 同时回答两个问题
 * =====================================================================
 *
 *  背景（2026-09-27 挖出来的）。之前一直以为「控制器从不回 ADV_START 事件」，
 *  其实**事件回来了**：状态块里 ble_evt_id = 0x207（就是 BLE_GAPM_EVT_ADV_START）、
 *  ble_evt_status = 0x4A。0x4A = BLE_GAP_ERR_ADV_DATA_INVALID
 *  ——「Duplicate or invalid advertising data」（广播数据非法/重复）。
 *  （那个 ble_evt_count 读出来是天文数字，是因为 main() 原来没清它、
 *    这块 RAM 又是 NOLOAD —— 不是计数器坏了。build 19 一并修掉。）
 *
 *  所以现在的怀疑是：广播**参数**没毛病、命令全被接受，但广播**数据**被协议栈
 *  在 adv_start 那一刻判非法，于是链路层根本没开始广播，空中一个包都没有。
 *  最可疑的一条：我们自己往广播包里塞了 Flags(0x01) —— 而 SDK 自带例程从来不塞，
 *  它靠协议栈按 disc_mode 自己加。两条 Flags 撞一起就是"duplicate"。
 *
 *  实验一（扫描）：起协议栈以后先当一会儿观察者，用
 *      ble_gap_scan_param_set + ble_gap_scan_start + BLE_GAPM_EVT_ADV_REPORT
 *    数周围能听到几个设备（去重后的设备数 + 原始条数 + 最后/最强 RSSI）。
 *      · 听得到  -> 射频活着、收通路是好的，问题在发送侧（下面实验二）
 *      · 听不到  -> 射频/链路层压根没跑起来，转去反汇编原厂固件的 BLE 使能路径
 *
 *  实验二（广播数据变体）：扫描结束后，把 6 种广播数据组合**挨个试一遍** ——
 *  每试一种就记下 ADV_START 事件带回来的 status，全部记进状态块。
 *  哪一种是 0，就说明那种数据是控制器认的；一次 flash 把「到底哪条 AD 结构
 *  非法」钉死，不用来回猜。
 *
 *  判据（bash status.sh 能直接读出来）：
 *      ble_scan_start_st = 0            扫描命令被接受
 *      ble_scan_rpts/devs > 0           射频活的
 *      ble_adv_stN       = 0            变体 N 的广播数据被接受
 *      ble_adv_stN       = 0x4A         就是这个变体的广播数据非法
 *      ble_adv_stN       = 0xFFFFFFFF   这个变体连事件都没等到
 * =====================================================================
 */
#include "zk_ble.h"
#include "zk_dbg.h"
#include "zk_epd_svc.h"      /* B2-A.2：GATT 服务 / 推图协议 */
#include "zk_pwr.h"          /* 2026-10-03 省电：广播间隔 ZK_ADV_INTERVAL */

#include "gr_includes.h"
#include "ble.h"

#include <string.h>

/* 协议栈要的堆表（SDK 的宏，必须在文件作用域） */
STACK_HEAP_INIT(heaps_table);

#define ZK_BLE_NAME  "ZK42V-EPD"

/* 1 = 先扫描（数设备）再挨个试广播数据变体；
   0 = 不做实验，开机直接用变体 #0 那套（已经实测被接受的）数据广播。

   build 19 是 1（那一轮的任务是回答「射频活没活 / 广播被什么拒了」）；
   build 20 改成 0 —— 两个问题都有答案了，产品路径上不该每次开机先听 4 秒。
   要把扫描实验再跑一遍（比如换板子、或者以后做「附近有什么」的功能）就改回 1。 */
#define ZK_BLE_SCAN_TEST 0

/* ------------------------------------------------------------------
 * B2-A.2：广播数据变体表
 *
 * 每个变体给出「广播包数据 + 扫描响应数据」一对。索引就是状态块里
 * ble_adv_stN 的 N。
 *
 * 顺序是**按嫌疑大小**排的：第一个就是最可能修好的那个（去掉 Flags），
 * 万一它一次就成了，串口/网页那边立刻就能用上；后面几个是排除法用的。
 * ------------------------------------------------------------------ */

/* 128 位服务 UUID：62750001-d828-918d-fb46-b6c11c675aec（小端 16 字节） */
static const uint8_t s_uuid_epd[] =
{
    0xEC, 0x5A, 0x67, 0x1C, 0xC1, 0xB6, 0x46, 0xFB,
    0x8D, 0x91, 0x28, 0xD8, 0x01, 0x00, 0x75, 0x62,
};

/* --------- 广播包数据（AD 结构已经排好长度，别手抖） --------- */

/* 变体 0：厂商数据 + 完整名字，**不带 Flags** —— 首要怀疑对象的修法 */
static const uint8_t s_adv_v0[] =
{
    /* 厂商数据：长度 = 1(type) + 2(公司ID) + 5("ZK42V") = 8。
       build 19 这里写的是 0x09（多算了一个字节），后果很具体：
       它把下一条结构的长度字节 0x0A 吞了 → 手机 nRF Connect 显示
       "<FFFF> 5A4B 3432 560A"（末尾那个 0A 就是赃物），
       而名字结构从此错位、被读成 type=0x5A，于是设备名显示成 **N/A**。
       0x08 才是对的 —— 见 tools/test_adv_data.py，那个自测今后会盯着这件事。 */
    0x08, 0xFF, 0xFF, 0xFF, 'Z', 'K', '4', '2', 'V',
    0x0A, 0x09, 'Z', 'K', '4', '2', 'V', '-', 'E', 'P', 'D',
};

/* 变体 1：现在这版（Flags + 厂商数据 + 名字）—— 对照组，
   如果实验没错，它应该还是回 0x4A */
static const uint8_t s_adv_v1[] =
{
    0x02, 0x01, 0x06,
    0x08, 0xFF, 0xFF, 0xFF, 'Z', 'K', '4', '2', 'V',
    0x0A, 0x09, 'Z', 'K', '4', '2', 'V', '-', 'E', 'P', 'D',
};

/* 变体 2：只剩名字 —— 把「厂商数据」这条也摘掉 */
static const uint8_t s_adv_v2[] =
{
    0x0A, 0x09, 'Z', 'K', '4', '2', 'V', '-', 'E', 'P', 'D',
};

/* 变体 3：照抄 SDK 例程的形状 —— 完整 128 位服务 UUID + 厂商数据，
   名字挪到 scan response 里（原版 EPD-nRF5 也是这么分的：广播带服务 UUID
   才能被 Web Bluetooth 按服务过滤出来） */
static const uint8_t s_adv_v3[] =
{
    0x11, 0x07,
    0xEC, 0x5A, 0x67, 0x1C, 0xC1, 0xB6, 0x46, 0xFB,
    0x8D, 0x91, 0x28, 0xD8, 0x01, 0x00, 0x75, 0x62,
    0x08, 0xFF, 0xFF, 0xFF, 'Z', 'K', '4', '2', 'V',
};

/* 变体 4：只有 Flags —— 直接验「Flags 这条 AD 结构本身非不非法」 */
static const uint8_t s_adv_v4[] =
{
    0x02, 0x01, 0x06,
};

/* --------- 扫描响应数据 --------- */

/* 128 位服务 UUID（连上之后网页按 optionalServices 找服务用） */
static const uint8_t s_rsp_uuid[] =
{
    0x11, 0x07,
    0xEC, 0x5A, 0x67, 0x1C, 0xC1, 0xB6, 0x46, 0xFB,
    0x8D, 0x91, 0x28, 0xD8, 0x01, 0x00, 0x75, 0x62,
};

/* 完整名字 */
static const uint8_t s_rsp_name[] =
{
    0x0A, 0x09, 'Z', 'K', '4', '2', 'V', '-', 'E', 'P', 'D',
};

/* 「不给 scan response」时用的占位指针（长度为 0，内容不会被用）。
   注意：这一条是"能不能把 scan response 清空"的实测 —— 万一协议栈拒绝
   长度为 0 的设置，那第 5 号变体实际用的还是上一个变体留下的 UUID，
   这时 ble_adv_ds2_err 会非 0，看状态块就能分辨，别把它当成"空 rsp 也失败" */
static const uint8_t s_rsp_none[1] = { 0x00 };

typedef struct
{
    const uint8_t *adv;
    uint8_t        adv_len;
    const uint8_t *rsp;
    uint8_t        rsp_len;
} zk_adv_variant_t;

static const zk_adv_variant_t s_variants[] =
{
    { s_adv_v0, (uint8_t)sizeof(s_adv_v0), s_rsp_uuid, (uint8_t)sizeof(s_rsp_uuid) },
    { s_adv_v1, (uint8_t)sizeof(s_adv_v1), s_rsp_uuid, (uint8_t)sizeof(s_rsp_uuid) },
    { s_adv_v2, (uint8_t)sizeof(s_adv_v2), s_rsp_uuid, (uint8_t)sizeof(s_rsp_uuid) },
    { s_adv_v3, (uint8_t)sizeof(s_adv_v3), s_rsp_name, (uint8_t)sizeof(s_rsp_name) },
    { s_adv_v4, (uint8_t)sizeof(s_adv_v4), s_rsp_uuid, (uint8_t)sizeof(s_rsp_uuid) },
    { s_adv_v0, (uint8_t)sizeof(s_adv_v0), s_rsp_none, 0 },
};

#define ZK_ADV_VARIANT_NUM  ((uint8_t)(sizeof(s_variants) / sizeof(s_variants[0])))

/* 状态块里 ble_adv_st0..ble_adv_st5 是同一类型的连续成员，所以可以当下标数组用。
   下面这个 sizeof 断言保证「变体数不超过格子上限」——超了编译就过不去。 */
#define ZK_ADV_SLOT_NUM     (6u)
typedef char zk_adv_slot_check[(ZK_ADV_VARIANT_NUM <= ZK_ADV_SLOT_NUM) ? 1 : -1];

/* ------------------------------------------------------------------
 * 内部状态
 * ------------------------------------------------------------------ */

#define ZK_SCAN_DEV_MAX       16u   /* 去重表多大（也决定「最多报几个设备」） */

/* 兜底超时。优先用 main.c 给的真毫秒；时基不可用（now_ms 恒 0）时退回圈数。
   扫描那 4s 本来由协议栈自己的 timeout 负责，这里只是「它连事件都不回」时的后手，
   所以给得宽一点（12s），别把正常的扫描掐掉。 */
#define ZK_SCAN_TIMEOUT_MS    12000u
#define ZK_SCAN_TIMEOUT_ITERS 2400u
#define ZK_ADV_TIMEOUT_MS     3000u
#define ZK_ADV_TIMEOUT_ITERS  600u

static ble_gap_adv_param_t      s_adv_param;
static ble_gap_adv_time_param_t s_adv_time;

static volatile uint8_t s_connected;

enum
{
    PH_IDLE = 0,
    PH_SCAN,        /* 正在扫描 */
    PH_ADV,         /* 正在挨个试广播数据变体 */
    PH_DONE,
};

static uint8_t  s_phase;
static uint32_t s_phase_iters;
static uint32_t s_phase_t0;         /* 本阶段开始时的毫秒数（0 = 没有时基） */
static uint32_t s_now_ms;           /* zk_ble_poll 每次带进来的最新时间 */
static uint8_t  s_adv_idx;
static uint8_t  s_adv_begun;
static int8_t   s_rssi_best = -127;

static uint8_t  s_scan_addr_tab[ZK_SCAN_DEV_MAX][6];
static uint8_t  s_scan_addr_num;

/* ------------------------------------------------------------------
 * 扫描
 * ------------------------------------------------------------------ */

static void zk_ble_scan_start(void)
{
    ble_gap_scan_param_t p;
    sdk_err_t            err;

    memset(&p, 0, sizeof(p));
    p.scan_type     = BLE_GAP_SCAN_ACTIVE;            /* 主动扫描：能收到 scan response */
    p.scan_mode     = BLE_GAP_SCAN_OBSERVER_MODE;
    p.scan_dup_filt = BLE_GAP_SCAN_FILT_DUPLIC_DIS;   /* 硬件不去重 —— 我们自己数 */
    p.use_whitelist = false;
    p.interval      = 160;      /* 100ms（单位 0.625ms） */
    p.window        = 160;      /* 和 interval 一样 = 一直在听 */
    p.timeout       = 400;      /* 4s（单位 10ms）—— 走完就自己停 */

    err = ble_gap_scan_param_set(BLE_GAP_OWN_ADDR_STATIC, &p);
    g_dbg.ble_scan_param_err = err;

    err = ble_gap_scan_start();
    g_dbg.ble_scan_start_err = err;

    g_dbg.ble_scan_state = ZK_SCAN_ST_STARTED;
    s_phase              = PH_SCAN;
    s_phase_iters        = 0;
    s_phase_t0           = s_now_ms;
}

/* ------------------------------------------------------------------
 * 广播数据变体
 * ------------------------------------------------------------------ */

static void zk_ble_adv_try(uint8_t idx)
{
    const zk_adv_variant_t *v;
    sdk_err_t               err;

    v = &s_variants[idx];
    s_adv_idx = idx;
    g_dbg.ble_adv_try = idx;
    g_dbg.ble_adv_try_status = ZK_NONE_U32;   /* 这个变体还没等到 ADV_START */

    err = ble_gap_adv_data_set(0, BLE_GAP_ADV_DATA_TYPE_DATA,
                               (uint8_t *)v->adv, v->adv_len);
    g_dbg.ble_adv_ds_err = err;

    err = ble_gap_adv_data_set(0, BLE_GAP_ADV_DATA_TYPE_SCAN_RSP,
                               (uint8_t *)v->rsp, v->rsp_len);
    g_dbg.ble_adv_ds2_err = err;

    err = ble_gap_adv_start(0, &s_adv_time);
    g_dbg.ble_adv_start_err = err;

    s_phase       = PH_ADV;
    s_phase_iters = 0;
    s_phase_t0    = s_now_ms;
}

/* 进入「挨个试广播数据变体」阶段。扫描正常超时、扫描起不来、兜底强停，
   三条路最后都汇到这里 —— s_adv_begun 保证只进一次。 */
static void zk_ble_adv_begin(void)
{
    uint8_t i;

    if (s_adv_begun)
    {
        return;
    }
    s_adv_begun     = 1;
    g_dbg.ble_adv_ok_variant = ZK_NONE_U32;   /* 还没成功 */
    for (i = 0; i < ZK_ADV_SLOT_NUM; i++)
    {
        ((volatile uint32_t *)&g_dbg.ble_adv_st0)[i] = ZK_NONE_U32;   /* 都还没试过 */
    }
    g_dbg.ble_state = ZK_BLE_ST_ADV;
    zk_ble_adv_try(0);
}

/* 当前变体不行（事件带了错误码，或者压根没等到事件）——换下一个 */
static void zk_ble_adv_next(void)
{
    if ((uint32_t)(s_adv_idx + 1u) >= ZK_ADV_VARIANT_NUM)
    {
        g_dbg.ble_adv_ok_variant = ZK_NONE_U32;
        g_dbg.ble_state          = ZK_BLE_ST_ADV_FAILED;
        s_phase                  = PH_DONE;
        return;
    }
    zk_ble_adv_try((uint8_t)(s_adv_idx + 1u));
}

/* ------------------------------------------------------------------
 * 广告上报：数设备
 * ------------------------------------------------------------------ */

static void zk_ble_on_adv_report(const ble_evt_t *p_evt)
{
    const ble_gap_evt_adv_report_t *r = &p_evt->evt.gapm_evt.params.adv_report;
    const uint8_t                  *a = r->broadcaster_addr.gap_addr.addr;
    uint8_t                         i;
    uint8_t                         found = 0;

    g_dbg.ble_scan_rpts++;

    /* RSSI 是 int8_t，存成「符号扩展的 32 位」；Python 那边按有符号还原 */
    g_dbg.ble_scan_rssi_last = (uint32_t)r->rssi;
    if ((int32_t)r->rssi > (int32_t)s_rssi_best)
    {
        s_rssi_best              = (int8_t)r->rssi;
        g_dbg.ble_scan_rssi_best = (uint32_t)r->rssi;
    }

    /* 最后一个上报地址（低位在前，跟扫描器里显示的顺序一致） */
    g_dbg.ble_scan_addr0 = (uint32_t)a[0] | ((uint32_t)a[1] << 8) |
                           ((uint32_t)a[2] << 16) | ((uint32_t)a[3] << 24);
    g_dbg.ble_scan_addr1 = (uint32_t)a[4] | ((uint32_t)a[5] << 8);

    /* 第一条上报：把广播数据的前 16 字节存下来当样本。
       目的很实在 —— 看别人（尤其是原厂价签/手机）在广播里到底放不放
       Flags(0x01)、名字放哪，我们照抄一份最不容易再被拒。 */
    if (g_dbg.ble_scan_rpts == 1u)
    {
        uint8_t n = (r->length > 16u) ? 16u : (uint8_t)r->length;

        g_dbg.ble_scan_last_len = r->length;
        if (n != 0u)
        {
            memcpy((void *)&g_dbg.ble_scan_data0, r->data, n);
        }
        else
        {
            g_dbg.ble_scan_data0 = 0;
            g_dbg.ble_scan_data1 = 0;
            g_dbg.ble_scan_data2 = 0;
            g_dbg.ble_scan_data3 = 0;
        }
    }

    /* 去重：地址表里找一遍，没有就加进去（表满了记溢出次数） */
    for (i = 0; i < s_scan_addr_num; i++)
    {
        if (0 == memcmp(s_scan_addr_tab[i], a, 6))
        {
            found = 1;
            break;
        }
    }
    if (!found)
    {
        if (s_scan_addr_num < ZK_SCAN_DEV_MAX)
        {
            memcpy(s_scan_addr_tab[s_scan_addr_num], a, 6);
            s_scan_addr_num++;
            g_dbg.ble_scan_devs = s_scan_addr_num;
        }
        else
        {
            g_dbg.ble_scan_ovf++;
        }
    }

    if (g_dbg.ble_scan_state < ZK_SCAN_ST_REPORTED)
    {
        g_dbg.ble_scan_state = ZK_SCAN_ST_REPORTED;
    }
}

/* ------------------------------------------------------------------
 * 协议栈起来之后：先扫描，再试广播
 * ------------------------------------------------------------------ */

static void zk_ble_gap_init(void)
{
    sdk_err_t err;

    err = ble_gap_device_name_set(BLE_GAP_WRITE_PERM_DISABLE,
                                  (uint8_t *)ZK_BLE_NAME, strlen(ZK_BLE_NAME));
    if (err)
    {
        g_dbg.ble_err = err;
    }

    memset(&s_adv_param, 0, sizeof(s_adv_param));
    /* 广播间隔：省电版 1000ms（=1600×0.625ms），老版本 100ms。
       为什么敢放到 1 秒：基站/网页那边是"扫 6 秒、扫到就停"，1 秒的广播间隔
       照样被抓到，只是连接建立慢最多 1 秒（屏刷本身 16 秒，察觉不出来）。
       而射频开着的平均电流大致降一个数量级 —— 对 CR2450 来说这是关键的一刀。 */
    s_adv_param.adv_intv_max = ZK_ADV_INTERVAL;
    s_adv_param.adv_intv_min = ZK_ADV_INTERVAL;
    s_adv_param.adv_mode     = BLE_GAP_ADV_TYPE_ADV_IND;
    s_adv_param.chnl_map     = BLE_GAP_ADV_CHANNEL_37_38_39;
    s_adv_param.disc_mode    = BLE_GAP_DISC_MODE_GEN_DISCOVERABLE;
    s_adv_param.filter_pol   = BLE_GAP_ADV_ALLOW_SCAN_ANY_CON_ANY;
    err = ble_gap_adv_param_set(0, BLE_GAP_OWN_ADDR_STATIC, &s_adv_param);
    if (err)
    {
        g_dbg.ble_err = err;
    }

    /* 广播数据不在这儿设 —— 交给实验二的变体表逐个设（zk_ble_adv_try） */

    /* MTU / 数据长度：图像是按 MTU 分块传的，尽量开大一点（网页会按 mtusize 切） */
    (void)ble_gap_l2cap_params_set(247, 247, 1);
    (void)ble_gap_data_length_set(251, 2120);
    (void)ble_gap_pref_phy_set(BLE_GAP_PHY_ANY, BLE_GAP_PHY_ANY);

    s_adv_time.duration    = 0;                     /* 一直广播 */
    s_adv_time.max_adv_evt = 0;
}

static void zk_ble_on_stack_init(void)
{
    ble_gap_bdaddr_t bd;

    zk_ble_gap_init();

    /* B2-A.2：把 GATT 服务建起来（网页要连的就是它）。
       必须在协议栈起来之后建 —— 跟 SDK 例程里 ble_app_init() 的位置一样。 */
    zk_epd_svc_init();

    /* 把自己的 BLE 地址记进状态块 —— 扫描列表里认不出名字时，就靠这个地址找我们 */
    if (0 == ble_gap_addr_get(&bd))
    {
        uint8_t *p = (uint8_t *)&g_dbg.ble_addr0;
        p[0] = bd.gap_addr.addr[0]; p[1] = bd.gap_addr.addr[1];
        p[2] = bd.gap_addr.addr[2]; p[3] = bd.gap_addr.addr[3];
        p[4] = bd.gap_addr.addr[4]; p[5] = bd.gap_addr.addr[5];
    }

#if ZK_BLE_SCAN_TEST
    /* 实验一：先听一会儿 */
    g_dbg.ble_state = ZK_BLE_ST_SCANNING;
    zk_ble_scan_start();
#else
    zk_ble_adv_begin();
#endif
}

/* ------------------------------------------------------------------
 * 事件
 * ------------------------------------------------------------------ */

void zk_ble_evt_handler(const ble_evt_t *p_evt)
{
    g_dbg.ble_evt_id     = p_evt->evt_id;
    g_dbg.ble_evt_status = p_evt->evt_status;
    g_dbg.ble_evt_count++;

    switch (p_evt->evt_id)
    {
        case BLE_COMMON_EVT_STACK_INIT:
            zk_ble_on_stack_init();
            break;

        case BLE_GAPM_EVT_SCAN_START:
            g_dbg.ble_scan_start_st = p_evt->evt_status;
            if (BLE_SUCCESS == p_evt->evt_status)
            {
                if (g_dbg.ble_scan_state < ZK_SCAN_ST_START_EVT)
                {
                    g_dbg.ble_scan_state = ZK_SCAN_ST_START_EVT;
                }
            }
            else
            {
                /* 连扫描都起不来：别把这一轮浪费掉，直接进广播变体实验 */
                zk_ble_adv_begin();
            }
            break;

        case BLE_GAPM_EVT_SCAN_STOP:
            g_dbg.ble_scan_stop_rsn =
                (uint32_t)p_evt->evt.gapm_evt.params.scan_stop.reason;
            if (g_dbg.ble_scan_state < ZK_SCAN_ST_STOPPED)
            {
                g_dbg.ble_scan_state = ZK_SCAN_ST_STOPPED;
            }
            /* 扫描结束（超时 / 被我们停掉）——接着试广播数据变体 */
            if (!s_adv_begun)
            {
                zk_ble_adv_begin();
            }
            break;

        case BLE_GAPM_EVT_ADV_REPORT:
            zk_ble_on_adv_report(p_evt);
            break;

        case BLE_GAPM_EVT_ADV_START:
            g_dbg.ble_adv_try_status = p_evt->evt_status;
            ((volatile uint32_t *)&g_dbg.ble_adv_st0)[s_adv_idx] = p_evt->evt_status;
            if (BLE_SUCCESS == p_evt->evt_status)
            {
                /* 这一种广播数据控制器认了 —— 我们就停在广播状态 */
                g_dbg.ble_adv_ok_variant = s_adv_idx;
                g_dbg.ble_state          = ZK_BLE_ST_ADV;
                s_phase                  = PH_DONE;
            }
            else
            {
                zk_ble_adv_next();
            }
            break;

        case BLE_GAPM_EVT_ADV_STOP:
            /* 广播自己停了。

               build 19 之前**完全没有这条记录** —— 万一广播中途停了，状态块里
               一个数都不会变，外面（我们、手机、网页）还以为它在广播。手机截图
               那一轮之所以能确认"空中真有包"，纯粹是靠运气好它没停。

               处理办法：被连接打断的（reason = CONN_EST）不动，等断开时那条路
               会重新开；其它原因（超时/被停/出错）就地重开，并记一笔。 */
            g_dbg.ble_adv_stop_cnt++;
            g_dbg.ble_adv_stop_rsn =
                (uint32_t)p_evt->evt.gapm_evt.params.adv_stop.reason;
            if (!s_connected &&
                BLE_GAP_STOPPED_REASON_CONN_EST !=
                    p_evt->evt.gapm_evt.params.adv_stop.reason &&
                0 == ble_gap_adv_start(0, &s_adv_time))
            {
                g_dbg.ble_adv_restart_cnt++;
            }
            break;

        case BLE_GAPC_EVT_CONNECTED:
            s_connected     = 1;
            g_dbg.ble_state = ZK_BLE_ST_CONNECTED;
            zk_epd_svc_on_connect(p_evt->evt.gapc_evt.index);
            break;

        case BLE_GAPC_EVT_DISCONNECTED:
            s_connected     = 0;
            g_dbg.ble_state = ZK_BLE_ST_ADV;
            zk_epd_svc_on_disconnect();
            /* 断开就用当前这套数据重开 */
            if (0 == ble_gap_adv_start(0, &s_adv_time))
            {
                g_dbg.ble_adv_restart_cnt++;
            }
            break;

        case BLE_GAPC_EVT_CONN_PARAM_UPDATE_REQ:
            ble_gap_conn_param_update_reply(p_evt->evt.gapc_evt.index, true);
            break;

        case BLE_GATT_COMMON_EVT_MTU_EXCHANGE:
            g_dbg.ble_mtu = p_evt->evt.gatt_common_evt.params.mtu_exchange.mtu;
            zk_epd_svc_on_mtu((uint16_t)p_evt->evt.gatt_common_evt.params.mtu_exchange.mtu);
            break;

        /* ---- B2-A.2：GATTS 的读写请求**不在这儿处理** -------------------
           SDK 的分发顺序是「先问 profile，谁认领就不再往下传」（ble_event_handle
           反汇编里看得很清楚）。我们的服务是用 ble_gatts_prf_add 注册的 profile，
           所以读/写事件只会进 zk_epd_svc.c 里那个 profile 回调，不会到这儿。
           这里留个注释是防止以后有人以为"没处理"又加一份 —— 加了两边都会回包。 */

        default:
            break;
    }
}

/* ------------------------------------------------------------------
 * 兜底节拍：每转一圈空闲循环叫一次
 *
 * 为什么要有它：上面整条实验流程都靠「协议栈回事件」往下推。万一链路层
 * 真的没跑（一个事件都不回），我们就永远停在第一步，这一轮 flash 就白瞎了。
 * 所以每个阶段都挂一个圈数上限，到点了自己往下走 —— 这样即使最坏情况，
 * 状态块里也能拿到「扫描/广播都没等到事件」这个结论。
 * ------------------------------------------------------------------ */
void zk_ble_poll(uint32_t now_ms)
{
    uint32_t elapsed;

    s_now_ms = now_ms;
    s_phase_iters++;

    if (PH_SCAN != s_phase && PH_ADV != s_phase)
    {
        return;
    }

    /* 有真毫秒就用毫秒判，没有（now_ms 恒 0）就用圈数兜底 */
    if (now_ms != 0u)
    {
        elapsed = now_ms - s_phase_t0;
    }
    else
    {
        elapsed = 0u;
    }

    if (PH_SCAN == s_phase)
    {
        if ((now_ms != 0u && elapsed >= ZK_SCAN_TIMEOUT_MS) ||
            (now_ms == 0u && s_phase_iters >= ZK_SCAN_TIMEOUT_ITERS))
        {
            (void)ble_gap_scan_stop();
            if (ZK_NONE_U32 == g_dbg.ble_scan_stop_rsn)
            {
                g_dbg.ble_scan_stop_rsn = 0xFFFFFFFEu;   /* 我们的兜底强停 */
            }
            if (g_dbg.ble_scan_state < ZK_SCAN_ST_STOPPED)
            {
                g_dbg.ble_scan_state = ZK_SCAN_ST_STOPPED;
            }
            zk_ble_adv_begin();
        }
    }
    else /* PH_ADV */
    {
        if ((now_ms != 0u && elapsed >= ZK_ADV_TIMEOUT_MS) ||
            (now_ms == 0u && s_phase_iters >= ZK_ADV_TIMEOUT_ITERS))
        {
            /* 这个变体连 ADV_START 事件都没等到 —— 记下来，换下一个 */
            ((volatile uint32_t *)&g_dbg.ble_adv_st0)[s_adv_idx] = ZK_NONE_U32;
            zk_ble_adv_next();
        }
    }
}

void zk_ble_start(void)
{
    sdk_err_t err = ble_stack_init(zk_ble_evt_handler, &heaps_table);
    g_dbg.ble_err = err;
}

int zk_ble_connected(void)
{
    return s_connected;
}
