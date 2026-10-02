#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
**和风天气的局域网中继** —— 跑在常开的机器上（NAS / Mac），让 ESP32 用纯 HTTP 取天气。

为什么需要它（2026-10-02 实机结论）：
  · 和风给每个账号分配的 API Host（xxx.re.qweatherapi.com）**解析到海外节点**
    （实测 139.162.26.165 = 新加坡 Linode），而且**只能用 HTTPS**；
  · 你的路由器直连它没问题（curl 0.09 秒、401），但**这台 ESP32 连它的 443 就是不通**
    （HTTPClient 报 -1，1 秒内被拒）；换 MTU、收紧超时、加大栈都没用。
  · 于是让**能连的那台机器**（NAS/Mac）去请求和风，ESP32 只跟局域网说话：
        和风(HTTPS+JWT) ←── 本脚本 ──→ ESP32（纯 HTTP，http://nas:8788/wx）
    额外好处：**Ed25519 私钥只存在 NAS/Mac 上**，ESP32 里连密钥都不用放。

用法（在这台机器上，跑一次就一直挂着）：

    cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/ble-base
    python3 wx-relay.py \
        --host kj4bjd22dq.re.qweatherapi.com \
        --jwt-key ed25519-private.pem \
        --kid KMWDYQGERV --sub 29TNG35JCC --dev-id Q92D603497 \
        --lat 22.7809 --lon 113.8861 --port 8788

    # 或者只用 API key（更省事，安全性差些）：
    #   --qweather-key 你的key

然后 ESP32 里填一行：

    #define WX_RELAY_URL "http://192.168.100.50:8788/wx"     // 换成这台机器的局域网 IP

自测（在 Mac 上）：  curl -s http://192.168.100.50:8788/wx
        {"code":3,"temp":29,"text":"阴","src":"qweather","obs":"2026-10-02T12:15+08:00"}

接口约定（固件只认这两个字段，别改名字）：
    code  固件那 9 个天气码（1晴 2多云 3阴 4小雨 5大雨 6雷阵雨 7雪 8雾 9风）
    temp  ℃，**整数**表示没小数（和风就是整数度），带小数点表示有小数（模型值）

