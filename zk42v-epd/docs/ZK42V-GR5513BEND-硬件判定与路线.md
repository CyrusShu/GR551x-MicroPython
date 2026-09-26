# ZK42V 电子价签（GOODIX GR5513BEND）硬件判定与可行路线

判定日期：2026-09-17
最后更新：2026-09-18
依据照片：/Users/mac/Downloads/IMG_2891.JPG（板子实拍，文字倒置 180°）

## 1. 硬件判读结果

| 项目 | 读到的内容 |
| --- | --- |
| PCB 丝印 | `ZK42V_V1.1`、`3.08.01.0294`（ZKONG 智控 4.2 寸价签） |
| 主控 | **GOODIX（汇顶）GR5513BEND**（用户现场核对芯片丝印为准），批次 `2110-0019` / `PV2N27.00` |
| 主控内核 | ARM Cortex-M4F + BLE 5.1 SoC（汇顶 GR551x 系列） |
| 调试/烧录焊盘 | `GND / IO / SWCLK / SWDIO / UTX / RST` + `VCC / URX` |
| 调试接口性质 | **SWCLK+SWDIO = ARM SWD**（不是 Telink 的 SWS 单线） |
| 屏接口测试点 | `CS / MOSI / BUSY / CLK / MISO / NRES`、`PREVGH / PREVGL`、24-pin FPC |
| 供电 | 2× CR2450 电池夹（J2/J3） |
| 射频 | ANT1 / ANT2 标注 |

结论：**这是一块 ARM SWD 调试的 Goodix GR5513BEND 标签**，既不是 Telink TLSR8359，也不是 Nordic nRF。

> 说明：早期从照片误读成 `GR5515IENDE`，用户核对实物丝印为 **GR5513BEND**。
> 对应 SDK 宏 `CHIP_TYPE = 6`（见 SDK `build/config/custom_config.h`）。两者的区别只在
> RAM 容量与 NVDS 地址，外设/内核（Cortex-M4F + BLE）一致，不影响软件路线。

| 芯片 | CHIP_TYPE | RAM | 内置 Flash | NVDS 起始 |
| --- | --- | --- | --- | --- |
| GR5513BEND（本板） | 6 | 128 KB（可用 112 KB） | 512 KB | 0x0107F000 |
| GR5513BENDU | 7 | 128 KB（可用 112 KB） | 512 KB | 0x0107F000 |

## 2. 为什么开源生态都不认它

1. **tsl0922/EPD-nRF5（网页 BLE 推送那套）** 只支持 `nrf51822 / nrf51802 / nrf52811 / nrf52810`，GR5513 不在列表里，驱动不了。
2. **OpenEPaperLink 官方不支持**。issue #385 "ZKong/Goodix GR51xx based tags" 里，维护者 atc1441 于 2024-10-26 明确回复：
   > "Hey. These are not supported or looked into so far"
   且该项目政策是"只做自己刷固件的自定义方案，不碰原厂固件/协议"，所以必须整块重新烧固件，不能靠 sniff 原厂协议接管。
3. OEPL 的标签侧固件只覆盖 Solum M2（ZBS243/SEM9110）、M3（nRF52811 / EFR32）、CC1x10、88MZ100 等平台，**没有任何 GR551x 标签固件**。
4. OEPL 的基站固件（2.92，2026-09-10）只有在 ESP32-S3 / ESP32-C6 / TLSR 上跑，**经典 ESP32（含 ESP32-CAM）不在支持范围**。

## 3. 三条可选路线

### A. 先跑通基站（推荐）
- 硬件：**ESP32-S3 N16R8**（16MB Flash / 8MB PSRAM）+ **ESP32-C6** 模块 + 8 根杜邦线。
- 烧写：S3 用 `install.openepaperlink.de` 选 **Yellow AP** 刷；C6 随后在 AP 后台 OTA 刷协处理器固件。
- 接线：Yellow AP 方案下 ESP32-S3 的 GPIO17/18 与 ESP32-C6 的 GPIO2/3 交叉相连，详见 OEPL wiki `Access-point-pinouts`。
- 标签：另买 OEPL 支持的型号（Solum M2 4.2" 三色，或 nRF52811 的 M3 2.2/2.9"）。
- 现有的 ESP32-CAM 只能当验证工具链/板子能否跑通的玩具。

