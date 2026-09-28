#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把本机开发目录里的 ZK42V 项目「快照」进这个仓库文件夹。

为什么要有这个东西：真正在跑的文件在
    /Users/mac/Documents/Codex/2026-09-15/a/outputs/...
（终端命令、pyOCD 脚本、编译脚本都在那儿按绝对路径写的）。这个仓库文件夹是
**留档 + 版本历史**：每改一版，跑一次这个脚本、提交，就有记录可查。

用法：
    python3 tools/make-snapshot.py              # 覆盖式同步（默认）
    python3 tools/make-snapshot.py --dry-run    # 只看要拷什么
    python3 tools/make-snapshot.py --root /别的/路径/a

注意：**不动** 原厂固件备份（zk42v-factory-backup-run1.bin）和编出来的
zk42v-custom-512k.bin —— 那是原厂的固件镜像/构建产物，不适合进库。
它们的 SHA-256 记在 docs/BACKUP-zk42v.md 里。
"""

import argparse
import os
import shutil
import sys

DEFAULT_ROOT = "/Users/mac/Documents/Codex/2026-09-15/a"

# (源文件相对 root 的路径, 在本仓库里的目标路径)
FILES = [
    # ---- 固件：源码 / 构建 / 打包器 -------------------------------------------------
    ("outputs/firmware/README.md",                      "firmware/README.md"),
    ("outputs/firmware/build.sh",                       "firmware/build.sh"),
    ("outputs/firmware/tools/fwpack.py",                "firmware/tools/fwpack.py"),
    ("outputs/firmware/tools/test_fwpack.py",           "firmware/tools/test_fwpack.py"),
    ("outputs/firmware/docs/B2A-ble-plan.md",          "firmware/docs/B2A-ble-plan.md"),
    ("outputs/firmware/tools/img2epd.py",               "firmware/tools/img2epd.py"),
    ("outputs/firmware/tools/test_img2epd.py",          "firmware/tools/test_img2epd.py"),
    ("outputs/firmware/tools/test_adv_data.py",         "firmware/tools/test_adv_data.py"),
    ("outputs/firmware/tools/test_dbg_layout.py",       "firmware/tools/test_dbg_layout.py"),
    # 日历/时钟页面 + 农历 + 画面选项（build 26/28）
    ("outputs/firmware/tools/gen_font.py",              "firmware/tools/gen_font.py"),
    ("outputs/firmware/tools/gen_lunar.py",             "firmware/tools/gen_lunar.py"),
    ("outputs/firmware/tools/gui_preview.c",            "firmware/tools/gui_preview.c"),
    ("outputs/firmware/tools/gui_preview.py",           "firmware/tools/gui_preview.py"),
    ("outputs/firmware/tools/test_gui.py",              "firmware/tools/test_gui.py"),
    ("outputs/firmware/tools/test_lunar.py",            "firmware/tools/test_lunar.py"),
    ("outputs/firmware/tools/test_opt.py",              "firmware/tools/test_opt.py"),
    ("outputs/firmware/docs/feature-backlog.md",        "firmware/docs/feature-backlog.md"),
    ("outputs/firmware/docs/preview/preview-calendar.png", "firmware/docs/preview/preview-calendar.png"),
    ("outputs/firmware/docs/preview/preview-clock.png",  "firmware/docs/preview/preview-clock.png"),
    ("outputs/firmware/zk42v-epd-app/GCC/Makefile",     "firmware/zk42v-epd-app/GCC/Makefile"),
    ("outputs/firmware/zk42v-epd-app/GCC/gcc_linker_zk42v.lds",
                                                        "firmware/zk42v-epd-app/GCC/gcc_linker_zk42v.lds"),
    ("outputs/firmware/zk42v-epd-app/Src/main.c",       "firmware/zk42v-epd-app/Src/main.c"),
    ("outputs/firmware/zk42v-epd-app/Src/ble/zk_ble.c", "firmware/zk42v-epd-app/Src/ble/zk_ble.c"),
    ("outputs/firmware/zk42v-epd-app/Src/ble/zk_ble.h", "firmware/zk42v-epd-app/Src/ble/zk_ble.h"),
    ("outputs/firmware/zk42v-epd-app/Src/config/custom_config.h",
                                                        "firmware/zk42v-epd-app/Src/config/custom_config.h"),
    ("outputs/firmware/zk42v-epd-app/Src/board/zk42v_board.h",
                                                        "firmware/zk42v-epd-app/Src/board/zk42v_board.h"),
    ("outputs/firmware/zk42v-epd-app/Src/board/zk_dbg.h",
                                                        "firmware/zk42v-epd-app/Src/board/zk_dbg.h"),
    ("outputs/firmware/zk42v-epd-app/Src/epd/epd_zk42v.c",
                                                        "firmware/zk42v-epd-app/Src/epd/epd_zk42v.c"),
    ("outputs/firmware/zk42v-epd-app/Src/epd/epd_zk42v.h",
                                                        "firmware/zk42v-epd-app/Src/epd/epd_zk42v.h"),
    ("outputs/firmware/zk42v-epd-app/Src/img/testimg.c",
                                                        "firmware/zk42v-epd-app/Src/img/testimg.c"),
    ("outputs/firmware/zk42v-epd-app/Src/img/testimg.h",
                                                        "firmware/zk42v-epd-app/Src/img/testimg.h"),
    ("outputs/firmware/zk42v-epd-app/Src/img/zkgui.c",
                                                        "firmware/zk42v-epd-app/Src/img/zkgui.c"),
    ("outputs/firmware/zk42v-epd-app/Src/img/zkgui.h",
                                                        "firmware/zk42v-epd-app/Src/img/zkgui.h"),
    ("outputs/firmware/zk42v-epd-app/Src/img/zkgui_font.h",
                                                        "firmware/zk42v-epd-app/Src/img/zkgui_font.h"),
    ("outputs/firmware/zk42v-epd-app/Src/img/zkgui_trig.h",
                                                        "firmware/zk42v-epd-app/Src/img/zkgui_trig.h"),
    ("outputs/firmware/zk42v-epd-app/Src/img/lunar.c",
                                                        "firmware/zk42v-epd-app/Src/img/lunar.c"),
    ("outputs/firmware/zk42v-epd-app/Src/img/lunar.h",
                                                        "firmware/zk42v-epd-app/Src/img/lunar.h"),
    ("outputs/firmware/zk42v-epd-app/Src/img/zk_opt.c",
                                                        "firmware/zk42v-epd-app/Src/img/zk_opt.c"),
    ("outputs/firmware/zk42v-epd-app/Src/img/zk_opt.h",
                                                        "firmware/zk42v-epd-app/Src/img/zk_opt.h"),
    ("outputs/firmware/zk42v-epd-app/Src/ble/zk_epd_svc.c",
                                                        "firmware/zk42v-epd-app/Src/ble/zk_epd_svc.c"),
    ("outputs/firmware/zk42v-epd-app/Src/ble/zk_epd_svc.h",
                                                        "firmware/zk42v-epd-app/Src/ble/zk_epd_svc.h"),

    # ---- 上位机：pyOCD 脚本（抢 SWD 窗口 / 写 flash / 体检） --------------------
    ("outputs/pyocd/led-window-user.py",                "host/pyocd/led-window-user.py"),
    ("outputs/pyocd/flash-app.sh",                      "host/pyocd/flash-app.sh"),
    ("outputs/pyocd/flash-write.sh",                    "host/pyocd/flash-write.sh"),
    ("outputs/pyocd/status.sh",                         "host/pyocd/status.sh"),
    ("outputs/pyocd/push-image.sh",                     "host/pyocd/push-image.sh"),
    ("outputs/pyocd/verify-backup.sh",                  "host/pyocd/verify-backup.sh"),
    ("outputs/pyocd/dump-resume.sh",                    "host/pyocd/dump-resume.sh"),
    ("outputs/pyocd/rst-check.sh",                      "host/pyocd/rst-check.sh"),
    ("outputs/pyocd/led-window.sh",                     "host/pyocd/led-window.sh"),
    ("outputs/pyocd/dap-info.sh",                       "host/pyocd/dap-info.sh"),
    ("outputs/pyocd/flash-lab.sh",                      "host/pyocd/flash-lab.sh"),
    ("outputs/pyocd/flash-lab2.sh",                     "host/pyocd/flash-lab2.sh"),
    ("outputs/pyocd/flash-lab3.sh",                     "host/pyocd/flash-lab3.sh"),
    ("outputs/pyocd/flash-lab4.sh",                     "host/pyocd/flash-lab4.sh"),
    ("outputs/pyocd/ram-dump.sh",                       "host/pyocd/ram-dump.sh"),
    ("outputs/pyocd/pulse-rst.sh",                      "host/pyocd/pulse-rst.sh"),
    ("outputs/pyocd/rst-hold.sh",                       "host/pyocd/rst-hold.sh"),
    ("outputs/pyocd/rst-ttl.py",                        "host/pyocd/rst-ttl.py"),
    # 离线测试台（不需要硬件就能跑）
    ("outputs/pyocd/test-flashwrite.py",                "host/pyocd/test-flashwrite.py"),
    ("outputs/pyocd/test-dumpresume-modes.py",          "host/pyocd/test-dumpresume-modes.py"),
    ("outputs/pyocd/test-sanity.py",                    "host/pyocd/test-sanity.py"),
    ("outputs/pyocd/test-flashlab.py",                  "host/pyocd/test-flashlab.py"),
    ("outputs/pyocd/test-flashlab2.py",                 "host/pyocd/test-flashlab2.py"),
    ("outputs/pyocd/test-dapinfo.py",                   "host/pyocd/test-dapinfo.py"),
    ("outputs/pyocd/test-led-user.py",                  "host/pyocd/test-led-user.py"),
    ("outputs/pyocd/test-symbols.py",                   "host/pyocd/test-symbols.py"),

    # ---- 文档 ---------------------------------------------------------------------
    ("outputs/pyocd/README.md",                         "docs/pyocd-notes.md"),
    ("outputs/pyocd/BACKUP-zk42v.md",                   "docs/BACKUP-zk42v.md"),
    ("outputs/pyocd/WRITE-zk42v.md",                    "docs/WRITE-zk42v.md"),
    ("outputs/analysis/PANEL-zk42v.md",                 "docs/PANEL-zk42v.md"),
    ("outputs/analysis/qbsg-生态调研.md",               "analysis/qbsg-生态调研.md"),
    ("outputs/ZK42V-原厂固件串口日志与flash布局.md",     "docs/ZK42V-原厂固件串口日志与flash布局.md"),
    ("outputs/ZK42V-GR5513BEND-硬件判定与路线.md",       "docs/ZK42V-GR5513BEND-硬件判定与路线.md"),

    # ---- 逆向分析留下的中间产物 -----------------------------------------------------
    ("outputs/analysis/epd-seq.txt",                    "analysis/epd-seq.txt"),
    ("outputs/analysis/tools/scan_epd.py",              "analysis/tools/scan_epd.py"),
    ("outputs/analysis/tools/xref.py",                  "analysis/tools/xref.py"),
    ("outputs/analysis/tools/dislib.py",                "analysis/tools/dislib.py"),

    # ---- 实机跑出来的日志（B1 那一轮，留作证据） ------------------------------------
    ("outputs/pyocd/flash-app-20260927-000803.log",     "docs/runs/2026-09-27-b1/flash-app-000803.log"),
    ("outputs/pyocd/flash-app-20260927-000921.log",     "docs/runs/2026-09-27-b1/flash-app-000921.log"),
    ("outputs/pyocd/status-20260927-001407.log",        "docs/runs/2026-09-27-b1/status-001407.log"),
    ("outputs/pyocd/status-20260927-002440.log",        "docs/runs/2026-09-27-b1/status-002440.log"),
    ("outputs/pyocd/status-20260927-004040.log",        "docs/runs/2026-09-27-b1/status-004040.log"),
]


def main(argv=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="把本机项目快照进这个仓库文件夹")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="本机项目根目录（默认 %s）" % DEFAULT_ROOT)
    ap.add_argument("--dest", default=here, help="快照输出目录（默认就是本文件夹）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    src_root = args.root
    dst_root = args.dest
    missing = []
    copied = 0
    total_bytes = 0

    print("源 : %s" % src_root)
    print("目标: %s" % dst_root)
    print("")

    for rel_src, rel_dst in FILES:
        src = os.path.join(src_root, rel_src)
        dst = os.path.join(dst_root, rel_dst)
        if not os.path.exists(src):
            missing.append(rel_src)
            continue
        n = os.path.getsize(src)
        total_bytes += n
        print("  %-64s %7d B" % (rel_dst, n))
        if args.dry_run:
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    print("")
    print("拷了 %d 个文件，共 %.1f KB" % (copied, total_bytes / 1024.0))
    if missing:
        print("")
        print("!! 有 %d 个源文件找不到（本机那边可能改名/删了）：" % len(missing))
        for m in missing:
            print("   %s" % m)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
