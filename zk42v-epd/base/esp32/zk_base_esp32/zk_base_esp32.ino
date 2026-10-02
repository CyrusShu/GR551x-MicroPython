/* ===========================================================================
 *  ZK42V 价签「基站」—— ESP32 / ESP32-S3 通用固件（build-24）
 *
 *  干什么：让一块 ESP32 当 BLE central，扫到价签就连上去，把
 *          · 时间（0x20：UTC 秒 + 时区 + 模式）
 *          · 天气（0x71：天气码 + 温度）
 *          写进价签，然后断开。价签自己会画整页农历月历（它固件里的事）。
 *
 *  协议（跟 Mac 那版 outputs/ble-base/zk_ble_base.py 完全一致）：
 *      服务    62750001-d828-918d-fb46-b6c11c675aec
 *      写/通知 62750002-d828-918d-fb46-b6c11c675aec
 *      版本(读) 62750003-d828-918d-fb46-b6c11c675aec   固件回 0x1A
 *      0x20 <utc_be32> <tz_s8> <mode> [wx] [t10_be16]
 *                                       mode: 0=保持当前页 1=日历 2=时钟
 *                                             （0 是 build 60 固件加的；基站一律发 0）
 *      0x71 <code> <temp_s8>            code: 1晴 2多云 3阴 4小雨 5大雨 6雷阵雨 7雪 8雾 9风
 *      价签回的通知： "t=<加过时区的秒>"   "wx=<code> t=<temp>"   "bat=<mV> pct=<%>"
 *
 *  天气：Open-Meteo（免 key）https://api.open-meteo.com/v1/forecast
 *        默认坐标 = 深圳公明广场 22.7809 / 113.8861
 *  时间：优先读 HTTP 响应的 Date 头（比 NTP 稳 —— 家里那条代理链路 UDP 123 不一定通），
 *        拿不到才退回 NTP。
 *
 *  第一次烧上去怎么验：
 *    ① BASE_MODE 先设 0（只扫描打印）—— 确认能看到 "ZK42V-EPD" 和它的 rssi；
 *    ② 再设回 1（扫描 + 连接 + 写时间和天气）；
 *    ③ 串口里应该看到价签回的 t= / wx= 两行，跟 Mac 版一样的对账方式。
 *
 *  ⚠ 这块板如果还接在价签 RST 焊盘上（GPIO4），先把那根线拔了再当基站用。
 * =========================================================================== */

#include <WiFi.h>
#include <HTTPClient.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <time.h>
#include <math.h>
#include <esp_heap_caps.h>

/* ------------------------------- 配置 ---------------------------------- */

// ① 家里 2.4G 的 WiFi（ESP32 只认 2.4G）。留空 = 不联网：
//    这时只能验证蓝牙，不会发时间和天气（没地方取）。
#define WIFI_SSID       "3808_be7000"
#define WIFI_PASS       "X3fafv3iyp"

// ② 位置：深圳公明广场（换地方改这两行；坐标来源见 ../README.md）
#define LAT             22.7809
#define LON             113.8861
#define TZ_HOURS        8                 // 北京时间 = UTC+8
// ②b 表头温度后面那个城市名（build 61 固件起支持）。换 LAT/LON 时记得一起改。
#define CITY_NAME       "深圳"
// ⚠ 固件里只有"常见的城市名用字"（tools/gen_font.py 的 CITY_CHARS，每个字 32 字节）。
#define CMD_SET_CITY    0x79

// ②c 纪念日提醒（build 67 固件起支持）：那天套黑框 + 在 1 号左边的空白处框出祝福语。
//    生日是"按月日重复"的，所以每年这个月都会亮。不想用就把 MEMO_DAY 设成 0。
//    ⚠ 文案只能用固件字模里有的字（tools/gen_font.py 的 MEMO_CHARS），认不出的会被跳过。
#define MEMO_MON        10
#define MEMO_DAY        5
#define MEMO_TEXT       "付婧文生日快乐！"
#define CMD_SET_MEMO    0x7A
#define CMD_SET_MEMO_MORE 0x7B   // 续传片段（MTU 只有 23 时一句祝福语要分几次发）

// ②d 天气预警（build 71 固件起支持）：有预警时，表头那格"天气文字"（雷阵雨）
//    会被预警名顶掉，≥3（黄/橙/红）画红的、1~2（白/蓝）画黑的。
//    数据来源 = NAS 上 wx-relay 取的和风实时预警（老接口 /v7/warning/now 已下架，
//    新的是 /weatheralert/v1/current/{纬度}/{经度}）；我们不直连，只读中继的 JSON。
#define CMD_SET_ALERT   0x7C

// ③ 价签：按广播名找（各平台看到的 MAC 不一样，名字最稳）
#define TAG_NAME        "ZK42V-EPD"
#define TAG_SVC_UUID    "62750001-d828-918d-fb46-b6c11c675aec"
#define WR_UUID         "62750002-d828-918d-fb46-b6c11c675aec"
#define VER_UUID        "62750003-d828-918d-fb46-b6c11c675aec"

// ④ 模式：0 = 只扫描打印（第一次验证射频用）；1 = 扫描 + 连接 + 写时间/天气
#define BASE_MODE       1

// ⑤ 节奏
#define SCAN_SECONDS        6             // 每轮扫描几秒
#define POLL_MS             3000          // 两轮之间歇多久
/* 天气多久**查**一次（30 分钟；查完值没变就不推、不刷屏）。用户 2026-09-30 拍板：
   Open-Meteo 本身 15 分钟一更新，30 分钟查一次足够；真正决定"闪不闪"的是阈值。 */
#define WEATHER_INTERVAL_MS (30UL * 60UL * 1000UL)
/* 温度要变这么多（十分之一度，10 = 1.0℃）才值得推一次 */
#define WX_TEMP_DELTA_T10   10
#define TIME_INTERVAL_MS    (24UL * 3600UL * 1000UL)   // 时间多久重校一次（1 天）
/* ⑦ 2026-10-01：**多久"没变化也推一次"**（默认 0 = 关）。
   为什么要这个旋钮：日历页只在「换天」和「收到命令」时重画（固件
   Src/ble/zk_epd_svc.c 的 zk_epd_svc_poll 里那段），所以屏上表头那个时分
   **只在每次推送的那一刻是对的**，之后就不动了 —— 用户 2026-10-01 看到
   "Mac 12:28、屏上 12:26"就是这个原因（不是钟慢，是那一页没重画）。
   设成 15*60*1000UL = 每 15 分钟不管有没有变化都推一次（时间 + 当前天气一起带，
   反正要重画一页），屏上时分最多旧 15 分钟；代价是每次一次 16 秒全刷。
   0 = 只在有变化时推（最省电，屏上时分可能停在几十分钟前）。 */
#define PUSH_EVERY_MS       0
#define NOTIFY_DWELL_MS     1500          // 写完命令后停留收通知的时间
/* 取天气失败重试几次（2026-10-01 从 3 次/800ms 提到 5 次/2s）：
   实机看到 WiFi 会掉一下再自己回来（信号弱 + BLE 扫描抢射频），
   原来 3 次 × 0.8 秒根本等不到它回来，结果"这一轮没天气"。
   现在给足 ~8 秒的窗口，成功率明显高；失败也不影响价签（有兜底）。 */
#define HTTP_TRIES          5
#define HTTP_RETRY_GAP_MS   2000
#define FETCH_FAIL_BACKOFF_MS 60000UL     // 失败后至少隔 60 秒再试，别刷屏

// ⑥ 天气走不走 TLS
//    0 = 纯 HTTP（**默认**）。为什么：ESP32 上 HTTPS 要在 WiFi + BLE 之外再挤
//    ~45KB 堆做 mbedTLS 握手，实测这块板子：BLE 起来后 heap 只剩 67KB，
//    信号又只有 -86dBm，握手直接失败 → HTTPClient 报 -1。
//    实测 api.open-meteo.com 的 80 端口**直接 200 不跳转**（2026-09-30 12:2x，
//    从同一网络里的 Mac 上验的），所以天气这种公开数据走明文完全够用。
//    1 = 走 https（堆够大、或者你有别的理由要用 TLS 时再开）。
#ifndef WX_USE_TLS
#define WX_USE_TLS          0
#endif
#ifndef WX_HOST
#define WX_HOST             "api.open-meteo.com"
#endif
/* ⑧ 天气用哪个模型（2026-10-01 加）。
   为什么要有：用户发现"价签 29.9℃、手机 33℃"，一查 —— **同一时刻同一坐标，
   Open-Meteo 各家模型差了整整 5℃**：
       best_match 29.9 / ecmwf 30.8 / cma(中国气象局) 31.7 / icon 32.4 / gfs 35.0
   手机（Apple 天气）国内用的是 和风天气/QWeather + 中国气象局实况那一路，
   跟我们的源本来就不是一个，差 2~3℃ 属于正常分歧。
      ""（默认）= best_match，行为跟以前完全一样
      "cma_grapes_global" = 中国气象局 GRAPES（国内源，最接近国产 App 的取向）
      "icon_seamless"     = 德国 DWD（实测那天最接近手机的 33℃）
      "gfs_seamless" / "ecmwf_ifs025" = 美国 NOAA / 欧洲中心
   想随时对比几家：跑 outputs/ble-base/wx-compare.py（不用改固件）。 */
