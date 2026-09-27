#include "zk_ble.h"
#include "zk_dbg.h"

#include "gr_includes.h"
#include "ble.h"

/* 协议栈要的堆表（SDK 的宏，必须在文件作用域） */
STACK_HEAP_INIT(heaps_table);

#define ZK_BLE_NAME  "ZK42V-EPD"

/* 广播数据 —— 2026-09-27 调整：**名字要放广播包里**，别放 scan response。
 *
 * 起因：用 Bluefy（iOS 的 Web Bluetooth 浏览器）也搜不到设备。回头去看那个网页，
 * 它是 acceptAllDevices: true —— 压根不按服务 UUID 筛，附近所有 BLE 设备都会列出来。
 * 也就是说"能不能被看到"只取决于：① 我们真在广播 ② 广播里**带名字**。
 * 我原来把名字放在 scan response 里，而 iOS 那边不一定去取 scan response，
 * 结果我们可能以"(未知)"的样子躺在列表里，谁也认不出来。
 * 原项目（EPD-nRF5）就是把完整名字放广播包里的（BLE_ADVDATA_FULL_NAME）。
 *
 * 所以现在：
 *   广播包    = flags + 完整名字
 *   扫描响应  = 128 位服务 UUID（连上之后按 optionalServices 找服务用，不影响被发现） */
static const uint8_t s_adv_data[] =
{
    0x02, 0x01, 0x06,
    0x0A, 0x09, 'Z', 'K', '4', '2', 'V', '-', 'E', 'P', 'D',
};

/* 128 位服务 UUID：62750001-d828-918d-fb46-b6c11c675aec 的小端 16 字节 */
static const uint8_t s_adv_rsp_data[] =
{
    0x11, 0x07,
    0xEC, 0x5A, 0x67, 0x1C, 0xC1, 0xB6, 0x46, 0xFB,
    0x8D, 0x91, 0x28, 0xD8, 0x01, 0x00, 0x75, 0x62,
};

static ble_gap_adv_param_t      s_adv_param;
static ble_gap_adv_time_param_t s_adv_time;
static volatile int             s_connected;

static void zk_ble_gap_init(void)
{
    sdk_err_t err;

    err = ble_gap_device_name_set(BLE_GAP_WRITE_PERM_DISABLE,
                                  (uint8_t *)ZK_BLE_NAME, strlen(ZK_BLE_NAME));
    if (err) { g_dbg.ble_err = err; }

    memset(&s_adv_param, 0, sizeof(s_adv_param));
    s_adv_param.adv_intv_max = 160;                 /* 100ms */
    s_adv_param.adv_intv_min = 160;
    s_adv_param.adv_mode     = BLE_GAP_ADV_TYPE_ADV_IND;
    s_adv_param.chnl_map     = BLE_GAP_ADV_CHANNEL_37_38_39;
    s_adv_param.disc_mode    = BLE_GAP_DISC_MODE_GEN_DISCOVERABLE;
    s_adv_param.filter_pol   = BLE_GAP_ADV_ALLOW_SCAN_ANY_CON_ANY;
    err = ble_gap_adv_param_set(0, BLE_GAP_OWN_ADDR_STATIC, &s_adv_param);
    if (err) { g_dbg.ble_err = err; }

    err = ble_gap_adv_data_set(0, BLE_GAP_ADV_DATA_TYPE_DATA,
                               (uint8_t *)s_adv_data, sizeof(s_adv_data));
    if (err) { g_dbg.ble_err = err; }

    err = ble_gap_adv_data_set(0, BLE_GAP_ADV_DATA_TYPE_SCAN_RSP,
                               (uint8_t *)s_adv_rsp_data, sizeof(s_adv_rsp_data));
    if (err) { g_dbg.ble_err = err; }

    /* MTU / 数据长度：图像是按 MTU 分块传的，尽量开大一点（网页会按 mtusize 切） */
    (void)ble_gap_l2cap_params_set(247, 247, 1);
    (void)ble_gap_data_length_set(251, 2120);
    (void)ble_gap_pref_phy_set(BLE_GAP_PHY_ANY, BLE_GAP_PHY_ANY);

    s_adv_time.duration    = 0;                     /* 一直广播 */
    s_adv_time.max_adv_evt = 0;
}

static void zk_ble_adv_start(void)
{
    sdk_err_t err = ble_gap_adv_start(0, &s_adv_time);
    if (err) { g_dbg.ble_err = err; }
}

static void zk_ble_on_stack_init(void)
{
    ble_gap_bdaddr_t bd;

    zk_ble_gap_init();

    /* 把自己的 BLE 地址记进状态块 —— 扫描列表里认不出名字时，就靠这个地址找我们 */
    if (0 == ble_gap_addr_get(&bd))
    {
        uint8_t *p = (uint8_t *)&g_dbg.ble_addr0;
        p[0] = bd.gap_addr.addr[0]; p[1] = bd.gap_addr.addr[1];
        p[2] = bd.gap_addr.addr[2]; p[3] = bd.gap_addr.addr[3];
        p[4] = bd.gap_addr.addr[4]; p[5] = bd.gap_addr.addr[5];
    }

    zk_ble_adv_start();
    g_dbg.ble_state = ZK_BLE_ST_ADV;      /* 状态块里能看到"在广播" */
}

void zk_ble_evt_handler(const ble_evt_t *p_evt)
{
    switch (p_evt->evt_id)
    {
        case BLE_COMMON_EVT_STACK_INIT:
            zk_ble_on_stack_init();
            break;

        case BLE_GAPM_EVT_ADV_START:
            break;

        case BLE_GAPC_EVT_CONNECTED:
            s_connected = 1;
            g_dbg.ble_state = ZK_BLE_ST_CONNECTED;
            break;

        case BLE_GAPC_EVT_DISCONNECTED:
            s_connected = 0;
            g_dbg.ble_state = ZK_BLE_ST_ADV;
            zk_ble_adv_start();               /* 断开就重新广播 */
            break;

        case BLE_GAPC_EVT_CONN_PARAM_UPDATE_REQ:
            ble_gap_conn_param_update_reply(p_evt->evt.gapc_evt.index, true);
            break;

        case BLE_GATT_COMMON_EVT_MTU_EXCHANGE:
            g_dbg.ble_mtu = p_evt->evt.gatt_common_evt.params.mtu_exchange.mtu;
            break;

        default:
            break;
    }
}

void zk_ble_start(void)
{
    sdk_err_t err = ble_stack_init(zk_ble_evt_handler, &heaps_table);
    if (err) { g_dbg.ble_err = err; }
}

int zk_ble_connected(void)
{
    return s_connected;
}
