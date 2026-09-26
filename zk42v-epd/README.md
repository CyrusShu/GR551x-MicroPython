# ZK42V 价签（GR5513BEND）→ 自研固件 / 墨水屏基站

把一块 ZKONG ZK42V 电子价签（Goodix **GR5513BEND** + 4.2 寸 400×300 黑白红三色墨水屏）
从「只能用原厂基站」变成「我们自己能刷、能画、能自己推图」的板子。

这个文件夹是**留档与版本历史**：真正在跑的代码在本机的
`/Users/mac/Documents/Codex/2026-09-15/a/outputs/` 下，这边每改一版同步一次、提交一次，
就有记录可查。同步脚本就在本文件夹里：`tools/make-snapshot.py`。

---

## 现在到哪一步了

| 阶段 | 内容 | 状态 |
|---|---|---|
| **A** | 原厂固件全片备份（512KB）+ SHA-256 验真 | ✅ 完成 |
| **B0** | 写入路径验通（Goodix 自己的 FLM 算法，擦/写/读回） | ✅ 完成，`restore`/`verify` 全通过 |
| **B1** | 屏参数全部逆向（引脚 / 初始化序列 / 图像格式 / 刷新序列） | ✅ 完成 |
| **B1** | 自研固件：把屏点亮 + 画极性体检图 | ⚠️ 刷进去了、镜像自洽，但**屏没亮**，3 秒后调试口消失 |
| **B1.1** | 疑似病根修复（AON 超深睡标志导致的重启循环）+ 两个计数器 | 🔨 已编好，待烧录验证 |
| **B2** | 价签侧 BLE + 兼容 EPD-nRF5 协议的图片推送 | ⏳ 待开始 |
| **B3** | ESP32 基站（复用既有 ESP32-CAM/RTSP 工程） | ⏳ 计划中 |

安全网一直在：**任何**时候都能把出厂固件刷回去 ——

```bash
cd <本机项目>/outputs/pyocd
MODE=restore bash flash-write.sh     # 整片刷回出厂备份（128 颗扇区，约 3 分钟）
MODE=verify  bash flash-write.sh     # 按一下 RST 再跑，看到 VERIFY_OK 就回厂了
```

---

## 目录结构

```
zk42v-epd/
├── firmware/                  自研固件（GR5513 裸机 C，GCC 工具链）
│   ├── README.md              ← **固件手册**：完整命令、判据、B1 复盘、已知不确定项
│   ├── build.sh               一键：编译 + 打包成能过原厂 bootloader 校验的整片镜像
│   ├── zk42v-epd-app/
│   │   ├── GCC/Makefile               用 SDK 的编译参数，目标 zk42v_epd
│   │   ├── GCC/gcc_linker_zk42v.lds   APP 落 0x0100A000，栈顶 0x3001F000，留 4KB 调试块
│   │   └── Src/
│   │       ├── main.c                 主流程 + 接管 main_init（清超深睡标志、启动计数）
│   │       ├── board/zk42v_board.h    引脚表 / 屏参数（每个值都带出处）
│   │       ├── board/zk_dbg.h         0x3001F000 调试状态块的定义
│   │       ├── epd/epd_zk42v.[ch]     屏驱动（重放原厂初始化/刷新序列）
│   │       ├── img/testimg.[ch]       极性+方向体检图（四条横带 + 定位小条）
│   │       └── config/custom_config.h SDK 配置（GR5513BEND、APP 在 0x0100A000）
│   └── tools/
│       ├── fwpack.py          把 APP 打包成整片镜像（含自检，会拒绝不合规的镜像）
│       └── test_fwpack.py     打包器的离线自测（18 项）
├── host/pyocd/                上位机工具（pyOCD 用户脚本 + 包装脚本 + 离线测试台）
│   ├── led-window-user.py     核心：抢 SWD 窗口、读写 flash、体检（≈4100 行）
│   ├── flash-app.sh           MODE=app 写自研 APP / MODE=appverify 只读验收
│   ├── flash-write.sh         原厂那套：probe / pagetest / peek / restore / verify
│   ├── status.sh              体检自研固件（多次采样 + 复位检测 + AON 寄存器）
│   └── test-*.py              离线测试台（不需要硬件，7 个文件、100+ 项断言）
├── docs/
│   ├── PANEL-zk42v.md                   屏的逆向结果（每条结论都有代码地址）
│   ├── BACKUP-zk42v.md                  原厂固件备份与验真记录
│   ├── WRITE-zk42v.md                   写入路径（FLM 算法）四步走
│   ├── pyocd-notes.md                   硬件访问踩过的所有坑（SWD 假数据、XIP、复位线…）
│   ├── ZK42V-原厂固件串口日志与flash布局.md
│   ├── ZK42V-GR5513BEND-硬件判定与路线.md
│   └── runs/2026-09-27-b1/              B1 那轮实机跑出来的原始日志
└── analysis/
    ├── epd-seq.txt            从原厂固件里扫出来的屏总线序列
    └── tools/                 反汇编扫描脚本
```

---

## 为什么这些路径是 `/Users/mac/...`

真正跑起来的那一份在：

