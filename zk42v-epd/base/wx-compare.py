#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把几个天气源在**同一坐标、同一时刻**的读数摆在一起 —— 用来回答
"为什么价签上的温度跟手机差了 3 度"。

    python3 wx-compare.py                      # 默认就用基站那组坐标（深圳公明广场）
    python3 wx-compare.py --lat 22.7809 --lon 113.8861
    python3 wx-compare.py --city-code 101280601   # 顺便查中国天气网的城市实况页

为什么要它（2026-10-01 实机）：
    价签 29.9℃、手机（Apple 天气·光明区）33℃，用户问"差这么多正常吗、你的源是哪来的"。
    一查：**同一时刻同一坐标，各家模型差了整整 5℃**
        best_match 29.9 / ecmwf 30.8 / cma(中国气象局) 31.7 / icon 32.4 / gfs 35.0
    所以差 2~3℃ 是**气象源本身的分歧**，不是我们算错了 —— 手机用的是它自己的
    数据链（国内是 和风天气/QWeather + 中国气象局实况），我们默认用的是
    Open-Meteo 的 best_match（它会挑一个模型，可能是最离群的那个）。

想更贴近手机：把基站的 `WX_MODEL` 换成 `icon_seamless` 或 `cma_grapes_global`，
或者干脆用这里打印的"多模型平均"（`WX_ENSEMBLE`）。
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

MODELS = [
    ("best_match", ""),                 # 我们基站默认用的那个
    ("ecmwf_ifs025", "ecmwf_ifs025"),   # 欧洲中心
    ("cma_grapes_global", "cma_grapes_global"),   # 中国气象局 GRAPES
    ("icon_seamless", "icon_seamless"),           # 德国 DWD
    ("gfs_seamless", "gfs_seamless"),             # 美国 NOAA
]

# WMO 天气码 → 固件那 9 个码（跟 tools 里的映射一致，只用来"看个大概"）
WMO_TO_TAG = {
    0: "晴", 1: "晴", 2: "多云", 3: "阴",
    45: "雾", 48: "雾", 51: "小雨", 53: "小雨", 55: "小雨",
    56: "小雨", 57: "小雨", 61: "小雨", 63: "大雨", 65: "大雨",
    66: "大雨", 67: "大雨", 71: "雪", 73: "雪", 75: "雪", 77: "雪",
    80: "小雨", 81: "大雨", 82: "大雨", 85: "雪", 86: "雪",
    95: "雷阵雨", 96: "雷阵雨", 99: "雷阵雨",
}


def get_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": "zk42v-wx-compare/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)          # 和风天气**总是** gzip（哪怕你写明 identity）
        return json.loads(raw.decode("utf-8", "replace"))


# ---- 和风天气（QWeather）：手机在国内用的就是这一路 ---------------------------
# 它给的是**实况温度**（观测/融合值），不是模型格点，所以更贴近手机上显示的数。
def qweather_code(text: str) -> int:
    """和风的天气文字 -> 固件那 9 个码（1晴 2多云 3阴 4小雨 5大雨 6雷阵雨 7雪 8雾 9风）"""
    t = text or ""
    if "雷" in t:
        return 6
    if "雪" in t or "冰" in t:
        return 7
    if any(k in t for k in ("雾", "霾", "沙", "尘")):
        return 8
    if "雨" in t:
        return 5 if any(k in t for k in ("中雨", "大雨", "暴雨", "强")) else 4
    if "阴" in t:
        return 3
    if any(k in t for k in ("多云", "少云", "晴间")):
        return 2
    if "晴" in t:
        return 1
    if any(k in t for k in ("风", "台风", "飑")):
        return 9
    return 2


def qweather(key: str, lat: float, lon: float, host: str = "devapi.qweather.com"):
    """注意 location 是**经度,纬度**（跟 Open-Meteo 反着来，踩过一次）"""
    return qweather_url("https://%s/v7/weather/now?location=%.4f,%.4f&key=%s&lang=zh&unit=m"
                        % (host, lon, lat, key))


