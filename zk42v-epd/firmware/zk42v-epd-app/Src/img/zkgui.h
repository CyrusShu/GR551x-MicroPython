/*
 * 日历 / 时钟页面的绘制（原厂那套 GUI 的简化版）
 *
 * 分工照原厂来：网页只发「时间戳 + 模式字节」，画是**固件**干的
 * （tsl0922 的 EPD-nRF5 里对应 GUI/DrawCalendar + DrawClock）。
 *
 * 这个文件刻意**不依赖 SDK**：只用 stdint/string + 一个 30000 字节的缓冲指针，
 * 于是可以用电脑上的 gcc 编出来直接出预览图（tools/gui_preview.py），
 * 刷机之前就能看到长什么样。
 */
#ifndef __ZK_GUI_H__
#define __ZK_GUI_H__

#include <stdint.h>

#define ZKGUI_W        400
#define ZKGUI_H        300
#define ZKGUI_PLANE    ((ZKGUI_W / 8) * ZKGUI_H)   /* 15000 */
#define ZKGUI_BYTES    (ZKGUI_PLANE * 2)           /* 30000 */

/* 显示模式：跟原厂/网页的 mode 字节一致 */
#define ZKGUI_MODE_PICTURE   0
#define ZKGUI_MODE_CALENDAR  1
#define ZKGUI_MODE_CLOCK     2

typedef struct
{
    uint32_t ts;      /* unix 秒（调用方已经把时区偏移加进去了） */
    uint8_t  mode;    /* ZKGUI_MODE_xxx */
    /* build 31 起：表头右上角要画电池/温度（照社区那版）
       bat_mv < 0 = 没读到（那就不画电池，宁可不画也别画假数据）
       temp_c10 == ZK_TEMP_NONE = 没读到 */
    int16_t  bat_mv;      /* 电池毫伏 */
    int8_t   bat_pct;     /* 0..100；-1 = 不知道（只画空电池框） */
    int16_t  temp_c10;    /* **片内**温度 ×10（手机没给天气温度时用它） */
    uint8_t  wx_code;     /* 天气码（0 = 不显示；1 晴 2 多云 …，见 weather.h） */
    int8_t   env_temp_c;  /* 手机下发的**天气温度**（℃）；-128 = 没收到过 */
} zkgui_info_t;

#define ZK_TEMP_NONE  ((int16_t)(-32768))   /* "温度没读到" */

/* 把这一页画进 30000 字节的三色缓冲（前 15000 黑白面、后 15000 红面） */
void zkgui_draw(uint8_t *buf, const zkgui_info_t *info);

/* 日历页那一行农历画不画（BLE 命令 0x70 的 ZK_OPT_NO_LUNAR 位）。
 * 默认画 —— 不开选项的时候行为跟以前一模一样。 */
void zkgui_set_lunar(int on);

/* 节气那两个字要不要加粗（BLE 命令 0x70 的 ZK_OPT_TERM_BOLD 位）。默认不加粗。 */
void zkgui_set_term_bold(int on);

/* 时间拆解（给外面判断"换天了 / 换分钟了"用，也方便自测） */
void zkgui_civil(uint32_t ts, int *year, int *mon, int *day,
                 int *wday, int *hour, int *min, int *sec);

#endif /* __ZK_GUI_H__ */