### B. 保留手上这块 GR5513BEND 标签（逆向工程路线）
1. 工具：汇顶官方 **GProgrammer**（PC 端，支持固件下载、Flash 读写、eFuse；Windows），配套 **GR551x SDK**（开源：`goodix-ble/GR551x.SDK`，含 Keil/IAR/GCC 工程与 drivers）。
2. 接法二选一：**ARM SWD**（SWCLK/SWDIO + RST，J-Link 或 DAPLink）或 **UART ROM bootloader**（板上有 UTX/URX，可用 USB-TTL 直连）。
3. 第一步先只做只读操作：读出原厂 Flash dump 和 Flash ID，确认芯片型号/容量与安全位状态，再决定是否擦写。
4. 难点：需要自己逆出屏的 SPI 初始化时序（面板驱动 IC 未知，需从屏排线/原固件里推）、按键/电池/射频板级配置，并写 BLE 服务。没有任何现成参考固件，属于长期项目。
5. 可参考的现成基础：`goodix-ble/GR551x-MicroPython`（GR551x 上的 MicroPython 移植，能在芯片上跑 BLE）。

### C. 只要图 1 那种"手机 BLE 网页推待办"的效果
- 买 nRF 系价签（如 Laowu 4.2" 三色：nrf51802 + UC8176，Pin Config `0A0B0C0D0E0F10`、Wakeup `09`、LED `03/04/05`），配合 `tsl0922/EPD-nRF5` + 网页工具，当天就能出效果。
- 代价：与 OEPL 基站体系不是同一套固件，不能混用，除非另外刷 OEPL 支持的固件。

## 4. 关联交付物
- `GR551x-MicroPython/ports/gr55xx/`：已改用汇顶**最新官方 SDK**（`goodix-ble/GR551x.SDK`）编译通过，支持 `make CHIP=gr5515` / `make CHIP=gr5513` 双芯片；详见该目录 `README.md`。
- `GR551x-SDK/`：克隆的汇顶官方最新 SDK（编译所需）。
- `outputs/ESP32CAM_SelfCheck/ESP32CAM_SelfCheck.ino`：ESP32-CAM 串口自检（已编译通过并烧写，板上闪光灯在闪）。
- `outputs/esp32cam-3x/CameraWebServer3x/CameraWebServer3x.ino`：适配 esp32 core 3.x 的摄像头 Web 服务器示例（编译通过）。
- `outputs/ZKONG墨水屏-ESP32CAM-对话记录.md`：从 Gemini 会话搬运的完整对话记录。

## 5. GR5513BEND 上的 MicroPython 进展（2026-09-18）

用官方最新 SDK 重编 `GR551x-MicroPython` 已成功，并在 `ports/gr55xx/Makefile` 里加了双芯片支持：

```
cd GR551x-MicroPython/ports/gr55xx
make CHIP=gr5513            # GR5513BEND (CHIP_TYPE=6), RAM 128KB
make CHIP=gr5515            # GR5515RGBD (CHIP_TYPE=4), RAM 256KB
```

验证结果：

| 项目 | GR5513BEND | GR5515 |
| --- | --- | --- |
| 链接脚本 | `gcc_linker_gr5513.lds` | `gcc_linker_gr5515.lds` |
| RAM 区 | `0x30004000`，长度 `0x1C000`（112KB） | `0x30004000`，长度 `0x3C000`（240KB） |
| 复位后栈顶(SP) | `0x30020000` | `0x30040000` |
| 镜像 | `build-5513/gr5513.bin`，327168 字节 | `build-5515/gr5515.bin`，327168 字节 |
| 占用 | text 327096 / data 72 / bss 67056 | 同左 |
| NVDS 起始 | `0x0107F000` | `0x010FF000` |

RAM 账（GR5513 可用 112KB）：`.data` 约 17KB + `.bss` 约 67KB + 8KB 栈 ≈ 92KB，余约 20KB。
Flash 512KB：镜像约 327KB，`0x01052000 ~ 0x0107E000` 约 176KB 空闲，可规划为文件系统区。

已可用：UART0 REPL（TX=P10 / RX=P11，115200 8N1）、`board.LED(1/2)`、`machine.Timer`、`utime`、启动 LED 自检。
暂未启用：文件系统（xflash 会覆盖应用自身，已关）、BLE 绑定（旧端口用 SDK V1 回调模型，需改写为新 SDK 事件模型）。

烧写：Windows 用汇顶 **GProgrammer** 的 `GR5xxx_console.exe program build-5513/gr5513.bin 'y' 0x200000 512 0 0`；或用 J-Link/DAPLink 通过 SWD 写 `0x01002000`。
