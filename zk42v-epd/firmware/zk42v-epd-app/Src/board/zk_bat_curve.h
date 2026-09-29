/*
 * 电量曲线 —— **唯一真相**就在这个头文件里，三个地方都用它：
 *
 *   固件          Src/board/zk_bat.c        （zk_bat_pct() -> 表头那个电池图标）
 *   主机预览      tools/gui_preview.c       （离线渲染表头，看到的就是屏上那几格）
 *   对照图脚本    tools/bat_sheet.py        （把这张表抠出来画 2.3~3.2V 的对照图）
 *
 * 为什么按 CR2450：这块价签的供电是 **2× CR2450 电池夹**（见
 * outputs/ZK42V-GR5513BEND-硬件判定与路线.md）。CR2450 是锂-二氧化锰，
 * **3.0V 标称**：新电池 3.0~3.3V（空载），带轻载 2.9~3.0V，2.0V 是厂家标定的截止点。
 * 早先照锂电写（3.0V=0% / 4.2V=100%）是错的 —— 那样满电的 CR2450 会显示 0 格。
 *
 * ⚠ 这条化学曲线**非常平**：八成容量都挤在 2.9~3.0V 那段，过了 2.6V 就"跳水"，
 *   所以只能"看个大概"；而且 CR2450 内阻十几到几十欧，刷一屏那种脉冲会让端电压
 *   瞬间跌 0.1~0.4V —— 同一颗电池"刷屏那一刻"和"闲着"读出来可能差一格。
 */
#ifndef __ZK_BAT_CURVE_H__
#define __ZK_BAT_CURVE_H__

/* "带载的端电压"(mV) -> 剩余电量(%)，从高到低；中间线性插值 */
static const unsigned short zk_bat_curve_mv_tab[] = {
    3000, 2950, 2900, 2850, 2800, 2750, 2700, 2650, 2600, 2550, 2500
};
static const unsigned char zk_bat_curve_pct_tab[] = {
    100, 85, 70, 58, 45, 35, 25, 16, 8, 3, 0
};
#define ZK_BAT_CURVE_N  (sizeof(zk_bat_curve_mv_tab) / sizeof(zk_bat_curve_mv_tab[0]))

/* 电压 -> 百分比（>=3.00V 算满，<2.50V 算空）。固件和主机预览共用这一个函数。 */
static inline int zk_bat_curve_pct(int mv)
{
    unsigned i;

    if (mv >= (int)zk_bat_curve_mv_tab[0])
    {
        return 100;
    }
    for (i = 1u; i < ZK_BAT_CURVE_N; i++)
    {
        if (mv >= (int)zk_bat_curve_mv_tab[i])
        {
            int hi_mv = (int)zk_bat_curve_mv_tab[i - 1u];
            int lo_mv = (int)zk_bat_curve_mv_tab[i];
            int hi_pc = (int)zk_bat_curve_pct_tab[i - 1u];
            int lo_pc = (int)zk_bat_curve_pct_tab[i];

            return lo_pc + (mv - lo_mv) * (hi_pc - lo_pc) / (hi_mv - lo_mv);
        }
    }
    return 0;
}

#endif /* __ZK_BAT_CURVE_H__ */