2026-10-02 补：**天气预警**（用户："手机上有暴雨警报、价签只有小雨"）
    和风的老接口 /v7/warning/now 已经**下架**（403 Deprecated），新的是
        GET /weatheralert/v1/current/{纬度}/{经度}     ← 注意是**纬度在前**
    预警字段摊平成几个**平铺**字段（不是嵌套对象）—— ESP32 那边是手写字符串查找，
    嵌套的 "code" 会和实况的 "code" 撞车：
        alert   生效中的预警条数（0 = 没有；字段缺省也当没有）
        alevel  最严重的一条的级别：1白 2蓝 3黄 4橙 5红（固件只有黑/白/红三色，
                所以 ≥4（橙/红）用红色画，其余用黑色）
        atype   预警类型名（暴雨 / 雷电 / 雷雨大风 / 台风 …）
        aend    到期时间（和风给的 ISO 串，本地时间）
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def load_wx_compare():
    """复用 wx-compare.py 里那套已经验通的取数逻辑（文件名带横线，只能这样导）"""
    spec = importlib.util.spec_from_file_location("wx_compare",
                                                  os.path.join(HERE, "wx-compare.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = load_wx_compare()

CACHE = {"t": 0.0, "body": None}       # 上次成功的结果（和风抽风时继续用它）


def fetch_once(a) -> dict:
    """取一次天气：优先和风（JWT 或 key），失败退回 Open-Meteo。"""
    # ① 和风
    try:
        if a.jwt_key and a.kid and a.sub and a.dev_id:
            # 签一次就够：实况和预警共用同一个 token（make_jwt 是 openssl 子进程 + 一堆打印，
            # 每 2 分钟签两遍既慢又吵）
            tok = W.make_jwt(a.jwt_key, a.kid, a.sub, a.dev_id, quiet=True)
            now, upd = W.qweather_jwt(a.jwt_key, a.kid, a.sub, a.lat, a.lon,
                                      a.host, a.dev_id, token=tok)
            alerts = W.qweather_alert(a.host, a.lat, a.lon, bearer=tok)
        elif a.qweather_key:
            now, upd = W.qweather(a.qweather_key, a.lat, a.lon, a.host)
            alerts = W.qweather_alert(a.host, a.lat, a.lon, key=a.qweather_key)
        else:
            raise RuntimeError("没配和风凭据（--jwt-key+--kid+--sub+--dev-id 或 --qweather-key）")
        text = now.get("text") or ""
        out = {"code": W.qweather_code(text), "temp": float(now["temp"]),
               "text": text, "src": "qweather", "obs": now.get("obsTime", "")}
        out.update(alert_fields(alerts))
        return out
    except Exception as e:
        err = str(e)[:200]

    # ② 退回 Open-Meteo（模型值，带小数）
    try:
        cur, glat, glon, elev = W.om(a.lat, a.lon, a.model)
        out = {"code": _code_of(cur.get("weather_code")),
               "temp": float(cur["temperature_2m"]),
               "text": W.WMO_TO_TAG.get(cur.get("weather_code"), ""),
               "src": "open-meteo", "obs": cur.get("time", ""), "qweather_err": err}
        return out
    except Exception as e2:
        raise RuntimeError("和风失败(%s)；Open-Meteo 也失败(%s)" % (err, str(e2)[:120]))


def alert_fields(alerts) -> dict:
    """预警 → JSON 里那几个平铺字段。**预警取不到绝不能让天气也拿不到**，
    所以这里出错一律当作"没有预警"（只印一行日志）。"""
    try:
        s = W.alert_summary(alerts)
    except Exception as e:
        print("[relay] 预警解析失败（当没有预警处理）：%s" % str(e)[:120])
        return {}
    if not s:
        return {"alert": 0}
    return {"alert": s["n"], "alevel": s["level"], "atype": s["type"], "aend": s["until"]}


def _code_of(wmo) -> int:
    """WMO 码 -> 固件那 9 个码（跟 wx-compare 里那张表一致）"""
    tag = W.WMO_TO_TAG.get(wmo, "")
    return {"晴": 1, "多云": 2, "阴": 3, "小雨": 4, "大雨": 5,
            "雷阵雨": 6, "雪": 7, "雾": 8, "风": 9}.get(tag, 2)


class Handler(BaseHTTPRequestHandler):
    a = None

    def log_message(self, fmt, *args):          # 别把每行访问日志刷满屏幕
        if os.environ.get("WX_RELAY_VERBOSE"):
            sys.stderr.write("[relay] " + fmt % args + "\n")

    def do_GET(self):
        if self.path.split("?")[0] not in ("/wx", "/weather", "/"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"use /wx"}')
            return

        age = time.time() - CACHE["t"]
        body = CACHE["body"]
        if body is None or age > self.a.cache:
            try:
                body = fetch_once(self.a)
                CACHE["t"], CACHE["body"] = time.time(), body
                print("[relay] %s %.1f℃ %s（%s）"
                      % (body["src"], body["temp"], body["text"], body.get("obs", "")),
                      end="")
                if body.get("alert"):
                    print("   ⚠ %s%s预警 到 %s" % (body.get("atype", ""),
                                                   {1: "白", 2: "蓝", 3: "黄", 4: "橙",
                                                    5: "红"}.get(body.get("alevel"), ""),
                                                   body.get("aend", "")))
                else:
                    print("   无预警")
            except Exception as e:
                if CACHE["body"] is not None:       # 取不到就继续用旧的（价签那边别断）
                    print("[relay] 取数失败，先用 %.0f 秒前的旧值：%s" % (age, str(e)[:120]))
                    body = CACHE["body"]
                else:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)[:200]}).encode())
                    return

        # temp：整数就发整数（固件那边显示 29℃），有小数就带小数（显示 27.7℃）
        t = body["temp"]
        out = dict(body)
        out["temp"] = int(round(t)) if abs(t - round(t)) < 0.05 else round(t, 1)
        # ⚠ 用**紧凑**写法（默认的 json.dumps 会在冒号后加空格：`"atype": "暴雨"`）。
        #   2026-10-02 实机就是这么坑了 ESP32 一次：它那个手写的字符串查找当时只认
        #   `"atype":"`，于是数字字段全对、字符串字段全空（预警名画不出来）。
        #   那边已经改成"跳空格"了，这里也跟着紧凑输出：省几个字节，也少一类坑。
        data = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    ap = argparse.ArgumentParser(description="和风天气局域网中继（给 ESP32 用纯 HTTP 取数）")
    env = os.environ.get
    ap.add_argument("--bind", default=env("WX_RELAY_BIND", "0.0.0.0"),
                    help="监听地址（默认所有网卡）")
    ap.add_argument("--port", type=int, default=int(env("WX_RELAY_PORT", "8788")))
    ap.add_argument("--cache", type=float, default=float(env("WX_RELAY_CACHE", "120")),
                    help="多少秒内不重复请求和风（默认 120 秒；ESP32 每 30 分钟才来一次）")
    ap.add_argument("--host", default=env("QWEATHER_HOST", "devapi.qweather.com"),
                    help="和风的 API Host（控制台-设置里那串）")
    ap.add_argument("--jwt-key", default=env("QWEATHER_JWT_KEY", ""),
                    help="Ed25519 私钥 PEM（kid/sub/dev-id 一起给）")
    ap.add_argument("--kid", default=env("QWEATHER_JWT_KID", ""), help="凭据 ID")
    ap.add_argument("--sub", default=env("QWEATHER_JWT_SUB", ""), help="项目 ID")
    ap.add_argument("--dev-id", default=env("QWEATHER_DEV_ID", ""),
                    help="开发者 ID（iss，Q 开头）")
    ap.add_argument("--qweather-key", default=env("QWEATHER_KEY", ""),
                    help="或者用 API key（不推荐，但没有的话更省事）")
    ap.add_argument("--lat", type=float, default=float(env("WX_RELAY_LAT", "22.7809")))
    ap.add_argument("--lon", type=float, default=float(env("WX_RELAY_LON", "113.8861")))
    ap.add_argument("--model", default="", help="退到 Open-Meteo 时用的模型（空=best_match）")
    a = ap.parse_args()
    a.port  = int(a.port)          # env 来的 default 不会被 argparse 转类型，自己转
    a.cache = float(a.cache)
    a.lat, a.lon = float(a.lat), float(a.lon)

    Handler.a = a
    print("和风中继启动：http://%s:%d/wx   （和风 Host=%s，%.4f,%.4f）"
          % (a.bind, a.port, a.host, a.lat, a.lon))
    print("ESP32 那边填：  #define WX_RELAY_URL \"http://<本机局域网IP>:%d/wx\"" % a.port)
    try:
        ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
