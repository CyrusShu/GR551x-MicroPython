#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 9 个天气图标渲染成一张对比图（PNG），给 README / 人眼用。

    python3 tools/wx_sheet.py [放大倍数] [输出.png]           # 当前方案（读 weather_img.py）
    /tmp/zkvenv/bin/python3 tools/wx_sheet.py --sets 文件.png  # 四个候选方案各一行（要 Pillow）

数据不是另抄一份：直接读 tools/weather_img.py（gen_weather.py 的产物），
所以图上看到的**就是**固件里那几个图标。纯标准库（zlib 手写 PNG），
不需要 Pillow —— gui_preview.py 也是这么干的。
"""

import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import weather_img as W       # noqa: E402

ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9]


def write_png(path, rows):
    h = len(rows)
    w = len(rows[0])
    raw = b''.join(b'\x00' + bytes(r) for r in rows)

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    hdr = struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', hdr) +
                chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def sheet(icons, scale=4, pad=4):
    """{码: ['#'/'.' 行]} -> 一整张 PNG 的像素行"""
    h = len(next(iter(icons.values())))
    w = len(next(iter(icons.values()))[0])
    cw = w * scale
    wp = (cw + pad) * len(ORDER) + pad
    hp = h * scale + 2 * pad

    rows = [[255] * wp for _ in range(hp)]
    for i, code in enumerate(ORDER):
        ox = pad + i * (cw + pad)
        for y in range(h):
            for x in range(w):
                if icons[code][y][x] != '#':
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        rows[pad + y * scale + dy][ox + x * scale + dx] = 0
        if i != len(ORDER) - 1:                 # 中间那几根浅灰分隔线
            for y in range(hp):
                rows[y][ox + cw + pad // 2] = 200
    return rows


def render_all_profiles():
    """四个候选方案（fa / wi / ms / mix）各渲染一遍 —— 要 Pillow（跟 gen_weather.py 一样）"""
    import gen_weather as GEN

    out = []
    for name in ('fa', 'wi', 'ms', 'mix'):
        icons = {}
        for code in ORDER:
            setname, cp = GEN.PROFILES[name][code]
            icons[code] = GEN.render_icon(GEN.icon_ttf(setname), cp,
                                          axes=GEN.SETS[setname].get('axes'))
        out.append((name, icons))
        print('  渲染完 %s' % name)
    return out


def main(argv):
    if len(argv) > 2 and argv[1] == '--sets':
        scale, pad = 4, 6
        out = argv[2]
        profs = render_all_profiles()
        one = sheet(profs[0][1], scale, pad)
        cw = len(one[0])
        ch = len(one)
        rows = []
        for i, (name, icons) in enumerate(profs):
            rows += sheet(icons, scale, pad)
            if i != len(profs) - 1:                     # 行之间来一条浅灰细线
                rows += [[150] * cw]
        write_png(out, rows)
        print('%s  %dx%d  行顺序：%s'
              % (out, cw, len(rows), ' / '.join(n for n, _ in profs)))
        return 0

    scale = int(argv[1]) if len(argv) > 1 else 4
    out = argv[2] if len(argv) > 2 else os.path.join(
        os.path.dirname(HERE), 'docs', 'preview', 'preview-weather-icons.png')
    pad = 4
    cw = W.ICON_W * scale
    wp = (cw + pad) * len(ORDER) + pad
    hp = cw + 2 * pad

    rows = [[255] * wp for _ in range(hp)]
    for i, code in enumerate(ORDER):
        ox = pad + i * (cw + pad)
        oy = pad
        for y in range(W.ICON_H):
            for x in range(W.ICON_W):
                if W.ICONS[code][y][x] != '#':
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        rows[oy + y * scale + dy][ox + x * scale + dx] = 0
        if i != len(ORDER) - 1:                 # 中间那几根浅灰分隔线
            for y in range(hp):
                rows[y][ox + cw + pad // 2] = 200
    write_png(out, rows)
    print('%s  %dx%d  放大 %d 倍' % (out, wp, hp, scale))
    print('顺序：' + ' '.join('%d' % c for c in ORDER))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
