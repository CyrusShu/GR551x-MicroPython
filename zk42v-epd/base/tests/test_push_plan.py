#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测：基站「什么时候才连上价签写东西」这套判断（zk_ble_base.decide_sync）。

为什么值得单测：价签一次整屏 ≈16~18 秒 ≈0.067 mAh —— 是电池最大的一笔。
2026-10-14 用户要求"**只在每天 0 点刷新一次**"，而这套判断里藏着一个很隐蔽的坑：

    do_time = reappeared or due_time or do_wx or new_day
                                    ^^^^^^^^
    天气关掉（`天气源: off`）之后 0x71 是不发了，但 due_wx 到点（6 小时）**还是真的**，
    于是基站照样每 6 小时推一次时间 → 每 6 小时白刷一屏，跟"只刷一次"正好相反。

所以这里把判据钉死：
  A 纯日历：第一眼推一次；之后**整天都不推**；跨到新的一天推一次；再之后又不推
  B 没关天气时：行为不变（due_wx / due_time 该真还是真）—— 免得这条改动误伤老路
  C 天荒地老：连着跨 3 天，每天各一次（不是"一天憋好几次"，也不是"一次都不推"）

    python3 tests/test_push_plan.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zk_ble_base as M      # noqa: E402

ADDR = "a0:a3:b8:2d:76:8c"
CHECKS = []


def check(label, ok, detail=""):
    CHECKS.append((label, ok, detail))


def args_for(no_weather):
    """造一个足够 decide_sync 用的 args（真 argparse 太吵）"""
    return types.SimpleNamespace(no_weather=no_weather,
                                 time_interval=86400.0,
                                 weather_interval=21600.0)


def simulate(no_weather, days=3, start_of_day=10 * 3600):
    """按 3 秒一轮跑一遍 watch 的决策，返回"每一轮推不推"的时间线。

    跟 cmd_watch 里那段保持一致：due_time/due_wx/new_day 由 decide_sync 给，
    真正决定"写不写"的那一行是 reappeared or due_time or due_wx or new_day。
    start_of_day：从这一天里的第几秒开始模拟（默认 10:00 —— 故意**不从 0 点开始**，
    这样"刚出现那次"和"跨天那次"不会撞在一起，两个都能单独看到）。
    """
    a = args_for(no_weather)
    last_time, last_wx_t, pushed_day = {}, {}, {}
    events = []                    # (第几秒, 推/不推, 为什么, 本地日期序号)
    step = 3.0
    for i in range(int(days * 86400 / step) + 1):
        now = i * step                                  # 用"秒"当单调钟
        date = "2026-10-%02d" % (1 + int((now + start_of_day) // 86400))
        reappeared = (i == 0)                           # 第一轮：价签"刚出现"
        due_time, due_wx, new_day = M.decide_sync(a, ADDR, now, last_time,
                                                  last_wx_t, pushed_day, date)
        if reappeared or due_time or due_wx or new_day:
            why = []
            if reappeared:
                why.append("刚出现")
            if due_time:
                why.append("首次/到点对表")
            if due_wx:
                why.append("天气到点")
            if new_day:
                why.append("跨天")
            events.append((now, True, "+".join(why), date))
            last_time[ADDR] = now
            if reappeared or due_wx:
                last_wx_t[ADDR] = now
            pushed_day[ADDR] = date
        else:
            events.append((now, False, "", date))
    return events


def pushes(events):
    return [e for e in events if e[1]]


def main():
    # A) 纯日历：一天只推一次（跨天那次）
    ev = simulate(no_weather=True, days=3)
    p = pushes(ev)
    check("A1: 纯日历 —— 3 天推 %d 次（刚出现 1 次 + 跨 3 次天 = 4）" % len(p),
          len(p) == 4,
          "实际：" + str([(int(t), w) for t, _b, w, _d in p]))
    first = p[0]
    check("A2: 第一次推就是价签刚出现（第 %d 秒，10:00 那种半路开机）" % int(first[0]),
          int(first[0]) == 0 and "刚出现" in first[2])
    # 跨天那几次：都在本地 0 点（± 一两个 3 秒轮次）
    cross = [(t, w) for t, _b, w, _d in p if "跨天" in w]
    offs = [int((t + 10 * 3600) % 86400) for t, _w in cross]
    check("A3: 3 次跨天推都落在本地 0 点（距 0 点 %s 秒）" % offs,
          len(cross) == 3 and all(o < 10 for o in offs))
    # 同一天里最多推一次（这是用户要的那句话："只在每天 0 点刷新一次"）
    per_day = {}
    for _t, _b, _w, d in p:
        per_day[d] = per_day.get(d, 0) + 1
    check("A4: 没有任何一天推超过一次（%s）"
          % ", ".join("%s:%d 次" % kv for kv in sorted(per_day.items())),
          max(per_day.values()) == 1)
    ts = [t for t, _b, _w, _d in p]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    check("A5: 相邻两次推的间隔（秒）= %s —— 除第一次外都是整天，绝不是 6 小时"
          % [int(g) for g in gaps],
          all(g >= 50390 for g in gaps) and all(abs(g - 86400) < 10 for g in gaps[1:]),
          "gap 明细：" + str([int(g) for g in gaps]))

    # 反面对照：这一条要是被改坏（due_wx 又真了），间隔会掉到 6 小时 ——
    # 这里显式验一下"天气没关时本来就该这样"，证明 A4 的判据是有意义的。
    ev_w = simulate(no_weather=False, days=1)
    p_w = pushes(ev_w)
    ts_w = [t for t, _b, _w, _d in p_w]
    gaps_w = [b - a for a, b in zip(ts_w, ts_w[1:])]
    check("B1: 没关天气时（对照）一天推 %d 次、间隔 %s —— 该真还是真，老行为没被误伤"
          % (len(p_w), [int(g) for g in gaps_w]),
          len(p_w) >= 4 and min(gaps_w) < 21610)
    check("B2: 对照组的**第一个**间隔正好是天气间隔（6 小时 = %d 秒）"
          % int(gaps_w[0]),
          abs(gaps_w[0] - 21600) < 15)

    bad = 0
    for label, ok, detail in CHECKS:
        print("  [%s] %s" % ("PASS" if ok else "**FAIL**", label))
        if not ok and detail:
            print("       · %s" % detail)
        bad += 0 if ok else 1
    print("全部通过 ✅" if bad == 0 else "有 %d 项失败 ❌" % bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
