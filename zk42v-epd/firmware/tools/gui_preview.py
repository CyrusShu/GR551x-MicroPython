#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 zkgui.c 画出来的那一页在电脑上渲染成 PNG —— 刷机之前先看效果。

    python3 tools/gui_preview.py 1 1758972000 /tmp/cal.png     # 日历模式
    python3 tools/gui_preview.py 2 1758972000 /tmp/clock.png   # 时钟模式

做法：拿宿主机 cc 把 tools/gui_preview.c + Src/img/zkgui.c 编成一个小程序，
（zkgui.c 画农历/节气要调 lunar.c 和 jieqi.c，所以这几个源文件都要进编译命令行）
跑出来 30000 字节的三色缓冲（前后各带 32 字节哨兵），在这里
  · 先检查哨兵有没有被写坏（越界写是屏上根本看不出来的 bug）
  · 再按"黑白面 bit=1 白 / 红面 bit=1 红"映射成 RGB，写 PNG
"""

import os
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
IMG = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img')

W, H = 400, 300
ROW = W // 8
PLANE = ROW * H
BYTES = PLANE * 2
GUARD = 32

C_WHITE = (255, 255, 255)
C_BLACK = (0, 0, 0)
C_RED = (190, 0, 0)          # 墨水屏的红没这么鲜艳，预览里压暗一点更像实物


def build(tmpdir):
    exe = os.path.join(tmpdir, 'gui_preview')
    cmd = ['cc', '-std=gnu99', '-O1', '-Wall', '-I', IMG,
           os.path.join(HERE, 'gui_preview.c'),
           os.path.join(IMG, 'zkgui.c'),
           os.path.join(IMG, 'lunar.c'),
           os.path.join(IMG, 'jieqi.c'), '-o', exe]
    subprocess.run(cmd, check=True)
    return exe


def render(exe, mode, ts, opt=0, bat_mv=0, temp_c10=0):
    """opt：选项位，跟固件 zk_opt.h 的 ZK_OPT_xxx 对齐（这里只用到 0x04 = 不画农历）
       bat_mv / temp_c10：电池毫伏、温度×10（0 = 按"没读到"画，右上角就不显示）"""
    out = subprocess.run([exe, str(mode), str(ts), str(opt),
                          str(bat_mv), str(temp_c10)], check=True,
                         stdout=subprocess.PIPE).stdout
    assert len(out) == BYTES + 2 * GUARD, len(out)
    head, body, tail = out[:GUARD], out[GUARD:GUARD + BYTES], out[GUARD + BYTES:]
    for name, g in (('前', head), ('后', tail)):
        if g != b'\xA5' * GUARD:
            raise AssertionError('%s哨兵被写坏了：缓冲越界！%s' % (name, g.hex()))
    return body


def to_rgb(body):
    """三色缓冲 -> 每行的 RGB（行 0 = 屏最上面，跟 epd_write_image 的约定一致）"""
    rows = []
    for y in range(H):
        line = bytearray()
        for x in range(W):
            o = y * ROW + (x >> 3)
            bit = 0x80 >> (x & 7)
            white = (body[o] & bit) != 0
            red = (body[PLANE + o] & bit) != 0
            c = C_RED if red else (C_WHITE if white else C_BLACK)
            line += bytes(c)
        rows.append(bytes(line))
    return rows


def write_png(path, rows):
    raw = b''.join(b'\x00' + r for r in rows)

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    hdr = struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', hdr) +
                chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    mode, ts, out = int(argv[1]), int(argv[2]), argv[3]
    # 可选第 4 个参数：时区小时数（默认 +8）。固件里是拿客户端发来的时间戳
    # 加上时区偏移再画，所以预览也这么干，看到的才是"本地时间"。
    tz = int(argv[4]) if len(argv) > 4 else 8
    bat_mv   = int(argv[5]) if len(argv) > 5 else 3970    # 预览默认给个像样的电池
    temp_c10 = int(argv[6]) if len(argv) > 6 else 264     # 26.4℃
    ts += tz * 3600

    with tempfile.TemporaryDirectory(prefix='zkgui-') as td:
        exe = build(td)
        body = render(exe, mode, ts, 0, bat_mv, temp_c10)
    rows = to_rgb(body)
    write_png(out, rows)

    # 顺带报几个数，方便脚本里判据
    black = sum(1 for r in rows for i in range(0, len(r), 3)
                if r[i:i + 3] == bytes(C_BLACK))
    red = sum(1 for r in rows for i in range(0, len(r), 3)
              if r[i:i + 3] == bytes(C_RED))
    print('%s: %dx%d  黑 %d 像素  红 %d 像素' % (out, W, H, black, red))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
