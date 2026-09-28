#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测 fwpack.py —— 不需要硬件，也不用真的固件。

造一份「假的原厂备份 + 假的自研 APP」，逐个验证：
  1  正常情况能打包，且打出来的镜像自洽（校验和 == 声明值）
  2  bootloader 段 / NVDS 段一字节不动
  3  0x2000 那条记录只改 bin_size 和 check_sum，其余照抄
  4  APP 的栈顶不像 RAM -> 拒绝
  5  APP 太大 -> 拒绝
  6  APP 的复位向量不在自己镜像里 -> 拒绝
  7  底包不是原厂那份（boot 头对不上）-> 拒绝
"""

import contextlib
import io
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fwpack  # noqa: E402

BASE = 0x01000000
TOTAL = 0x80000
APP_BASE = 0x0100A000

CHECKS = []


def check(label, ok):
    CHECKS.append((label, bool(ok)))


def make_base():
    """造一份长得像原厂备份的底包：boot 头 + 0x2000 的 APP 信息 + 一点内容"""
    d = bytearray(b'\xFF' * TOTAL)
    struct.pack_into('<IIII', d, 0, *fwpack.BOOT_HDR_WANT)
    off = fwpack.APP_INFO_OFF
    struct.pack_into('<HH', d, off, fwpack.APP_INFO_PATTERN, 1)
    struct.pack_into('<IIII', d, off + 4, 0x1F8F0, 0x00CCE71B, APP_BASE, APP_BASE)
    struct.pack_into('<II', d, off + 20, 0x03, 0x0744)
    d[off + 28:off + 40] = fwpack.APP_COMMENTS
    # bootloader 段和 NVDS 段放点可辨认的内容，方便验证「没被动过」
    d[0x3000:0x3000 + 64] = bytes(range(64))
    d[0x7F000:0x7F000 + 64] = bytes(range(64, 128))
    return bytes(d)


def make_app(size=0x3500, sp=0x3001F000, rv=None):
    app = bytearray(b'\xA5' * size)
    struct.pack_into('<II', app, 0, sp,
                     APP_BASE + 0x101 if rv is None else rv)
    tag = b'ZK42V-EPD-CUSTOM-FW-B1'
    app[0x200:0x200 + len(tag)] = tag
    return bytes(app)


def run_pack(base, app, extra=None):
    d = tempfile.mkdtemp(prefix='fwpack-')
    bp = os.path.join(d, 'base.bin')
    ap = os.path.join(d, 'app.bin')
    op = os.path.join(d, 'out.bin')
    open(bp, 'wb').write(base)
    open(ap, 'wb').write(app)
    argv = ['--base', bp, '--app', ap, '--out', op]
    if extra:
        argv += extra
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fwpack.main(argv)
    out = buf.getvalue()
    img = open(op, 'rb').read() if os.path.exists(op) else None
    return rc, out, img


def main():
    base = make_base()
    app = make_app()

    # 1 / 2 / 3
    rc, out, img = run_pack(base, app)
    check('1: 正常情况打包成功', rc == 0 and img is not None)
    check('1: 报出自洽', '[OK  ] 可以刷了' in out)
    off = fwpack.APP_INFO_OFF
    size, sum_, load, run = struct.unpack_from('<IIII', img, off + 4)
    check('3: bin_size 换成了新 APP 的长度', size == len(app))
    check('3: check_sum 换成了新 APP 的逐字节和',
          sum_ == (sum(app) & 0xFFFFFFFF))
    check('3: load/run 没变', load == APP_BASE and run == APP_BASE)
    check('3: comments 一字节没改',
          img[off + 28:off + 40] == fwpack.APP_COMMENTS)
    check('3: pattern/version/xqspi/flags 没改',
          img[off:off + 4] == base[off:off + 4]
          and img[off + 20:off + 28] == base[off + 20:off + 28])
    check('2: bootloader 段一字节没动',
          img[0x3000:0x3000 + 64] == base[0x3000:0x3000 + 64])
    check('2: NVDS 段一字节没动',
          img[0x7F000:0x7F000 + 64] == base[0x7F000:0x7F000 + 64])
    check('2: boot 头一字节没动', img[0:16] == base[0:16])
    wipe_end = (0xA000 + len(app) + 0xFFF) // 0x1000 * 0x1000
    check('2: APP 区 == 新 APP + 0xFF 补到扇区尾',
          img[0xA000:0xA000 + len(app)] == app
          and set(img[0xA000 + len(app):wipe_end]) == {0xFF}
          and img[wipe_end:0x7F000] == base[wipe_end:0x7F000])   # 扇区尾之后照抄底包
    check('1: 打出来还是整片 512KB', len(img) == TOTAL)
    # 自洽性：拿打好的镜像再算一遍
    check('1: 打出来的镜像能自洽验回去',
          (sum(img[0xA000:0xA000 + size]) & 0xFFFFFFFF) == sum_)

    # 4 栈顶不像 RAM
    rc, out, img = run_pack(base, make_app(sp=0x12345678))
    check('4: 栈顶不像 RAM 就拒绝', rc != 0 and '不像这颗芯片的 RAM' in out)
    check('4: 拒绝时不产出文件', img is None)

    # 5 APP 太大
    rc, out, img = run_pack(base, make_app(size=fwpack.APP_LIMIT + 0x1000))
    check('5: APP 超过可用空间就拒绝', rc != 0 and '超过' in out)

    # 6 复位向量不在镜像里
    rc, out, img = run_pack(base, make_app(rv=0x01003001))
    check('6: 复位向量不在镜像里就拒绝', rc != 0 and '没落在自己的镜像里' in out)

    # 7 底包不对
    bad_base = bytearray(make_base())
    struct.pack_into('<I', bad_base, 4, 0xDEADBEEF)
    rc, out, img = run_pack(bytes(bad_base), app)
    check('7: 底包 boot 头对不上就拒绝', rc != 0 and 'boot 头' in out)

    # 8 bootloader 自己的硬上限（0x24FC0）—— 超了要拒刷，别让板子起不来
    #   2026-09-28 实测过：build 26 = 156164 字节 → 芯片卡在 bootloader 断言里
    rc, out, img = run_pack(base, make_app(size=fwpack.BOOTLOADER_APP_LIMIT + 4))
    check('8: 超过 bootloader 上限(0x24FC0)就拒绝',
          rc != 0 and 'bootloader 的上限' in out)
    check('8: 说明超限的后果', 'Fw load data err' in out)
    # 刚好在上限上要能过（边界）
    rc, out, img = run_pack(base, make_app(size=fwpack.BOOTLOADER_APP_LIMIT))
    check('8b: 正好等于上限时放行', rc == 0)

    bad = 0
    for label, ok in CHECKS:
        print('  [%s] %s' % ('PASS' if ok else '**FAIL**', label))
        bad += 0 if ok else 1
    print('全部通过 ✅' if bad == 0 else '有 %d 项失败 ❌' % bad)
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
