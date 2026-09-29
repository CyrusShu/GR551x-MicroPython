/*
 * 画面选项：对**整帧三色缓冲**做变换（反色 / 旋转 180°）。
 *
 * 为什么单独拆一个模块：这两件事跟"谁来画这一帧"无关 —— 不管是网页推来的图，
 * 还是固件自己画的日历/时钟页，最后都落在同一块 30000 字节缓冲里
 * （前 15000 黑白面、后 15000 红面）。所以在**写屏之前**做一次变换，
 * 两条路就都照顾到了。
 *
 * 这里刻意不依赖任何板级/硬件头文件：主机端能编同一份 .c 来自测
 * （见 tools/test_opt.py），几何形状做成参数传进来。
 */
#ifndef __ZK_OPT_H__
#define __ZK_OPT_H__

#include <stdint.h>

/* 选项位（BLE 命令 0x70 SET_OPTIONS 的 payload 就是这个掩码） */
#define ZK_OPT_INVERT    0x01u   /* 反色：黑白面取反（黑<->白）。红面不动 —— 红是"有色"，
                                    反色不该把红色也抹掉，这一点跟网页里那个"反色"一致 */
#define ZK_OPT_ROT180    0x02u   /* 整屏旋转 180°（价签挂反了的时候用） */
#define ZK_OPT_NO_LUNAR  0x04u   /* 日历页不画农历那一行（这条不碰缓冲，是画页面时查的） */
#define ZK_OPT_TERM_BOLD 0x08u   /* 节气那两个字"加粗"（同样的字错开 1px 再画一遍） */

#define ZK_OPT_ALL       0x0Fu

/* 就地变换一帧。
 *
 *   buf       指向帧头；buf[0 .. plane-1] 是黑白面，buf[plane .. 2*plane-1] 是红面
 *   row_bytes 每行多少字节（400/8 = 50）
 *   rows      多少行（300）
 *   opts      ZK_OPT_xxx 的掩码；**0 的时候一个字节都不动**
 *             （只有 ZK_OPT_INVERT / ZK_OPT_ROT180 会改缓冲，
 *               NO_LUNAR / TERM_BOLD 是画页面时查的开关）
 *
 * 两个变换都是"自逆"的：连做两次等于还原。所以固件里可以
 * 「写屏前变换一次、写完再变换一次」，缓冲内容跟没动过一样 ——
 * SWD 信箱那条路读到的还是上位机写进来的原图。
 */
void zk_opt_transform(uint8_t *buf, uint32_t row_bytes, uint32_t rows, uint8_t opts);

#endif /* __ZK_OPT_H__ */