def _http_error_body(e) -> str:
    """把 urllib 抛的 HTTPError 里的**响应体**抠出来（和风把原因写在里面）。"""
    try:
        raw = e.read()
        if getattr(e, "headers", None) is not None and \
           (e.headers.get("Content-Encoding") or "") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")[:300]
    except Exception:
        return ""


def qweather_url(url: str, bearer: str = ""):
    req = urllib.request.Request(url, headers={"User-Agent": "zk42v-wx-compare/1"})
    if bearer:
        req.add_header("Authorization", "Bearer " + bearer)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as e:
        body = _http_error_body(e)
        hint = ""
        if "Invalid Host" in body:
            hint = ("\n    → **域名不对**：和风给每个帐号分配独立的 API Host，"
                    "去 控制台-设置 复制那串（形如 xxx.xx.qweatherapi.com）填到 --qweather-host；"
                    "\n      老的 devapi/api/geoapi 已从 2026 年起逐步停服")
        raise RuntimeError("HTTP %s  响应体：%s%s" % (e.code, body, hint))
    d = json.loads(raw.decode("utf-8", "replace"))
    if str(d.get("code")) != "200":
        raise RuntimeError("和风返回 code=%s（401/403 通常是 key 不对，"
                           "或这个 key 没开通「实时天气 now」）" % d.get("code"))
    return d["now"], d.get("updateTime", "")


# ---- 和风 JWT（EdDSA）：在 Mac 上用私钥签一个，用来**不刷机先验通** -----------
#  ⚠ 跟 keygen 脚本一样的坑：/usr/bin/openssl 是 LibreSSL，不支持 ed25519 ——
#    所以这里也要自己找一个能干活的 openssl。
def find_openssl() -> str:
    for c in (os.environ.get("OPENSSL"), "/opt/homebrew/bin/openssl",
              "/usr/local/bin/openssl", "/opt/homebrew/opt/openssl@3/bin/openssl",
              "/usr/local/opt/openssl@3/bin/openssl", shutil.which("openssl"),
              "/usr/bin/openssl"):
        if not c or not os.path.exists(c):
            continue
        r = subprocess.run([c, "genpkey", "-algorithm", "ed25519", "-out", os.devnull],
                           capture_output=True)
        if r.returncode == 0:
            return c
    raise RuntimeError("找不到支持 ed25519 的 openssl（brew install openssl@3）")


_PUB_SHOWN = False


def make_jwt(pem_path: str, kid: str, sub: str, iss: str = "") -> str:
    global _PUB_SHOWN
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()   # noqa: E731
    now = int(time.time())
    hdr = json.dumps({"alg": "EdDSA", "kid": kid}, separators=(",", ":"))
    # ⚠ payload 必须是四个字段：iss（开发者 ID，Q 开头）/ sub（项目 ID）/ iat / exp。
    #   我第一版漏了 iss —— 和风只回一句 "Authentication failed"，排了好久。
    pay = json.dumps({"iss": iss, "sub": sub, "iat": now - 30, "exp": now + 900},
                     separators=(",", ":"))
    # 打印出"我到底签了什么"——排 401 的时候一眼就能看出 kid/sub 是不是填反了
    print("    JWT header  = %s" % hdr)
    print("    JWT payload = %s" % pay)
    try:      # 顺手报一下"这把私钥对应的公钥"——401 时拿它跟控制台里那把对一眼
        if not _PUB_SHOWN:
            der = subprocess.run([find_openssl(), "pkey", "-in", pem_path, "-pubout",
                                  "-outform", "DER"], capture_output=True, check=True).stdout
            print("    本机私钥对应的公钥 = %s" % der[-32:].hex())
            _PUB_SHOWN = True
    except Exception:
        pass
    signing_input = b64(hdr.encode()) + "." + b64(pay.encode())
    with tempfile.TemporaryDirectory() as td:
        m = os.path.join(td, "m"); s = os.path.join(td, "s")
        open(m, "w").write(signing_input)
        r = subprocess.run([find_openssl(), "pkeyutl", "-sign", "-rawin",
                            "-inkey", pem_path, "-in", m, "-out", s],
                           capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(
                "私钥文件用不了：%s\n"
                "    → 签名必须用**私钥**（ed25519-private.pem）。"
                "如果你给的是公钥（ed25519-public.pem / *-public.pem），它只能验证不能签名。"
                % pem_path)
        sig = open(s, "rb").read()
    return signing_input + "." + b64(sig)


def qweather_jwt(pem_path: str, kid: str, sub: str, lat: float, lon: float,
                 host: str = "devapi.qweather.com", iss: str = ""):
    url = ("https://%s/v7/weather/now?location=%.4f,%.4f&lang=zh&unit=m"
           % (host, lon, lat))
    try:
        return qweather_url(url, bearer=make_jwt(pem_path, kid, sub, iss))
    except RuntimeError as e:
        # 401/403 十有八九是**两个 ID 填反了** —— 控制台里"凭据 ID"和"项目 ID"
        # 长得一模一样（都是 10 来位大写字母数字）。这里自动换个顺序再试一次，
        # 能通就直接告诉你反了，省得来回猜。
        if "401" not in str(e) and "403" not in str(e):
            raise
        print("    （第一次 401/403 —— 换一下 kid/sub 顺序再试一遍…）")
        try:
            now, upd = qweather_url(url, bearer=make_jwt(pem_path, sub, kid, iss))
            print("    ⚠⚠ **两个 ID 填反了**！正确的用法是：")
            print("         --qweather-kid %s --qweather-sub %s" % (sub, kid))
            return now, upd
        except RuntimeError:
            raise e


def om(lat: float, lon: float, model: str):
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
           "&timezone=auto" % (lat, lon))
    if model:
        url += "&models=" + model
    d = get_json(url)
    return d["current"], d.get("latitude"), d.get("longitude"), d.get("elevation")


