#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把自研 APP 打包成「原厂 bootloader 会认」的整片镜像。

背景（都是从原厂固件里挖出来的，见 outputs/pyocd/WRITE-zk42v.md）：

    flash 布局
      0x01000000  boot_info         ROM 按这个找 bootloader（我们不动它）
      0x01000040  镜像信息 1         "second_boot_"
      0x01002000  镜像信息 2         ← bootloader 另外存的一份 APP 信息，**要改**
      0x01003000  原厂 bootloader    （我们不动它）
      0x0100A000  原厂 APP           ← 换成我们的 APP
      0x0107F000  NVDS              （我们不动它）

    0x01002000 那条记录（dfu_image_info_t，40 字节）：
      +0  u16  pattern    = 0x4744
      +2  u16  version    = 1
      +4  u32  bin_size   ← 要改
      +8  u32  check_sum  ← 要改（就是 APP 镜像逐字节求和取低 32 位！）
      +12 u32  load_addr  = 0x0100A000
      +16 u32  run_addr   = 0x0100A000
      +20 u32  xqspi_xip_cmd
      +24 u32  flags
      +28 u8[12] comments  = "zk_esl_goodi"（原厂的，一字节都别动）

  校验和是减法验证过的：拿出厂镜像按这个公式算，
    bootloader 0x3000 起 0x3B00 字节  ->  0x00173927（跟串口日志一致）
    APP        0xA000 起 0x1F8F0 字节 ->  0x00CCE71B（跟串口日志一致）
  两个都严丝合缝，所以这个公式不会错。
