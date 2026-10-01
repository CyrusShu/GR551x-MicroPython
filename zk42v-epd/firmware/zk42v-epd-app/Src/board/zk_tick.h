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
    /* build 58：**观测用**（不改逻辑）—— 看"钟快好几倍"到底是"匀速快"还是"跳变累加"。
       判据（见 build 56 那段注释）：无符号下溢式的竞争会一次性灌进 d ≈ 2^32，
       折算成毫秒就是每次跳 ~268435 ms；而"匀速快"则 jumps 恒为 0。 */
    uint32_t jumps;         /* 两次调用之间 d > 1 秒 的次数 */
    uint32_t max_jump_ms;   /* 其中最大的一次折算成多少毫秒 */
} zk_tick_t;

/* ---------------------------------------------------------------- 临界区
 *
 * build 56：**修掉"钟跑快 2~3 倍"的 bug**。
 *
 * 症状（2026-09-30/10-01 实测）：价签的单调毫秒比真实时间快约 3 倍，
 *   于是日历每 ~8 小时就跨一天（用户看到"9-30 晚上就显示 10 月 1 号"）。
 *   已排除的假设：主频不是 16/48MHz 切换（`test_step` 两次都读 16000000），
 *   CYCCNT 频率也是对的（`status.sh` 自检 ~16MHz）—— 两个输入都对，输出却快 3 倍。
 *
 * 现行根因：**下面这个"读 CYCCNT → 更新 t->cyc"的读改写，被并发调用破坏了**。
 *   原注释写"调用点都在主循环里（不是中断）"，但实际上（BLE 协议栈回调 / pwr_mgmt
 *   调度那几条路）会在中断上下文里也调 zk_tick_ms()/zk_tick_ms64()，于是：
 *       A 读到 cyc=C1 →（被抢占）→ B 读到 C2(>C1)、把 t->cyc 更新成 C2
 *       → A 恢复后算 d = C1 - t->cyc = C1 - C2 ⇒ **无符号下溢**（≈4.29e9）
 *       → whole = d/16000 ≈ 268435 ⇒ 一次性跳 +4.5 分钟。
 *   表现就是"钟不是匀速快，而是突发式往前跳"，平均下来 2~3 倍。
 *
 * 修法：把这段读改写放进 PRIMASK 临界区（关中断 → 读改写 → 恢复原状态）。
 *   用行内汇编直接读写 PRIMASK，不依赖 CMSIS 头（本文件是 header-only、
 *   主机端自测 tools/test_tick.py 也要能编同一份代码 —— 那边 __arm__ 未定义，
 *   自动退化成空操作，逻辑不受影响）。
 */
#if defined(__arm__)
static inline uint32_t zk_tick_irq_save(void)
{
    uint32_t pm;
    __asm volatile ("mrs %0, primask" : "=r" (pm));
    __asm volatile ("cpsid i" ::: "memory");
    return pm;
}
static inline void zk_tick_irq_restore(uint32_t pm)
{
    if (!(pm & 1u))                 /* 原本开着中断才恢复 */
    {
        __asm volatile ("cpsie i" ::: "memory");
    }
}
#else
static inline uint32_t zk_tick_irq_save(void) { return 0u; }
static inline void zk_tick_irq_restore(uint32_t pm) { (void)pm; }
#endif

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
        uint32_t pm    = zk_tick_irq_save();   /* build 56：读改写必须原子，见上面的说明 */
        uint32_t d     = cyc - t->cyc;         /* 无符号：计数器回绕也算对 */
        uint32_t whole = d / per_ms;           /* 这期间走过了多少个"整毫秒" */

        /* build 58 观测量：这次调用之间隔了超过 1 秒？记下来（只在临界区内读 t，安全） */
        if (d > per_ms * 1000u)
        {
            t->jumps++;
            if (whole > t->max_jump_ms)
            {
                t->max_jump_ms = whole;
            }
        }

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
        zk_tick_irq_restore(pm);
    }

    return ((uint64_t)t->ms_hi << 32) | (uint64_t)t->ms;
}