#define WX_MODEL            ""

/* ⑨ **局域网中继**（2026-10-02 起和风的唯一通道）：
   和风的 HTTPS 这台 ESP32 传不出去（实测 10ms 级本机失败），所以和风那部分整体挪到
   常开的 NAS 上跑（wx-relay.py，Docker 容器 wx-relay，2026-10-02 已部署）：
       和风(HTTPS+JWT) ←── NAS 上的 wx-relay ──→ ESP32（纯 HTTP，几毫秒）
   好处：ESP32 不用 TLS、不用 gzip、**连和风凭据和私钥都不存**。
   取不到会自动退回 Open-Meteo（纯 HTTP，海外也通）。留空 = 不用中继。 */

/*  ⚠⚠ 这一行**必须是 http:// + 局域网地址** —— 两个坑都踩过（2026-10-02 实机）：
      ① **不能用 https://**：这台板子的 TLS 根本发不出去（和风 443 当初就是这么失败的），
         http.begin("https://…") 会直接回 HTTP -1。中继存在的意义就是**绕开 TLS**。
      ② **那个域名只有 AAAA 记录**（公网通配符指向 NAS 的 IPv6），而这块板子只跑 IPv4，
         写域名连解析都过不去。要让域名在局域网里能用，得在路由器上给它加一条局域网
         A 记录（2026-10-02 已加：weather.swimbirds.com -> 192.168.100.221）。
    所以默认用 **NAS 的局域网 IP**（最稳、不依赖 DNS）；想用域名就把下面那行注释换上来
    （端口仍旧直连中继自己的 8788，不走 Lucky 的 443）。 */
/* #define WX_RELAY_URL       "http://192.168.100.221:8788/wx"   /* 飞牛 NAS 上的 wx-relay（2026-10-02 部署） */
#define WX_RELAY_URL    "http://weather.swimbirds.com:8788/wx"   /* 局域网域名（要上面那条 A 记录） */

#define MODE_CALENDAR   1

/* ------------------------------ 内部状态 -------------------------------- */

static const char *SVC_UUID = TAG_SVC_UUID;

static unsigned long lastWeatherMs = 0;
static unsigned long lastTimeMs    = 0;
static unsigned long lastBeatMs    = 0;
static unsigned long lastIdleLogMs = 0;
static unsigned long lastFetchTryMs = 0;
static unsigned long lastFetchOkMs  = 0;   /* 上次成功取到时间/天气的时刻（millis） */
static unsigned long lastEpochMs    = 0;   /* utcEpoch 是**哪一刻**的表（millis）——
                                              发之前用它把"这几秒/几分钟"补回来 */
static unsigned long lastPushMs     = 0;   /* 上次"没变化也推一次"的时刻（PUSH_EVERY_MS 用） */
#define RESYNC_BEFORE_SEND_MS 300000UL     /* 发的时侯表比这个旧就先重新对一次（5 分钟） */

/* build-24（B）：**手里没有有用数据时先别连** —— 用户："价签开机刷的次数太多了"。
   实机（build-23）第一轮连接发生在 17~19 秒，那会儿 WiFi/HTTP 还没成：手里既没有时间
   也没有天气，连上去只能发**城市名 + 纪念日**这两条静态配置 —— 却让价签整页重画了 3 次
   （≈48 秒在闪），而屏上那 60 秒照样是一张没有正确时间的日历。
   所以：没时间、没天气 → 这一轮不连，等第一份数据到手再连。
   但要留兜底：价签出现后最多等 TAG_FIRST_WAIT_MS，到点还是连一次（至少把静态配置送过去、
   顺便验证链路），免得"中继和 Open-Meteo 同时不通"时它什么都不做。 */
#define TAG_FIRST_WAIT_MS     60000UL      /* 价签出现后最多等这么久（还是没数据就先连一次） */

/* build-24：**耗时画像**（用户："时间和天气为啥要那么久呢"）。
   先说结论：**取数本身不慢** —— 局域网中继是本机 HTTP，几毫秒的事（实测日志里
   "天气源 = 局域网中继"那一行和上一行的时间戳是同一秒）；Open-Meteo 也就几百毫秒。
   真正占时间的是「扫到价签 → BLE 连上 → 写完命令」这几段，所以每一步都打一个毫秒数，
   下一份开机日志就能一眼看出时间花在哪（以前只有"秒"级时间戳，8 秒的连接过程
   和瞬间完成的取数长得一模一样）。 */

/* WiFi 关联的状态机：关联本身可能要几十秒（信号弱时实测 ~100 秒），
   所以记下"什么时候开始喊的"，30 秒内不重复 WiFi.begin()。 */
static bool          wifiStarted = false;
static unsigned long wifiBegunAt = 0;

static bool  haveWeather = false;
static int   wxCode = 0;          // 0 = 不显示
static int   wxTemp = -128;       // 十分之一度（346 = 34.6℃）
/* build-15：**这个温度有没有小数**。
   和风给的是整数度（29℃），Open-Meteo 的模型值带一位小数（27.7℃）。
   固件那边"有小数就画 29.0℃、整数就画 29℃"是两条不同的载荷格式：
     有小数 → 20 … <wx> <t_hi> <t_lo>（int16 十分之一度，10 字节）
     整数   → 20 … <wx> <t_int8>       （9 字节）
   所以这里得记住来源有没有小数 —— 否则和风那 29℃ 会显示成 29.0℃（白占宽度还挤城市名）。*/
static bool  wxTempHasTenths = true;
static int   wxSentCode = -1;     // 上次真发出去的（"值没变就不发"）
static int   wxSentTemp = -999;

/* build-23：**天气预警**（用户："手机上是局地雷暴雨而且有暴雨警报，中继获取的是小雨"）。
   和风的老预警接口 /v7/warning/now 已经下架（403 Deprecated），新的是
   /weatheralert/v1/current/纬度/经度 —— 这一趟由 NAS 上的中继代跑，我们只从它的
   JSON 里读几个**平铺**字段（不是嵌套对象，方便手写查找）：
     alert  生效中的条数（0/缺省 = 没有）
     alevel 最严重那条的级别：1白 2蓝 3黄 4橙 5红
     atype  类型名（暴雨 / 雷电 / 雷雨大风 / 台风 …）
     aend   到期时间（ISO 串）
   屏上只有黑/白/红三色，所以固件那边拿 level>=4 当"红"，其余当"黑"。*/
static int   wxAlertLevel = 0;
static char  wxAlertType[24] = "";
static char  wxAlertEnd[32]  = "";
static unsigned long wxAlertAtMs = 0;   /* 上次从中继拿到预警的时刻（判断"过期了没有"） */
static const unsigned long ALERT_STALE_MS = 12UL * 3600UL * 1000UL;

static int32_t utcEpoch = 0;        // UTC 秒（0 = 还没拿到）
static bool    tagPresent = false;  // 上一轮扫描有没有看到价签
static bool    forceTimeSync = true;
static unsigned long tagSeenAtMs = 0;   // 这一轮"价签在线"是从什么时候开始的（B 的兜底计时）
static unsigned long scanStartedAtMs = 0;   // 本轮扫描是从什么时候开始的（算"多久才扫到"）
static bool    everSynced = false;  // 开机后至少连过一次（验证链路用）
static char    citySent[24] = "";   // 上次发出去的城市名（build 61；没变就不重发）
static bool    memoSent = false;    // 纪念日发过没有（build 67）
static int     alertSentLevel = -1; // 上次发出去的预警级别（build 71；-1 = 还没发过）
static char    alertSentType[24] = "";   // 上次发出去的预警类型名

/* ------------------------------ 小工具 ---------------------------------- */

static void logf(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    Serial.print("[BASE ");
    Serial.print((unsigned long)(millis() / 1000));
    Serial.print("s] ");
    Serial.println(buf);
}

