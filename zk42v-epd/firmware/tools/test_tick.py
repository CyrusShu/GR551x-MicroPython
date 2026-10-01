#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测毫秒时基（Src/board/zk_tick.h）—— 盯的是 build 29 那个「屏自己每 4.5 分钟刷一次」。

病根：`tick_ms()` 以前是 `return DWT->CYCCNT / (clk/1000)`。CYCCNT 是 32 位自由
计数器，16 MHz 下 2^32/16e6 ≈ **268 秒绕一圈**，于是这个"毫秒"也跟着绕 ——
日历页那句 `cur = 网页时间戳 + (tick - 设时间时的 tick)/1000` 在绕圈的一瞬间
会得到 +49.7 天，固件以为"换天了"，就重画 + 整屏全刷（一次 18 秒）。

这个测试直接把**固件那份头文件**编进来跑（不是另写一遍 Python 版）：

  1  常态：每 1 毫秒喂一次 CYCCNT，喂 600 秒（跨 2 次回绕）—— 返回值必须
     **严格等于累计毫秒**，一次抖动都不许有
  2  同一段轨迹用**老公式**（cyc/per_ms）算一遍 —— 必须有回绕跳变（证明这个测试
     真的在盯这件事，不是自说自话）
  3  调用间隔不均匀（有时隔 1ms、有时隔 250ms、有时隔一整圈）也要对
  4  低 32 位毫秒绕圈时，高 32 位要进位（白盒：直接把状态摆到边界上喂）
  5  源码守卫：main.c 里那句直除不能再出现，必须是 zk_tick_step

    python3 tools/test_tick.py
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
INC = os.path.join(FW, 'zk42v-epd-app', 'Src', 'board')
MAIN_C = os.path.join(FW, 'zk42v-epd-app', 'Src', 'main.c')

FAIL = []


def check(label, ok, detail=''):
    print(('  PASS  ' if ok else '  FAIL  ') + label)
    if not ok:
        FAIL.append(label)
        if detail:
            print('        · %s' % detail)


