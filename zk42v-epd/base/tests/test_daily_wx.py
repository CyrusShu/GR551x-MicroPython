#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「当天天气」（固件 build 102 的 BLE 命令 0x74）这条路的离线自测。

    .venv/bin/python tests/test_daily_wx.py

为什么要有它：这条路上有三个**看不出来**的坑，都只能在这儿钉住 ——
  ① 中继返回的是**文字**（"多云"），固件要的是**码**（2）：折算错了屏上就是另一个天气；
  ② 白天/夜间**两个码一样时得发 0**（屏上不画「转」），不然"多云转多云"那种会多两个字；
  ③ 日期必须进"值"里 —— 跨天时哪怕天气一字不差也得发一次（0 点那次推送靠它）。
另外验一下：中继没带 day/night/tmin/tmax 时**要抛出清楚的话**（提示去更新 NAS 上那个文件），
而不是静默发一版"没有天气"。
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, BASE)

CHECKS = []


def check(label, ok, detail=""):
    CHECKS.append((label, bool(ok), detail))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


W = load("zk_wx_compare", os.path.join(BASE, "wx-compare.py"))
B = load("zk_ble_base", os.path.join(BASE, "zk_ble_base.py"))


class Args:
    """只放这条路上用到的几个属性（fetch_daily_any 会 getattr，给默认值就行）"""
    relay = ""
    wx_source = "open-meteo"
    qweather_key = ""
    qweather_host = "devapi.qweather.com"


def main():
    # 1) 折算 + 载荷：用户 2026-10-16 实测那条（多云/多云 25~31 → 74 02 00 19 1F）
    rec = W.daily_to_protocol("多云", "多云", 25, 31, "2026-10-16")
    check("1: 白天/夜间都是多云 → 夜间码发 0（屏上不画「转」）", rec["night"] == 0, rec)
    check("1: 载荷 = 74 02 00 19 1F（跟用户实测一致）",
          W.daily_payload(rec).hex(" ").upper() == "74 02 00 19 1F",
          W.daily_payload(rec).hex(" "))

    # 2) 两个码不同 → 都发出去（屏上写「晴转多云」）
    rec2 = W.daily_to_protocol("晴", "多云", 24, 31, "2026-10-16")
    check("2: 白天晴/夜间多云 → 白天码 1、夜间码 2（要画「转」）",
          (rec2["day"], rec2["night"]) == (1, 2), rec2)
    check("2: 载荷 = 74 01 02 18 1F",
          W.daily_payload(rec2).hex(" ").upper() == "74 01 02 18 1F",
          W.daily_payload(rec2).hex(" "))

    # 3) 上下限写反了要自己换过来（屏上不能出现 "31~25"）
    rec3 = W.daily_to_protocol("阴", "阴", 31, 24)
    check("3: tmin/tmax 写反了自动换过来", (rec3["tmin"], rec3["tmax"]) == (24, 31), rec3)

    # 4) 零下（int8 补码）：-3~5 → FD 05
    rec4 = W.daily_to_protocol("晴", "晴", -3, 5)
    check("4: 零下温度按有符号 int8 发（-3 → 0xFD）",
          W.daily_payload(rec4).hex(" ").upper() == "74 01 00 FD 05",
          W.daily_payload(rec4).hex(" "))

    # 5) 中继那份 JSON → 载荷（中继返回的是**文字**，得折算对）
    j = {"code": 2, "temp": 27, "day": "晴", "night": "多云", "tmin": 24, "tmax": 31,
         "ddate": "2026-10-16"}
    a = Args()
    a.relay = "http://example.invalid/wx"
    saved = B.fetch_relay_json
    B.fetch_relay_json = lambda args, url: j          # 打桩：不打网络
    try:
        rec5, src5 = B.fetch_daily_any(a, 22.78, 113.89)
        check("5: 中继的 day/night/tmin/tmax 折算成 0x74 载荷",
              W.daily_payload(rec5).hex(" ").upper() == "74 01 02 18 1F", (rec5, src5))
        check("5: 源名写着「局域网中继(当天)」（日志里一眼看出走了哪条路）",
              "中继" in src5, src5)

        # 6) 老中继（没这四个字段）→ 要抛清楚的话，别静默当"没有天气"
        B.fetch_relay_json = lambda args, url: {"code": 2, "temp": 27}
        try:
            B.fetch_daily_any(a, 22.78, 113.89)
            check("6: 中继没带当天天气时抛异常", False, "居然没抛")
        except RuntimeError as e:
            check("6: 中继没带当天天气时抛清楚的话（提示更新中继）",
                  "wx-relay" in str(e), str(e))
    finally:
        B.fetch_relay_json = saved

    # 7) "值"里必须有日期：跨天一定要发一次（哪怕天气一字不差）
    d1 = B.daily_key(W.daily_to_protocol("多云", "多云", 25, 31, "2026-10-16"))
    d2 = B.daily_key(W.daily_to_protocol("多云", "多云", 25, 31, "2026-10-17"))
    d3 = B.daily_key(W.daily_to_protocol("多云", "多云", 25, 31, "2026-10-16"))
    check("7: 同一天、天气没变 → key 一样（不发）", d1 == d3, (d1, d3))
    check("7: 跨天、天气没变 → key 不一样（要发，0 点靠它）", d1 != d2, (d1, d2))

    # 8) Open-Meteo 兜底：一天只有一个码、没有"转"（夜间码必须是 0）
    rec8 = W.daily_to_protocol(W.WMO_TO_TAG.get(61, ""), "", 20, 26, "2026-10-16")
    check("8: Open-Meteo 兜底（61=小雨）→ 白天码 4、夜间码 0",
          (rec8["day"], rec8["night"]) == (4, 0), rec8)

    bad = 0
    for label, ok, detail in CHECKS:
        print("  [%s] %s" % ("PASS" if ok else "**FAIL**", label))
        if not ok and detail:
            print("       · %s" % (detail,))
        bad += 0 if ok else 1
    print("全部通过 ✅" if bad == 0 else "有 %d 项失败 ❌" % bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
