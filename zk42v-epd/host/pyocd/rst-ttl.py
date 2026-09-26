#!/usr/bin/env python3
# =====================================================================
#  用 USB-TTL 适配器的 RTS / DTR 引脚去拉价签的 RST —— 完全不碰 ST-Link
#
#  为什么要有这个：ST-Link 的 nRST 是它固件里的一条命令（DRIVE_NRST），
#  克隆版上这根针经常"印了字但没接到能推它的脚上"，程序说拉低了它照样 3.3V。
#  USB-TTL 的 RTS/DTR 是驱动芯片直接推的，说低就是低，不靠固件。
#  （esptool 自动把 ESP32 按进下载模式，用的就是这一招。）
#
#  接线：适配器 GND ── 价签 GND（共地，必须）
#        适配器 RTS ── 价签 RST 焊盘
#
#  电平约定：RTS/DTR 是低有效。驱动"断言"它，引脚就是 0V。本工具按这个来，
#  万一你的模块是反的，加 --invert。
#
#  用法：
#      python3 rst-ttl.py --port /dev/cu.usbserial-210 --mode low
#      python3 rst-ttl.py --port /dev/cu.usbserial-210 --mode high --seconds 10
#      python3 rst-ttl.py --port /dev/cu.usbserial-210 --mode toggle
#      python3 rst-ttl.py --port /dev/cu.usbserial-210 --line dtr --mode low
#
#  纯标准库（termios + fcntl），不需要 pyserial。
# =====================================================================

import argparse
import fcntl
import os
import struct
import sys
import termios
import time


class Unsupported(Exception):
    """这块串口 / 驱动不让手动控制 RTS/DTR。"""


def bits(fd):
    try:
        return struct.unpack('i', fcntl.ioctl(fd, termios.TIOCMGET, b'\x00' * 4))[0]
    except Exception:
        return None


def set_bit(fd, bit, on):
    """TIOCMBIS = 置位（断言），TIOCMBIC = 清位（放开）。只动这一位。"""
    req = termios.TIOCMBIS if on else termios.TIOCMBIC
    fcntl.ioctl(fd, req, struct.pack('i', bit))


def describe(v, bit, name, invert):
    if v is None:
        return "%s=?" % name
    asserted = bool(v & bit) != invert        # 断言 = 引脚 0V
    return "%s=%s" % (name, "拉低(应该 0V)" if asserted else "放开(应该 3.3V)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default=os.environ.get('TTL_PORT', '/dev/cu.usbserial-210'))
    ap.add_argument('--line', default='rts', choices=('rts', 'dtr'))
    ap.add_argument('--mode', default='low', choices=('low', 'high', 'toggle'))
    ap.add_argument('--seconds', type=float, default=0.0, help='0 = 一直保持到 Ctrl-C')
    ap.add_argument('--toggle-ms', type=int, default=2000)
    ap.add_argument('--leave', default='low', choices=('low', 'high'))
    ap.add_argument('--invert', action='store_true', help='模块电平是反的时用')
    args = ap.parse_args()

    bit = termios.TIOCM_RTS if args.line == 'rts' else termios.TIOCM_DTR
    name = args.line.upper()

    if not os.path.exists(args.port):
        print("找不到串口: %s" % args.port)
        print("看看有哪些：ls /dev/cu.*")
        return 1

    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)

    def drive(low):
        on = low != args.invert
        try:
            set_bit(fd, bit, on)
        except OSError as e:
            raise Unsupported(str(e))
        return describe(bits(fd), bit, name, args.invert)

    try:
        print("串口   : %s" % args.port)
        print("引脚   : %s%s" % (name, "（反相）" if args.invert else ""))
        print("模式   : %s" % args.mode)
        v = bits(fd)
        if v is not None:
            print("开始前 : %s" % describe(v, bit, name, args.invert))
        print("")
        print("------------------------------------------------------------------")
        print("  万用表【直流电压】档，黑笔接【价签 GND】，红笔接插到")
        print("  %s 上那根杜邦线。先别接价签 RST，量准了再接。" % name)
        print("------------------------------------------------------------------")
        print("  拉低 = 0V 左右  <- 我们要的")
        print("  放开 = 3.3V 左右；量到 5V 说明模块是 5V 电平，")
        print("         那根线别直接接价签 RST，串个 1k 电阻再上。")
        print("------------------------------------------------------------------")
        print("")

        try:
            drive(args.mode != 'high')
        except Unsupported as e:
            print("  这块串口不让手动控制 RTS/DTR：%s" % e)
            print("")
            print("  常见原因：")
            print("    1. 转接头没把 RTS/DTR 引出来（模块上只有 VCC/GND/TX/RX 四个脚）")
            print("       -> 看模块上有没有 RTS / DTR 丝印")
            print("    2. 驱动不支持（少见）")
            print("")
            print("  换一路：拿手边那块 ESP32 的 GPIO 当复位源，")
            print("  或者 MANUAL=1 bash led-window.sh 用手碰 RST。")
            return 2

        start = time.monotonic()
        try:
            if args.mode == 'toggle':
                n = 0
                while True:
                    n += 1
                    print("  >>> 第 %d 次：拉低   %s   (%.1fs)"
                          % (n, drive(True), time.monotonic() - start))
                    sys.stdout.flush()
                    time.sleep(args.toggle_ms / 1000.0)
                    if args.seconds and (time.monotonic() - start) >= args.seconds:
                        break
                    print("  >>> 第 %d 次：放开   %s   (%.1fs)"
                          % (n, drive(False), time.monotonic() - start))
                    sys.stdout.flush()
                    time.sleep(args.toggle_ms / 1000.0)
                    if args.seconds and (time.monotonic() - start) >= args.seconds:
                        break
            else:
                low = (args.mode == 'low')
                print("  %s 现在是【%s】，%s" % (name, "拉低 LOW" if low else "放开 HIGH",
                                              drive(low)))
                print("  现在开始量，Ctrl-C 停。" if not args.seconds
                      else "  会保持 %.0f 秒。" % args.seconds)
                sys.stdout.flush()
                last = 0.0
                while True:
                    el = time.monotonic() - start
                    if args.seconds and el >= args.seconds:
                        break
                    if el - last >= 10.0:
                        last = el
                        print("  [%6.1fs] 还保持着：%s" % (el, drive(low)))
                        sys.stdout.flush()
                    time.sleep(0.25)
        except KeyboardInterrupt:
            print("")
            print("  收到 Ctrl-C。")
    finally:
        try:
            leave_low = (args.leave == 'low')
            drive(leave_low)
            print("  退出，把 %s 留在【%s】。" % (name, "拉低 LOW" if leave_low else "放开 HIGH"))
            if leave_low:
                print("  留 0V 是故意的：对芯片最安全（价签被按在复位里）。")
                print("  把杜邦线从价签 RST 上拔掉，价签就恢复跑。")
        except Exception as e:
            print("  (收尾失败: %s)" % e)
        try:
            os.close(fd)
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
