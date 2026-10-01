#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把日历页**表头那 27 行**单独裁出来放大，看版式细节（表头只有 26px 高，
整页预览里那行字太小，看不出字重/间距对不对）。

    python3 tools/header_zoom.py 1 <unix秒> 输出.png [时区] [电池mV] [温度x10] [选项位] [天气码] [天气温度]

参数跟 tools/gui_preview.py 完全一样（也是拿它渲染的），只是最后裁表头 + 放大 4 倍。
"""

import os
import struct
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gui_preview as G       # noqa: E402

HDR_H = 27          # 表头 26 行 + 1 行分隔黑线
SCALE = 4


def write_png(path, rows):
    """自己写一份 —— gui_preview.write_png 把 400x300 写死在文件头里了（它只写整页）"""
    h = len(rows)
    w = len(rows[0]) // 3
    raw = b''.join(b'\x00' + r for r in rows)

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    hdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', hdr) +
                chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    mode, ts, out = int(argv[1]), int(argv[2]), argv[3]
    tz       = int(argv[4]) if len(argv) > 4 else 8
    bat_mv   = int(argv[5]) if len(argv) > 5 else 3970
    temp_c10 = int(argv[6]) if len(argv) > 6 else 264
    opt      = int(argv[7], 0) if len(argv) > 7 else 0
    wx_code  = int(argv[8]) if len(argv) > 8 else 0
    env_temp = int(argv[9]) if len(argv) > 9 else -128
    city     = argv[10] if len(argv) > 10 else ''
    memo_s   = argv[11] if len(argv) > 11 else ''
    memo_t   = argv[12] if len(argv) > 12 else ''

    with tempfile.TemporaryDirectory(prefix='zkhdr-') as td:
        exe = G.build(td)
        rows = G.to_rgb(G.render(exe, mode, ts + tz * 3600, opt, bat_mv, temp_c10,
                                 wx_code, env_temp, city, memo_s, memo_t))
    crop = [r[:G.W * 3] for r in rows[:HDR_H]]
    # 每个像素放大成 SCALE x SCALE
    big = []
    for r in crop:
        px = [r[i:i + 3] for i in range(0, len(r), 3)]
        line = b''.join(p * SCALE for p in px)
        for _ in range(SCALE):
            big.append(line)
    write_png(out, [bytes(b) for b in big])
    print('%s  %dx%d  表头放大 %d 倍' % (out, G.W * SCALE, HDR_H * SCALE, SCALE))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
