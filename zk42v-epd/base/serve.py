#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
**一个服务，两件事**（2026-10-03 用户："把刚才的服务整合到一起"）：

  ① 提供那个网页 —— 就是原来 `cd work/github/epd-nrf5-user/html && python3 -m http.server 8777`
     然后浏览器开 http://localhost:8777/ 的那个「墨水屏日历」页面（原版 EPD-nRF5 的网页版，
     浏览器用 **Web Bluetooth** 直连价签，能推图、切页面、发命令）。
  ② 在**后台当基站** —— 用 zk_ble_base.py 那套（扫价签 → 连上 → 推时间/天气/城市名/
     纪念日/预警），不用再单开一个终端窗口。

外加一个小接口层，让网页能看见基站在干什么：

    GET  /api/status   价签在不在 / 上次推了啥 / 现在的天气和预警 / 最近日志
    GET  /api/config   读 配置.json
    POST /api/config   写 配置.json（改城市、坐标、纪念日、和风 key）
    POST /api/sync     让基站**立刻**重新推一次（不等下一轮）
    GET  /wx           当前天气的 JSON（跟 NAS 上那个中继同一个格式）
    GET  /base         基站面板（状态 + 配置，给不想敲命令的人用）

用法：

    python3 serve.py                      # 默认 8777 端口，网页目录自动找
    python3 serve.py --port 8777 --config kit/配置.json
    python3 serve.py --no-base            # 只当网页服务器（老用法）

⚠ 一件事要知道：网页那边（浏览器）和这边的基站都要用蓝牙适配器。macOS/Windows 上
多路 central 是允许的，但基站每几秒扫一次，浏览器连接**可能**会慢一点。
如果你正在用网页推图，可以 `--no-base` 只开网页。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import zk_ble_base as B                    # noqa: E402  基站本体（同一个目录）

# 网页目录：优先找本机那份 epd-nrf5-user/html；找不到就退到 kit/web（送人版会带上）
CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(HERE)),
                 'work', 'github', 'epd-nrf5-user', 'html'),
    '/Users/mac/Documents/Codex/2026-09-15/a/work/github/epd-nrf5-user/html',
    os.path.join(HERE, 'kit', 'web'),
]

MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.jpg': 'image/jpeg',
        '.svg': 'image/svg+xml', '.json': 'application/json; charset=utf-8',
        '.ico': 'image/x-icon', '.txt': 'text/plain; charset=utf-8'}


def find_web_dir(given: str) -> str:
    if given:
        return given
    for c in CANDIDATES:
        if os.path.isdir(c) and os.path.exists(os.path.join(c, 'index.html')):
            return c
    return ''


