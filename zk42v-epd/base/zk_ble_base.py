#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZK42V 价签「基站」模拟器 —— 让 Mac 当基站，价签一上电就自动拿到时间和天气。

背景（详见 outputs/HANDOFF.md）：
  * 价签固件（build 21 起）跑的是 tsl0922/EPD-nRF5 那套网页协议，
    价签是从机（GATT server + 一直广播），手机/电脑是主机（central）。
  * 所以「让 Mac 当基站」= 让 Mac 当 BLE central：扫到 ZK42V-EPD → 连上 →
    发 0x20（时间+时区+模式[+天气]）→（没能合并时才单独发 0x71）→ 断开。
  * 全程不需要传图：日历页是固件自己画的，我们只喂「现在几点」和「天气」。
    这正好补上 build 46 的缺口（默认日历模式，但时间没同步过就不会自动画）。

  2026-10-01 三条约定（用户提的）：
    1. **推天气的时候把时间一起带上**（0x20 的合并格式）—— 每次推天气都顺便对一次表，
       价签的钟一直是校准的；而且那一条命令本来就会让它整页重画，代价为零。
    2. 带时间但**不想动页面**时，模式字节发 **0 = 保持当前页**（build 60 起的固件支持）。
       ⚠ 基站并不知道你现在看的是日历页 / 时钟页 / 推的图，发 1 会把页面顶掉。
       watch 常驻模式默认就用 0。
    3. 时间戳**在写下去的前一刻才取**（`now = int(time.time())` 挪到 send 之前）——
       早取几秒（取天气 + 连蓝牙都要时间）就会把旧时刻写进价签。
       ⚠ 顺带记住：日历页**只在换天和收到命令时重画**，所以屏上那个时分只在
         每次推送的那一刻是对的，之后就不动了（不是钟慢）。

三个子命令：
  probe   只扫描，列出附近 BLE 设备（先确认 Mac 能看见价签）
  sync    扫一次，找到就同步一次就退出（手动/调试用）
  watch   常驻：盯着广播，价签一出现就同步；之后按 --interval 定时再同步

用法（venv 见同目录 setup.sh / run.sh）：
  ./run.sh probe
  ./run.sh sync  --tz 8
  ./run.sh watch --tz 8 --weather-interval 21600
  （坐标默认就是深圳公明广场 22.7809/113.8861，换地方用 --lat/--lon）

macOS 两个坑（跟本项目无关，是系统规矩）：
  1. CoreBluetooth 要「蓝牙」权限，必须在 Terminal 里跑（Terminal 持有那份授权）。
     Codex 的沙箱里跑会拿到 state=2(unsupported) —— 实测见 README。
  2. macOS 给的 address 不是 MAC，是本机视角的 UUID，换台电脑就变，
     所以默认按名字找设备（ZK42V-EPD），别写死 address。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner

# ---------------------------------------------------------------- 协议常量
# 服务/特征 UUID：跟固件 Src/ble/zk_epd_svc.c 里那张属性表一字不差
SVC_UUID = "62750001-d828-918d-fb46-b6c11c675aec"
WR_UUID = "62750002-d828-918d-fb46-b6c11c675aec"   # 写命令 / 收通知
VER_UUID = "62750003-d828-918d-fb46-b6c11c675aec"  # 读固件版本（>=0x16 才走新流程）

DEV_NAME = "ZK42V-EPD"          # 广播里的完整名字（zk_ble.c: ZK_BLE_NAME）

CMD_SET_TIME = 0x20             # [0x20, utc_be32, tz_s8, mode]
CMD_SET_WX = 0x71               # [0x71, code, temp_s8]
CMD_READ_BAT = 0x72             # 立刻重读电池 + 重画一页
CMD_SET_CITY = 0x79             # [0x79, utf8 城市名]（build 61：表头温度后面那个）
CMD_SET_MEMO = 0x7A             # [0x7A, mon, day, utf8 祝福语]（build 67：纪念日高亮）
CMD_SET_ALERT = 0x7C            # [0x7C, level, utf8 类型名]（build 71：和风的天气预警）
CMD_SET_ALERT_ICON = 0x7D       # [0x7D, hi, lo]（build 74：和风的预警图标编号）

MODE_KEEP = 0                   # build 60 起的固件认这个：只对表、别动页面
MODE_CALENDAR = 1
MODE_CLOCK = 2

# 温度要变这么多（十分之一度，10 = 1.0℃）才值得推一次
WX_TEMP_DELTA_T10 = 10

WX_SUN, WX_CLOUDY, WX_OVERCAST = 1, 2, 3
WX_LIGHT_RAIN, WX_HEAVY_RAIN, WX_THUNDER = 4, 5, 6
WX_SNOW, WX_FOG, WX_WIND = 7, 8, 9

# WMO weather_code（Open-Meteo 用的就是这套）→ 固件那 9 个码
WMO_MAP = {
    0: WX_SUN, 1: WX_SUN,
    2: WX_CLOUDY, 3: WX_OVERCAST,
    45: WX_FOG, 48: WX_FOG,
    51: WX_LIGHT_RAIN, 53: WX_LIGHT_RAIN, 55: WX_LIGHT_RAIN,
    56: WX_LIGHT_RAIN, 57: WX_LIGHT_RAIN,
    61: WX_LIGHT_RAIN, 80: WX_LIGHT_RAIN,
    63: WX_HEAVY_RAIN, 65: WX_HEAVY_RAIN,
    66: WX_HEAVY_RAIN, 67: WX_HEAVY_RAIN,
    81: WX_HEAVY_RAIN, 82: WX_HEAVY_RAIN,
    71: WX_SNOW, 73: WX_SNOW, 75: WX_SNOW, 77: WX_SNOW,
    85: WX_SNOW, 86: WX_SNOW,
    95: WX_THUNDER, 96: WX_THUNDER, 99: WX_THUNDER,
}