"""

import argparse
import hashlib
import os
import struct
import sys

# ---------------------------------------------------------------- 常量

FLASH_BASE        = 0x01000000
FLASH_TOTAL       = 0x00080000
APP_BASE_DEFAULT  = 0x0100A000
APP_LIMIT         = 0x00075000          # 0x0100A000 .. 0x0107F000

# 原厂 bootloader 自己的上限，比 APP_LIMIT 严得多。见 check_app_image() 里的推导：
# bootloader 要求 (bin_size + 0x1040) 按 4096 向上取整后不超过 0x26000，
# 也就是 bin_size <= 0x24FC0。超了它就把 APP 判成无效、停在断言里不启动。
# （2026-09-28 实测：build 26 = 156164 字节 → 卡死在 bootloader；开 -Os 后 145732 ✅）
BOOTLOADER_APP_LIMIT = 0x00024FC0
APP_INFO_OFF      = 0x002000
APP_INFO_PATTERN  = 0x4744
DFU_IMG_INFO_LEN  = 40
APP_COMMENTS      = b"zk_esl_goodi"

FACTORY_SHA256 = ("1dfab92f558352642f5c477b22d1e66d12efc1be959d5e676ae27fe05cedb704")

BOOT_HDR_OFF   = 0x0000
BOOT_HDR_WANT  = (0x00003B00, 0x00173927, 0x01003000, 0x01003000)

SECTOR = 0x1000

PASS = "OK  "
WARN = "WARN"
FAIL = "FAIL"


def log(status, msg):
    print("[%s] %s" % (status, msg))


def u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def p32(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def round_up(v, n):
    return (v + n - 1) // n * n


# ---------------------------------------------------------------- 检查

def check_base_image(data):
    """确认这就是那份原厂全片备份（至少别是别的东西）"""
    ok = True
    if len(data) != FLASH_TOTAL:
        log(FAIL, "底包大小 %d 字节，应该是 %d（0x%X）" % (len(data), FLASH_TOTAL, FLASH_TOTAL))
        return False

    got = tuple(u32(data, BOOT_HDR_OFF + 4 * i) for i in range(4))
    if got != BOOT_HDR_WANT:
        log(FAIL, "底包 boot 头 %s，期望 %s" % (got, BOOT_HDR_WANT))
        ok = False
    else:
        log(PASS, "底包 boot 头对得上：bin=0x%X sum=0x%08X load=0x%08X run=0x%08X" % got)

    h = sha256_of(data)
    if h == FACTORY_SHA256:
        log(PASS, "底包 SHA-256 = 出厂备份（%s...）" % h[:16])
    else:
        log(WARN, "底包 SHA-256 = %s...，跟出厂备份（%s...）不一样"
            % (h[:16], FACTORY_SHA256[:16]))
        log(WARN, "  如果你是有意改了底包，忽略这条；否则先确认底包是哪来的。")

    rec = data[APP_INFO_OFF:APP_INFO_OFF + DFU_IMG_INFO_LEN]
    pat, ver = struct.unpack_from("<HH", rec, 0)
    load, run = u32(rec, 12), u32(rec, 16)
    if pat != APP_INFO_PATTERN:
        log(FAIL, "0x%05X 那条 APP 信息 pattern = 0x%04X，应该是 0x%04X"
            % (APP_INFO_OFF, pat, APP_INFO_PATTERN))
        ok = False
    elif load != APP_BASE_DEFAULT or run != APP_BASE_DEFAULT:
        log(FAIL, "0x%05X 那条 APP 信息 load/run = 0x%08X/0x%08X，期望都是 0x%08X"
            % (APP_INFO_OFF, load, run, APP_BASE_DEFAULT))
        ok = False
    else:
        log(PASS, "0x%05X APP 信息：pattern=0x%04X ver=%d load=run=0x%08X"
            % (APP_INFO_OFF, pat, ver, load))

    log(PASS, "底包 NVDS 段（0x0107F000 起 4096 字节）原样保留")
    return ok


def check_app_image(app, app_base):
    """自研 APP 自己的体检：栈顶、复位向量、大小、标记字符串"""
    ok = True
    n = len(app)
    if n == 0:
        log(FAIL, "APP 镜像 0 字节")
        return False
    # 原厂 bootloader 的硬上限（比 flash 里给 APP 留的 0x75000 小得多）。
    # 依据：boot.asm 0x0100392A 那一段
    #     r0 = bin_size + 0x1040 ; 按 4096 向上取整 ; cmp r5, #0x26000
    #     bls 继续校验 ; 否则打印 "Fw load data err" 并把 APP 判成无效 -> 走 DFU
    # 反推：bin_size + 0x1040 <= 0x26000 ⇒ bin_size <= 0x24FC0 = 151488。
    # 2026-09-28 就栽在这上面：build 26 没开优化、又加了日历字模，涨到 156164,
    # 超限 4676 字节 —— 现象是芯片停在 bootloader 的断言里、我们的固件一个字节
    # 都不会被执行（status.sh 里 magic 不对、PC 在 0x01003720）。
    if n > BOOTLOADER_APP_LIMIT:
        log(FAIL, "APP 镜像 %d 字节，超过 bootloader 的上限 0x%X (%d)"
            % (n, BOOTLOADER_APP_LIMIT, BOOTLOADER_APP_LIMIT))
        log(FAIL, "  超限后果：bootloader 打印 \"Fw load data err\" 并拒绝启动，"
                  "芯片会停在它自己的断言里")
        log(FAIL, "  腾地方的办法：确认 Makefile 里有 -Os；实在还超就砍功能/字模")
        return False
    if n > APP_LIMIT:
        log(FAIL, "APP 镜像 %d 字节，超过 0x%X 的可用空间" % (n, APP_LIMIT))
        return False
    if n % 4:
        log(WARN, "APP 镜像 %d 字节不是 4 的倍数（原厂是，最好也对齐）" % n)

    sp = u32(app, 0)
    rv = u32(app, 4)

    sp_ok = ((0x30000000 <= sp <= 0x30020000) or
             (0x00800000 <= sp <= 0x00820000))
    if not sp_ok:
        log(FAIL, "APP 第 0 个字（初始栈顶）= 0x%08X，不像这颗芯片的 RAM" % sp)
        ok = False
    else:
        log(PASS, "初始栈顶 SP = 0x%08X" % sp)

    if not (app_base <= rv < app_base + n):
        log(FAIL, "APP 复位向量 = 0x%08X，没落在自己的镜像里（0x%08X + 0x%X）"
            % (rv, app_base, n))
        ok = False
    elif rv % 2 == 0:
        log(WARN, "复位向量 0x%08X 是偶数 —— Cortex-M 的向量应该带 thumb 位（奇数）" % rv)
    else:
        log(PASS, "复位向量 = 0x%08X（在镜像内，thumb 位正常）" % rv)

    tag = b"ZK42V-EPD-CUSTOM-FW"
    if tag in app:
        log(PASS, "镜像里有标记字符串 %s" % tag.decode())
    else:
        log(WARN, "镜像里没找到标记字符串 %s（不影响运行，验脚本少一个抓手）" % tag.decode())

    return ok


# ---------------------------------------------------------------- 打包

def pack(base, app, app_base, out_path):
    bin_size = len(app)
    check_sum = sum(app) & 0xFFFFFFFF

    img = bytearray(base)

    # 1) APP 区：先按扇区整片擦成 0xFF，再写 APP
    used_end = app_base - FLASH_BASE + bin_size
    wipe_end = round_up(used_end, SECTOR)
    img[app_base - FLASH_BASE:wipe_end] = b"\xFF" * (wipe_end - (app_base - FLASH_BASE))
    img[app_base - FLASH_BASE:app_base - FLASH_BASE + bin_size] = app

    # 2) 0x01002000 那条镜像信息：只改 bin_size 和 check_sum，其余一字节不动
    o = APP_INFO_OFF
    old = bytes(img[o:o + DFU_IMG_INFO_LEN])
    img[o + 4:o + 8] = p32(bin_size)
    img[o + 8:o + 12] = p32(check_sum)
    new = bytes(img[o:o + DFU_IMG_INFO_LEN])

    # 3) 自检：重新解析一遍，确认减法验证得回去
    chk = bytearray(img)
    got_size = u32(chk, o + 4)
    got_sum = u32(chk, o + 8)
    load = u32(chk, o + 12)
    run = u32(chk, o + 16)
    body = bytes(chk[app_base - FLASH_BASE:app_base - FLASH_BASE + got_size])
    real_sum = sum(body) & 0xFFFFFFFF
    body_same = body == app

    print("")
    log(PASS if got_size == bin_size else FAIL, "镜像信息 bin_size = 0x%X (%d)" % (got_size, got_size))
    log(PASS if got_sum == real_sum else FAIL,
        "镜像信息 check_sum = 0x%08X，实测镜像逐字节和 = 0x%08X" % (got_sum, real_sum))
    log(PASS if (load == app_base and run == app_base) else FAIL,
        "load/run = 0x%08X/0x%08X" % (load, run))
    log(PASS if body_same else FAIL, "APP 区内容跟输入镜像逐字节一致")

    if not (got_size == bin_size and got_sum == real_sum and body_same
            and load == app_base and run == app_base):
        return None

    print("")
    print("  0x%05X 那条记录  改之前: %s" % (o, old.hex(" ")))
    print("  0x%05X 那条记录  改之后: %s" % (o, new.hex(" ")))
    print("")

    with open(out_path, "wb") as f:
        f.write(img)

    nsec_app = (wipe_end - (app_base - FLASH_BASE)) // SECTOR
    print("  写出 : %s" % out_path)
    print("  大小 : %d 字节 (0x%X)  SHA-256 %s" % (len(img), len(img), sha256_of(bytes(img))))
    print("  要写的扇区：")
    print("     APP  : 0x%08X 起 %d 颗（0x%05X .. 0x%05X）"
          % (app_base, nsec_app, app_base - FLASH_BASE, wipe_end - SECTOR))
    print("     INFO : 1 颗（0x%05X，最后写）" % APP_INFO_OFF)
    print("     合计 %d 颗扇区" % (nsec_app + 1))
    return bytes(img)


# ---------------------------------------------------------------- main

def default_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    fw_root = os.path.dirname(here)          # .../outputs/firmware
    out_root = os.path.dirname(fw_root)      # .../outputs
    return {
        "base": os.path.join(out_root, "pyocd", "zk42v-factory-backup-run1.bin"),
        "app": os.path.join(fw_root, "zk42v-epd-app", "GCC", "out", "zk42v_epd.bin"),
        "out": os.path.join(fw_root, "zk42v-custom-512k.bin"),
    }


def main(argv=None):
    d = default_paths()
    ap = argparse.ArgumentParser(description="把自研 APP 打包成整片 512KB 镜像")
    ap.add_argument("--base", default=d["base"], help="底包（原厂全片备份）")
    ap.add_argument("--app", default=d["app"], help="自研 APP 的 .bin")
    ap.add_argument("--out", default=d["out"], help="输出整片镜像")
    ap.add_argument("--app-base", default="0x0100A000", help="APP 在 flash 里的地址")
    ap.add_argument("--check-only", action="store_true", help="只体检，不写文件")
    args = ap.parse_args(argv)

    app_base = int(args.app_base, 0)
    print("=" * 68)
    print("  打包 ZK42V 自研固件")
    print("=" * 68)
    print("  底包 : %s" % args.base)
    print("  APP  : %s" % args.app)
    print("  输出 : %s" % args.out)
    print("")

    for p, what in ((args.base, "底包"), (args.app, "APP 镜像")):
        if not os.path.exists(p):
            log(FAIL, "找不到%s：%s" % (what, p))
            return 2

    with open(args.base, "rb") as f:
        base = f.read()
    with open(args.app, "rb") as f:
        app = f.read()

    print("--- 底包体检 ---")
    ok_base = check_base_image(base)
    print("")
    print("--- APP 体检 ---")
    ok_app = check_app_image(app, app_base)
    print("")

    if not (ok_base and ok_app):
        log(FAIL, "体检没过，不打包。")
        return 1

    print("--- 打包 ---")
    img = pack(base, app, app_base, args.out)
    if img is None:
        log(FAIL, "自检没过，别刷。")
        return 1

    print("")
    log(PASS, "可以刷了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