class Handler(BaseHTTPRequestHandler):
    web_dir = ''
    cfg_path = ''

    def log_message(self, fmt, *args):
        if os.environ.get('SERVE_VERBOSE'):
            sys.stderr.write('[serve] ' + fmt % args + '\n')

    # ------------------------------------------------------------ 小工具
    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path):
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write('not found'.encode())
            return
        self.send_response(200)
        self.send_header('Content-Type',
                         MIME.get(os.path.splitext(path)[1].lower(),
                                  'application/octet-stream'))
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ GET
    def do_GET(self):
        path = self.path.split('?')[0]

        if path == '/api/status':
            self._json(status_obj())
            return
        if path == '/api/config':
            self._json(read_cfg(self.cfg_path))
            return
        if path == '/wx':
            # 跟 NAS 中继同一个格式，方便别人/别的脚本接
            w = dict(B.STATUS.get('weather') or {})
            a = B.STATUS.get('alert') or {}
            if w:
                w['alert'] = a.get('n', 0)
                w['alevel'] = a.get('level', 0)
                w['atype'] = a.get('type', '')
                w['aicon'] = a.get('icon', 0)
            self._json(w or {"error": "基站还没取到天气"})
            return
        if path in ('/base', '/base/'):
            p = os.path.join(HERE, 'kit', '基站面板.html')
            if os.path.exists(p):
                self._file(p)
            else:
                self._json({"error": "缺 kit/基站面板.html"}, 500)
            return

        # 其它一律当静态文件（网页本身）
        rel = path.lstrip('/') or 'index.html'
        full = os.path.normpath(os.path.join(self.web_dir, rel))
        if not full.startswith(os.path.abspath(self.web_dir)):     # 别被 ../ 爬出去
            self.send_response(403)
            self.end_headers()
            return
        if os.path.isdir(full):
            full = os.path.join(full, 'index.html')
        self._file(full)

    # ------------------------------------------------------------ POST
    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else b''
        path = self.path.split('?')[0]

        if path == '/api/sync':
            B.FORCE_SYNC['at'] = time.time()
            B.log('网页点了「立刻同步」→ 下一轮强行推一次', None)
            self._json({"ok": True})
            return
        if path == '/api/config':
            try:
                cfg = json.loads(body.decode('utf-8'))
            except Exception as e:
                self._json({"error": "不是合法 JSON：%s" % e}, 400)
                return
            if not isinstance(cfg, dict):
                self._json({"error": "应该是个 JSON 对象"}, 400)
                return
            try:
                with open(self.cfg_path, 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self._json({"error": "写不进去：%s" % e}, 500)
                return
            B.log('网页改了配置（重启服务后生效）', None)
            self._json({"ok": True, "note": "改完要重启这个服务才生效"})
            return

        self.send_response(404)
        self.end_headers()


# ---------------------------------------------------------------- 状态 / 配置
def read_cfg(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return {"_读不到配置": str(e)}


def status_obj():
    s = B.STATUS
    now = time.time()

    def ago(t):
        return round(now - t, 1) if t else None

    w = dict(s.get('weather') or {})
    a = dict(s.get('alert') or {})
    return {
        "service": "运行中",
        "uptime_s": round(now - s.get("started", now), 1),
        "tag": s.get("tag", False),
        "tag_seen_ago": ago(s.get("tag_seen", 0)),
        "last_push_ago": ago(s.get("last_push", 0)),
        "weather": w,
        "alert": a,
        "log": list(s.get("log", []))[-40:],
    }


# ---------------------------------------------------------------- 基站线程
def base_thread(cfg_path, web_dir, no_base):
    if no_base:
        B.log('--no-base：只当网页服务器，不跑基站', None)
        return

    argv = ["zk_ble_base.py", "watch"]
    if cfg_path:
        argv += ["--config", cfg_path]
    if web_dir:
        argv += ["--log", os.path.join(os.path.dirname(cfg_path) or HERE, '运行日志.txt')]
    sys.argv = argv
    args = B.build_parser().parse_args()
    B.load_config(args)
    B.STATUS["config"] = {k: getattr(args, k, None)
                          for k in ("city", "lat", "lon", "tz", "memo",
                                    "wx_source", "qweather_key", "qweather_host")}
    B.STATUS["config"]["qweather_key"] = bool(args.qweather_key)     # 别把 key 露出去
    B.log('基站线程启动（网页那边照旧用 Web Bluetooth，两条路互不干扰）', None)
    try:
        import asyncio
        asyncio.run(B.cmd_watch(args))
    except Exception as e:
        B.log('基站线程挂了：%s' % B.explain(e), None)


def main() -> int:
    ap = argparse.ArgumentParser(description="价签服务：网页 + 后台基站，一个端口")
    ap.add_argument("--port", type=int, default=8777, help="端口（默认 8777，跟以前一样）")
    ap.add_argument("--bind", default="127.0.0.1", help="监听地址（默认只有本机能访问）")
    ap.add_argument("--web", default="", help="网页目录（默认自动找 epd-nrf5-user/html）")
    ap.add_argument("--config", default=os.path.join(HERE, "kit", "配置.json"))
    ap.add_argument("--no-base", action="store_true", help="只当网页服务器")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    a = ap.parse_args()

    web = find_web_dir(a.web)
    if not web:
        print("❌ 找不到网页目录（work/github/epd-nrf5-user/html）—— 用 --web 指定")
        return 2
    Handler.web_dir = web
    Handler.cfg_path = a.config

    t = threading.Thread(target=base_thread, args=(a.config, web, a.no_base), daemon=True)
    t.start()

    url = "http://localhost:%d/" % a.port
    print("=" * 60)
    print(" 墨水屏价签服务")
    print("   网页   : %s            （推图/切页面/发命令，浏览器直连价签）" % url)
    print("   面板   : %sbase        （基站状态 + 改配置，不用敲命令）" % url)
    print("   网页目录: %s" % web)
    print("   配置    : %s" % a.config)
    print("=" * 60)
    if a.open:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n退出。")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