def main() -> int:
    ap = argparse.ArgumentParser(description="几个天气源的同点同时对比")
    ap.add_argument("--lat", type=float, default=22.7809, help="纬度（默认：基站那组）")
    ap.add_argument("--lon", type=float, default=113.8861, help="经度（默认：基站那组）")
    ap.add_argument("--city-code", default="",
                    help="中国天气网城市编号（深圳=101280601），会打印它的实况页链接")
    ap.add_argument("--qweather-key", default=os.environ.get("QWEATHER_KEY", ""),
                    help="和风天气的 key（也可以放环境变量 QWEATHER_KEY）。填了就一并对比它")
    ap.add_argument("--qweather-host", default=os.environ.get("QWEATHER_HOST",
                                                             "devapi.qweather.com"),
                    help="**你自己的 API Host**（控制台-设置里那串，形如 "
                         "h2a9cf3mhs.xy.qweatherapi.com）。⚠ 老的 devapi/api/geoapi "
                         "是公共地址，2026 起逐步停服，新帐号用它会报 Invalid Host")
    ap.add_argument("--qweather-jwt-key", default="",
                    help="Ed25519 私钥 PEM（keygen 生成的 ed25519-private.pem）。"
                         "给了它 + kid + sub 就用 JWT 请求 —— **不刷机先验通**")
    ap.add_argument("--qweather-kid", default=os.environ.get("QWEATHER_JWT_KID", ""),
                    help="凭据 ID（控制台建的 JWT 凭据）")
    ap.add_argument("--qweather-sub", default=os.environ.get("QWEATHER_JWT_SUB", ""),
                    help="项目 ID（控制台-项目管理 里的「项目 ID」）")
    ap.add_argument("--qweather-dev-id", default=os.environ.get("QWEATHER_DEV_ID", ""),
                    help="**开发者 ID（iss）**：控制台-设置 里那个 Q 开头的 10 位。"
                         "⚠ JWT 的 payload 必须有它，少了就和风一句 Authentication failed")
    args = ap.parse_args()

    print("坐标 %.4f, %.4f（跟基站 WX 请求完全一致）" % (args.lat, args.lon))
    print("")

    if args.qweather_jwt_key and args.qweather_kid and args.qweather_sub and \
            not args.qweather_dev_id:
        print("⚠ 少了 --qweather-dev-id（开发者 ID，iss）：去 控制台-设置 复制那个 Q 开头的 10 位。")
        print("  和风的 JWT payload 必须是 {iss, sub, iat, exp} 四个字段，缺 iss 一定 401。")
        print("")
    if args.qweather_jwt_key and args.qweather_kid and args.qweather_sub:
        try:
            now, upd = qweather_jwt(args.qweather_jwt_key, args.qweather_kid,
                                    args.qweather_sub, args.lat, args.lon,
                                    args.qweather_host, args.qweather_dev_id)
            print("★ 和风天气（**JWT 认证**，实况）: %s°C  体感 %s°C  %s  ↦ 固件码 %d"
                  % (now.get("temp"), now.get("feelsLike"), now.get("text"),
                     qweather_code(now.get("text"))))
            print("    观测时刻 %s   更新 %s" % (now.get("obsTime"), upd))
            print("    ✅ JWT 这套（私钥/kid/sub）是通的 —— ESP32 那边照这个填就行")
            print("")
        except Exception as e:
            print("★ 和风 JWT 取不到：%s" % e)
            print("    · 401 且 host 已经对了，只剩两种可能：")
            print("      ① 两个 ID 反了（控制台里「凭据 ID」和「项目 ID」长得一样）——")
            print("         本工具刚才应该自动试过反的顺序了，没成功就再手动对调一次")
            print("      ② 控制台里那把**公钥不是这个私钥的**（比如换过密钥、或上次上传失败）——")
            print("         重跑 `bash qweather-jwt-keygen.sh` 它会打印现有私钥对应的公钥 PEM，"
                  "拿它**重新上传**一次")
            print("    · 还想用 API key 试：--qweather-key")
            print("")
    elif args.qweather_key:
        try:
            now, upd = qweather(args.qweather_key, args.lat, args.lon, args.qweather_host)
            print("★ 和风天气（实况，跟手机同一路）: %s°C  体感 %s°C  %s  ↦ 固件码 %d"
                  % (now.get("temp"), now.get("feelsLike"), now.get("text"),
                     qweather_code(now.get("text"))))
            print("    观测时刻 %s   更新 %s   风 %s km/h   湿度 %s%%"
                  % (now.get("obsTime"), upd, now.get("windSpeed"), now.get("humidity")))
            print("    ↑ 想让它成为价签的源：ESP32 里 QWEATHER_KEY 填同一个 key")
            print("")
        except Exception as e:
            print("★ 和风天气取不到：%s" % e)
            print("")
    else:
        print("（没给和风凭据，跳过和风：--qweather-jwt-key + --qweather-kid + --qweather-sub，")
        print("  或者先用 API key：--qweather-key）")
        print("")

    print("%-20s %-8s %-10s %-9s %-8s %s" %
          ("源 / 模型", "温度", "体感", "天气", "风", "时刻"))
    print("-" * 74)

    temps = []
    for name, model in MODELS:
        try:
            cur, glat, glon, elev = om(args.lat, args.lon, model)
            t = cur["temperature_2m"]
            temps.append(t)
            print("%-20s %-8s %-10s %-9s %-8s %s" % (
                name, "%.1f°C" % t,
                "%.1f°C" % cur.get("apparent_temperature", float("nan")),
                WMO_TO_TAG.get(cur.get("weather_code"), str(cur.get("weather_code"))),
                "%.1f km/h" % cur.get("wind_speed_10m", 0),
                cur["time"]))
            if model == "":
                print("  ↑ 基站现在用的就是这个（%.4f,%.4f 网格点，海拔 %.0fm）"
                      % (glat, glon, elev or 0))
        except Exception as e:
            print("%-20s 取不到：%s" % (name, e))

    if temps:
        print("-" * 74)
        print("%-20s %.1f°C   （%s）" % (
            "多模型平均", sum(temps) / len(temps),
            " / ".join("%.1f" % t for t in temps)))
        print("%-20s %.1f°C   ← 各家最大分歧" % ("极差", max(temps) - min(temps)))

    if args.city_code:
        print("")
        print("中国天气网（城市实况，手机/网页同源）:")
        print("  https://www.weather.com.cn/weather1d/%s.shtml" % args.city_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
