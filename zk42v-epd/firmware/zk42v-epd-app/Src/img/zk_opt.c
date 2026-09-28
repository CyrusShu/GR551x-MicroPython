/*
 * 画面选项的实现，见 zk_opt.h。
 *
 * 数据布局（跟 epd_write_image 的约定一致）：
 *   每个面 rows 行、每行 row_bytes 字节；每行内 MSB first（bit7 = 最左边那个像素）。
 *   所以"水平翻转"= 每个字节内部把 8 个 bit 反过来，"垂直翻转"= 把行序倒过来。
 *   两个一起 = 旋转 180°。
 */
#include "zk_opt.h"

static uint8_t rev8(uint8_t b)
{
    b = (uint8_t)(((b & 0xF0u) >> 4) | ((b & 0x0Fu) << 4));
    b = (uint8_t)(((b & 0xCCu) >> 2) | ((b & 0x33u) << 2));
    b = (uint8_t)(((b & 0xAAu) >> 1) | ((b & 0x55u) << 1));
    return b;
}

/* 水平翻转一行：**字节顺序倒过来 + 每个字节的比特倒过来**。
   两件事都得做 —— 只翻比特的话，像素 x 会落到 8*(x/8)+(7-x%8)，
   而不是 W-1-x（400/8=50 是整数，字节一换位置才对得上）。 */
static void flip_row_h(uint8_t *row, uint32_t row_bytes)
{
    uint32_t i, j;

    for (i = 0, j = row_bytes - 1u; i < j; i++, j--)
    {
        uint8_t a = rev8(row[i]);
        uint8_t b = rev8(row[j]);

        row[i] = b;
        row[j] = a;
    }
    if (i == j)
    {
        row[i] = rev8(row[i]);
    }
}

/* 把一个面的行序倒过来（垂直翻转） */
static void flip_rows(uint8_t *plane, uint32_t row_bytes, uint32_t rows)
{
    uint32_t r0, r1, i;

    for (r0 = 0, r1 = rows - 1u; r0 < r1; r0++, r1--)
    {
        uint8_t *a = plane + (uint32_t)r0 * row_bytes;
        uint8_t *b = plane + (uint32_t)r1 * row_bytes;

        for (i = 0; i < row_bytes; i++)
        {
            uint8_t t = a[i];
            a[i] = b[i];
            b[i] = t;
        }
    }
}

void zk_opt_transform(uint8_t *buf, uint32_t row_bytes, uint32_t rows, uint8_t opts)
{
    const uint32_t plane = row_bytes * rows;
    uint32_t       p;

    if ((0u == row_bytes) || (0u == rows))
    {
        return;
    }
    if (0u == (opts & (ZK_OPT_INVERT | ZK_OPT_ROT180)))
    {
        return;                       /* 什么都没开：一个字节都不动 */
    }

    if (opts & ZK_OPT_ROT180)
    {
        for (p = 0; p < 2u; p++)
        {
            uint8_t *pl = buf + p * plane;
            uint32_t r;

            flip_rows(pl, row_bytes, rows);
            for (r = 0; r < rows; r++)
            {
                flip_row_h(pl + (uint32_t)r * row_bytes, row_bytes);
            }
        }
    }

    if (opts & ZK_OPT_INVERT)
    {
        uint32_t i;

        /* 只翻黑白面：bit=1 是白，取反就是黑<->白。红面保持不动。 */
        for (i = 0; i < plane; i++)
        {
            buf[i] = (uint8_t)~buf[i];
        }
    }
}