/* ======================================================================
 * build 59：**低频自由计数器（AON 定时器）**的时基
 *
 * 为什么又加了一条路（上面那条 CYCCNT 的路不是好好的吗）：
 *   CYCCNT 数的是 **CPU 周期**，而 GR5513 的主频会变：空闲时 SystemCoreClock
 *   = 16 MHz，刷屏/连着 BLE 的时候明显更高。于是"毫秒"平均快 3~4.8 倍
 *   （2026-10-01 的实测证据：build 58 记下的那次跳变 = 1e9 个周期，
 *    16 MHz 下算成 62 秒，而真实只过了 20.8 秒 —— 正好是一次全刷的时长），
 *   日历因此每 ~8 小时跨一天（用户看到"9-30 晚上就显示 10 月 1 号"）。
 *
 *   AON 定时器（`AON->TIMER_VAL`，0xA000C594）跑在**低功耗时钟**上，
 *   与 CPU 主频无关 —— 实测 ~28 kHz、而且**递减**计数。拿它当毫秒基准，
 *   主频怎么变都不影响。（频率在开机时标定一次，见 main.c 的 zk_timebase_init()）
 *
 * 两个坑，这个实现都处理了：
 *   ① **递减**计数器：调用方（main.c）先用 `0 - raw` 归一成"递增"，这里只按
 *      递增累加 —— 所以本文件的算法对方向不敏感，标定/自测里两种方向都能跑；
 *   ② 频率不是 1000 的整数倍（28000）：**累加原始 tick、只在返回时除一次**，
 *      不会每次都截断（那会越走越慢）。64 位累加，`tick * 1000` 到 6.6e11 秒
 *      才溢出，够用几万年。
 *
 * 与上面 `zk_tick_step()` 的关系：**两条路互不干扰**，各自一个结构体、各自一份
 * 状态。开机选一条用（AON 优先），AON 读不到/不涨时 main.c 才回退到 CYCCNT。
 * ====================================================================== */

typedef struct
{
    uint32_t last;      /* 上次的原始计数（调用方已归一成"递增"方向） */
    uint32_t tk_lo;     /* 累计原始 tick 的低 32 位 */
    uint32_t tk_hi;     /* 高 32 位（低 32 位绕一圈才 +1） */
    uint32_t ms;        /* 最近算出来的单调毫秒（低 32 位） */
    uint32_t ms_hi;
    uint32_t hz;        /* 每秒多少 tick（开机标定得到） */
    uint32_t jumps;     /* 单次间隔 > 1 秒的次数（观测：长阻塞的"补记"） */
    uint32_t bad;       /* 单次间隔 >= 2^31 的次数（计数器被复位/方向反了；正常 0） */
    uint8_t  started;   /* 第一次调用只记基准，不累加 */
} zk_tick_aon_t;

/* 喂一个**已归一成递增**的低频计数器读数 + 每秒 tick 数，返回单调 64 位毫秒。
 *
 *   cyc 必须满足：两次调用之间走过的 tick 数 < 2^31（28 kHz 下是 21 小时，
 *       主循环的频率远高于它；真超过 2^31 会被当成异常、只记一笔不计时）；
 *   hz == 0 表示这条时基不可用：原样返回上次的值，不推进、不崩。
 */
static inline uint64_t zk_tick_aon_step(zk_tick_aon_t *t, uint32_t cyc, uint32_t hz)
{
    uint32_t pm;
    uint64_t ms;

    if (0u == hz)
    {
        return ((uint64_t)t->ms_hi << 32) | (uint64_t)t->ms;
    }

    pm = zk_tick_irq_save();            /* 读改写要原子（跟 build 56 同样的理由） */

    t->hz = hz;

    if (!t->started)
    {
        t->started = 1;
        t->last    = cyc;
    }
    else
    {
        uint32_t d = cyc - t->last;     /* 无符号：计数器回绕也算对 */

        if (d >= 0x80000000u)
        {
            /* 半个 32 位 —— 要么计数器被复位，要么方向搞反了。
               这种"一档"不计时（宁可丢一点时间，也不要一次跳 21 小时）。 */
            t->bad++;
        }
        else if (d)
        {
            uint32_t prev = t->tk_lo;

            t->tk_lo += d;
            if (t->tk_lo < prev)
            {
                t->tk_hi++;
            }
            if (d > hz)                 /* 隔了超过 1 秒才来喂（观测用） */
            {
                t->jumps++;
            }
        }
        t->last = cyc;
    }

    {
        uint64_t ticks = ((uint64_t)t->tk_hi << 32) | (uint64_t)t->tk_lo;

        ms = ticks * 1000u / (uint64_t)hz;   /* 只在返回时除一次，余数留在 tick 里 */
    }
    t->ms    = (uint32_t)ms;
    t->ms_hi = (uint32_t)(ms >> 32);

    zk_tick_irq_restore(pm);
    return ms;
}

#endif /* __ZK_TICK_H__ */
