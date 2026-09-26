/*
 * 第一帧测试图 —— 一次刷下去就能同时回答三个问题：
 *   ① 屏到底有没有被正确驱动（有没有图）
 *   ② 极性：黑/白/红各对应 RAM 里的什么值
 *   ③ 方向：我们 buffer 的第 0 行，是屏的上边还是下边；第 0 列是左还是右
 */
#ifndef __TESTIMG_H__
#define __TESTIMG_H__

#include <stdint.h>

/* 把 30000 字节的测试图画进 buf：[0..14999] 黑白面，[15000..29999] 红面 */
void zk_testimg_build(uint8_t *buf);

#endif /* __TESTIMG_H__ */
