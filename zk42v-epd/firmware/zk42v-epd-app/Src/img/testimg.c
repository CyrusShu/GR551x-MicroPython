/*
 * 测试图：一屏四条高度不同的横带 + 左上角一个「定位小条」。
 *   buffer 第 0 行 = 我们先当作「屏的上边」，对不对由这一帧的观察结果定。
 *
 *   行区间         黑白面 BW   红面 RED     四带高度故意不一样（100/80/70/42），
 *   ---------------------------------------------------------------
 *   0   .. 7        左半 0xFF   0x00        这样即使上下颠倒了也能一眼认出来
 *                   右半 0x00   0x00
 *   8   .. 107      0x00        0x00   ← 100 行
 *   108 .. 187      0xFF        0x00   ←  80 行
 *   188 .. 257      0xFF        0xFF   ←  70 行
 *   258 .. 299      0x00        0xFF   ←  42 行
 *
 *   每行 50 字节，每字节 8 个像素，bit7 = 该字节最左边的那个像素（MSB first）。
 */
#include "testimg.h"
#include "zk42v_board.h"

#include <string.h>

static void put_px(uint8_t *plane, int x, int y, int v)
{
    uint32_t idx = (uint32_t)y * ZK42V_EPD_ROW_BYTES + (uint32_t)(x >> 3);
    uint8_t  m   = (uint8_t)(0x80u >> (x & 7));

    if (v)
    {
        plane[idx] |= m;
    }
    else
    {
        plane[idx] &= (uint8_t)~m;
    }
}

static void fill_rect(uint8_t *bw, uint8_t *red,
                      int x, int y, int w, int h, int v_bw, int v_red)
{
    int i, j;

    for (j = y; j < y + h; j++)
    {
        for (i = x; i < x + w; i++)
        {
            put_px(bw,  i, j, v_bw);
            put_px(red, i, j, v_red);
        }
    }
}

void zk_testimg_build(uint8_t *buf)
{
    uint8_t *bw  = buf;
    uint8_t *red = buf + ZK42V_EPD_PLANE_BYTES;

    /* 底色：两片全 0（BW=0 / RED=0） */
    memset(buf, 0x00, ZK42V_EPD_IMG_BYTES);

    /* 顶部定位条：只有左半边（x 0..199），高 8 行，BW=1、RED=0 */
    fill_rect(bw, red, 0, 0, 200, 8, 1, 0);

    /* 四条横带 */
    fill_rect(bw, red, 0, 108, ZK42V_EPD_WIDTH,  80, 1, 0);   /* BW=1 RED=0 */
    fill_rect(bw, red, 0, 188, ZK42V_EPD_WIDTH,  70, 1, 1);   /* BW=1 RED=1 */
    fill_rect(bw, red, 0, 258, ZK42V_EPD_WIDTH,  42, 0, 1);   /* BW=0 RED=1 */

    /* 行 8..107 与行 0..7 右半、整幅底色一样，都是 BW=0 RED=0，不用额外画 */
}

void zk_testimg_orient(uint8_t *buf)
{
    uint8_t *bw  = buf;
    uint8_t *red = buf + ZK42V_EPD_PLANE_BYTES;

    /* 底色：白（BW=1，红面 0） */
    memset(bw,  0xFF, ZK42V_EPD_PLANE_BYTES);
    memset(red, 0x00, ZK42V_EPD_PLANE_BYTES);

    /* 上边一条黑横条：整宽 10 行，BW=0 → 黑 */
    fill_rect(bw, red, 0, 0, ZK42V_EPD_WIDTH, 10, 0, 0);

    /* 左边一条红竖条：整高 10 列，BW=1 且 RED=1 → 红（红盖过黑白面） */
    fill_rect(bw, red, 0, 0, 10, ZK42V_EPD_HEIGHT, 1, 1);

    /* 正中间一个 60x60 黑方块：BW=0 → 黑 */
    fill_rect(bw, red, 170, 120, 60, 60, 0, 0);
}
