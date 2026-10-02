# 把原厂固件刷回去（写 flash）

> **2026-09-22 实测结果：四步全通。** 最后 `MODE=verify` 打出
> `VERIFY_OK`，芯片里 524288 字节跟备份 SHA-256 完全一致
> （`1dfab92f...cedb704`），刷完芯片能正常启动、串口日志完好。
> 详见 `BACKUP-zk42v.md` 的「写入路径验通记录」。

## 先说清楚一件事

动手写之前，这块价签的 flash 一次都没被改过：dump / flashlab2 / flashlab4 走的全是
SWD 的读路径，一条擦除或写入命令都没发过（flashlab2 路 3 那次「CPU 搬运」只写了
RAM）。所以不管后面怎么写，手里都有一份真正干净的原厂镜像。

所以当初「刷回出厂固件」严格说不成立 —— 它还在里面。

真正值得做的是**把写入路径验通**：拿备份原样写进去，再读回来对 SHA-256。
内容一模一样，所以哪怕写失败最坏也只是「需要再写一次」，原厂固件不会因此更危险。
**这一步 2026-09-22 已经做完并验通**，下面这套流程留着，以后要往这颗芯片写
自己的固件时照用。

## 写入用的是什么

不让脚本去手戳 QSPI 寄存器，而是用 Goodix 自己那份 CMSIS-Pack 算法：

    /Users/mac/Documents/Codex/2026-09-15/a/GR551x-SDK/build/keil/GR5xxx_16MB_Flash.FLM

pyOCD 能直接吃 FLM。QSPI 时序、XIP/缓存、各芯片（BALBOA / BALI / CAIRO）的初始化
都在算法里面，比自己搓稳得多。

**地址语义**（从 FLM 反汇编确认的）：算法内部干的是

    offset = addr - g_chip_spec_info[0].start      // 低 24 位进 QSPI 命令

`g_chip_spec_info` 是算法 Init 时按芯片型号填好的一张表，5 组 {start, size}：

    第 1 段  0x01000000 + 0x800000    XIP 窗口，8MB  ← 算偏移用的就是这一段
    第 2 段  0x30000000 + 0x40000     别名窗口
    第 3 段  0x00800000 + 0x40000     RAM 区
    第 4/5 段 结束标记

（`EraseSector` 只在「地址落在第 1 段」时才动手，`ProgramPage` 同理。）

注意别被 `fw_start_addr` 那个符号误导：它是给 DFU 镜像那套机制用的，这个模式下
实机读出来就是 0，拿它当判据会一路误报。真正的基地址是 `g_chip_spec_info[0].start`。

工具为此加了一道**硬联锁**：Init 之后把这两个数（start 和 size）从算法的 RAM 里
读回来，`start != FW_BASE`、或者 `FW_BASE + FW_TOTAL` 超出这段范围，就直接拒绝写。

实机 probe 结果（2026-09-22）：

    Init(addr=0x01000000, op=擦除) 返回 0
    g_chip_spec_info[0].start = 0x01000000      <- 跟 FW_BASE 一致
    第 1 段 : start=0x01000000  size=0x800000

FLM 头里写的 `start=0x00100000 / size=16MB` 是给 IDE 看的占位值，不是芯片上的真实映射。

## 四步走，别跳步

    cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd

**第 1 步 · 只探不写（默认）**

    MODE=probe bash flash-write.sh

看三样东西：

1. `Init(...) 返回 0` —— 算法认了这颗芯片、认了这个地址
2. `g_chip_spec_info[0].start = 0x01000000` —— 跟 `FW_BASE` 一致
3. 「算法认的地址范围」那几行里，第一段是 `0x01000000 + 0x80000`

这三样都对，才往下走。全程没擦没写。

**第 2 步 · 拿一颗空白扇区真写一次**

    MODE=pagetest bash flash-write.sh

它会挑 0x0107F000 这颗扇区（备份里这一段是 0xFF 空白）走一遍
`擦 -> 写 -> 读回比对`。