static const char *wxName(int code)
{
    switch (code)
    {
        case 1:  return "晴";
        case 2:  return "多云";
        case 3:  return "阴";
        case 4:  return "小雨";
        case 5:  return "大雨";
        case 6:  return "雷阵雨";
        case 7:  return "雪";
        case 8:  return "雾";
        case 9:  return "风";
        default: return "无";
    }
}

/* 预警级别：1白 2蓝 3黄 4橙 5红（和风 color.code） */
static const char *alertLevelName(int lv)
{
    switch (lv)
    {
        case 1:  return "白色预警";
        case 2:  return "蓝色预警";
        case 3:  return "黄色预警";
        case 4:  return "橙色预警";
        case 5:  return "红色预警";
        default: return "预警";
    }
}

/* 现在生效的预警级别（0 = 没有/过期了）。
   为什么要有"过期"这一说：预警是从中继顺带读来的，中继不通时（NAS 重启之类）
   我们不该继续拿几小时前的旧预警吓自己。12 小时没更新就当它过期。 */
static int alertLevelNow()
{
    if (wxAlertLevel <= 0) return 0;
    if ((unsigned long)(millis() - wxAlertAtMs) > ALERT_STALE_MS) return 0;
    return wxAlertLevel;
}

// WMO weather_code（Open-Meteo 用的）→ 固件那 9 个码
static int wmoToCode(int wmo)
{
    switch (wmo)
    {
        case 0: case 1:                                    return 1;   // 晴
        case 2:                                            return 2;   // 多云
        case 3:                                            return 3;   // 阴
        case 45: case 48:                                  return 8;   // 雾
        case 51: case 53: case 55: case 56: case 57:
        case 61: case 80:                                  return 4;   // 小雨
        case 63: case 65: case 66: case 67:
        case 81: case 82:                                  return 5;   // 大雨
        case 71: case 73: case 75: case 77:
        case 85: case 86:                                  return 7;   // 雪
        case 95: case 96: case 99:                         return 6;   // 雷阵雨
        default:                                           return 2;
    }
}

/* 在 JSON 里找 "key": <数字>。

   ⚠ 这里踩过一个真坑（build-3）：Open-Meteo 的返回里 "temperature_2m" 和
   "weather_code" 各出现**两次** ——
       "current_units":{ ... "temperature_2m":"°C", "weather_code":"wmo code" ... }
       "current":      { ... "temperature_2m":33.6,  "weather_code":0 ... }
   先出现的是**单位表（字符串）**，后出现的才是真值。用 indexOf 直接找第一个的话
   toFloat() 会拿到 0 —— 实机表现就是"价签收到天气码对、温度 0℃"
   （status 调试块：`天气（手机下发）：晴  天气温度 0℃`）。
   所以这里要求冒号后面**紧跟数字**，是字符串就当没找到、继续往后搜。 */
static bool findJsonNumber(const String &s, const char *key, float *out)
{
    String pat = String("\"") + key + "\":";
    int i = s.indexOf(pat);
    while (i >= 0)
    {
        int p = i + (int)pat.length();
        while (p < (int)s.length() && (s[p] == ' ' || s[p] == '\t')) p++;
        char c = (p < (int)s.length()) ? s[p] : 0;
        if (c == '-' || (c >= '0' && c <= '9'))
        {
            *out = s.substring(p).toFloat();
            return true;
        }
        i = s.indexOf(pat, i + 1);          // 命中单位表，继续往下找
    }
    return false;
}

static bool wifiConfigured() { return strlen(WIFI_SSID) > 0; }

/* 在 current 段里取一个**字符串**字段（例如观测时刻 "time":"2026-09-30T13:30"）。
   为什么要先定位 "current": —— 因为 current_units 里也有个 "time"（值是 "iso8601"），
   直接找第一个会拿到单位表（跟之前温度踩的坑同源）。 */
static bool findJsonStringInCurrent(const String &body, const char *key, char *out, size_t n)
{
    int c = body.indexOf("\"current\":");
    if (c < 0) return false;
    String s   = body.substring(c);
    String pat = String("\"") + key + "\":";
    int i = s.indexOf(pat);
    while (i >= 0)
    {
        int p = i + (int)pat.length();
        while (p < (int)s.length() && (s[p] == ' ' || s[p] == '\t')) p++;
        if (p < (int)s.length() && s[p] == '"')
        {
            int j = s.indexOf('"', p + 1);
            if (j < 0) return false;
            s.substring(p + 1, j).toCharArray(out, n);
            return true;
        }
        i = s.indexOf(pat, i + 1);
    }
    return false;
}

/* 顶层取字符串字段（中继那套平铺 JSON 用的，比如 "atype":"暴雨"）。
   ⚠ 不能复用上面那个：那个会先找 "current":，中继的返回里没有这一段。

   ⚠⚠ 冒号后面的**空格一定要跳过**（2026-10-02 实机踩到）：中继是 Python 的
   json.dumps 出来的，默认写法是 `"atype": "暴雨"`（冒号后带空格）。原来这里找的是
   `"atype":"`（没有空格），于是**数字字段（那个函数本来就跳空格）全对、字符串字段全空** ——
   串口里显示成"⚠ 预警 橙色预警 到"（类型名和到期时间都是空的），屏上也就画不出预警。
   下面这个写法跟 findJsonNumber 一样：找到 key 后跳空格、再看是不是引号，不是就继续往下找。 */
static bool findJsonString(const String &body, const char *key, char *out, size_t n)
{
    String pat = String("\"") + key + "\":";
    int i = body.indexOf(pat);
    while (i >= 0)
    {
        int p = i + (int)pat.length();
        while (p < (int)body.length() && (body[p] == ' ' || body[p] == '\t')) p++;
        if (p < (int)body.length() && body[p] == '"')
        {
            int j = body.indexOf('"', p + 1);
            if (j < 0) return false;
            body.substring(p + 1, j).toCharArray(out, n);
            return true;
        }
        i = body.indexOf(pat, i + 1);
    }
    return false;
}

// 现在该不该校时 / 该不该发天气（连之前先算，没事就不连）
static bool needTimeSyncNow()
{
    /* ⚠ lastTimeMs == 0 表示**从来没成功发过时间**（不是"刚发过"）——
       这里必须单独判一次。原来只判 (millis() - lastTimeMs) > TIME_INTERVAL_MS，
       开机后 millis() 很小，减去 0 反而永远不满足 → **价签一直收不到时间**
       （实机日志里从来没有 `→ 时间` 那一行，价签的"网页给的时间"停在 Mac 那次）。
       2026-09-30 实机发现。 */
    if (lastTimeMs == 0) return true;
    return forceTimeSync || utcEpoch <= 0 || (millis() - lastTimeMs) > TIME_INTERVAL_MS;
}

static bool weatherChangedNow()
{
    /* 阈值判定（2026-09-30 用户拍板）：天气码变化 → 发；温度变化 ≥1.0℃ → 发；
       从没发过（wxSentCode<0，比如价签刚重启）→ 发；其余不发（省一次 17 秒全刷）。 */
    if (!haveWeather) return false;
    if (wxSentCode < 0) return true;
    if (wxCode != wxSentCode) return true;
    {
        int d = wxTemp - wxSentTemp;
        if (d < 0) d = -d;
        return d >= WX_TEMP_DELTA_T10;      /* WX_TEMP_DELTA_T10 = 10 = 1.0℃ */
    }
}

/* 现在这一刻的 UTC 秒 = 上次取到的 epoch + 之后 millis() 走掉的秒数。
   为什么要补：手里的 utcEpoch 是上次取天气时拿的，可能已经过去几分钟
   （发之前那次复查只保证 ≤5 分钟）。直接发就等于把几分钟前的时刻写进价签 ——
   用户 2026-10-01 看到"屏上比 Mac 慢一~两分钟"，一部分就是这个。
   补上之后，推送落地的那一刻价签的表就是准的。 */
static int32_t epochNow()
{
    if (utcEpoch <= 0) return 0;
    return utcEpoch + (int32_t)((millis() - lastEpochMs) / 1000UL);
}

