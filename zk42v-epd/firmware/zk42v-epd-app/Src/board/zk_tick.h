/*
 * 把"32 位自由周期计数器"扩成**单调的毫秒时钟**。
 *
 * 为什么需要它（build 30 修的这个 bug）：
 *   GR5513 上我们用 DWT->CYCCNT 当毫秒基准，而 CYCCNT 是 32 位、按主频自由计数 ——
 *   16 MHz 时 **2^32 / 16e6 ≈ 268 秒就绕一圈**。
 *   老代码是 `return CYCCNT / (clk/1000);`，于是这个"毫秒"也是**每 268 秒从
 *   268435 掉回 0** 的锯齿波。
 *
 *   凡是把它当**绝对时间**用的地方都会在那一下崩掉。最典型的就是日历页：
 *       cur = s_ts(网页给的时间戳) + (tick_ms() - s_ts_ms) / 1000
 *   绕圈的一瞬间无符号减法变成 ≈ +4.29e6 秒（≈ 49.7 天），`cur/86400` 变了 ->
 *   固件判定"换天了" -> 重画 + 整屏全刷（一次 18 秒）。绕回去再刷一次。
 *   表现就是**什么都不干、屏每 4.5 分钟自己刷一次**。
 *
 * 这里改成"软件高位"：每次调用按**无符号差值**累加（无符号减法天然处理计数器回绕），
 * 只推进整毫秒、余数留在计数器里，于是返回的是真正单调的 64 位毫秒 ——
 * 连续跑 5.8 亿年才需要担心绕圈。
 *
 * 刻意写成 **header-only 的纯 C**：主机端能编同一份代码自测
 * （见 tools/test_tick.py，喂一段带回绕的 CYCCNT 轨迹进去对拍）。
 * 调用点都在主循环里（不是中断），所以这里的读改写没有并发问题。
 */
#ifndef __ZK_TICK_H__
#define __ZK_TICK_H__

#include <stdint.h>

typedef struct
{
    uint32_t ms;        /* 单调毫秒的低 32 位 */
    uint32_t ms_hi;     /* 高 32 位（低 32 位绕一圈才 +1） */
    uint32_t cyc;       /* 上次已经折算掉的那部分周期数 */
    uint8_t  started;   /* 第一次调用只记基准，不累加 */
} zk_tick_t;

/* 喂一个周期计数值（cyc）+ 每毫秒多少周期（per_ms），返回**单调的 64 位毫秒**。
 *
 *   cyc 必须是自由计数器：会自己回绕，两次调用之间走过的周期数必须 < 2^32
 *       （16 MHz 下就是 268 秒，主循环调用频率远高于它，没问题）；
 *   per_ms = 主频 / 1000（16 MHz -> 16000）。传 0 表示时基不可用，直接返回当前值。
 */
static inline uint64_t zk_tick_step(zk_tick_t *t, uint32_t cyc, uint32_t per_ms)
{
    if (0u == per_ms)
    {
        return ((uint64_t)t->ms_hi << 32) | (uint64_t)t->ms;
    }

    if (!t->started)
    {
        t->started = 1;
        t->cyc     = cyc;
        return ((uint64_t)t->ms_hi << 32) | (uint64_t)t->ms;
    }

    {
        uint32_t d     = cyc - t->cyc;      /* 无符号：计数器回绕也算对 */
        uint32_t whole = d / per_ms;        /* 这期间走过了多少个"整毫秒" */

        if (whole)
        {
            uint32_t prev = t->ms;

            t->ms += whole;
            if (t->ms < prev)               /* 低 32 位绕一圈 = 49.7 天 */
            {
                t->ms_hi++;
            }
            t->cyc += whole * per_ms;       /* 只推进整毫秒，不足 1ms 的余数留着 */
        }
    }

    return ((uint64_t)t->ms_hi << 32) | (uint64_t)t->ms;
}

#endif /* __ZK_TICK_H__ */
