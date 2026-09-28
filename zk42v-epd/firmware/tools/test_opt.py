#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测「画面选项」变换（Src/img/zk_opt.c）：反色 / 旋转 180°。

为什么要自测：这两个变换是**在写屏之前悄悄改整块缓冲**的，屏上出问题特别难
     判断（"怎么倒过来了""怎么红变黑了"）。而它们的正确性完全是数学上的，
     在电脑上就能钉死：

  1  opts = 0 时**一个字节都不许动**（默认路径必须跟以前逐字节一致）
  2  只开 NO_LUNAR 时也不许动缓冲（那条是画页面时查的，不碰缓冲）
  3  反色：黑白面逐字节取反，**红面原样不动**
  4  旋转 180°：原图 (x,y) 的像素要出现在 (W-1-x, H-1-y)
  5  两个变换都是"自逆"的：连做两次 == 还原（固件靠这条在写完屏后把缓冲还原）
  6  组合（反色+旋转）也要自逆，而且跟分开做的结果一致

    python3 tools/test_opt.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
IMG = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img')

W, H = 400, 300
ROW = W // 8
PLANE = ROW * H
BYTES = PLANE * 2
GUARD = 32

OPT_INVERT = 0x01
OPT_ROT180 = 0x02
OPT_NO_LUNAR = 0x04

CHECKS = []


def check(label, ok, detail=''):
    CHECKS.append((label, bool(ok), detail))


SRC = r'''
/* 把 options 逻辑搬到 Python 里对拍太容易各写各的错，所以直接调固件那份 .c。
   stdin 进来：opts(1B) + 1B 哨兵 + 30000 字节缓冲；
   stdout 出去：同样一份，变换之后的。 */
#include <stdio.h>
#include <stdlib.h>

#include "zk_opt.h"

#define ROW_BYTES 50u
#define ROWS      300u
#define BYTES     (ROW_BYTES * ROWS * 2u)

static unsigned char buf[BYTES + 64];

int main(int argc, char **argv)
{
    size_t n;
    unsigned opts;

    if (argc < 2) return 2;
    opts = (unsigned)strtoul(argv[1], NULL, 0);

    n = fread(buf, 1, sizeof(buf), stdin);
    if (n != sizeof(buf)) return 3;          /* 前面 64 字节是哨兵 */

    zk_opt_transform(buf + 32, ROW_BYTES, ROWS, (uint8_t)opts);

    fwrite(buf, 1, sizeof(buf), stdout);
    return 0;
}
'''


def build(tmpdir):
    src = os.path.join(tmpdir, 't.c')
    open(src, 'w', encoding='utf-8').write(SRC)
    exe = os.path.join(tmpdir, 't')
    subprocess.run(['cc', '-std=gnu99', '-O1', '-Wall', '-I', IMG,
                    src, os.path.join(IMG, 'zk_opt.c'), '-o', exe], check=True)
    return exe


def run(exe, opts, body):
    data = bytes([0xA5]) * GUARD + body + bytes([0xA5]) * GUARD
    out = subprocess.run([exe, str(opts)], input=data, check=True,
                         stdout=subprocess.PIPE).stdout
    assert len(out) == len(data), (len(out), len(data))
    for name, g in (('前', out[:32]), ('后', out[-32:])):
        if g != b'\xA5' * 32:
            raise AssertionError('%s哨兵被写坏了：%s' % (name, g.hex()))
    return out[32:-32]


def px(buf, x, y, red=False):
    o = y * ROW + (x >> 3)
    if red:
        o += PLANE
    return 1 if (buf[o] & (0x80 >> (x & 7))) else 0


def mkimg():
    """造一张有辨识度的图：每个像素的颜色由坐标决定"""
    b = bytearray(BYTES)
    for y in range(H):
        for x in range(W):
            o = y * ROW + (x >> 3)
            m = 0x80 >> (x & 7)
            if (x + y) % 3 == 0:
                b[o] |= m                     # 白
            if (x // 4 + y // 4) % 5 == 0:
                b[PLANE + o] |= m             # 红
    return bytes(b)


def main():
    with tempfile.TemporaryDirectory(prefix='zkopt-') as td:
        exe = build(td)
        img = mkimg()

        # 1 / 2：默认路径不许动缓冲
        check('1: opts=0 时缓冲逐字节不变', run(exe, 0, img) == img)
        check('2: 只开"不画农历"时缓冲也不变',
              run(exe, OPT_NO_LUNAR, img) == img)

        # 3：反色
        inv = run(exe, OPT_INVERT, img)
        bw_ok = all(inv[i] == (~img[i] & 0xFF) for i in range(PLANE))
        red_ok = inv[PLANE:] == img[PLANE:]
        check('3: 反色把黑白面逐字节取反', bw_ok)
        check('3: 反色不碰红面', red_ok)

        # 4：旋转 180°
        rot = run(exe, OPT_ROT180, img)
        n = 0
        bad = []
        for y in range(0, H, 7):
            for x in range(0, W, 5):
                for red in (False, True):
                    if px(rot, W - 1 - x, H - 1 - y, red) != px(img, x, y, red):
                        bad.append((x, y, red))
                    n += 1
        check('4: 旋转 180° —— (x,y) 的像素跑到 (W-1-x, H-1-y)（抽查 %d 个）' % n,
              not bad, str(bad[:4]))
        # 顺手确认旋转"真的不等于不动"（免得因为图本身对称而假通过）
        check('4: 对照 —— 旋转后跟原图确实不一样', rot != img)

        # 5 / 6：自逆
        for label, opts in (('反色', OPT_INVERT), ('旋转', OPT_ROT180),
                            ('反色+旋转', OPT_INVERT | OPT_ROT180)):
            once = run(exe, opts, img)
            twice = run(exe, opts, once)
            check('5: %s 连做两次 == 还原' % label, twice == img)

        both = run(exe, OPT_INVERT | OPT_ROT180, img)
        seq = run(exe, OPT_INVERT, run(exe, OPT_ROT180, img))
        check('6: 一起做 == 分开做（两个变换互相独立）', both == seq)

    bad = 0
    for label, ok, detail in CHECKS:
        print('  [%s] %s' % ('PASS' if ok else '**FAIL**', label))
        if not ok and detail:
            print('       · %s' % detail)
        bad += 0 if ok else 1
    print('全部通过 ✅' if bad == 0 else '有 %d 项失败 ❌' % bad)
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
