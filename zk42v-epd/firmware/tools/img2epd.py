#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把任意图片转成 ZK42V 那块 4.2 寸三色墨水屏要的 30000 字节。

产物格式（B1 实测确认过的极性）：
    前 15000 字节 = 黑白面：bit=1 白、bit=0 黑
    后 15000 字节 = 红面  ：bit=1 红
    每行 50 字节（400 像素），行优先，**每字节 bit7 是最左边那个像素**（MSB first）
    红面 bit=1 的像素显示成红色（红盖过黑白面）

用法：
    python3 img2epd.py 图片.png --out /tmp/a.epd --preview /tmp/a.preview.png
    python3 img2epd.py 照片.jpg --mode bw        # 只出黑白（照片更清楚）
    python3 img2epd.py 图.png --mode bwr --no-dither

图片格式：
    PNG 内置解码（纯 Python，不依赖 PIL）；
    其它格式（jpg/heic/tiff…）会先调 macOS 的 sips 转成 PNG —— 那一步只在你自己
    终端里跑有效（在我这边的沙箱里 sips 出的是全黑图）。
"""

import argparse
import os
import struct
import subprocess
import sys
import zlib

W, H = 400, 300
ROW_BYTES = W // 8              # 50
PLANE = ROW_BYTES * H           # 15000
IMG = PLANE * 2                 # 30000

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED   = (190, 30, 40)           # 面板上那个红的大致色


# ---------------------------------------------------------------- PNG 解码
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def load_png(path):
    """返回 (w, h, rows)，rows 是每行一个 bytearray，每像素 3 字节 RGB"""
    d = open(path, 'rb').read()
    if d[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('不是 PNG')

    pos = 8
    idat = b''
    plte = None
    w = h = bd = ct = None
    while pos < len(d):
        ln = struct.unpack_from('>I', d, pos)[0]
        typ = d[pos + 4:pos + 8]
        data = d[pos + 8:pos + 8 + ln]
        if typ == b'IHDR':
            w, h, bd, ct, comp, filt, inter = struct.unpack('>IIBBBBB', data)
            if inter:
                raise ValueError('不支持隔行扫描的 PNG')
        elif typ == b'PLTE':
            plte = data
        elif typ == b'IDAT':
            idat += data
        elif typ == b'IEND':
            break
        pos += 12 + ln

    if bd not in (1, 2, 4, 8, 16):
        raise ValueError('不支持的位深 %d' % bd)
    if ct not in (0, 2, 3, 4, 6):
        raise ValueError('不支持的颜色类型 %d' % ct)

    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    bits_pp = nch * bd
    stride = (w * bits_pp + 7) // 8
    raw = zlib.decompress(idat)

    rows = []
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        if f == 1:
            for i in range(nch, stride):
                line[i] = (line[i] + line[i - nch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                b = prev[i]
                c = prev[i - nch] if i >= nch else 0
                line[i] = (line[i] + _paeth(a, b, c)) & 255
        rows.append(line)
        prev = line

    out = []
    for line in rows:
        px = bytearray(w * 3)
        if ct == 3:
            # 调色板
            idxs = []
            if bd == 8:
                idxs = list(line[:w])
            else:
                per = 8 // bd
                mask = (1 << bd) - 1
                for x in range(w):
                    byte = line[x // per]
                    shift = 8 - bd * (x % per + 1)
                    idxs.append((byte >> shift) & mask)
            for x in range(w):
                i = idxs[x] * 3
                px[x * 3:x * 3 + 3] = plte[i:i + 3] if plte else b'\x00\x00\x00'
        elif bd == 16:
            step = nch * 2
            for x in range(w):
                o = x * step
                if ct in (0, 4):
                    v = line[o]
                    px[x * 3:x * 3 + 3] = bytes((v, v, v))
                else:
                    px[x * 3:x * 3 + 3] = bytes((line[o], line[o + 2], line[o + 4]))
        else:
            for x in range(w):
                o = x * nch
                if ct in (0, 4):
                    v = line[o]
                    px[x * 3:x * 3 + 3] = bytes((v, v, v))
                else:
                    px[x * 3:x * 3 + 3] = bytes((line[o], line[o + 1], line[o + 2]))
        out.append(px)
    return w, h, out


def load_image(path):
    """PNG 直接读；别的格式先让 sips 转成 PNG 再读"""
    if path.lower().endswith('.png'):
        return load_png(path)
    tmp = os.path.join(os.path.dirname(os.path.abspath(path)) or '.', '_img2epd_tmp.png')
    print('  非 PNG，先用 sips 转一下：%s -> %s' % (os.path.basename(path), os.path.basename(tmp)))
    r = subprocess.run(['sips', '-s', 'format', 'png', path, '--out', tmp],
                       capture_output=True)
    if r.returncode != 0 or not os.path.exists(tmp):
        raise RuntimeError('sips 转换失败：%s' % (r.stderr.decode('utf-8', 'replace')[:200]))
    got = load_png(tmp)
    return got


# ---------------------------------------------------------------- 缩放
def resize(w, h, rows, nw=W, nh=H):
    """面积平均缩小 / 最近邻放大。返回 nw*nh 的 bytearray 列表"""
    out = []
    for y in range(nh):
        sy0 = y * h / nh
        sy1 = (y + 1) * h / nh
        r0 = int(sy0); r1 = max(r0 + 1, int(sy1 + 0.999))
        row = bytearray(nw * 3)
        for x in range(nw):
            sx0 = x * w / nw
            sx1 = (x + 1) * w / nw
            c0 = int(sx0); c1 = max(c0 + 1, int(sx1 + 0.999))
            tr = tg = tb = 0
            n = 0
            for yy in range(r0, min(r1, h)):
                src = rows[yy]
                for xx in range(c0, min(c1, w)):
                    o = xx * 3
                    tr += src[o]; tg += src[o + 1]; tb += src[o + 2]
                    n += 1
            if n == 0:
                n = 1
            row[x * 3] = tr // n
            row[x * 3 + 1] = tg // n
            row[x * 3 + 2] = tb // n
        out.append(row)
    return out


# ---------------------------------------------------------------- 转三色
def to_planes(rows, mode='bwr', dither=True):
    """返回 30000 字节：前 15000 黑白面，后 15000 红面"""
    pal = [BLACK, WHITE, RED] if mode == 'bwr' else [BLACK, WHITE]
    # 误差缓冲（浮点，RGB）
    err = [[0.0] * (W * 3) for _ in range(2)]
    bw = bytearray(PLANE)
    rd = bytearray(PLANE)

    for y in range(H):
        cur = err[y % 2]
        nxt = err[(y + 1) % 2]
        for i in range(len(nxt)):
            nxt[i] = 0.0
        src = rows[y]
        for x in range(W):
            o = x * 3
            r = src[o] + cur[o]
            g = src[o + 1] + cur[o + 1]
            b = src[o + 2] + cur[o + 2]
            if mode != 'bwr':
                v = 0.299 * r + 0.587 * g + 0.114 * b
                r = g = b = v
            # 找最近的颜色
            best = pal[0]; bd2 = None
            for c in pal:
                d2 = (r - c[0]) ** 2 + (g - c[1]) ** 2 + (b - c[2]) ** 2
                if bd2 is None or d2 < bd2:
                    bd2 = d2; best = c
            if best == RED:
                bit_r = 1; bit_bw = 1        # 红：红面 1，黑白面按白（红盖过黑）
            elif best == WHITE:
                bit_r = 0; bit_bw = 1
            else:
                bit_r = 0; bit_bw = 0
            if bit_bw:
                bw[y * ROW_BYTES + (x >> 3)] |= (0x80 >> (x & 7))
            if bit_r:
                rd[y * ROW_BYTES + (x >> 3)] |= (0x80 >> (x & 7))
            if not dither:
                continue
            er = r - best[0]; eg = g - best[1]; eb = b - best[2]
            def add(xx, wgt):
                if 0 <= xx < W:
                    oo = xx * 3
                    cur[oo] += er * wgt; cur[oo + 1] += eg * wgt; cur[oo + 2] += eb * wgt
            def add_next(xx, wgt):
                if 0 <= xx < W:
                    oo = xx * 3
                    nxt[oo] += er * wgt; nxt[oo + 1] += eg * wgt; nxt[oo + 2] += eb * wgt
            add(x + 1, 7.0 / 16)
            add_next(x - 1, 3.0 / 16)
            add_next(x, 5.0 / 16)
            add_next(x + 1, 1.0 / 16)
    return bytes(bw) + bytes(rd)


# ---------------------------------------------------------------- PNG 写出（预览）
def write_png(path, w, h, rows):
    raw = b''.join(b'\x00' + bytes(r) for r in rows)
    def chunk(t, d):
        return (struct.pack('>I', len(d)) + t + d +
                struct.pack('>I', zlib.crc32(t + d) & 0xffffffff))
    hdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', hdr) +
                chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def preview_rows(epd):
    """把 30000 字节的图还原成 400x300 的 RGB 行，用来出预览"""
    rows = []
    for y in range(H):
        row = bytearray(W * 3)
        for x in range(W):
            o = y * ROW_BYTES + (x >> 3)
            m = 0x80 >> (x & 7)
            white = (epd[o] & m) != 0
            red = (epd[PLANE + o] & m) != 0
            c = RED if red else (WHITE if white else BLACK)
            row[x * 3:x * 3 + 3] = bytes(c)
        rows.append(row)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description='图片 -> ZK42V 三色墨水屏的 30000 字节')
    ap.add_argument('image')
    ap.add_argument('--out', default=None, help='输出的 .epd（默认跟输入同名的 .epd）')
    ap.add_argument('--preview', default=None, help='同时写一张预览 PNG，看看屏上大概是什么样')
    ap.add_argument('--mode', choices=('bwr', 'bw'), default='bwr',
                    help='bwr=三色（默认），bw=只用黑白（照片通常更好看）')
    ap.add_argument('--no-dither', action='store_true', help='不做抖动，直接取最近色')
    args = ap.parse_args(argv)

    if not os.path.exists(args.image):
        print('找不到输入图：%s' % args.image)
        return 2

    print('读图 :', args.image)
    w, h, rows = load_image(args.image)
    print('尺寸 : %dx%d' % (w, h))
    rows = resize(w, h, rows)
    print('缩放 :-> %dx%d' % (W, H))
    epd = to_planes(rows, mode=args.mode, dither=not args.no_dither)
    assert len(epd) == IMG

    out = args.out or (os.path.splitext(args.image)[0] + '.epd')
    with open(out, 'wb') as f:
        f.write(epd)
    sum_ = sum(epd) & 0xFFFFFFFF
    black = white = red = 0
    for y in range(H):
        for x in range(W):
            o = y * ROW_BYTES + (x >> 3)
            m = 0x80 >> (x & 7)
            is_red = (epd[PLANE + o] & m) != 0
            is_white = (epd[o] & m) != 0
            if is_red:
                red += 1
            elif is_white:
                white += 1
            else:
                black += 1
    print('写出 : %s  （%d 字节，逐字节和 0x%08X）' % (out, len(epd), sum_))
    print('像素 : 黑 %d (%.1f%%)  白 %d (%.1f%%)  红 %d (%.1f%%)'
          % (black, black * 100.0 / (W * H), white, white * 100.0 / (W * H),
             red, red * 100.0 / (W * H)))
    if args.preview:
        write_png(args.preview, W, H, preview_rows(epd))
        print('预览 : %s' % args.preview)
    if red == 0 and args.mode == 'bwr':
        print('提示 : 这一版一个红点都没有（原图里没有明显的红色）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