```
/Users/mac/Documents/Codex/2026-09-15/a/
├── GR551x-SDK/                 Goodix 官方 SDK（另一份 clone，不在本仓库）
├── tools/pyocd/pyocd           打包好的 pyOCD 0.45
└── outputs/
    ├── firmware/               自研固件（= 本仓库的 firmware/）
    ├── pyocd/                  上位机脚本（= 本仓库的 host/pyocd/）
    └── analysis/               逆向中间产物
```

`firmware/zk42v-epd-app/GCC/Makefile` 里的 `SDK_ROOT`、`led-window-user.py` 里的
`FLM_PATHS`、几个 `.sh` 里的 `ROOT`，都是按这个布局写死的。想在别的机器上跑，
改这几处路径（Makefile 里可以用 `make SDK_ROOT=/别的/路径` 覆盖）。

所以这个仓库文件夹的定位是：**留档 + 历史**，不是运行位置。

---

## 重新编译 / 烧录（在本机项目目录里跑）

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware
bash build.sh                 # 编译 + 打包 → zk42v-custom-512k.bin（会打印 SHA-256）

cd ../pyocd
MODE=app bash flash-app.sh      # 只写 0x0100A000 那 19 颗扇区 + 更新 0x01002000 的镜像信息
MODE=appverify bash flash-app.sh
bash status.sh                  # 体检
```

离线测试（不用硬件，随时可跑）：

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware && python3 tools/test_fwpack.py
cd ../pyocd && for t in test-*.py; do python3 "$t"; done
```

## 怎么更新这个文件夹（每次改完都这么做）

真正的代码在本机 `outputs/` 下改，改完把快照刷到这边、**本地提交**：

```bash
cd <本地仓库>/zk42v-epd
python3 tools/make-snapshot.py          # 从本机 outputs/ 覆盖式同步（--dry-run 可先看）
git add -A && git commit -m "说明这次改了什么"
```

**推送的节奏**：平时只本地提交攒着，**到一个里程碑再 `git push`**（省得每改一行就往
GitHub 推一次）。里程碑指的是：B1 点亮、B2 BLE 能推图、B3 基站能跑 这种阶段性的点。

`tools/make-snapshot.py` 里的 `FILES` 表就是「哪些文件算项目文件」的清单 ——
加新文件时在表里加一行。它**不会**动原厂固件备份和构建产物（见下节）。

当前这个文件夹在 **`zk42v-epd` 分支**上，远端是 `CyrusShu/GR551x-MicroPython`
（没有直接写进 `master`，这样 fork 的 `master` 还能干净地跟上游
`goodix-ble/GR551x-MicroPython` 同步）。想把合并进 `master`：

```bash
git checkout master && git merge zk42v-epd && git push
```

---

## 技术上真正关键的三件事（免得以后忘）

1. **原厂 bootloader 留着不动，只换 APP。** 它按 `0x01002000` 那条 40 字节镜像信息
   找 APP，校验方式是「APP 镜像逐字节求和取低 32 位」。这个公式是**反向验证过**的：
   拿出厂镜像按它算，bootloader 段得 `0x00173927`、APP 段得 `0x00CCE71B`，
   跟串口日志里原样打出来的两个数一字不差。
2. **屏是 SSD1680/1683 那一族**（用 EPD-nRF5 的命令表交叉验证过：`0x74`=ANALOG_BLOCK_CTRL、
   `0x7E`=DIGITAL_BLOCK_CTRL、`0x2B`=VCOM_CTRL）。引脚、初始化字节、缓冲区布局全部来自原厂固件。
3. **B1 的教训**：刷进去≠跑起来。那次的现象（写入 OK、一开始能连、几秒后调试口消失、屏不亮）
   指向平台 `soc_init()` 里的 `ultra_deep_sleep_wakeup_handle()` —— 它查 AON `SOFTWARE_1`
   低 16 位，等于 `0xF175` 就复位整个系统，而这个标志软复位不清，于是无限重启。
   详见 `firmware/README.md` 附录。

---

## 刻意没进库的东西

| 没进库 | 为什么 |
|---|---|
| `zk42v-factory-backup-run1.bin`（512KB 原厂固件） | 那是原厂的东西，不适合公开；需要就从本机 `outputs/pyocd/` 拿，SHA-256 记在 `docs/BACKUP-zk42v.md` |
| `zk42v-custom-512k.bin`（构建产物） | 跑一次 `firmware/build.sh` 就能重新生成，进库只会污染 diff |
| `app.asm` / `boot.asm`（2MB 反汇编） | 用固定命令就能从备份重新生成，命令记在 `docs/PANEL-zk42v.md` |
| `GR551x-SDK/` | 官方 SDK 另一个仓库，用相对引用就行 |

---

## 接下来（B2）

价签侧加 BLE（SDK 里 GATT server 全套都有，`ble_app_uart`/`ble_app_throughput` 可抄），
协议上对齐 `tsl0922/EPD-nRF5` 那套（`SET_PINS`/`INIT`/`WRITE_IMAGE`/`REFRESH`/`SLEEP`），
这样它现成的网页端（Web Bluetooth + 抖动 + RLE）就能直接往这块价签推图，不用等基站。

再往后：ESP32-CAM 那套（RTSP 取景/截帧）+ BLE 中枢 = 基站。
