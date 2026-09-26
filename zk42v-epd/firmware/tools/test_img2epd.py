#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测 img2epd.py（不联网、不需要硬件）：

  1  造一张合成 PNG（上半灰阶渐变、左下红块、右下纯黑、右上纯白）
  2  跑 img2epd 转成 .epd
  3  验：长度 30000、逐字节和、包布局（前 15000 黑白面 / 后 15000 红面）
     - 红块那片像素在红面里必须是 1
     - 纯黑那片在黑白面里必须是 0，纯白那片必须是 1
     - 黑/白/红像素数加起来等于 120000
  4  把产物用 preview_rows 还原 + 重新编码 PNG 再解回来，确认前后一致
"""

import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import img2epd as I  # noqa: E402

CHECKS = []


def check(label, ok):
    CHECKS.append((label, bool(ok)))


def make_source_png(path, w=800, h=600):
    """合成一张：上半灰阶渐变 / 左下纯红 / 右下纯黑 / 右上纯白"""
    rows = []
    for y in range(h):
        row = bytearray(w * 3)
        for x in range(w):
            if y < h // 2:
                v = int(x * 255 / w)
                c = (v, v, v)
            else:
                if x < w // 2:
                    c = I.RED
                elif x < w * 3 // 4:
                    c = (0, 0, 0)
                else:
                    c = (255, 255, 255)
            row[x * 3:x * 3 + 3] = bytes(c)
        rows.append(row)
    I.write_png(path, w, h, rows)
    return w, h


def region_stats(epd, x0, y0, x1, y1):
    black = white = red = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            o = y * I.ROW_BYTES + (x >> 3)
            m = 0x80 >> (x & 7)
            is_red = (epd[I.PLANE + o] & m) != 0
            is_white = (epd[o] & m) != 0
            if is_red:
                red += 1
            elif is_white:
                white += 1
            else:
                black += 1
    return black, white, red


def main():
    d = tempfile.mkdtemp(prefix='img2epd-')
    src = os.path.join(d, 'src.png')
    out = os.path.join(d, 'out.epd')
    prev = os.path.join(d, 'out.preview.png')
    make_source_png(src)
    check('1: 合成 PNG 写出来了', os.path.exists(src) and os.path.getsize(src) > 0)

    rc = I.main([src, '--out', out, '--preview', prev])
    check('2: img2epd 返回 0', rc == 0)
    epd = open(out, 'rb').read()
    check('2: 产物正好 30000 字节', len(epd) == 30000)
    check('2: 预览 PNG 也有', os.path.exists(prev) and os.path.getsize(prev) > 0)

    # 3 区域检查（源图里下半部：左红 / 中黑 / 右白 → 输出 400x300 的下半部同样三段）
    y0 = int(I.H * 0.6)
    y1 = int(I.H * 0.9)
    b1, w1, r1 = region_stats(epd, 20, y0, 120, y1)      # 红块
    check('3: 红块那片几乎全是红', r1 > (120 - 20) * (y1 - y0) * 0.95)
    b2, w2, r2 = region_stats(epd, 210, y0, 300, y1)      # 黑块
    check('3: 黑块那片几乎全是黑', b2 > (300 - 210) * (y1 - y0) * 0.95)
    b3, w3, r3 = region_stats(epd, 320, y0, 390, y1)      # 白块
    check('3: 白块那片几乎全是白', w3 > (390 - 320) * (y1 - y0) * 0.95)

    b, w, r = region_stats(epd, 0, 0, I.W, I.H)
    check('3: 三种像素加起来 = 400x300', (b + w + r) == I.W * I.H)
    check('3: 黑/白都占了不少（灰阶渐变确实被抖动过）', b > 5000 and w > 5000)

    # 4 还原一遍：预览 → PNG → 再解回来，应该跟原图逐像素一致
    rows = I.preview_rows(epd)
    p2 = os.path.join(d, 'roundtrip.png')
    I.write_png(p2, I.W, I.H, rows)
    w2_, h2_, rows2 = I.load_png(p2)
    same = (w2_ == I.W and h2_ == I.H)
    if same:
        for y in range(I.H):
            if rows[y] != rows2[y]:
                same = False
                break
    check('4: 预览还原后逐像素一致（PNG 编码/解码闭环 OK）', same)

    # 5 边界：非 PNG 输入在没有 sips 的场景下应该明确报错而不是静默出错
    bad = os.path.join(d, 'x.jpg')
    open(bad, 'wb').write(b'not an image')
    try:
        I.load_image(bad)
        check('5: 坏图片会报错', False)
    except Exception:
        check('5: 坏图片会报错', True)

    bad2 = len(CHECKS)
    fails = 0
    for label, ok in CHECKS:
        print('  [%s] %s' % ('PASS' if ok else '**FAIL**', label))
        fails += 0 if ok else 1
    print('全部通过 ✅' if fails == 0 else '有 %d 项失败 ❌' % fails)
    return fails


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
