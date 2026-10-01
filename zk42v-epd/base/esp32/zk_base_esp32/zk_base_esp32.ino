/* ===========================================================================
 *  ZK42V 价签「基站」—— ESP32 / ESP32-S3 通用固件（build-9）
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
#define HTTP_TRIES          3             // 取天气失败重试几次
#define HTTP_RETRY_GAP_MS   800
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

/* WiFi 关联的状态机：关联本身可能要几十秒（信号弱时实测 ~100 秒），
   所以记下"什么时候开始喊的"，30 秒内不重复 WiFi.begin()。 */
static bool          wifiStarted = false;
static unsigned long wifiBegunAt = 0;

static bool  haveWeather = false;
static int   wxCode = 0;          // 0 = 不显示
static int   wxTemp = -128;       // -128 = 没有
static int   wxSentCode = -1;     // 上次真发出去的（"值没变就不发"）
static int   wxSentTemp = -999;

static int32_t utcEpoch = 0;        // UTC 秒（0 = 还没拿到）
static bool    tagPresent = false;  // 上一轮扫描有没有看到价签
static bool    forceTimeSync = true;
static bool    everSynced = false;  // 开机后至少连过一次（验证链路用）
static char    citySent[24] = "";   // 上次发出去的城市名（build 61；没变就不重发）
static bool    memoSent = false;    // 纪念日发过没有（build 67）

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
    String pat = String("\"") + key + "\":\"";
    int i = s.indexOf(pat);
    if (i < 0) return false;
    int j = s.indexOf('"', i + (int)pat.length());
    if (j < 0) return false;
    s.substring(i + (int)pat.length(), j).toCharArray(out, n);
    return true;
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

static bool fetchWeatherAndTime()
{
    String url = String(WX_USE_TLS ? "https://" : "http://") + WX_HOST
               + String("/v1/forecast?latitude=")
               + String(LAT, 4) + "&longitude=" + String(LON, 4)
               + "&current=temperature_2m,weather_code,wind_speed_10m&timezone=auto";

    String body, dateHdr;
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
            body    = http.getString();
            dateHdr = http.header("Date");
            got     = true;
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
    lastFetchOkMs = millis();
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
            logf("→ 命中价签：%s  rssi=%d  addr=%s", name.c_str(), dev.getRSSI(),
                 dev.getAddress().toString().c_str());
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

    logf("连接中 ...");
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    bool ok = client->connectTimeout(&dev, 15000);      // 毫秒
#else
    client->setConnectTimeout(15);                      // 秒
    bool ok = client->connect(&dev);
#endif
    if (!ok)
    {
        logf("连接失败");
        return false;
    }
    logf("连上了");

    /* 顺手把 MTU 谈大（build-9）：价签支持到 244（网页那套协议就是靠它推图的），
       谈大之后一次能发 244 字节，长文案/以后的功能都不用再分片。
       谈不成也不影响 —— 下面写命令的地方全都按 ≤20 字节分片了。 */
    if (client->setMTU(247))
    {
        logf("MTU 协商后 = %d（一次能发 %d 字节）",
             (int)client->getMTU(), (int)client->getMTU() - 3);
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
        if (haveWeather)                   /* 合并：20 <utc4> <tz> <mode> <wx> <temp> */
        {
            p[7] = (uint8_t)wxCode;
            p[8] = (uint8_t)((wxTemp >> 8) & 0xFF);   /* 十分之一度，int16 */
            p[9] = (uint8_t)(wxTemp & 0xFF);
            n    = 10;
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
    if (haveWeather)
    {
        if (!sendWx)
        {
            logf("= 天气没变（%s %.1f℃），不重发 —— 省一次全刷",
                 wxName(wxCode), (double)wxTemp / 10.0);
        }
        else if (!sentTime)
        {
            uint8_t p[4] = {0x71, (uint8_t)wxCode,
                            (uint8_t)((wxTemp >> 8) & 0xFF), (uint8_t)(wxTemp & 0xFF)};
            wr->writeValue(p, sizeof(p), true);
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

    delay(NOTIFY_DWELL_MS);                 // 留点时间把价签的回包收全
    client->disconnect();

    forceTimeSync = false;
    logf("已断开。%s",
         (sentTime || sentWx || wroteCity || wroteMemo) ? "价签这时在刷屏，约 16 秒"
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
    Serial.println(" ZK42V 价签基站 (ESP32) build-9");
    Serial.printf (" 芯片: %s rev%d %d 核 @%dMHz  Flash %uMB  PSRAM %s\n",
                   ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(),
                   ESP.getCpuFreqMHz(), (unsigned)(ESP.getFlashChipSize() / 1048576),
                   psramFound() ? "有" : "无");
    Serial.printf (" 时间源: HTTP Date 头   天气: Open-Meteo（%s）\n",
                   WX_USE_TLS ? "HTTPS/TLS" : "纯 HTTP，绕开 TLS 吃堆");
    Serial.printf (" 坐标: %.4f, %.4f   时区: UTC%+d   模式: %s\n",
                   (double)LAT, (double)LON, TZ_HOURS,
                   BASE_MODE == 0 ? "0 只扫描" : "1 扫描+连接+写");
    if (!wifiConfigured())
    {
        Serial.println(" !! 没填 WIFI_SSID：只能验证蓝牙，不会发时间/天气");
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
        // 失败要退避：不然每一轮（约 10 秒）都去撞一次，日志刷屏、还费电
        if ((needWeather || needTime) &&
            (millis() - lastFetchTryMs) > FETCH_FAIL_BACKOFF_MS)
        {
            lastFetchTryMs = millis();
            if (fetchWeatherAndTime()) lastWeatherMs = millis();
            delay(200);
        }
    }

    // 2) 扫一轮价签
    foundFlag = false;
    BLEScan *scan = BLEDevice::getScan();
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
        }
        tagPresent = true;

        // 连之前先算：有东西要发，或者开机后还没连过（验证链路）才连。
        // 没有新数据就别连了 —— 省电，也少打扰价签（每次连接它都会发一遍配置通知）。
        // 2026-10-01：PUSH_EVERY_MS 到点也算"有东西要发"（定期把时间+天气重推一次，
        // 让屏上那个时分不至于停在几十分钟前）。
        bool pushDueLoop = (PUSH_EVERY_MS != 0UL) &&
                           (lastPushMs == 0UL || (millis() - lastPushMs) > PUSH_EVERY_MS);
        bool haveData = (needTimeSyncNow() && utcEpoch > 0) || weatherChangedNow() || pushDueLoop;
        if (BASE_MODE == 0 || haveData || !everSynced)
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
        else if (millis() - lastIdleLogMs > 300000UL)      // 5 分钟才提一次，别刷屏
        {
            lastIdleLogMs = millis();
            if (!wifiConfigured())
            {
                logf("价签在，但没填 WIFI_SSID → 没东西可发，本轮不连接");
            }
            else
            {
                logf("价签在，但没有新东西要发（时间刚校过、天气没变）→ 本轮不连接");
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
