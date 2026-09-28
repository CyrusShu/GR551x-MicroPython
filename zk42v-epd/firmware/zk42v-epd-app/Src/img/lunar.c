/*
 * 农历数据表 + 换算 —— **自动生成，别手改**。
 * 数据来源：原厂固件那份 GUI/Lunar.c（生成脚本 tools/gen_lunar.py）。
 * 作者保留原样：只去掉我们用不到的节气/生肖/干支/月建那几段。
 */
#include "lunar.h"

const char zk_lunar_month_cn[13][7] = {
    "----", "正月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "冬月", "腊月"
};

const char zk_lunar_leap_cn[2][4] = {
    " ", "闰"
};

const char zk_lunar_day_cn[31][7] = {
    "----", "初一", "初二", "初三", "初四", "初五", "初六", "初七",
    "初八", "初九", "初十", "十一", "十二", "十三", "十四", "十五",
    "十六", "十七", "十八", "十九", "二十", "廿一", "廿二", "廿三",
    "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
};

/* 第 i 年农历正月初一对应的公历日期：(年<<9)|(月<<5)|日。第 0 项是基年本身 */
static const uint32_t s_solar_1_1[55] = {
    0x000007CD, 0x000F9C3C, 0x000F9E50, 0x000FA045, 0x000FA238, 0x000FA44C, 0x000FA641, 0x000FA836, 0x000FAA49, 0x000FAC3D, 0x000FAE52, 0x000FB047,
    0x000FB23A, 0x000FB44E, 0x000FB643, 0x000FB837, 0x000FBA4A, 0x000FBC3F, 0x000FBE53, 0x000FC048, 0x000FC23C, 0x000FC450, 0x000FC645, 0x000FC839,
    0x000FCA4C, 0x000FCC41, 0x000FCE36, 0x000FD04A, 0x000FD23D, 0x000FD451, 0x000FD646, 0x000FD83A, 0x000FDA4D, 0x000FDC43, 0x000FDE37, 0x000FE04B,
    0x000FE23F, 0x000FE453, 0x000FE648, 0x000FE83C, 0x000FEA4F, 0x000FEC44, 0x000FEE38, 0x000FF04C, 0x000FF241, 0x000FF436, 0x000FF64A, 0x000FF83E,
    0x000FFA51, 0x000FFC46, 0x000FFE3A, 0x0010004E, 0x00100242, 0x00100437, 0x0010064B,
};

/* 第 i 年的月长位图 + 闰月号（bit13..16） */
static const uint32_t s_lunar_month_days[55] = {
    0x000007CD, 0x0000B26D, 0x0000125C, 0x0000192C, 0x00009A95, 0x00001A94, 0x00001B4A, 0x00004B55, 0x00000AD4, 0x0000F55B, 0x000004BA, 0x0000125A,
    0x0000B92B, 0x0000152A, 0x00001694, 0x000096AA, 0x000015AA, 0x00012AB5, 0x00000974, 0x000014B6, 0x0000CA57, 0x00000A56, 0x00001526, 0x00008E95,
    0x00000D54, 0x000015AA, 0x000049B5, 0x0000096C, 0x0000D4AE, 0x0000149C, 0x00001A4C, 0x0000BD26, 0x00001AA6, 0x00000B54, 0x00006D6A, 0x000012DA,
    0x0001695D, 0x0000095A, 0x0000149A, 0x0000DA4B, 0x00001A4A, 0x00001AA4, 0x0000BB54, 0x000016B4, 0x00000ADA, 0x0000495B, 0x00000936, 0x0000F497,
    0x00001496, 0x0000154A, 0x0000B6A5, 0x00000DA4, 0x000015B4, 0x00006AB6, 0x0000126E,
};

static uint32_t bit_int(uint32_t data, uint8_t len, uint8_t shift)
{
    return (data & (((1u << len) - 1u) << shift)) >> shift;
}

/* 公历 -> 儒略日式的天数（只用来算差值，跟原厂同一套公式） */
static uint16_t solar_to_int(uint16_t y, uint8_t m, uint8_t d)
{
    m = (uint8_t)((m + 9) % 12);
    y = (uint16_t)(y - m / 10);
    return (uint16_t)(365 * y + y / 4 - y / 100 + y / 400 + (m * 306 + 5) / 10 + (d - 1));
}

int zk_lunar_from_solar(uint16_t solar_year, uint8_t solar_month,
                        uint8_t solar_date, uint8_t *out_mon,
                        uint8_t *out_day, uint8_t *out_is_leap)
{
    const uint32_t base = s_solar_1_1[0];
    uint8_t  i, lunar_m, leap, dm;
    uint16_t year_index, offset, y;
    uint32_t solar_data, solar11, days;
    uint8_t  m, d;

    *out_mon = 0;
    *out_day = 0;
    *out_is_leap = 0;

    if (solar_month < 1 || solar_month > 12 || solar_date < 1 || solar_date > 31)
    {
        return -1;
    }
    if (solar_year < base + 3 || solar_year > base + (sizeof(s_solar_1_1) / 4) - 1)
    {
        return -1;                       /* 表外的年份：让调用方画 "--" */
    }

    year_index = (uint16_t)(solar_year - base);
    solar_data = ((uint32_t)solar_year << 9) | ((uint32_t)solar_month << 5) | solar_date;
    if (s_solar_1_1[year_index] > solar_data)
    {
        year_index--;
    }
    solar11 = s_solar_1_1[year_index];
    y = (uint16_t)bit_int(solar11, 12, 9);
    m = (uint8_t)bit_int(solar11, 4, 5);
    d = (uint8_t)bit_int(solar11, 5, 0);
    offset = (uint16_t)(solar_to_int(solar_year, solar_month, solar_date) -
                        solar_to_int(y, m, d));

    days = s_lunar_month_days[year_index];
    leap = (uint8_t)bit_int(days, 4, 13);

    lunar_m = 1;
    offset++;
    for (i = 0; i < 13; i++)
    {
        dm = (uint8_t)((bit_int(days, 1, 12 - i) == 1) ? 30 : 29);
        if (offset > dm)
        {
            lunar_m++;
            offset = (uint16_t)(offset - dm);
        }
        else
        {
            break;
        }
    }

    if (leap != 0 && lunar_m > leap)
    {
        if (lunar_m == leap + 1)
        {
            *out_is_leap = 1;
        }
        lunar_m--;
    }

    *out_mon = lunar_m;
    *out_day = (uint8_t)offset;
    return 0;
}
