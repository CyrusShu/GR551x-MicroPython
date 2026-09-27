# ZK42V 价签（GR5513BEND）原厂固件备份步骤

日期：2026-09-18　适用：ZK42V_V1.1 / GR5513BEND / 512KB flash

## 0. 先明确"要备份的是什么"

来自官方 SDK（`GR551x-SDK/README.md` 与 `platform/soc/linker/gcc/gcc_linker_gr5513.lds`）：

| 项目 | 值 |
| --- | --- |
| 内置 Flash | **512 KB**（GR5513 系列，片内 programmable Flash，不是外部 QSPI 片） |
| Flash 地址窗口 | `0x01000000` ~ `0x0107FFFF`（XIP 映射窗口本身更大，实际只用到 512KB） |
| 应用运行地址 | `0x01002000`（`APP_CODE_RUN_ADDR`） |
| 应用校验头 | `0x01002200`（`BUILD_IN_APP_INFO`，magic `0x47525858` = "XXRG"） |
| NVDS 扇区 | `0x0107F000`（最后一个 4KB 扇区，放 BLE 协议栈数据、配对信息、校准值） |
| RAM | 128 KB（`0x30004000` ~ `0x3001FFFF`） |

所以**备份 = 把 `0x01000000` 起 512KB 整片读成一个 .bin**。不要只读 `0x01002000` 之后的应用区，
前面 8KB 和最后 4KB 一样可能含必要信息。

## 1. 【首选】ST-Link V2 + OpenOCD（Mac / Windows 都能跑）

现成的配置和脚本在 `outputs/openocd/`，见其中的 `README.md`。总结：

接线（蓝色克隆版 ST-Link V2 的 10 针排针）：

| ST-Link | 价签 | |
| --- | --- | --- |
| GND | GND | 必接 |
| SWCLK | SWCLK | 必接 |
| SWDIO | SWDIO | 必接 |
| RST | RST | 可选（不接也能读，只是少一层兜底，详见 `openocd/README.md`） |
| 3.3V | VCC | **先拆 CR2450 电池**，用 ST-Link 供电 |

跑：

```bash
openocd -f /Users/mac/Documents/Codex/2026-09-15/a/outputs/openocd/gr551x-stlink.cfg
```

会自动判断"直接读"还是"复位后读"，并落盘 `zk42v-factory-512k.bin`（524288 字节）和 `zk42v-nvds-4k.bin`。
Windows 侧用 `outputs/openocd/run-windows.bat`（需先下载 xPack OpenOCD for Windows）。

原理上的三个坑，脚本里已经处理：

1. ST-Link V2（非 V2-1）不支持 `dapdirect_swd`，只能走 `hla_swd`；
2. GR5513 的片内 flash 是经 XQSPI 映射到 `0x01000000` 的，必须等 ROM 的 BL0/BL1 把 XQSPI 配好才能读到内容，
   所以脚本会在读不到时自动改成"复位 → 运行 500ms → 再读"；
3. RST 焊盘不接也能备份（SWD 本身不需要复位线）。脚本的兜底顺序是
   **软件复位（写 AIRCR，只走 SWD）→ SRST 硬复位（要接 RST）**，
   所以没接 RST 时第一次兜底仍然有效。

## 2. 备选：GProgrammer（Windows 官方工具）

汇顶官方的 GProgrammer 支持两种连接：**J-Link(SWD)** 和 **UART(ROM 下载模式)**。
备份请优先用 SWD，因为它能读整片 flash；UART 模式需要芯片进 ROM bootloader，在价签上不一定能触发。

步骤：

1. 接线：`SWCLK / SWDIO / RST / GND / VCC` 接到 J-Link。
2. 打开 GProgrammer → 连接设置选 J-Link → 连接。
3. 切到 **读取（Read）**：
   - 起始地址 `0x01000000`
   - 长度 `512 KB`（`0x80000`）
   - 保存为 `zk42v-factory-01000000-512k.bin`
4. **只点"读取"，不要点"擦除"或"下载"。**
5. 再读一遍存成第二个文件，两个文件 MD5 一致才可信。
6. 顺手用 GProgrammer 的 eFuse 页面**读取**安全位状态（是否有 secure boot / 读保护），同样只看不写。