// "Wed, 30 Sep 2026 03:30:00 GMT" → UTC 秒（失败返回 0）
static int32_t epochFromHttpDate(const String &s)
{
    static const char *mon[] = {"Jan","Feb","Mar","Apr","May","Jun",
                                "Jul","Aug","Sep","Oct","Nov","Dec"};
    int day = 0, year = 0, hh = 0, mm = 0, ss = 0;
    char m3[4] = {0};

    if (sscanf(s.c_str(), "%*3s, %d %3s %d %d:%d:%d", &day, m3, &year, &hh, &mm, &ss) != 6)
    {
        return 0;
    }
    int monIdx = -1;
    for (int i = 0; i < 12; i++)
    {
        if (strncmp(m3, mon[i], 3) == 0) { monIdx = i; break; }
    }
    if (monIdx < 0 || year < 2020) return 0;

    int days = 0;
    for (int yy = 1970; yy < year; yy++)
    {
        days += (yy % 4 == 0 && (yy % 100 != 0 || yy % 400 == 0)) ? 366 : 365;
    }
    static const int mdays[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    bool leap = (year % 4 == 0 && (year % 100 != 0 || year % 400 == 0));
    for (int i = 0; i < monIdx; i++)
    {
        days += mdays[i] + ((i == 1 && leap) ? 1 : 0);
    }
    days += day - 1;
    return (int32_t)days * 86400 + hh * 3600 + mm * 60 + ss;
}

static void wallClockText(int32_t utc, int tz, char *out, size_t n)
{
    int32_t t = utc + (int32_t)tz * 3600;
    int32_t days = t / 86400;
    int32_t rem  = t % 86400;
    int year = 1970;
    for (;;)
    {
        bool leap = (year % 4 == 0 && (year % 100 != 0 || year % 400 == 0));
        int yd = leap ? 366 : 365;
        if (days < yd) break;
        days -= yd;
        year++;
    }
    static const int mdays[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    bool leap = (year % 4 == 0 && (year % 100 != 0 || year % 400 == 0));
    int mon = 0;
    while (mon < 12)
    {
        int dm = mdays[mon] + ((mon == 1 && leap) ? 1 : 0);
        if (days < dm) break;
        days -= dm;
        mon++;
    }
    snprintf(out, n, "%04d-%02d-%02d %02d:%02d:%02d",
             year, mon + 1, (int)days + 1,
             (int)(rem / 3600), (int)((rem % 3600) / 60), (int)(rem % 60));
}

/* --------------------------- WiFi / 网络 -------------------------------- */

static bool ensureWifi()
{
    if (!wifiConfigured()) return false;
    if (WiFi.status() == WL_CONNECTED) return true;

    // 关联（associate）本来就可能要几十秒，尤其是信号弱的时候（实测这块板 -70~-86dBm 时
    // 要 ~100 秒）。所以：
    //   · 只有"从来没开始过"或"上一次开始已经超过 30 秒"才重新 WiFi.begin() ——
    //     否则会在正在关联时又喊一遍，日志里就是 `sta is connecting, cannot set config`；
    //   · 每轮最多等 8 秒，然后先回去扫 BLE，下一轮接着等。
    if (!wifiStarted || (millis() - wifiBegunAt) > 30000UL)
    {
        logf("连 WiFi \"%s\" ...（信号弱的话这一步可能几十秒）", WIFI_SSID);
        WiFi.mode(WIFI_STA);
        WiFi.setSleep(false);    // 关省电：和 BLE 抢射频时稳一点
        WiFi.begin(WIFI_SSID, WIFI_PASS);
        wifiBegunAt = millis();
        wifiStarted = true;
    }

    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 8000)
    {
        delay(250);
        Serial.print(".");
    }
    Serial.println();
    if (WiFi.status() == WL_CONNECTED)
    {
        logf("WiFi 好了，IP = %s  rssi = %d dBm",
             WiFi.localIP().toString().c_str(), WiFi.RSSI());
        return true;
    }
    logf("WiFi 还没连上（状态 %d，已试 %lu 秒），这轮先跳过网络",
         (int)WiFi.status(), (unsigned long)((millis() - wifiBegunAt) / 1000));
    return false;
}

/* ------------------------------ 天气 ------------------------------------ */

// 连不上时把"卡在哪一层"打出来：IP/网关/DNS → 域名能不能解析 → 堆还剩多少
#ifdef WX_NO_NET_DIAG
static void dumpNetDiag() { logf("（这份编译用 -DWX_NO_NET_DIAG 关掉了网络诊断）"); }
#else
static void dumpNetDiag()
{
    logf("—— 网络诊断 ——");
    logf("  IP=%s  网关=%s  DNS=%s  信号=%d dBm",
         WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str(),
         WiFi.dnsIP().toString().c_str(), WiFi.RSSI());
    IPAddress resolved;
    unsigned long t0 = millis();
    bool ok = WiFi.hostByName(WX_HOST, resolved);
    logf("  DNS 查 %s → %s（耗时 %lu ms）", WX_HOST,
         ok ? resolved.toString().c_str() : "**失败**", (unsigned long)(millis() - t0));
    logf("  堆：free=%u  最大连续块=%u",
         (unsigned)ESP.getFreeHeap(),
         (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    logf("  （TLS 要 ~45KB 连续堆；BLE+WiFi 起来后剩得少的话，HTTPS 会直接连不上）");
}
#endif

/* 和风天气：HTTPS 取 now，解 gzip 后抓 temp/text。成功返回 true 并把结果写进
   wxCode/wxTemp（十分之一度）。失败返回 false（调用方会退回 Open-Meteo）。 */
/* 把 HTTP 的 Date 头变成"现在几点的表"（build-10 抽成函数：和风那条路也要用，
   之前写在 Open-Meteo 后面，和风一成功提前 return 就**跳过校时**了 —— 自己踩的坑）。 */
static void applyDateHeader(const String &dateHdr)
{
    int32_t ep = epochFromHttpDate(dateHdr);
    if (ep > 0)
    {
        utcEpoch = ep;
        lastEpochMs = millis();            /* 记下这个表是哪一刻的（发的时候要补漂移） */
        char txt[32];
        wallClockText(utcEpoch, TZ_HOURS, txt, sizeof(txt));
        logf("时间（来自 HTTP Date 头）= UTC %d → 屏上应显示 %s", (int)utcEpoch, txt);
    }
    else
    {
        logf("这次没拿到 Date 头（'%s'），时间保持上一次的值", dateHdr.c_str());
    }
}


typedef struct
{
    volatile bool done;
    volatile bool ok;
} wx_job_t;

static void wxJobTask(void *arg)
{
    wx_job_t *j = (wx_job_t *)arg;

    j->ok   = fetchWeatherAndTimeInner();
    j->done = true;
    vTaskDelete(NULL);
}

static bool fetchWeatherAndTime(void)
{
    static wx_job_t job;               /* static：任务结束前调用方一直在等，这样最稳 */

    job.done = false;
    job.ok   = false;
    if (xTaskCreatePinnedToCore(wxJobTask, "wxjob", 20 * 1024, &job, 5, NULL, 1) != pdPASS)
    {
        logf("（建 wxjob 任务失败，退回本任务里跑 —— 可能会栈溢出）");
        return fetchWeatherAndTimeInner();
    }
    /* ⚠ 看门狗：最多等 30 秒。超了就不再堵着主循环（基站期间要扫 BLE、连价签），
       直接用 Open-Meteo 的值；那个任务自己会把这次请求跑完然后 vTaskDelete —— 
       不杀它（杀 TLS 任务会漏 socket），反正最多也就多跑一会儿。 */
    {
        const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(30000);

        while (!job.done)
        {
            if (xTaskGetTickCount() > deadline)
            {
                logf("（和风这次太慢，>30 秒还没回来 → 先不等了，这次用 Open-Meteo 的值）");
                return true;
            }
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
    return job.ok;
}

/* 问局域网中继（纯 HTTP，几毫秒的事）。成功返回 true 并写好 wxCode/wxTemp。 */
static bool fetchFromRelay(String *dateOut)
{
    HTTPClient http;
    String     body;
    int        code;

    if (!http.begin(WX_RELAY_URL))
    {
        logf("  中继：begin 失败（%s）", WX_RELAY_URL);
        return false;
    }
    http.setTimeout(5000);
    {
        const char *hdrs[] = {"Date"};
        http.collectHeaders(hdrs, 1);
    }
    code = http.GET();
    if (code != 200)
    {
        logf("  中继：HTTP %d（%s）", code, WX_RELAY_URL);
        http.end();
        return false;
    }
    body = http.getString();
    if (dateOut != 0)
    {
        *dateOut = http.header("Date");
    }
    http.end();

    {
        float wx = -1, tp = -999;
        bool  okWx = findJsonNumber(body, "code", &wx);
        bool  okTp = findJsonNumber(body, "temp", &tp);

        if (!okWx || !okTp)
        {
            logf("  中继：返回里没有 code/temp（前 80 字节：%s）", body.substring(0, 80).c_str());
            return false;
        }
        /* 预警（中继 build-23 才有这几个字段；老版中继没有 → 当作"没有预警"）。
           ⚠ 每个字段单独判，缺一个不会把整条天气判失败 —— 预警是"锦上添花"，
           不能因为它没取到就让价签连天气都没有。 */
        {
            float n = 0, lv = 0;

            wxAlertLevel  = 0;
            wxAlertType[0] = 0;
            wxAlertEnd[0]  = 0;
            if (findJsonNumber(body, "alert", &n) && n > 0 &&
                findJsonNumber(body, "alevel", &lv))
            {
                wxAlertLevel = (int)lv;
                wxAlertAtMs  = millis();
                findJsonString(body, "atype", wxAlertType, sizeof(wxAlertType));
                findJsonString(body, "aend", wxAlertEnd, sizeof(wxAlertEnd));
                /* 自检：有预警却读不出类型名 = JSON 的字段名/写法跟我们约定不一样
                   （2026-10-02 就是这么静默失效的：数字读到了、字符串全空，
                   屏上画不出预警，只有盯着串口才发现）。 */
                if (wxAlertType[0] == 0)
                {
                    logf("  !! 有预警但读不到 atype（JSON 写法变了？）前 120 字节：%s",
                         body.substring(0, 120).c_str());
                }
            }
        }
        wxCode = (int)wx;
        /* 中继发整数就按整数度处理（屏上 29℃），带小数就当十分之一度（屏上 27.7℃） */
        if (fabsf(tp - lroundf(tp)) < 0.05f)
        {
            wxTemp          = (int)lroundf(tp) * 10;
            wxTempHasTenths = false;
        }
        else
        {
            wxTemp          = (int)lroundf(tp * 10.0f);
            wxTempHasTenths = true;
        }
        haveWeather = true;
        logf("天气源 = **局域网中继** %.1f℃ %s（%s）",
             wxTemp / 10.0, wxName(wxCode), WX_RELAY_URL);
        if (wxAlertLevel > 0)
            logf("  ⚠ 预警 %s%s 到 %s（中继一并带上来的）",
                 wxAlertType, alertLevelName(wxAlertLevel), wxAlertEnd);
    }
    return true;
}


static bool fetchWeatherAndTimeInner()
{
    String dateHdr;

    /* ⑨ 配了中继就优先走它（纯 HTTP、几毫秒；拿不到时间才继续往下跑） */
    if (strlen(WX_RELAY_URL) > 0)
    {
        String relayDate;

        if (fetchFromRelay(&relayDate))
        {
            if (relayDate.length() > 0)
            {
                applyDateHeader(relayDate);
            }
            lastFetchOkMs = millis();
            return true;
        }
        /* 中继没答上来（NAS 关机/端口不通）→ 直接问 Open-Meteo，价签照常工作 */
        logf("中继没取到 → 这次用 Open-Meteo 兜底（价签照常工作）");
    }

    /* 取数顺序（2026-10-02 定稿）：
         ① 配了 WX_RELAY_URL 就先问**局域网中继**（NAS 上的 wx-relay.py）——
            它替我们跑和风的 HTTPS+JWT，我们只走纯 HTTP，几毫秒；
         ② 中继没配 / 没答上来 → 直接问 Open-Meteo（纯 HTTP，海外也通），
            它同时提供 HTTP Date 头当钟表。
       为什么不让 ESP32 自己连和风：实测这台板子的 TLS 根本发不出去（和风 443 失败；
       对别的域名做 TLS 探测 **10 毫秒**就失败 = 本机失败），与 2026-09-30 记的
       "TLS 要 ~45KB 连续堆"一致。所以和风那部分整体挪到了 NAS 上。 */

    String url = String(WX_USE_TLS ? "https://" : "http://") + WX_HOST
               + String("/v1/forecast?latitude=")
               + String(LAT, 4) + "&longitude=" + String(LON, 4)
               + "&current=temperature_2m,weather_code,wind_speed_10m&timezone=auto";
    if (strlen(WX_MODEL) > 0)
    {
        url += "&models=" + String(WX_MODEL);      /* ⑧ 换模型（默认空 = best_match） */
    }

    String body;
    bool got = false;
    for (int attempt = 1; attempt <= HTTP_TRIES && !got; attempt++)
    {
        HTTPClient http;
        http.setTimeout(10000);
        if (!http.begin(url))
        {
            logf("HTTP begin 失败（%s）", url.c_str());
            return false;
        }
        const char *hdrs[] = {"Date"};
        http.collectHeaders(hdrs, 1);

        int code = http.GET();
        if (code == 200)
        {
            body = http.getString();
            if (dateHdr.length() == 0)
            {
                dateHdr = http.header("Date");
            }
            got = true;
        }
        else
        {
            logf("取天气失败（第 %d/%d 次）：HTTP %d", attempt, HTTP_TRIES, code);
        }
        http.end();
        if (!got)
        {
            if (attempt == 1) dumpNetDiag();       // 第一次失败就把链路摊开看
            delay(HTTP_RETRY_GAP_MS);
        }
    }
    if (!got)
    {
        return false;
    }
    logf("天气接口通了（%s，%u 字节）", WX_USE_TLS ? "HTTPS" : "HTTP", (unsigned)body.length());

    applyDateHeader(dateHdr);

    // 天气：只抠我们要的两个数（不引第三方 JSON 库），注意别被 current_units 骗了
    float wmoF = -1, tempF = -999;
    bool okWmo  = findJsonNumber(body, "weather_code", &wmoF);
    bool okTemp = findJsonNumber(body, "temperature_2m", &tempF);
    if (!okWmo || !okTemp)
    {
        logf("JSON 解析失败（weather_code=%d temperature_2m=%d）—— 原样贴出前 200 字节：",
             (int)okWmo, (int)okTemp);
        logf("  %s", body.substring(0, 200).c_str());
        return false;
    }
    int   wmo  = (int)wmoF;
    float temp = tempF;

    int c = wmoToCode(wmo);
    /* **不四舍五入**（用户 2026-09-30 要求）：按"十分之一度"发 int16（34.6℃ → 346），
       面板上显示一位小数。t 只用于打日志。 */
    int t10 = (int)lroundf(temp * 10.0f);
    if (t10 >  32767) t10 =  32767;
    if (t10 < -32768) t10 = -32768;
    int t = t10 / 10;
    haveWeather = true;
    wxCode = c;
    wxTemp = t10;               /* 现在存的是"十分之一度" */
    wxTempHasTenths = true;     /* Open-Meteo 的模型值有小数 */
    lastFetchOkMs = millis();
    logf("（用的是 Open-Meteo 的 %s 模型；换源看 WX_MODEL 那段注释）",
         (strlen(WX_MODEL) > 0) ? WX_MODEL : "best_match");
    logf("天气取好了（%.4f,%.4f）：%s %d℃  （WMO=%d 原始温度=%.1f）",
         (double)LAT, (double)LON, wxName(c), t, wmo, (double)temp);
    /* 这两行是"到底在动还是被缓存了"的判据（2026-09-30 用户提出）：
       · 观测时刻每次都在前进（13:30 → 14:30 …）= 响应是新的；
         卡着不动 = 中间有缓存（代理/CDN），那就要给 URL 加时间戳破坏缓存。
       · 原始温度带一位小数，能看出 34.6→35 这种"四舍五入后看起来没变"的情况。 */
    {
        char obs[24] = "?";
        findJsonStringInCurrent(body, "time", obs, sizeof(obs));
        logf("  Open-Meteo 观测时刻 = %s   原始温度 = %.1f℃（屏幕上是四舍五入后的 %d℃）",
             obs, (double)temp, t);
    }

    return true;
}

/* ------------------------------- BLE ------------------------------------ */

static BLEAdvertisedDevice foundDevice;
static volatile bool       foundFlag = false;

static bool deviceMatches(BLEAdvertisedDevice &dev)
{
    if (dev.haveName() && String(dev.getName().c_str()).indexOf(TAG_NAME) >= 0) return true;
    if (dev.haveServiceUUID())
    {
        for (int i = 0; i < dev.getServiceUUIDCount(); i++)
        {
            if (dev.getServiceUUID(i).equals(BLEUUID(SVC_UUID))) return true;
        }
    }
    return false;
}

class ScanCallbacks : public BLEAdvertisedDeviceCallbacks
{
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    void onResult(BLEAdvertisedDevice advertisedDevice) override
    {
        handle(advertisedDevice);
    }
#else
    void onResult(BLEAdvertisedDevice *advertisedDevice) override
    {
        handle(*advertisedDevice);
    }
#endif

    void handle(BLEAdvertisedDevice &dev)
    {
        String name = dev.haveName() ? dev.getName() : String("(无名)");

        if (BASE_MODE == 0)
        {
            logf("  扫到 %-16s rssi=%4d  %s", name.c_str(), dev.getRSSI(),
                 dev.getAddress().toString().c_str());
        }
        if (deviceMatches(dev) && !foundFlag)
        {
            foundDevice = dev;
            foundFlag   = true;
            logf("→ 命中价签：%s  rssi=%d  addr=%s（扫描开始后 %lu 毫秒扫到）",
                 name.c_str(), dev.getRSSI(), dev.getAddress().toString().c_str(),
                 millis() - scanStartedAtMs);
            /* build-24：**扫到就停**。SCAN_SECONDS=6 是"最多扫 6 秒"的意思，不是
               "必须扫满 6 秒" —— 可原来的写法会把窗口跑满。实机画像：
                 扫描开始后 1119 毫秒扫到 → 但函数到 6 秒才返回 → 白等 ~5 秒。
               在回调里 stop() 是官方例子的用法，start() 会提前返回。 */
            BLEDevice::getScan()->stop();
        }
    }
};

static void notifyCB(BLERemoteCharacteristic *ch, uint8_t *data, size_t len, bool isNotify)
{
    (void)ch; (void)isNotify;
    char txt[80];
    size_t n = (len < sizeof(txt) - 1) ? len : sizeof(txt) - 1;
    for (size_t i = 0; i < n; i++)
    {
        char c = (char)data[i];
        txt[i] = (c >= 32 && c < 127) ? c : '.';
    }
    txt[n] = 0;
    Serial.print("        <- 价签: \"");
    Serial.print(txt);
    Serial.print("\"  (");
    for (size_t i = 0; i < len; i++)
    {
        if (data[i] < 16) Serial.print('0');
        Serial.print(data[i], HEX);
        if (i + 1 < len) Serial.print(' ');
    }
    Serial.println(")");
}

static bool doSync(BLEAdvertisedDevice &dev)
{
    // core 3.3.11 里 BLEDevice::createClient() 是单例（内部存 m_pClient），
    // 而且**没有** deleteClient() —— 所以建一次就一直复用，别每次 new/delete。
    static BLEClient *client = nullptr;
    if (client == nullptr)
    {
        client = BLEDevice::createClient();
    }

    unsigned long tConn0 = millis();
    unsigned long tLap;

    logf("连接中 ...");
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    bool ok = client->connectTimeout(&dev, 15000);      // 毫秒
#else
    client->setConnectTimeout(15);                      // 秒
    bool ok = client->connect(&dev);
#endif
    if (!ok)
    {
        logf("连接失败（等了 %lu 毫秒）", millis() - tConn0);
        return false;
    }
    logf("连上了（BLE 连接本身用了 %lu 毫秒）", millis() - tConn0);
    tLap = millis();

    /* 顺手把 MTU 谈大（build-9）：价签支持到 244（网页那套协议就是靠它推图的），
       谈大之后一次能发 244 字节，长文案/以后的功能都不用再分片。
       谈不成也不影响 —— 下面写命令的地方全都按 ≤20 字节分片了。 */
    if (client->setMTU(247))
    {
        logf("MTU 协商后 = %d（一次能发 %d 字节，耗时 %lu 毫秒）",
             (int)client->getMTU(), (int)client->getMTU() - 3, millis() - tLap);
    }
    else
    {
        logf("MTU 协商没成功（现在是 %d，写入按 %d 字节分片）",
             (int)client->getMTU(), (int)client->getMTU() - 3);
    }

    BLERemoteService *svc = client->getService(BLEUUID(SVC_UUID));
    if (svc == nullptr)
    {
        logf("找不到服务 %s", SVC_UUID);
        client->disconnect();
        return false;
    }

    // 只扫描模式：连一下证明链路通，立刻退出来
    if (BASE_MODE == 0)
    {
        logf("（只扫描模式）连接验证通过，主动断开");
        client->disconnect();
        return true;
    }

    BLERemoteCharacteristic *verCh = svc->getCharacteristic(BLEUUID(VER_UUID));
    if (verCh != nullptr && verCh->canRead())
    {
        String v = verCh->readValue();
        if (v.length() > 0)
        {
            logf("固件协议版本 = 0x%02X（>=0x16 就是新流程）", (uint8_t)v[0]);
        }
    }

    BLERemoteCharacteristic *wr = svc->getCharacteristic(BLEUUID(WR_UUID));
    if (wr == nullptr || (!wr->canWrite() && !wr->canWriteNoResponse()))
    {
        logf("找不到可写的特征 %s", WR_UUID);
        client->disconnect();
        return false;
    }

    wr->registerForNotify(notifyCB);

    /* 耗时画像（build-24）：从这里开始到"命令写完"花了多久。
       用户问"时间和天气为啥要那么久" —— 取数（中继 HTTP）只有几毫秒，
       真正吃时间的是连接和下面这串写。 */
    unsigned long tWr0 = millis();

    // ① 时间：刚上电（重新出现）、超过一天没校、**或者这次要推天气** → 一起发
    //
    //   2026-10-01 用户要求：**取天气/温度的时候把时间放同一条命令里推下来**，
    //   这样每次推天气都顺便对一次表，价签的钟一直是校准的。
    //   代价为零：反正那一条命令本来就会让价签整页重画一次。
    //
    //   ⚠ 模式字节发 **0**（= 保持当前模式，build 60 起的固件支持）：
    //     基站不知道用户现在看的是日历页 / 时钟页 / 推的图，发 1 就会把页面顶掉。
    bool needTime = needTimeSyncNow();
    bool sendWx   = weatherChangedNow();   /* 天气码变了 / 温度差 ≥1.0℃ / 从没发过 → 才发 */
    bool pushDue  = (PUSH_EVERY_MS != 0UL) && (lastPushMs == 0UL ||
                                              (millis() - lastPushMs) > PUSH_EVERY_MS);
    bool sentTime = false;
    bool sendTime = (utcEpoch > 0) && (needTime || sendWx || pushDue);

    if (sendTime)
    {
        uint8_t p[10];                     /* 合并包：20 + 时间4 + 时区1 + 模式1 + [天气2] */
        uint8_t n = 7;
        uint32_t ts = (uint32_t)epochNow();   /* ⚠ 现场算，不用几分钟前的旧值 */
        p[0] = 0x20;
        p[1] = (ts >> 24) & 0xFF;
        p[2] = (ts >> 16) & 0xFF;
        p[3] = (ts >> 8) & 0xFF;
        p[4] = ts & 0xFF;
        p[5] = (uint8_t)(TZ_HOURS & 0xFF);
        p[6] = 0;                          /* 0 = 保持当前模式（别顶掉用户选的页面） */
        if (haveWeather)
        {
            p[7] = (uint8_t)wxCode;
            if (wxTempHasTenths)
            {
                /* 20 <utc4> <tz> <mode> <wx> <t_hi> <t_lo> —— 带一位小数 */
                p[8] = (uint8_t)((wxTemp >> 8) & 0xFF);
                p[9] = (uint8_t)(wxTemp & 0xFF);
                n    = 10;
            }
            else
            {
                /* 20 <utc4> <tz> <mode> <wx> <t_int8> —— 整数度（和风就是这种） */
                p[8] = (uint8_t)(int8_t)((wxTemp >= 0) ? ((wxTemp + 5) / 10)
                                                       : ((wxTemp - 5) / 10));
                n    = 9;
            }
        }
        wr->writeValue(p, n, true);
        sentTime   = true;
        lastTimeMs = millis();             /* 刚对过表 —— 之后 24 小时内不用专门校时了 */
        lastPushMs = millis();
        if (haveWeather)
        {
            wxSentCode = wxCode;
            wxSentTemp = wxTemp;
        }
        char txt[32];
        wallClockText((int32_t)ts, TZ_HOURS, txt, sizeof(txt));
        logf("→ 时间+天气一条走：UTC %u + 时区%d → 价签应显示 %s（模式=0 保持当前页）%s  载荷=%s",
             (unsigned)ts, TZ_HOURS, txt,
             haveWeather ? "" : "（这次手里没天气，只发时间）",
             haveWeather ? "10 字节" : "7 字节");
    }
    else if (needTime || pushDue)
    {
        if (!wifiConfigured())
        {
            logf("没填 WIFI_SSID → 取不到时间/天气（这块现在只能当蓝牙验证用）");
        }
        else
        {
            logf("要校时但手里没有时间（WiFi/HTTP 没成），跳过");
        }
    }

    // ② 天气：**没能跟时间合并时**才单独发（例如手里还没有效时间）
    bool sentWx = sendWx && !sentTime;
    bool wroteCity = false;                 /* 这一轮到底写没写东西（日志别乱说"没写"） */
    bool wroteMemo = false;
    bool wroteAlert = false;                /* build 71：这一轮发没发天气预警 */
    if (haveWeather)
    {
        if (!sendWx)
        {
            logf("= 天气没变（%s %.1f℃），不重发 —— 省一次全刷",
                 wxName(wxCode), (double)wxTemp / 10.0);
        }
        else if (!sentTime)
        {
            uint8_t p[4];
            size_t  pn;
            p[0] = 0x71;
            p[1] = (uint8_t)wxCode;
            if (wxTempHasTenths)
            {
                p[2] = (uint8_t)((wxTemp >> 8) & 0xFF);
                p[3] = (uint8_t)(wxTemp & 0xFF);
                pn   = 4;
            }
            else
            {
                p[2] = (uint8_t)(int8_t)((wxTemp >= 0) ? ((wxTemp + 5) / 10)
                                                       : ((wxTemp - 5) / 10));
                pn   = 3;
            }
            wr->writeValue(p, pn, true);
            wxSentCode = wxCode;
            wxSentTemp = wxTemp;
            logf("→ 天气 %s %d℃（单独发）  载荷=%02X %02X %02X",
                 wxName(wxCode), wxTemp, p[0], p[1], p[2]);
        }
    }

    // ③ 城市名（build 61）：表头温度后面写它。基站知道自己的经纬度（LAT/LON），
    //    所以由基站发；固件只认常见城市名的字模。城市名没变就不重发。
    if (strlen(CITY_NAME) > 0 && strcmp(CITY_NAME, citySent) != 0)
    {
        uint8_t p[32];
        size_t  cl = strlen(CITY_NAME);
        if (cl > sizeof(p) - 1) cl = sizeof(p) - 1;
        p[0] = CMD_SET_CITY;
        memcpy(p + 1, CITY_NAME, cl);
        wr->writeValue(p, (size_t)(cl + 1), true);
        strncpy(citySent, CITY_NAME, sizeof(citySent) - 1);
        citySent[sizeof(citySent) - 1] = 0;
        wroteCity = true;
        logf("→ 城市名 %s（温度后面那几个字）", CITY_NAME);
    }

    // ③b 纪念日提醒（build 67）：那天套黑框 + 在 1 号左边的空白处框出祝福语。
    //     ⚠ 必须**按 MTU 分片**：ATT 一次只能发 MTU-3 字节，ESP32 默认 MTU=23
    //     （只有 20 字节），而"付婧文生日快乐！"是 24 字节 —— 一条 27 字节的写会触发
    //     Bluedroid 的"长写（prepare/execute）"，价签的 GATT 不支持 → 卡 ~40 秒后失败，
    //     屏上一直不出框（2026-10-01 实机就是这么踩到的）。
    //     所以：0x7A 起头（带月日）、0x7B 续传，每段都 ≤ 20 字节、按 UTF-8 边界切。
    if (MEMO_DAY > 0 && strlen(MEMO_TEXT) > 0 && !memoSent)
    {
        const char *t     = MEMO_TEXT;
        const size_t total = strlen(t);
        size_t       off   = 0;
        const size_t chunk = 15;            /* 一段最多 15 字节文案 = 5 个汉字 */
        int          parts = 0;

        while (off < total)
        {
            size_t n = total - off;
            uint8_t p[24];
            size_t  k;

            if (n > chunk) n = chunk;
            /* 别把汉字切成两半：**看下一段的第一个字节**是不是"续字节"（10xxxxxx）——
               是的话说明这个位置正切在一个字的中间，往前退到字符边界。
               （⚠ 不能看本段的最后一个字节：汉字 3 字节，那样会退成 1 字节的碎片段） */
            while (n > 1 && (off + n) < total &&
                   ((uint8_t)t[off + n] & 0xC0) == 0x80)
            {
                n--;
            }

            if (off == 0)                   /* 第一段：7A <mon> <day> <文案> */
            {
                p[0] = CMD_SET_MEMO;
                p[1] = (uint8_t)MEMO_MON;
                p[2] = (uint8_t)MEMO_DAY;
                k    = 3;
            }
            else                            /* 后面几段：7B <文案> */
            {
                p[0] = CMD_SET_MEMO_MORE;
                k    = 1;
            }
            memcpy(p + k, t + off, n);
            wr->writeValue(p, k + n, true);
            off += n;
            parts++;
            delay(20);                      /* 给价签一点时间处理（它跑在协议栈回调里） */
        }
        memoSent   = true;
        wroteMemo  = true;
        logf("→ 纪念日 %02d-%02d「%s」（%d 段发完：那天套黑框 + 空白处框出这句话）",
             MEMO_MON, MEMO_DAY, MEMO_TEXT, parts);
    }

    // ③c 天气预警（build 71）：和风的实时预警，由 NAS 中继取回（我们只读 json）。
    //     有预警 → 表头那格"天气文字"改画预警名；预警结束（level 回到 0）也要发一条
    //     清掉它，否则屏上会挂着一条早就过期的预警。
    //     载荷 = 7C <level> <utf8 类型名>，最多 2+15=17 字节（< MTU-3，不用分片）。
    {
        int  lv  = alertLevelNow();
        bool chg = (lv != alertSentLevel) || (strcmp(wxAlertType, alertSentType) != 0);

        if (chg && (lv > 0 || alertSentLevel > 0))
        {
            uint8_t p[20];
            size_t  n  = 0;
            size_t  tl = (lv > 0) ? strlen(wxAlertType) : 0;

            if (tl > 15) tl = 15;           /* 表头那格最多画 3 个字，15 字节绰绰有余 */
            p[n++] = CMD_SET_ALERT;
            p[n++] = (uint8_t)((lv > 0) ? lv : 0);
            memcpy(p + n, wxAlertType, tl);
            n += tl;
            wr->writeValue(p, n, true);
            alertSentLevel = lv;
            strncpy(alertSentType, wxAlertType, sizeof(alertSentType) - 1);
            alertSentType[sizeof(alertSentType) - 1] = 0;
            wroteAlert = true;
            if (lv > 0)
            {
                logf("→ 天气预警 %s%s（表头那格改画它）", wxAlertType, alertLevelName(lv));
            }
            else
            {
                logf("→ 天气预警已解除（发一条清掉屏上那条）");
            }
        }
    }

    logf("命令发完（从连上到发完这一串：%lu 毫秒）", millis() - tWr0);

    delay(NOTIFY_DWELL_MS);                 // 留点时间把价签的回包收全
    client->disconnect();

    forceTimeSync = false;
    logf("已断开（本次连接总耗时 %lu 毫秒）。%s", millis() - tConn0,
         (sentTime || sentWx || wroteCity || wroteMemo || wroteAlert)
             ? "价签这时在刷屏，约 16 秒"
             : "本轮没写任何命令，价签不会重画");
    return true;
}

/* ------------------------------- 主流程 ---------------------------------- */

void setup()
{
    Serial.begin(115200);
    delay(1200);                            // 等 USB 串口稳定
    Serial.println();
    Serial.println("=================================================");
    Serial.println(" ZK42V 价签基站 (ESP32) build-24");
    Serial.printf (" 芯片: %s rev%d %d 核 @%dMHz  Flash %uMB  PSRAM %s\n",
                   ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(),
                   ESP.getCpuFreqMHz(), (unsigned)(ESP.getFlashChipSize() / 1048576),
                   psramFound() ? "有" : "无");
    Serial.printf (" 时间源: HTTP Date 头   天气: %s\n",
                   strlen(WX_RELAY_URL) > 0 ? "NAS 局域网中继（首选）+ Open-Meteo 兜底"
                                            : "Open-Meteo（纯 HTTP）");
    Serial.printf (" 坐标: %.4f, %.4f   时区: UTC%+d   模式: %s\n",
                   (double)LAT, (double)LON, TZ_HOURS,
                   BASE_MODE == 0 ? "0 只扫描" : "1 扫描+连接+写");
    if (!wifiConfigured())
    {
        Serial.println(" !! 没填 WIFI_SSID：只能验证蓝牙，不会发时间/天气");
    }
    /* 中继地址的自检（2026-10-02 加）：那天把 WX_RELAY_URL 写成了
       "https://weather.swimbirds.com/wx"，结果中继一直是 HTTP -1、静默退回 Open-Meteo
       （天气还能刷，**只是没有预警**，不看串口根本发现不了）。
       与其再猜一次，不如开机就把原因喊出来。 */
    if (strncmp(WX_RELAY_URL, "https://", 8) == 0)
    {
        Serial.println(" !! WX_RELAY_URL 是 https:// —— 这台板子**做不了 TLS**，中继一定失败！");
        Serial.println("    改成 http://<NAS 的局域网IP>:8788/wx（中继本身没有加密，靠局域网隔离）");
    }
    else if (strstr(WX_RELAY_URL, "://") != 0 && strncmp(WX_RELAY_URL, "http://", 7) == 0)
    {
        const char *host = WX_RELAY_URL + 7;
        bool        allDigitsDots = true;

        for (const char *q = host; *q != 0 && *q != ':' && *q != '/'; q++)
        {
            if ((*q < '0' || *q > '9') && *q != '.')
            {
                allDigitsDots = false;
                break;
            }
        }
        if (!allDigitsDots)
        {
            Serial.println(" 提示: 中继用的是域名 —— 这块板子只跑 IPv4，域名必须在局域网里能解析出");
            Serial.println("       A 记录（路由器上加：weather.swimbirds.com -> NAS 的 IPv4）。");
            Serial.println("       解析不了就会静默退回 Open-Meteo（有天气、但没有预警）。");
        }
    }
    Serial.println("=================================================");

    BLEDevice::init("zk42v-base");
    BLEDevice::setPower(ESP_PWR_LVL_P9);     // 最大发射功率，链路稳一点
    BLEScan *scan = BLEDevice::getScan();
    scan->setAdvertisedDeviceCallbacks(new ScanCallbacks());
    scan->setActiveScan(true);
    scan->setInterval(100);
    scan->setWindow(99);
    logf("BLE 起来了，%s", BASE_MODE == 0 ? "开始只扫描（不做任何写入）" : "开始工作");
}

void loop()
{
    // 1) 网络（没配 WiFi 就跳过）
    if (wifiConfigured() && ensureWifi())
    {
        bool needWeather = (lastWeatherMs == 0) ||
                           (millis() - lastWeatherMs) > WEATHER_INTERVAL_MS;
        bool needTime    = (utcEpoch <= 0);
        /* 失败要退避：不然每一轮（约 10 秒）都去撞一次，日志刷屏、还费电。
           ⚠ build-24 修：`lastFetchTryMs` 初值是 0，而这里原来是
           `(millis() - lastFetchTryMs) > 60000` —— **开机后 60 秒内一次都不会取**。
           实机日志里第一份数据永远落在 61~71 秒（build-11/15/17/20/22/23 全是这样），
           就是它：价签 8 秒就出现了，却要等一分钟才拿到时间和天气。
           所以**第一次**要立刻取（lastFetchTryMs == 0 = 从来没试过）。 */
        bool firstFetch = (lastFetchTryMs == 0UL);

        if ((needWeather || needTime) &&
            (firstFetch || (millis() - lastFetchTryMs) > FETCH_FAIL_BACKOFF_MS))
        {
            lastFetchTryMs = millis();
            if (fetchWeatherAndTime()) lastWeatherMs = millis();
            delay(200);
        }
    }

    // 2) 扫一轮价签
    foundFlag = false;
    BLEScan *scan = BLEDevice::getScan();
    scanStartedAtMs = millis();
    scan->start(SCAN_SECONDS, false);       // 阻塞式扫描
    scan->clearResults();

    if (foundFlag)
    {
        if (!tagPresent)
        {
            /* 价签重新出现 = 它刚上电/刷过固件：它自己那份"时间/天气"已经清空，
               所以我们这边的"发过什么"缓存也必须作废 —— 否则会一直打
               "天气没变（晴 35℃）"而不重发，屏上的温度就退回固件内部的片内温度
               （实测：用户看到屏上是 30℃，而芯片温度是 29.9℃）。
               2026-09-30 实机发现。 */
            logf("价签出现了（应该是刚上电/刚刷过）→ 本轮强制校时 + 天气缓存作废");
            forceTimeSync = true;
            lastTimeMs     = 0;          /* 时间也要重发 */
            wxSentCode     = -1;         /* 天气也要重发 */
            wxSentTemp     = -999;
            citySent[0]    = 0;          /* 城市名也要重发（价签掉电后 RAM 里那个没了） */
            memoSent       = false;      /* 纪念日同理 */
            tagSeenAtMs    = millis();   /* B：从这一刻起算"最多等 60 秒" */
        }
        tagPresent = true;

        // 连之前先算：有东西要发，或者开机后还没连过（验证链路）才连。
        // 没有新数据就别连了 —— 省电，也少打扰价签（每次连接它都会发一遍配置通知）。
        // 2026-10-01：PUSH_EVERY_MS 到点也算"有东西要发"（定期把时间+天气重推一次，
        // 让屏上那个时分不至于停在几十分钟前）。
        bool pushDueLoop = (PUSH_EVERY_MS != 0UL) &&
                           (lastPushMs == 0UL || (millis() - lastPushMs) > PUSH_EVERY_MS);
        bool haveData = (needTimeSyncNow() && utcEpoch > 0) || weatherChangedNow() || pushDueLoop;
        /* build-24（B）：手里有没有"值得为它连一次"的数据（时间或天气）。
           ⚠ 城市名/纪念日**不算** —— 它们是静态配置，连上去换一次 16 秒整页刷新不划算，
           等第一份数据一起来的时候顺手带上就行。 */
        bool haveUsable = (utcEpoch > 0) || haveWeather;
        bool waitExpire = (tagSeenAtMs != 0) &&
                          (millis() - tagSeenAtMs) > TAG_FIRST_WAIT_MS;
        /* ⚠ "开机后还没连过"这个条件**必须带 !everSynced**（build-24 第一版把它写反了：
           firstDone = everSynced || … → 第一次连完之后恒为真 → 之后每一轮扫描都连上去、
           发现"没东西要发"再断开。实机日志里 25s / 38s 那两次空连接就是它。
           这里回成原来的 !everSynced，只把"要不要等"这部分叠上去。 */
        bool firstSync  = !everSynced && (haveUsable || waitExpire);
        if (BASE_MODE == 0 || haveData || firstSync)
        {
            /* 发之前先看表"新不新"：手里的 utcEpoch 是上次取天气时拿的（可能几十分钟前），
               直接发就会把旧时刻写进价签 —— 实机踩过：慢了 67 分钟。
               超过 5 分钟就先重新取一次（顺带刷新天气；值没变的话后面照样不重发）。 */
            if (utcEpoch > 0 && (millis() - lastFetchOkMs) > RESYNC_BEFORE_SEND_MS &&
                WiFi.status() == WL_CONNECTED)
            {
                logf("手里的时间已经 %lu 分钟没更新了，发之前先对一次表",
                     (unsigned long)((millis() - lastFetchOkMs) / 60000UL));
                fetchWeatherAndTime();
            }
            doSync(foundDevice);
            everSynced = true;
            delay(1000);
        }
        else
        {
            /* 没连的两种原因分开说，免得日志误导（build-24 起）：
               ① 手里还没有时间/天气（正在等第一次取数）—— 这是 B 主动"先别连"的状态，
                  有 60 秒兜底，值得每 20 秒提一次；
               ② 单纯"没有新东西要发"（时间刚校过、天气没变）—— 5 分钟才提一次。 */
            bool          waiting = !haveUsable;
            unsigned long every   = waiting ? 20000UL : 300000UL;

            if (millis() - lastIdleLogMs > every)
            {
                lastIdleLogMs = millis();
                if (!wifiConfigured())
                {
                    logf("价签在，但没填 WIFI_SSID → 没东西可发，本轮不连接");
                }
                else if (waiting)
                {
                    logf("价签在，但手里还没有时间/天气 → 先不连（省一次整页刷新；已等 %lu 秒，最多 %lu 秒）",
                         (unsigned long)((millis() - tagSeenAtMs) / 1000UL),
                         (unsigned long)(TAG_FIRST_WAIT_MS / 1000UL));
                }
                else
                {
                    logf("价签在，但没有新东西要发（时间刚校过、天气没变）→ 本轮不连接");
                }
            }
        }
    }
    else
    {
        if (tagPresent)
        {
            logf("价签从空中消失了（关机/走远了）");
        }
        tagPresent = false;
    }

    // 3) 心跳
    if (millis() - lastBeatMs > 30000)
    {
        lastBeatMs = millis();
        logf("心跳  heap=%u  wifi=%s  rssi=%d  天气=%s  价签=%s",
             (unsigned)ESP.getFreeHeap(),
             WiFi.status() == WL_CONNECTED ? "OK" : "--",
             WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0,
             haveWeather ? wxName(wxCode) : "无",
             tagPresent ? "在" : "不在");
    }

    delay(POLL_MS);
}