**这一步的「读回」只能当参考，别当结论。** 原因：算法一 `Init`，QSPI 就进了它
自己的命令模式，这会儿从 flash 窗口读回来的是一个**死值**（实机上量到的是
`00 34 C0 F7` 反复，也就是 `0xF7C03400`），看上去像「写了 4080 个字节不一样」，
其实只是读不到。

为什么能一眼识破：试写的图案是 `A5 AC B3 BA ...`，256 个值各出现 16 次，
所以**任何常数读回都恰好只有 16 个字节对得上、4080 个不一样** —— 看到这个
数字，就说明读回的是一整片常数，不是真数据。

而且别指望「算法卸掉之后窗口就回来了」：**实测这颗芯片上 UnInit 并不把 XIP
窗口交还回来**（2026-09-22 那次 128 颗扇区的 restore，逐颗读回全是死值，就是
证据）。所以任何「写完立刻读回比」在这颗芯片上都只能算参考。

**第 2.5 步 · 干净重连，直接看那颗扇区的真身**

    # 先把价签的 RST 碰一下 GND 再松开（应用一起就会把 XIP 打开）
    MODE=peek bash flash-write.sh

这一步不装算法、不碰 QSPI，等窗口活了才读，所以读到的才是真数据。它的判据：

| peek 看到 | 意思 | 下一步 |
|---|---|---|
| `跟试写图案一模一样 ✅` | 擦 + 写这条路是通的 | 往下走 `MODE=restore` |
| 整颗都是 `0xFF` | 擦掉了但没写进去 | 先别 restore，把输出发我 |
| 整颗同一个值（如 `0xF7C03400`） | 这次窗口没活 | 再按一次 RST，重跑 peek |
| 有内容但不是图案 | 意外 | 把 hexdump 发我 |

**第 3 步 · 原样刷回去**

    MODE=restore bash flash-write.sh

128 颗扇区，**从高地址往低地址**一颗一颗做（擦完立刻写、写完立刻读回比对）。
这个顺序是故意的：万一中途出错，低地址那段（含 bootloader）还没动，芯片还能从
flash 启动，重跑一次接着做就行。

动手前它还会先拿参照点验一遍备份文件本身（boot/app 镜像头 + app 镜像校验和 +
字符串），这份文件不合规就拒刷。

**第 4 步 · 只读验收**

    # 先把价签的 RST 碰一下 GND 再松开
    MODE=verify bash flash-write.sh

它复位后等 XIP 打开、整片读回来跟备份逐字节比。打出

    >>> 逐字节一样（524288 字节全部对上） VERIFY_OK

才算收工。同时看一眼串口有没有正常开机日志、灯有没有按
「绿 1 秒 -> 蓝 1 秒 -> 红」走一遍。

## 备选：完全不动 flash 的验收

只想确认原厂固件还在、还能跑，不需要任何写入：

    bash verify-backup.sh          # 备份文件本身 + SHA-256
    MANUAL=hold bash flash-lab4.sh # 再读一遍，跟备份比 SHA-256

只读那条路已经验证过了：`SHA-256 1dfab92f...cedb704`。

## 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `MODE` | `probe` | probe / pagetest / peek / restore / verify |
| `ADDR` / `LEN` | `0x0107F000` / `0x1000` | peek 看哪儿、看多少 |
| `FW_FILE` | `zk42v-factory-backup-run1.bin` | 要写的镜像 |
| `FW_BASE` / `FW_TOTAL` | `0x01000000` / `0x80000` | 写哪儿、写多少 |
| `FLM` | SDK 里那份 Keil FLM | 换算法 |
| `FORCE` | `0` | 联锁没过时硬写（不建议） |
| `FW_SKIP_SANITY` | `0` | 备份没过真伪校验时也刷（不建议） |
| `ALGO_RAM` | `0x00818000` | 算法在 RAM 里落脚的位置 |

## 离线自测

    python3 test-flashwrite.py

用假 pyOCD + 假芯片跑，覆盖：联锁拦住错误基地址、扇区顺序是高到低、中途失败时
低地址段不动、备份坏了拒刷、verify 能抓出被改动的字节。
