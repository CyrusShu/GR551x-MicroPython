# ZK42V 价签（GR5513BEND）原厂固件备份记录

> **状态：完结。** 原厂固件已备份、已验真、写入路径已验通（2026-09-22）。
> 备份和芯片现在都是 `1dfab92f...cedb704`。下一步可以放心拿它做实验了。

## 备份文件

| 文件 | 说明 |
|---|---|
| `zk42v-factory-backup-run1.bin` | 第一次成功的整片读回，524288 字节（512 KB） |
| `zk42v-live-512k.bin` | 同一次读回的目标文件（内容一致，SHA-256 相同） |
| `zk42v-factory-512k.FAKE-single-value.bin.bak` | 早期失败产物：整片同一个死值，**不是备份**，留着当反例 |

    SHA-256: 1dfab92f558352642f5c477b22d1e66d12efc1be959d5e676ae27fe05cedb704

读回方式：`MANUAL=hold bash flash-lab4.sh`
（连着 SWD 不 halt，让 CPU 自己跑到 XIP 打开，窗口一变活立刻 halt 再整片读）
全程只读：只有 pyOCD 的读路径，没有一条擦/写 flash 的命令。

## 逐条验证（都可以离线重算）

1. 两个镜像头跟串口开机日志逐字节一致
   - `0x0000`：BinSize=0x00003B00 CheckSum=0x00173927 LoadAddr/RunAddr=0x01003000
   - `0x2004`：BinSize=0x0001F8F0 CheckSum=0x00CCE71B LoadAddr/RunAddr=0x0100A000
2. app 镜像字节累加 == 头部 CheckSum
   `sum(d[0xA000 : 0xA000+0x1F8F0]) & 0xFFFFFFFF == 0x00CCE71B`
   （同一算法验 boot 镜像：`sum(d[0x3000:0x3000+0x3B00]) == 0x00173927`）
3. 字符串在位：ZKC42V-N @0x57014、esl_mac @0x1DA1C、batt_volt @0x1B6C1、
   no AP @0x13818、hd_info @0x1BBAC
4. 布局：0x0 boot 头、0x2000 app 头、0x3000 boot 代码、0xA000 app 代码（到 0x298F0）
5. 每 64KB 的熵/填充分布正常（低 0x60000 有内容，0x60000 以上基本是 0xFF 空白）

## 怎么自己再验一遍

    cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
    shasum -a 256 zk42v-factory-backup-run1.bin
    python3 test-sanity.py          # 真 dump 必须 SANITY_OK，假数据必须 SANITY_FAIL

## 再读一次对校验（可选，验可复现性）

    cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
    DUMP_OUT=$PWD/zk42v-live-512k.run2.bin DUMP_RESTART=1 MANUAL=hold bash flash-lab4.sh
    shasum -a 256 zk42v-live-512k.run2.bin

两次 SHA-256 一样 = 读回稳定可复现。

## 写入路径验通记录（2026-09-22）

四步都留着日志，随时可以翻：

| 步骤 | 命令 | 结果 | 日志 |
|---|---|---|---|
| 1 只探不写 | `MODE=probe bash flash-write.sh` | `Init 返回 0`，`g_chip_spec_info[0].start = 0x01000000` | `flash-write-20260922-205607.log` |
| 2 试写一颗扇区 | `MODE=pagetest bash flash-write.sh` | 擦+写返回成功，但逐颗读回是死值（见下） | `flash-write-20260922-210731.log` |
| 2.5 干净重连看真身 | `MODE=peek bash flash-write.sh` | `跟试写图案一模一样 ✅` | `flash-write-20260922-211616.log` |
| 3 整片刷回 | `MODE=restore bash flash-write.sh` | `128 颗扇区全部做完`，175 秒 | `flash-write-20260922-211905.log` |
| 4 只读验收 | `MODE=verify bash flash-write.sh` | `VERIFY_OK`，524288 字节全对上 | `flash-write-20260922-212933.log` |

### 这颗芯片上两条容易踩的坑

**坑 1：`UnInit` 不会把 XIP 窗口交还回来。**
算法一 `Init`，QSPI 就进了它自己的命令模式；卸掉算法之后窗口**也不会**回来
（restore 那次 128 颗逐颗读回全是死值，就是实测证据）。所以「写完立刻读回比」
在这颗芯片上只能当参考。

判据怎么做：死值长这样 —— `00 34 C0 F7` 反复，即 `0xF7C03400`。
试写图案 `A5 AC B3 BA ...` 是周期 256、256 个值各出现 16 次，所以
**任何常数读回都必然恰好给 16/4096 对上、4080 个不一样**。看到 4080 这个数，
就等于看到「这是一整片常数」，跟写没写进去无关。

**坑 2：真验收只有「干净重连」这一条路。**
必须让芯片先正常启动、应用把 XIP 打开，再读。也就是：

    # 按 RST，等窗口活，再读
    MODE=peek bash flash-write.sh     # 看某一颗扇区
    MODE=verify bash flash-write.sh   # 整片跟备份逐字节比

两者都不装算法、不碰 QSPI，所以读到的是真数据。

### 刷写之后的旁证

刷完按 RST，串口打出完整开机日志，`check APP img valid.` + `Jump to APP FW.`
—— bootloader 自己拿 flash 里的 app 镜像算校验和、比对通过才跳转的。
`version` / `model` / `esl_mac` / `secret` / `nfc_uid` 跟刷之前一字不差。