WX_NAME = {
    0: "不显示", WX_SUN: "晴", WX_CLOUDY: "多云", WX_OVERCAST: "阴",
    WX_LIGHT_RAIN: "小雨", WX_HEAVY_RAIN: "大雨", WX_THUNDER: "雷阵雨",
    WX_SNOW: "雪", WX_FOG: "雾", WX_WIND: "风",
}


# ---------------------------------------------------------------- 小工具
def log(msg: str, logfile=None) -> None:
    line = "[" + datetime.now().strftime("%H:%M:%S") + "] " + msg
    print(line, flush=True)
    if logfile:
        try:
            with open(logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def local_tz_hours() -> int:
    """本机时区偏移（小时，整数）。价签屏上的墙钟 = UTC秒 + 这个偏移。"""
    off = datetime.now().astimezone().utcoffset()
    return int(round(off.total_seconds() / 3600.0)) if off else 0


def fetch_weather(lat: float, lon: float, timeout: float = 10.0):
    """Open-Meteo 取实时天气（免 key）。返回 (固件码, 温度, 原始 WMO 码, 风速)。"""
    url = ("https://api.open-meteo.com/v1/forecast"
           "?latitude=" + str(lat) + "&longitude=" + str(lon) +
           "&current=temperature_2m,weather_code,wind_speed_10m"
           "&timezone=auto")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        j = json.load(r)
    cur = j["current"]
    wmo = int(cur["weather_code"])
    temp = float(cur["temperature_2m"])          # ⚠ 别在这儿四舍五入：小数还有用
    wind = float(cur.get("wind_speed_10m") or 0.0)
    code = WMO_MAP.get(wmo, WX_CLOUDY)
    # 没降水但风很大 → 用「风」那个图标（9）
    if code in (WX_SUN, WX_CLOUDY) and wind >= 30.0:
        code = WX_WIND
    return code, temp, wmo, wind


# ---- 和风天气（QWeather，2026-10-01 接）-------------------------------------
#  为什么接它：手机（Apple 天气）在国内用的就是这一路（和风/中国气象局实况），
#  而 Open-Meteo 是**模型格点**，实测同一时刻能差 3~5℃。
#  和风的 now.temp 是**实况温度**，更贴近手机显示的数。
#  ⚠ 三个坑（都实测过）：
#    ① location 是 **"经度,纬度"**（跟 Open-Meteo 反着来）；
#    ② 响应**总是 gzip**（哪怕你写 Accept-Encoding: identity），得自己解；
#    ③ 免费订阅必须用 devapi.qweather.com，标准订阅才是 api.qweather.com。
QW_TEXT_TO_CODE = [
    ("雷", 6), ("雪", 7), ("冰", 7), ("雾", 8), ("霾", 8), ("沙", 8), ("尘", 8),
    ("中雨", 5), ("大雨", 5), ("暴雨", 5), ("雨", 4),
    ("阴", 3), ("多云", 2), ("少云", 2), ("晴间", 2), ("晴", 1),
    ("风", 9), ("台风", 9),
]


def qweather_text_to_code(text: str) -> int:
    for key, code in QW_TEXT_TO_CODE:
        if key in (text or ""):
            return code
    return WX_CLOUDY


# 2026-10-02：**优先用和风的 icon 编号**判天气码（跟 wx-compare.py 同一张表）。
# 为什么：文字匹配（"雷"→雷阵雨、"大雨/暴雨"→大雨…）碰到"雨夹雪/冻雨/阵雨"很容易猜歪，
# 而 icon 编号是枚举、没有歧义（305 小中雨 / 307 大雨 / 302 雷阵雨 / 404 雨夹雪）。
QW_ICON_TO_CODE = {
    100: WX_SUN, 150: WX_SUN,
    101: WX_CLOUDY, 102: WX_CLOUDY, 103: WX_CLOUDY,
    151: WX_CLOUDY, 152: WX_CLOUDY, 153: WX_CLOUDY,
    104: WX_OVERCAST,
    300: WX_LIGHT_RAIN, 305: WX_LIGHT_RAIN, 309: WX_LIGHT_RAIN, 313: WX_LIGHT_RAIN,
    314: WX_LIGHT_RAIN, 350: WX_LIGHT_RAIN, 399: WX_LIGHT_RAIN,
    301: WX_HEAVY_RAIN, 306: WX_HEAVY_RAIN, 307: WX_HEAVY_RAIN, 308: WX_HEAVY_RAIN,
    310: WX_HEAVY_RAIN, 311: WX_HEAVY_RAIN, 312: WX_HEAVY_RAIN, 315: WX_HEAVY_RAIN,
    316: WX_HEAVY_RAIN, 317: WX_HEAVY_RAIN, 318: WX_HEAVY_RAIN, 351: WX_HEAVY_RAIN,
    302: WX_THUNDER, 303: WX_THUNDER, 304: WX_THUNDER,
    400: WX_SNOW, 401: WX_SNOW, 402: WX_SNOW, 403: WX_SNOW, 404: WX_SNOW,
    405: WX_SNOW, 406: WX_SNOW, 407: WX_SNOW, 408: WX_SNOW, 409: WX_SNOW,
    410: WX_SNOW, 456: WX_SNOW, 457: WX_SNOW, 499: WX_SNOW,
    500: WX_FOG, 501: WX_FOG, 502: WX_FOG, 503: WX_FOG, 504: WX_FOG,
    507: WX_FOG, 508: WX_FOG, 509: WX_FOG, 510: WX_FOG, 511: WX_FOG,
    512: WX_FOG, 513: WX_FOG, 514: WX_FOG, 515: WX_FOG,
    900: WX_CLOUDY, 901: WX_CLOUDY,
}


def qweather_code_of_now(now):
    """实况对象 -> 固件码：优先 icon 编号，缺了/认不出才退回文字匹配。
    返回 (码, 判据) —— 判据用来打日志（"icon" / "text"）。"""
    try:
        c = QW_ICON_TO_CODE.get(int((now or {}).get("icon")))
    except (TypeError, ValueError):
        c = None
    if c:
        return c, "icon"
    return qweather_text_to_code((now or {}).get("text")), "text"


def fetch_weather_qweather(lat: float, lon: float, key: str,
                           host: str = "devapi.qweather.com", timeout: float = 10.0):
    """和风天气的**实况**。返回跟 fetch_weather() 一样的 (码, 温度, WMO占位, 风速)。"""
    url = ("https://%s/v7/weather/now?location=%.4f,%.4f&key=%s&lang=zh&unit=m"
           % (host, lon, lat, key))
    req = urllib.request.Request(url, headers={"User-Agent": "zk42v-base/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip as _gz
            raw = _gz.decompress(raw)
    j = json.loads(raw.decode("utf-8", "replace"))
    if str(j.get("code")) != "200":
        raise RuntimeError("和风返回 code=%s（key 不对 / 没开通「实时天气」）" % j.get("code"))
    now = j["now"]
    temp = int(round(float(now["temp"])))
    wind = float(now.get("windSpeed") or 0.0)
    code, _by = qweather_code_of_now(now)          # 优先 icon 编号（见上面的表）
    if code in (WX_SUN, WX_CLOUDY) and wind >= 30.0:
        code = WX_WIND
    return code, temp, now.get("icon"), wind


def fetch_weather_any(args, lat: float, lon: float):
    """按 --wx-source / 有没有 key 决定用谁。
    返回 (码, 温度, WMO, 风速, 源名, 有没有小数)。
    "有没有小数"决定发给固件的载荷格式：带小数用 10 字节（画 27.7℃），
    整数用 9 字节（画 29℃）—— 和风给的就是整数度，别让屏上出现没意义的 "29.0℃"。"""
    src = (getattr(args, "wx_source", "auto") or "auto").lower()
    key = (getattr(args, "qweather_key", "") or os.environ.get("QWEATHER_KEY", "")).strip()
    host = getattr(args, "qweather_host", "devapi.qweather.com")

    relay = (getattr(args, "relay", "") or "").strip()
    if relay:
        return fetch_from_relay(args, relay)

    if src in ("auto", "qweather") and key:
        c, t, w, wind = fetch_weather_qweather(lat, lon, key, host)
        return c, t, w, wind, "和风天气(实况)", False        # 整数度
    if src == "qweather":
        raise RuntimeError("选了和风天气但没给 key（--qweather-key 或环境变量 QWEATHER_KEY）")
    c, t, w, wind = fetch_weather(lat, lon)
    return c, t, w, wind, "Open-Meteo(模型)", True          # 模型值带一位小数


# ---- 局域网中继（NAS 上的 wx-relay.py，2026-10-02 起是**首选**）---------------
#  ESP32 那版基站直连和风连不上（TLS 发不出去），所以在 NAS 上放了个中继替它跑
#  HTTPS+JWT；Mac 这版也能用同一个中继（好处：不用在 Mac 上放和风凭据，
#  而且**预警**也在同一个 JSON 里，跟 ESP32 完全对称）。
#  中继的返回形如：
#     {"code":4,"temp":28,"text":"小雨","src":"qweather","obs":"…",
#      "alert":3,"alevel":4,"atype":"暴雨","aend":"2026-10-03T18:13+08:00"}
def fetch_from_relay(args, url: str):
    """跟 ESP32 基站读的是同一份 JSON。返回跟 fetch_weather_any 一样的六元组。"""
    req = urllib.request.Request(url, headers={"User-Agent": "zk42v-base/1"})
    with urllib.request.urlopen(req, timeout=10.0) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip as _gz
            raw = _gz.decompress(raw)
    j = json.loads(raw.decode("utf-8", "replace"))
    code = int(j["code"])
    temp = float(j["temp"])
    # 中继发的温度"整数就是整数度、带小数就是模型值"——跟固件的两条载荷格式对齐
    has_tenths = abs(temp - round(temp)) >= 0.05
    setattr(args, "_relay_alert", (int(j.get("alert") or 0), int(j.get("alevel") or 0),
                                   str(j.get("atype") or ""), int(j.get("aicon") or 0)))
    return code, temp, None, 0.0, "局域网中继(" + url + ")", has_tenths


# ---- 和风「实时天气预警」（2026-10-02 补）------------------------------------
#  用户："手机上是局地雷暴雨而且有暴雨警报，中继获取的是小雨"。
#  ⚠ 老接口 **/v7/warning/now 已经被和风下架**（403 Deprecated，"Please use the
#    latest version"）；新的是
#        GET /weatheralert/v1/current/{纬度}/{经度}
#    又是**纬度在前**（和风自己都不统一：weather/now 是 经度,纬度）。
#  返回 {"metadata":{…},"alerts":[{eventType,severity,color,headline,expireTime…}]}
ALERT_LEVEL = {"white": 1, "blue": 2, "yellow": 3, "orange": 4, "red": 5}
ALERT_SEVERITY = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}


def fetch_alert_qweather(lat: float, lon: float, key: str,
                         host: str = "devapi.qweather.com", timeout: float = 10.0):
    """返回 (level, 类型名, 条数)：没有生效中的预警就是 (0, "", 0)。
    只有和风这一条路（预警必须带凭据），所以没 key 时调用方根本不该调它。"""
    url = ("https://%s/weatheralert/v1/current/%.2f/%.2f?lang=zh&localTime=true&key=%s"
           % (host, lat, lon, urllib.parse.quote(key)))
    req = urllib.request.Request(url, headers={"User-Agent": "zk42v-base/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip as _gz
            raw = _gz.decompress(raw)
    j = json.loads(raw.decode("utf-8", "replace"))
    alerts = j.get("alerts") or []
    if not alerts:
        return 0, "", 0

    def rank(a):
        col = ((a.get("color") or {}).get("code") or "").lower()
        return (ALERT_LEVEL.get(col, 0), ALERT_SEVERITY.get(a.get("severity") or "", 0))

    top = max(alerts, key=rank)
    col = ((top.get("color") or {}).get("code") or "").lower()
    # ⚠ 元组一定要带括号：`[A, B for x in y]` 里的逗号会被当成两个元素，
    #   解析器随即在 for 处报 SyntaxError（Mac 自带 Python 3.9.6 上踩过）。
    return (ALERT_LEVEL.get(col, 0),
            (top.get("eventType") or {}).get("name") or "",
            len(alerts),
            # 和风的预警图标编号（1003 暴雨 / 1014 雷电…）—— 固件拿它画表头那格
            int(top.get("icon") or 0))


def fetch_alert_any(args, lat: float, lon: float):
    """按当前配置取预警；取不到就返回 None（**绝不能让预警把天气也拖垮**）。"""
    # 走中继的话，预警是刚才那次请求顺手带回来的（同一个 JSON），不用再问一遍
    ra = getattr(args, "_relay_alert", None)
    if ra is not None:
        n, lv, atype, aic = ra
        return (lv, atype, n, aic) if n > 0 and lv > 0 else (0, "", 0, 0)

    key = (getattr(args, "qweather_key", "") or os.environ.get("QWEATHER_KEY", "")).strip()
    host = getattr(args, "qweather_host", "devapi.qweather.com")
    if not key:
        return None
    try:
        return fetch_alert_qweather(lat, lon, key, host)
    except Exception as e:
        log("  ! 取预警失败（不影响天气）: " + str(e)[:160], args.log)
        return None


def explain(e: Exception) -> str:
    """把 bleak 的错误翻成一句能照做的话。"""
    s = str(e)
    if "turned off" in s:
        return "Mac 的蓝牙是关的 —— 在菜单栏/系统设置里打开蓝牙就行（脚本会自己重试）。"
    if "BLE is unsupported" in s:
        return ("CoreBluetooth 报 unsupported：进程没有蓝牙权限。"
                "在 Codex 里跑必然是这样（沙箱），请改在 Terminal 里跑。")
    if "unauthorized" in s.lower():
        return "蓝牙权限被拒：系统设置 → 隐私与安全性 → 蓝牙，把 Terminal 打开。"
    return type(e).__name__ + ": " + s


def is_tag(dev, adv) -> bool:
    name = (dev.name or "") + (adv.local_name or "")
    if DEV_NAME in name:
        return True
    # 名字万一没解析出来，就按扫描响应里的服务 UUID 认
    return SVC_UUID in [u.lower() for u in (adv.service_uuids or [])]


# ---------------------------------------------------------------- probe
async def cmd_probe(args) -> int:
    found = {}

    def cb(dev, adv):
        found[dev.address] = (dev.name, adv.rssi, tuple(adv.service_uuids or ()))

    log("扫 " + str(args.scan) + " 秒（看得到 ZK42V-EPD 就说明 Mac 这侧没问题）…", args.log)
    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(args.scan)
    await scanner.stop()

    if not found:
        log("一个设备都没扫到 —— 多半是蓝牙没授权/没开，或者价签没上电。", args.log)
        return 2

    log("扫到 " + str(len(found)) + " 个设备：", args.log)
    for addr, (name, rssi, svcs) in sorted(found.items(), key=lambda kv: -(kv[1][1] or -999)):
        tag = "  <== 价签" if (name and DEV_NAME in name) else ""
        log("  rssi=" + str(rssi) + "  " + addr + "  " + repr(name) + "  " + str(svcs) + tag, args.log)
    return 0


# ---------------------------------------------------------------- 找设备
async def find_tag(args, scan_s: float, quiet: bool = False):
    """扫 scan_s 秒找价签；找到返回 (device, adv)。"""
    hit = {}

    def cb(dev, adv):
        if is_tag(dev, adv):
            hit["dev"] = dev
            hit["adv"] = adv

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(scan_s)
    await scanner.stop()
    if "dev" not in hit:
        if not quiet:
            log("这一轮没看到价签。", args.log)
        return None, None
    return hit["dev"], hit["adv"]



# ---------------------------------------------------------------- sync
async def sync_once(dev, adv, args, do_time=True, do_weather=True, last_wx=None,
                    last_alert=None) -> bool:
    """连上去把时间和/或天气写进价签。

    ⚠ 固件的脾气（Src/ble/zk_epd_svc.c:930 起）：日历模式只在**换天**时自己重画，
       但我们每发一条 0x20 / 0x71 / 0x72 都会置 s_need_gui → **一次 16 秒全刷**。
       所以基站不能刷得太勤：时间在「刚上电 / 每天一次 / 要推天气时」发，
       天气只在**值变了**才发（last_wx 就是拿来做这个比对的）。
       ⚠ 顺带说明屏上那个时分为什么"不动"：日历页只在换天和收到命令时重画，
         所以它只在每次推送的那一刻是对的。想让它一直新，要么用时钟模式
         （每分钟一张，每张 16 秒全刷），要么把推的间隔调小。
    """
    tz = args.tz if args.tz is not None else local_tz_hours()
    # 模式字节：0 = 保持当前页（只对表）。watch 常驻模式默认走这个 ——
    # 基站不该把用户在网页上选的页面（时钟页/推的图）顶掉。
    if getattr(args, "keep_mode", None):
        mode = MODE_KEEP
    else:
        mode = MODE_CLOCK if args.mode == 2 else MODE_CALENDAR
    # ⚠ 时间戳**不能在这儿取**：下面取天气（HTTP）和连蓝牙都可能花几秒~几十秒，
    #   早取就把旧时刻写进价签了。挪到真正 write 的前一刻（见下面 now = ...）。

    # 天气**先取好**再连：这样连上之后两条命令是挨着发出去的。
    # 为什么在意这个：固件对每条命令都置 s_need_gui → 各刷一屏（`画过 2 次` 就是这么来的）。
    # 挨着发至少有机会被合并；先取天气则少 2 秒的中间等待。
    wx_payload = None
    alert_payload = None
    alert_icon_payload = None       # build 74：预警图标编号单独一条命令
    if do_weather and not args.no_weather:
        try:
            code, temp, wmo, wind, wsrc, has_tenths = fetch_weather_any(args, args.lat,
                                                                        args.lon)
            # **不四舍五入**（用户 2026-09-30 要求）：按十分之一度发，面板上显示一位小数。
            # 但和风给的是**整数度** —— 那种就发整数形态，屏上写 29℃ 而不是 29.0℃
            #（少占 12px，表头挤城市名时很宝贵）。
            t10 = max(-32768, min(32767, int(round(temp * 10))))
            # 阈值判定（用户 2026-09-30 拍板）：天气码变了必发；温度变化 ≥1.0℃ 才发；
            # 从没发过也发。其余不推 —— 省一次 17 秒全刷。
            prev = last_wx.get("v") if last_wx else None
            changed = True
            if prev is not None:
                pc, pt10 = prev
                changed = (pc != code) or (abs(t10 - pt10) >= WX_TEMP_DELTA_T10)
            if not changed:
                log("  = 天气没变（" + WX_NAME.get(code, str(code)) + " " + str(temp)
                    + "℃），不重发 —— 省一次全刷。", args.log)
            else:
                # 71 <code> <t_hi> <t_lo>：t 是 int16 的"十分之一度"（34.6℃ → 346）；
                # 整数度就用 71 <code> <t_int8>（固件认这个"没小数"的短格式）
                if has_tenths:
                    wx_payload = bytes([CMD_SET_WX, code, (t10 >> 8) & 0xFF, t10 & 0xFF])
                else:
                    ti = int(round(temp))
                    wx_payload = bytes([CMD_SET_WX, code, ti & 0xFF])
                log("  · 天气取好了[" + wsrc + "]（" + str(args.lat) + "," + str(args.lon) + "）："
                    + WX_NAME.get(code, str(code)) + " " + ("%.1f" % temp) + "℃"
                    + "（Open-Meteo WMO=" + str(wmo) + " 风速=" + str(wind) + "km/h）", args.log)
                if last_wx is not None:
                    last_wx["v"] = (code, t10)
        except Exception as e:
            log("  ! 取天气失败：" + explain(e), args.log)

        # 顺带取**天气预警**（和风）：有预警时表头那格"天气文字"改画它。
        # 预警和天气是**两条独立的路** —— 预警取不到（没 key / 接口抽风）不影响天气。
        # 只在"有 / 没有"或"类型名或级别变了"时才发，不然每轮都白刷一屏。
        alert = fetch_alert_any(args, args.lat, args.lon)
        if alert is not None:
            lv, atype, an, aic = alert
            prev_a = (last_alert or {}).get("a")
            if prev_a != (lv, atype):
                if lv > 0:
                    alert_payload = bytes([CMD_SET_ALERT, lv & 0xFF]) + atype.encode("utf-8")
                    if aic > 0:      # build 74：预警图标编号（单独一条 0x7D，老固件会忽略）
                        alert_icon_payload = bytes([CMD_SET_ALERT_ICON,
                                                    (aic >> 8) & 0xFF, aic & 0xFF])
                    log("  · 天气预警[" + str(an) + " 条]：最严重的是 " + atype
                        + {1: "白色", 2: "蓝色", 3: "黄色", 4: "橙色", 5: "红色"}.get(lv, "")
                        + "预警 → 表头那格改画它（" + ("红色" if lv >= 3 else "黑色") + "）",
                        args.log)
                else:
                    alert_payload = bytes([CMD_SET_ALERT, 0])       # 清了
                    log("  · 天气预警已解除 → 发一条清掉屏上那条", args.log)
                if last_alert is not None:
                    last_alert["a"] = (lv, atype)

    # 2026-10-01（用户要求）：**只要这次要推天气，就把时间也一起带上** ——
    # 每次推天气都顺便对一次表，价签的钟一直是校准的；反正那条命令本来就要重画一页。
    put_time = bool(do_time or (wx_payload is not None))

    log("找到价签：" + repr(dev.name) + " rssi=" + str(adv.rssi) + " addr=" + dev.address + "；连接中…", args.log)

    try:
        async with BleakClient(dev, timeout=args.connect_timeout) as cli:
            try:
                ver = await cli.read_gatt_char(VER_UUID)
                log("  固件协议版本 = 0x" + ver[0].to_bytes(1, "big").hex().upper() +
                    "（>=0x16 就是新流程）", args.log)
            except Exception as e:
                log("  读版本失败（不影响）：" + explain(e), args.log)

            def on_notify(_h, data: bytearray):
                txt = bytes(data).decode("latin-1", "replace")
                log("  ← 价签：" + repr(txt) + "  (" + bytes(data).hex(" ") + ")", args.log)

            try:
                await cli.start_notify(WR_UUID, on_notify)
            except Exception as e:
                log("  开通知失败（只影响回读日志）：" + explain(e), args.log)

            # 1) 时间 + 时区 + 模式 [+ 天气] —— 这条落地后价签会整页重画（约 16 秒）
            #    build 50 起支持把天气**一起带上**：20 <utc4> <tz> <mode> [wx] [temp]
            #    2026-10-01：**只要这次要推天气，就顺带把时间也放进来**（用户要求）——
            #    每次推天气都对一次表，而且反正只画一页、只刷一次。
            merged = (wx_payload is not None) and put_time
            if put_time:
                now = int(time.time())          # ← 写下去的前一刻才取（见上面那条注释）
                wall = datetime.fromtimestamp(now + tz * 3600, tz=timezone.utc)
                payload = bytes([CMD_SET_TIME,
                                 (now >> 24) & 0xFF, (now >> 16) & 0xFF,
                                 (now >> 8) & 0xFF, now & 0xFF,
                                 tz & 0xFF, mode])
                if merged:
                    payload += wx_payload[1:]          # 去掉 0x71 那个命令字节，只带 code+temp
                await cli.write_gatt_char(WR_UUID, payload, response=True)
                log("  → 时间：UTC " + str(now) + " + 时区" + str(tz) + "h → 价签应显示 "
                    + wall.strftime("%Y-%m-%d %H:%M:%S")
                    + "，模式=" + str(mode) + "（"
                    + {MODE_KEEP: "保持当前页，只对表",
                       MODE_CALENDAR: "日历", MODE_CLOCK: "时钟"}[mode] + "）"
                    + ("+天气（合并成一条）" if merged else "")
                    + "  载荷=" + payload.hex(" "),
                    args.log)

            # 2) 天气（前面已经取好了，这里就是紧跟着上一条发出去）
            if wx_payload is not None and not merged:
                await cli.write_gatt_char(WR_UUID, wx_payload, response=True)
                log("  → 天气  载荷=" + wx_payload.hex(" "), args.log)
            elif merged:
                log("  （天气已经并进上一条命令了，不用单独发）", args.log)

            # 3) 城市名（build 61）—— 表头温度后面写它。基站知道自己的经纬度，
            #    所以由基站发；固件只认常见城市名的字模（北京的"京"有、生僻字没有）。
            #    ⚠ 改 --lat/--lon 时记得把 --city 也改了（这里不做逆地理编码，
            #      免得多一条外部依赖；城市名就是给屏上看的一个标签）。
            city = (getattr(args, "city", None) or "").strip()
            if city:
                try:
                    await cli.write_gatt_char(WR_UUID,
                                              bytes([CMD_SET_CITY]) + city.encode("utf-8"),
                                              response=True)
                    log("  → 城市名：" + city, args.log)
                except Exception as e:
                    log("  城市名发送失败（不影响）：" + explain(e), args.log)

            # 4) 纪念日提醒（build 67）—— 那天套黑框 + 空白处框出祝福语
            #    格式：--memo "10-05=付婧文生日快乐！"（生日按月日重复，每年都亮）
            #    ⚠ 祝福语只能用固件字模里有的字（tools/gen_font.py 的 MEMO_CHARS），
            #      认不出的字会被静默跳过 —— 要加字就改那张表再重编固件。
            memo = (getattr(args, "memo", None) or "").strip()
            if memo and "=" in memo:
                spec, text = memo.split("=", 1)
                try:
                    mm, dd = [int(v) for v in spec.replace("/", "-").split("-")]
                    payload = bytes([CMD_SET_MEMO, mm & 0xFF, dd & 0xFF]) + text.encode("utf-8")
                    await cli.write_gatt_char(WR_UUID, payload, response=True)
                    log("  → 纪念日：%02d-%02d %s（那天套黑框 + 空白处框出这句话）"
                        % (mm, dd, text), args.log)
                except Exception as e:
                    log("  纪念日发送失败（不影响）：" + explain(e), args.log)
            elif memo == "off":                 # 清掉
                await cli.write_gatt_char(WR_UUID, bytes([CMD_SET_MEMO]), response=True)
                log("  → 已清掉纪念日提醒", args.log)

            # 4b) 天气预警（build 71）—— 和风的实时预警，有预警时表头那格改画它
            #     （详见 fetch_alert_qweather 上面那段注释：老接口已下架，新路径
            #      是 /weatheralert/v1/current/纬度/经度）
            if alert_payload is not None:
                try:
                    await cli.write_gatt_char(WR_UUID, alert_payload, response=True)
                    log("  → 天气预警  载荷=" + alert_payload.hex(" "), args.log)
                    if alert_icon_payload is not None:
                        await cli.write_gatt_char(WR_UUID, alert_icon_payload, response=True)
                        log("  → 预警图标  载荷=" + alert_icon_payload.hex(" "), args.log)
                except Exception as e:
                    log("  天气预警发送失败（不影响）：" + explain(e), args.log)

            # 5) 顺手读一次电池（价签会回 bat=.. pct=..）
            if args.battery:
                await cli.write_gatt_char(WR_UUID, bytes([CMD_READ_BAT]), response=True)
                log("  → 已请求读电池（0x72）", args.log)

            # 留点时间把通知收完；整页刷新会在我们断开后自己跑完
            await asyncio.sleep(args.dwell)
            try:
                await cli.stop_notify(WR_UUID)
            except Exception:
                pass
    except Exception as e:
        log("  连接/写入失败：" + explain(e), args.log)
        return False

    log("  同步完成，已断开（价签这时在刷屏，约 16 秒）。", args.log)
    return True


# ---------------------------------------------------------------- sync 子命令
async def cmd_sync(args) -> int:
    dev, adv = await find_tag(args, args.scan)
    if not dev:
        log("没找到价签：确认它上电了（换电池/刚刷机后要碰一下 RST），"
            "以及这台 Mac 的蓝牙已授权。", args.log)
        return 2
    return 0 if await sync_once(dev, adv, args) else 1


# ---------------------------------------------------------------- raw 子命令
async def cmd_raw(args) -> int:
    """把原始字节发给价签的写特征（调试用）。用法：

       ./run.sh raw "03 18 | 04 80 | 03 1A | 04 55 | 03 22 | 04 D7 | 03 20"

       `03 xx` = SEND_CMD（把一个字节当命令发给屏）
       `04 xx` = SEND_DATA（把一个字节当数据发给屏）
       两条都是固件里现成的调试命令，直接透传到面板（UC8176）。
    """
    dev, adv = await find_tag(args, args.scan)
    if not dev:
        log("没找到价签。", args.log)
        return 2

    hexstr = args.hex or args.hexpos            # 两种写法都认：raw --hex "..." / raw "..."
    if not hexstr:
        log('raw 模式要带字节串，例如：raw "03 22 | 04 C7 | 03 20"', args.log)
        return 2
    groups = [g.strip() for g in hexstr.split("|") if g.strip()]
    log("连价签发原始命令：" + " | ".join(groups), args.log)
    try:
        async with BleakClient(dev, timeout=args.connect_timeout) as cli:
            for g in groups:
                payload = bytes.fromhex(g.replace(" ", ""))
                await cli.write_gatt_char(WR_UUID, payload, response=True)
                log("  → " + payload.hex(" "), args.log)
                await asyncio.sleep(0.06)
            await asyncio.sleep(args.dwell)
    except Exception as e:
        log("发送失败：" + explain(e), args.log)
        return 1
    log("发完了。", args.log)
    return 0


# ---------------------------------------------------------------- watch 子命令
async def cmd_watch(args) -> int:
    # watch 是"常驻基站"：它只在天气变了/到点时才连一次，**不该动用户在网页上选的页面**，
    # 所以默认用模式 0（保持当前页）—— 除非命令行里显式 --no-keep-mode。
    if args.keep_mode is None:
        args.keep_mode = True
    log("基站模式启动：每 " + str(args.scan) + " 秒扫一轮；价签一出现就同步时间+天气，"
        "之后时间每 " + str(int(args.time_interval)) + " 秒、天气每 "
        + str(int(args.weather_interval)) + " 秒复查一次（值没变不发）；ctrl-C 退出。",
        args.log)
    if args.keep_mode:
        log("（推命令时模式字节发 0 = 保持价签当前页面，不会把它顶回日历页）", args.log)
    last_time = {}      # addr -> 上次成功同步时间的 monotonic 时间
    last_wx_t = {}      # addr -> 上次查天气的 monotonic 时间
    last_wx = {}        # addr -> {"v": (code, temp)} 上次真发出去的天气值
    last_alert = {}     # addr -> {"a": (level, 类型名)} 上次真发出去的预警
    present = set()     # 上一轮在广播的设备

    while True:
        try:
            dev, adv = await find_tag(args, args.scan, quiet=True)
        except Exception as e:
            log("扫描出错：" + explain(e), args.log)
            await asyncio.sleep(5)
            continue

        if dev is None:
            if present:
                log("价签从空中消失了（关机/走远了）。", args.log)
            present = set()
            await asyncio.sleep(args.poll)
            continue

        addr = dev.address
        now = time.monotonic()
        reappeared = addr not in present        # 刚上电/刚回来 → 立刻同步
        due_time = addr not in last_time or (now - last_time[addr]) >= args.time_interval
        due_wx = addr not in last_wx_t or (now - last_wx_t[addr]) >= args.weather_interval
        present = {addr}

        if reappeared:
            log("价签出现了（应该是刚上电，它自己的时间是空的）→ 立刻校时。", args.log)

        if reappeared or due_time or due_wx:
            do_wx = reappeared or due_wx
            # 2026-10-01（用户要求）：推天气的时候把时间一起带上（合并成一条命令），
            # 这样每次推天气都顺便对一次表 —— 所以 do_wx 也就意味着 do_time。
            do_time = reappeared or due_time or do_wx
            if await sync_once(dev, adv, args, do_time, do_wx, last_wx.setdefault(addr, {}),
                               last_alert.setdefault(addr, {})):
                if do_time:
                    last_time[addr] = time.monotonic()
                if do_wx:
                    last_wx_t[addr] = time.monotonic()
            else:
                await asyncio.sleep(5)
        await asyncio.sleep(args.poll)


# ---------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ZK42V 价签基站模拟器（Mac 当 BLE central）")
    p.add_argument("cmd", choices=["probe", "sync", "watch", "raw"])
    p.add_argument("--hex", default="", help='raw 模式要发的字节，用 | 分组，例："03 18 | 04 80"')
    p.add_argument("hexpos", nargs="?", default="",
                   help='同上（位置写法）：raw "03 22 | 04 C7 | 03 20"')
    p.add_argument("--tz", type=int, default=None,
                   help="时区小时数（默认用本机时区；北京时间填 8）")
    p.add_argument("--mode", type=int, default=1, choices=[1, 2],
                   help="1=日历页（默认） 2=时钟页")
    p.add_argument("--keep-mode", dest="keep_mode", action="store_true", default=None,
                   help="模式字节发 0（build 60 固件）= 保持价签当前页面，只对表/推天气。"
                        "sync 默认关（按 --mode 切页面）；watch 默认开。")
    p.add_argument("--no-keep-mode", dest="keep_mode", action="store_false",
                   help="watch 里也按 --mode 切页面（会把价签顶回日历页）")
    # 默认点 = 深圳公明广场（光明区公明街道）。坐标来源见 README 第 2.1 节：
    # OSM(Overpass) 里"公明广场"本体 22.7809/113.8861，三个同名条目 + 公明广场地铁站都在 20m 内。
    p.add_argument("--lat", type=float, default=22.7809, help="纬度（默认：深圳公明广场 22.7809）")
    p.add_argument("--lon", type=float, default=113.8861, help="经度（默认：深圳公明广场 113.8861）")
    p.add_argument("--city", default="深圳",
                   help="表头温度后面显示的城市名（build 61 起；空串 = 不显示）。"
                        "换 --lat/--lon 时记得一起改（不做逆地理编码）")
    p.add_argument("--memo", default="",
                   help='纪念日提醒（build 67 起）："10-05=付婧文生日快乐！" = 那天套黑框 + '
                        '空白处框出这句话（按月日重复）；"off" = 清掉。'
                        "文案只能用固件字模里有的字（gen_font.py 的 MEMO_CHARS）")
    p.add_argument("--wx-source", default="auto", choices=["auto", "open-meteo", "qweather"],
                   help="天气用哪个源：auto（有 key 就用和风，没有就 Open-Meteo）/ "
                        "open-meteo（模型格点，免 key）/ qweather（实况，要 key）")
    p.add_argument("--qweather-key", default="",
                   help="和风天气的 key（免费订阅即可；也可以放环境变量 QWEATHER_KEY）。"
                        "填了 auto 就会自动改用它 —— 它给的是**实况**，最贴近手机上那个数")
    p.add_argument("--qweather-host", default="devapi.qweather.com",
                   help="免费订阅 devapi.qweather.com；标准订阅 api.qweather.com")
    p.add_argument("--relay", default=os.environ.get("WX_RELAY_URL", ""),
                   help="局域网中继的地址（例 http://192.168.100.221:8788/wx）。"
                        "给了它就走中继：Mac 上不用放和风凭据，而且**天气预警**也"
                        "在同一份 JSON 里 —— 跟 ESP32 基站完全对称。")
    p.add_argument("--no-weather", action="store_true", help="不发天气，只对时间")
    p.add_argument("--battery", action="store_true", help="顺带发 0x72 读一次电池")
    p.add_argument("--scan", type=float, default=6.0, help="单轮扫描秒数（默认 6）")
    p.add_argument("--poll", type=float, default=3.0, help="watch 模式每轮之间歇（秒）")
    p.add_argument("--time-interval", type=float, default=86400.0,
                   help="watch 里多久重新校一次时间（秒，默认 86400=一天）")
    p.add_argument("--weather-interval", type=float, default=21600.0,
                   help="watch 里多久查一次天气（秒，默认 21600=6 小时；值没变不会发）")
    p.add_argument("--dwell", type=float, default=2.0, help="同步后停留收通知的秒数")
    p.add_argument("--connect-timeout", type=float, default=20.0)
    p.add_argument("--log", default=None, help="同时把日志写进这个文件")
    return p


def main() -> int:
    args = build_parser().parse_args()
    runner = {"probe": cmd_probe, "sync": cmd_sync,
              "watch": cmd_watch, "raw": cmd_raw}[args.cmd]
    try:
        return asyncio.run(runner(args))
    except KeyboardInterrupt:
        log("收到 ctrl-C，退出。", args.log)
        return 0
    except Exception as e:
        log(explain(e), args.log)
        return 3


if __name__ == "__main__":
    sys.exit(main())