命令行版（`GR5xxx_console.exe` 不带参数会打印用法，按它给的参数顺序填）：

```
GR5xxx_console.exe read zk42v-factory.bin 0x01000000 0x80000
```

（具体子命令名以 `GR5xxx_console.exe -h` 的输出为准。）

## 3. 备选：J-Link Commander

SEGGER 的 JLinkExe 有 macOS 版。GR5513 用通用 `Cortex-M4` 设备即可。

新建 `zk42v-dump.jlink`：

```
si SWD
speed 4000
device Cortex-M4
connect
r
go
sleep 500
halt
savebin /Users/mac/Documents/Codex/2026-09-15/a/outputs/zk42v-factory-512k.bin 0x01000000 0x80000
exit
```

执行：

```
/Applications/SEGGER/JLink/JLinkExe -CommanderScript zk42v-dump.jlink
```

要点：**先 `go` 让它跑一下再 `halt`**。GR551x 的 flash 是 XIP（内存映射）方式访问，
必须等 ROM 里的 BL0/BL1 把 XQSPI 配好之后，`0x01000000` 才能被 SWD 正常读出内容。
如果直接在复位瞬间读，会读到全 0 或触发总线错误。

## 4. 备选：DAPLink / 树莓派 Pico

把 Pico 刷成 `debugprobe` 或买一个 DAPLink，用 OpenOCD：

```
openocd -f interface/cmsis-dap.cfg -f target/cortex-m4.cfg \
  -c "init; reset run; sleep 500; halt; dump_image zk42v-factory-512k.bin 0x01000000 0x80000; shutdown"
```

## 5. 读出来之后怎么判断"备份成功"

```bash
ls -l outputs/zk42v-factory-512k.bin      # 应该是 524288 字节
md5 outputs/zk42v-factory-512k.bin        # 两次读取对比
xxd -l 32 outputs/zk42v-factory-512k.bin  # 开头不应是全 0 或全 FF
xxd -s 0x2200 -l 32 outputs/zk42v-factory-512k.bin
```

- `0x2200` 处如果是 `58 58 52 47`（`0x47525858`，"XXRG"），说明原厂应用镜像头完整读到了。
- 如果整片都是 `0xFF` / `0x00`，说明被读保护（flash security）挡住了，或者读得太早（XQSPI 没配好）。
- 存三份：本机、云盘、U 盘。这块板子一旦擦掉，没有第二来源可以恢复（ZKONG 不公开固件）。

## 6. 以后要还原

用同一工具把整片写回去：

```
# GProgrammer / J-Link 均先把 0x01000000 起 512KB 擦除，再写入 zk42v-factory-512k.bin
loadbin /Users/mac/.../zk42v-factory-512k.bin 0x01000000
```

注意：**eFuse 不在这 512KB 里，也不能靠这份备份还原**。所以从始至终不要写 eFuse。

## 7. 三个必须知道的风险

1. **可能有读保护（flash security）**。汇顶 SDK 里有
   `dfu_flash_get_security()` / `dfu_flash_set_security()`，原厂可以打开它。
   打开后 SWD 读 flash 会拿到全 0 或全 FF，备份就没意义了。GProgrammer 连上后能看出当前状态。
2. **可能有 secure boot（eFuse 里存密钥）**。如果原厂启用了签名校验，你**根本无法把自编固件烧进去**，
   这时候 GR5513 逆向路线直接作废，只能走"另买 OEPL 支持的标签"那条路。所以第二步（读 eFuse 状态）比读 flash 还关键。
3. **NVDS 扇区含每台设备独有的数据**（BLE MAC、校准等）。备份后即使之后刷了 MicroPython，
   这个扇区可以单独还原回来，或者把它读出来当参考。

## 8. 下一步

拿到 dump 之后我可以帮你：
- 检查 `0x2200` 的 app_info，确认原厂应用的实际大小和加载地址；
- 在原厂固件里搜墨水屏驱动 IC 的初始化序列（找 SPI 命令表、`0x11`/`0x12`/LUT 之类的特征字节），
  这样以后自己写 MicroPython 的屏驱动就有参考；
- 找 BLE 服务 UUID / 广播名，判断原厂是怎么被网关唤醒的。

把 `zk42v-factory-512k.bin` 放到 `outputs/` 下就行。