SRC = r'''
#include <stdio.h>
#include <string.h>
#include "zk_tick.h"

#define PER_MS 16000u          /* 16MHz：每毫秒 16000 个周期 */

int main(void)
{
    zk_tick_t t;
    uint32_t  cyc, per_ms = PER_MS;
    uint32_t  i;
    uint64_t  ms;

    /* ---- 1/2：1 毫秒一步，喂 600 秒（会跨 2 次 CYCCNT 回绕）---- */
    memset(&t, 0, sizeof(t));
    cyc = 0xFFFFF000u;                      /* 起始相位随便挑，很快就绕 */
    (void)zk_tick_step(&t, cyc, per_ms);    /* 第一次调用只记基准（固件里开机就调过） */
    for (i = 0; i < 600000u; i++)
    {
        ms = zk_tick_step(&t, cyc, per_ms);
        if (i % 1000u == 0u || i + 1u == 600000u)
        {
            printf("A %u %llu %u\n", i, (unsigned long long)ms, cyc / per_ms);
        }
        cyc += per_ms;                      /* 每步 1ms */
    }

    /* ---- 3：间隔不均匀（1ms / 250ms / 一圈 268 秒）---- */
    memset(&t, 0, sizeof(t));
    cyc = 0x12345678u;
    (void)zk_tick_step(&t, cyc, per_ms);    /* 先建基准 */
    {
        const uint32_t steps[6] = { 1u, 250u, 1u, 268000u, 1u, 1000u };
        uint64_t total = 0;
        unsigned  k;

        for (k = 0; k < 6u; k++)
        {
            total += steps[k];
            cyc   += steps[k] * per_ms;
            ms     = zk_tick_step(&t, cyc, per_ms);
            printf("B %u %llu %llu\n", k, (unsigned long long)ms,
                   (unsigned long long)total);
        }
    }

    /* ---- 4：低 32 位毫秒绕圈 -> 高位进位（白盒）---- */
    memset(&t, 0, sizeof(t));
    t.started = 1;
    t.cyc     = 0u;
    t.ms      = 0xFFFFFFF0u;                /* 还差 16ms 就绕 */
    t.ms_hi   = 7u;
    ms = zk_tick_step(&t, 32u * per_ms, per_ms);
    printf("C %llu\n", (unsigned long long)ms);

    /* ---- 5：条件 "per_ms = 0"（时基不可用）时不许崩、也不许乱加 ---- */
    memset(&t, 0, sizeof(t));
    ms = zk_tick_step(&t, 12345u, 0u);
    ms = zk_tick_step(&t, 99999u, 0u);
    printf("D %llu\n", (unsigned long long)ms);

    /* ---- 6：build 59 的 AON 时基（低频递减计数器 + 每秒 tick 数）----
       频率取本机实测那一档 28000（不是 32768！必然除不尽，专门盯截断误差），
       起点挑在离绕圈很近的地方，让它跨过一次 32 位回绕。 */
    {
        zk_tick_aon_t a;
        const uint32_t hz = 28000u;
        uint32_t ct, i;

        memset(&a, 0, sizeof(a));
        ct = 0xFFFFFF00u;
        (void)zk_tick_aon_step(&a, ct, hz);         /* 第一次调用只记基准 */
        for (i = 0; i < 200000u; i++)
        {
            ct += 28u;                              /* 每步 1ms（28 tick） */
            ms = zk_tick_aon_step(&a, ct, hz);
            if (i % 20000u == 0u || i + 1u == 200000u)
            {
                printf("E %u %llu %llu\n", i, (unsigned long long)ms,
                       (unsigned long long)(((uint64_t)(i + 1u) * 28u) * 1000u / hz));
            }
        }

        /* 间隔不均匀（1ms / 250ms / 1ms / 40 秒），累计 tick 换算必须跟得上 */
        memset(&a, 0, sizeof(a));
        ct = 0x00000100u;
        (void)zk_tick_aon_step(&a, ct, hz);
        {
            const uint32_t step_ms[4] = { 1u, 250u, 1u, 40000u };
            uint64_t total_ms = 0;
            unsigned  k;

            for (k = 0; k < 4u; k++)
            {
                total_ms += step_ms[k];
                ct       += step_ms[k] * 28u;
                ms        = zk_tick_aon_step(&a, ct, hz);
                printf("F %u %llu %llu\n", k, (unsigned long long)ms,
                       (unsigned long long)((total_ms * 28u) * 1000u / hz));
            }
        }

        /* 白盒：累计 tick 大到"毫秒超过 2^32"时，高 32 位要进位 */
        memset(&a, 0, sizeof(a));
        a.started = 1;
        a.last    = 0u;
        a.tk_lo   = 0u;
        a.tk_hi   = 5u;                             /* 5 * 2^32 tick */
        ms = zk_tick_aon_step(&a, 0u, hz);
        printf("G %llu %llu\n", (unsigned long long)ms,
               (unsigned long long)((5ull << 32) * 1000u / hz));

        /* 计数器被复位/方向反了：一次 >= 2^31 的跳变不许计时，只记一笔 */
        memset(&a, 0, sizeof(a));
        ct = 100000u;
        (void)zk_tick_aon_step(&a, ct, hz);
        ct += 28u;
        ms = zk_tick_aon_step(&a, ct, hz);
        printf("H %llu %u ", (unsigned long long)ms, a.bad);
        ct = 500u;                                  /* 突然倒退一大截（被复位） */
        ms = zk_tick_aon_step(&a, ct, hz);
        printf("%llu %u\n", (unsigned long long)ms, a.bad);

        /* hz = 0（这条时基不可用）：原样返回、不崩 */
        memset(&a, 0, sizeof(a));
        ms = zk_tick_aon_step(&a, 1234u, 0u);
        ms = zk_tick_aon_step(&a, 5678u, 0u);
        printf("I %llu\n", (unsigned long long)ms);
    }

    return 0;
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='zktick-') as td:
        src = os.path.join(td, 't.c')
        open(src, 'w', encoding='utf-8').write(SRC)
        exe = os.path.join(td, 't')
        r = subprocess.run(['cc', '-std=gnu99', '-O1', '-Wall', '-Wextra',
                            '-I', INC, src, '-o', exe],
                           capture_output=True)
        if r.returncode != 0:
            print(r.stderr.decode('utf-8', 'replace'))
            return 1
        out = subprocess.run([exe], capture_output=True).stdout.decode('utf-8')

    A = []
    B = []
    E = []
    F = []
    G = []
    C = D = None
    H = None
    I = None
    for line in out.splitlines():
        f = line.split()
        if not f:
            continue
        if f[0] == 'A':
            A.append((int(f[1]), int(f[2]), int(f[3])))    # i, 新, 老
        elif f[0] == 'B':
            B.append((int(f[1]), int(f[2]), int(f[3])))    # k, 新, 期望累计
        elif f[0] == 'C':
            C = int(f[1])
        elif f[0] == 'D':
            D = int(f[1])
        elif f[0] == 'E':
            E.append((int(f[1]), int(f[2]), int(f[3])))    # i, 新, 期望
        elif f[0] == 'F':
            F.append((int(f[1]), int(f[2]), int(f[3])))    # k, 新, 期望
        elif f[0] == 'G':
            G = (int(f[1]), int(f[2]))
        elif f[0] == 'H':
            H = [int(x) for x in f[1:]]                    # ms1 bad1 ms2 bad2
        elif f[0] == 'I':
            I = int(f[1])

    # 1) 新公式：严格等于累计毫秒
    errs = [(i, ms, i) for (i, ms, _old) in A if ms != i]
    check('1: 600 秒轨迹上返回值严格等于累计毫秒（%d 个采样点）' % len(A),
          not errs, str(errs[:3]))

    # 2) 老公式：必须有回绕跳变
    old = [o for (_i, _ms, o) in A]
    drops = sum(1 for k in range(1, len(old)) if old[k] < old[k - 1])
    jump = max(old) - min(old)
    check('2: 对照 —— 老公式（cyc/per_ms）在这条轨迹上跳了 %d 次、量级 %d'
          % (drops, jump), drops >= 2,
          '老公式要是没有跳变，说明这个测试根本没盯住 build 29 那个 bug')

    # 3) 间隔不均匀
    bad = [(k, ms, want) for (k, ms, want) in B if ms != want]
    check('3: 间隔不均匀（含一整圈 268 秒）也对得上', not bad, str(bad))

    # 4) 进位
    check('4: 低 32 位绕圈时高位进位（得到 %s，期望 %d）'
          % (C, (8 << 32) + 16), C == (8 << 32) + 16)

    # 5) 时基不可用
    check('5: per_ms=0 时原样返回、不乱加', D == 0, '得到 %s' % D)

    # 6) build 59：AON 低频计数器那条路（1ms 一步、跨回绕、频率除不尽）
    errs = [(i, ms, want) for (i, ms, want) in E if ms != want]
    check('6: AON 时基：28000 tick/秒、1ms 一步走 200 秒（跨回绕）都对得上',
          len(E) >= 5 and not errs, str(errs[:3]))

    # 7) 间隔不均匀也要对得上
    bad = [(k, ms, want) for (k, ms, want) in F if ms != want]
    check('7: AON 时基：间隔不均匀（含 40 秒那一档）也对得上', len(F) == 4 and not bad,
          str(bad))

    # 8) 毫秒超过 2^32 时高位进位（白盒）
    check('8: AON 时基：累计 tick 很大时毫秒高 32 位也不丢（%s，期望 %s）'
          % (G[0] if G else None, G[1] if G else None),
          bool(G) and G[0] == G[1])

    # 9) 计数器被复位/倒退：不计时、只记一笔
    check('9: AON 时基：一次 >=2^31 的跳变不推进（%s）也不崩，bad 记一笔（%s）'
          % (H[:2] if H else None, H[2:] if H else None),
          H == [1, 0, 1, 1])

    # 10) hz = 0
    check('10: AON 时基 hz=0 时原样返回、不乱加', I == 0, '得到 %s' % I)

    # 11) 源码守卫
    src = open(MAIN_C, encoding='utf-8').read()
    check('11: main.c 里不再有 CYCCNT 直除那种写法',
          'DWT->CYCCNT / (clk / 1000u)' not in src)
    check('11: main.c 走 zk_tick_step（CYCCNT 那条路还在，当兜底）',
          'zk_tick_step(&s_tick' in src)
    check('11: main.c 的时基换成了 AON 定时器（AON->TIMER_VAL + zk_tick_aon_step）',
          'AON->TIMER_VAL' in src and 'zk_tick_aon_step' in src)
    check('11: 标定过 AON 频率（开机用 DWT 量 + 跟 sys_lpclk_get 对账）',
          'sys_lpclk_get' in src and 'zk_timebase_init' in src)
    check('11: main.c 里没有把 CPU 周期率写死成 16000',
          re.search(r'\b16000u\b', src) is None)
    tick_h = open(os.path.join(INC, 'zk_tick.h'), encoding='utf-8').read()
    check('11: zk_tick.h 里有 zk_tick_aon_step（主机端测的就是固件那份）',
          'zk_tick_aon_step' in tick_h)
    check('11: 日历页用的是 64 位时基',
          'zk_tick_ms64()' in open(os.path.join(FW, 'zk42v-epd-app', 'Src',
                                                'ble', 'zk_epd_svc.c'),
                                   encoding='utf-8').read())

    print()
    if FAIL:
        print('有 %d 项失败 ❌' % len(FAIL))
        return 1
    print('全部通过 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
