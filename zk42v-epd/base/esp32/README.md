# ESP32 基站固件（build-1）—— 让 ESP32 替掉 Mac 当基站

> 2026-09-30 · 配套固件：`zk_base_esp32/zk_base_esp32.ino`
> 协议跟 Mac 那版 `outputs/ble-base/zk_ble_base.py` **完全一致**，两边可以随时互相替代。

## 0. 为什么最后没走 OpenEPaperLink 那条路

查证过 2.92（2026-09-10）的发布资产和官方 wiki，结论是 **Spaghetti AP 那套跟我们的价签不在一层**：

| 事实 | 出处 |
|---|---|
| AP 固件只有 `ESP32_S3_16_8_*`（Yellow/4inch/LILYGO/C6_NANO）、PoE、`BLE_ONLY_AP` 这些镜像；**没有经典 ESP32（我们那块 ESP32-CAM）** | `https://api.github.com/repos/OpenEPaperLink/OpenEPaperLink/releases` 里 2.92 的 35 个资产 |
| wiki 原文："Release **2.75** was the last release that supported ESP32-S2 based APs or ESP32-S3 APs with less than 16MB flash and 8MB RAM." | wiki `Home.md` |
| OEPL 价签固件只有这些芯片：ZBS243/SEM9110、**nRF52811**、EFR32xG22、88MZ100、CC1x10、Chroma、Opticon —— **全是 802.15.4 或 SubGHz** | GitHub 组织 `OpenEPaperLink` 的 `Tag_FW_*` 仓库列表 |
| 我们这块 ZK42V 是 **Goodix GR5513B：纯 BLE 5.1，没有 802.15.4 射频** | `outputs/ZK42V-GR5513BEND-硬件判定与路线.md` + 本目录 2.1 节实测 |

所以：**OEPL 要用就得换价签**（或给 GR5513 重写一套 OEPL 的 BLE 协议，属于探路）。
我们价签侧已经全部做完（ble 推图 + 自绘月历 + 天气），**只差一个常开的基站** —— 那就用 ESP32 自己写，
协议照搬 Mac 那版即可。ESP32-CAM 因此是"验证工具链"的角色，正好够用。

## 1. 编译验证（本机实测，不是估的）

用你机器上已经装好的 esp32 core 3.3.11（`~/Library/Arduino15`）编译：

```bash
CLI="/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"

# ① ESP32-CAM（AI-Thinker，经典 ESP32）—— 验证板
"$CLI" compile --fqbn esp32:esp32:esp32cam \
  --build-path /tmp/zkbuild \
  /Users/mac/Documents/Codex/2026-09-15/a/outputs/ble-base/esp32/zk_base_esp32

# ② ESP32-S3（以后常驻的部署板；N16R8 = 16MB flash + 8MB OPI PSRAM）
"$CLI" compile --fqbn "esp32:esp32:esp32s3:FlashSize=16M,PartitionScheme=huge_app,PSRAM=opi" \
  --build-path /tmp/zkbuild_s3 \
  /Users/mac/Documents/Codex/2026-09-15/a/outputs/ble-base/esp32/zk_base_esp32
```

| 目标 | app 大小 | 占分区 | RAM | 结果 |
|---|---|---|---|---|
| `esp32:esp32:esp32cam` | **1,791,659 B (1.71 MB)** | 56%（分区 3 MB） | 64,156 B (19%) | ✅ 通过 |
| `esp32:esp32:esp32s3`（默认 4MB 分区表） | 1,247,596 B (1.19 MB) | **95%**（分区 1.25 MB） | 48,252 B (14%) | ⚠️ 能编但太挤，别用这个配置 |
| `esp32:esp32:esp32s3` **N16R8 实际配置**（16M + `huge_app` 3MB 分区 + OPI PSRAM） | **1,252,902 B (1.19 MB)** | **39%**（分区 3 MB） | 48,720 B (14%) | ✅ 推荐 |

> 尺寸是**按目标平台**差很多的：同一份源码在 CAM（经典 ESP32）上是 1.79MB，在 S3 上只要 1.25MB ——
> 差别来自各自的 WiFi/PHY 预编译库（`libnet80211.a`/`libpp.a`/`libwpa_supplicant.a` 在 ESP32 上要胖一圈）。
> 所以换板子一定要重量，别把 CAM 的数字套到 S3 上。
>
> 另外记一笔翻车经历：我在 12:08 那版量到过 **1,187,571 B（37%）**，之后用 4 个全新目录重编都复现不出来
> （稳定在 1.79MB）。那一版也正是实机上 HTTPS 报 `HTTP -1` 的那一版 —— 怀疑那次链接不完整
> （`nm` 对比显示它少了 mbedTLS 和一部分 WiFi 关联代码）。**结论：别信单次测得的体积，要连编两次对比**；
> 现在文档里这三个数字都是连编两次一致的值。

编译产物（可以直接烧，不用自己编）：

| 文件 | 大小 | SHA-256（前 16 位） |
|---|---|---|
| `build/zk_base_esp32.ino.merged.bin` | 4,194,304 B | `2928ef3d2984fc55` |
| `build/zk_base_esp32.ino.bin`（仅 app） | 1,791,808 B | `721b68ba1b5b99ad` |

### 1.4 实机验收通过（2026-09-30 13:0x）

配合价签固件 **build 47**（激活重试，见 `outputs/firmware/docs/feature-backlog.md`），
第一次看到**屏真的刷新**：

```
[BASE 61s] 天气接口通了（HTTP，427 字节）
[BASE 61s] 时间（来自 HTTP Date 头）= UTC 1790744426 → 屏上应显示 2026-09-30 13:00:26
[BASE 61s] 天气取好了（22.7809,113.8861）：晴 35℃  （WMO=0 原始温度=34.6）
[BASE 138s] → 时间 UTC 1790744426 + 时区8 → 屏上应显示 2026-09-30 13:00:26  载荷=20 6A BC 97 6A 08 01
        <- 价签: "t=1790773226"
[BASE 138s] → 天气 晴 35℃  载荷=71 01 23
        <- 价签: "wx=1 t=35"
```

用户确认：**屏刷了一次、温度变成 35℃** ✅（这条链路 = 屏驱动 + BLE + 天气 + 校时 全通）。

> 同一个坑的另一半在价签那边：`epd_pins_release()` 每次刷完会把 `P1_8(AUX)`（屏供电）也松开，
> 冷启动的第一次激活会被屏忽略 —— 只发一条命令时屏永远不变。价签 build 47 加了"激活重试"，
> 两边一起才通。**所以这个功能是"价签 build 47 + 基站 build-4"一对，缺一边都不行。**

> 横幅版本号这次也一起改了（上一份固件代码已经是 build-4，但 `setup()` 里还印着 build-3，
> 对日志容易搞混）。**教训：改代码时连版本号一起改。**

结论：**ESP32-CAM 的 4MB flash 跑我们自己的固件绰绰有余**（它跑不了 OEPL 的 1.90MB AP + 15.94MB 整包，
那是 16MB flash 的板子才装得下）。

> 关于哈希：ESP32 的固件**不是逐字节可复现的**。同一个 .ino 重编译一次，大小一模一样，
> 但会有几十个字节不同，集中在 app 描述符（偏移 0xB0 附近，`app_elf_sha256`）和末尾的整体校验上 ——
> 原因就是 ESP-IDF 把**编译时间**写进了 app 描述符，哈希跟着连锁变化。所以哈希只能当"这一份产物"的标识，
> 不能当"源码对账"用；要对账就看 `Sketch uses ... bytes` 和 RAM 这两个数字。

### 1.1 build-2 相对 build-1 的改动（2026-09-30 12:05，实机日志驱动）

第一版在实机上跑完，BLE 全通（见第 4 节），但暴露出三点，已经修掉：

1. **没有新数据就不连接了**：原来每轮扫描（6 秒扫描 + 3 秒歇，约 9 秒一轮）都会连上去一次，
   然后发现没事可做又断开。现在连之前先算 `needTimeSyncNow() || weatherChangedNow()`，
   没有新数据就只打一行（5 分钟才提一次）。开机后仍保证**至少连一次**（验证链路用）。
2. **消息不再说反**：以前哪怕一条命令都没写，也会说"价签这时在刷屏"。
   现在只在真的写了命令时才这么说，否则打"本轮没写任何命令，价签不会重画"。
3. **区分"没填 WiFi"和"联网失败"**：没填 SSID 时明确提示"这块现在只能当蓝牙验证用"，
   不再和"HTTP 失败"混在一句话里。

### 1.2 build-3 的改动（2026-09-30 12:2x，也是实机日志驱动）

你在实机上跑出来的这行是关键证据：`取天气失败：HTTP -1`（`wifi=OK`、`rssi=-86`）。
同一时刻在 Mac 上（同一个网络）测：`api.open-meteo.com` 的 **HTTPS 200（1.2s）**，
**纯 HTTP 80 也是 200 且不跳转**。所以不是家里网络的问题，是 ESP32 这一侧连不上。

1. **天气改走纯 HTTP（`WX_USE_TLS 0`）**：ESP32 上 HTTPS 要在 WiFi + BLE 之外再挤 ~45KB 连续堆做
   mbedTLS 握手 —— 你那次日志里 `heap=67744`，本来就紧，加上信号只有 -86dBm，握手基本必失败。
   `api.open-meteo.com` 的 80 端口实测直接 200（上面这两条），天气是公开数据，走明文没有代价。
   想改回 TLS：编译时加 `-DWX_USE_TLS=1`（或用下面的 `--build-property`）。
2. **失败时打分层诊断**（`dumpNetDiag()`）：IP/网关/DNS、`WiFi.hostByName()` 查询结果、堆的 free 与
   最大连续块 —— 下次再失败，一眼能看出是 DNS、路由还是内存。
3. **重试 + 退避**：一次失败重试 3 遍（间隔 800ms），整体再失败就退避 60 秒才重试，
   不再每 10 秒撞一次（原来会刷屏，还费电）。

编译命令（默认就是 build-3 的行为）：

```bash
CLI="/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"
SK=/Users/mac/Documents/Codex/2026-09-15/a/outputs/ble-base/esp32/zk_base_esp32

# 要临时改回 HTTPS：
# "$CLI" compile --build-property "compiler.cpp.extra_flags=-DWX_USE_TLS=1" ...
```

### 1.3 build-4 的两个修正（2026-09-30 12:5x，同样由实机日志驱动）

**① 温度被解析成 0℃（真 bug，已修）**

实机日志：`天气取好了（22.7809,113.8861）：晴 0℃（WMO=0 原始温度=0.0）`，
价签回 `wx=1 t=0`，status 调试块也写着 `天气（手机下发）：晴  天气温度 0℃`。

原因：Open-Meteo 的返回里 `"temperature_2m"` / `"weather_code"` **各出现两次** ——
先出现在 `current_units`（值是 `"°C"` / `"wmo code"` 这种**字符串**），
后出现的才是 `current` 里的真值。原来用 `indexOf` 取第一个，`toFloat()` 解析字符串得 0。
（`weather_code` 也一样中招，只是今天恰好真值也是 0，所以只暴露了温度。）

修法：新增 `findJsonNumber()` —— 要求冒号后面**紧跟数字**，是字符串就跳过继续找。
离线拿真实返回验证过：旧逻辑 `"weather_code" -> '"wmo code"'`、`'"temperature_2m" -> "\\u00b0C"'`；
新逻辑 `-> 0`、`-> 33.6` ✅

**② WiFi 关联被自己打断（已修）**

实机日志：连了 4 次才连上（`状态 4` → `状态 6` → `状态 6` → 成功，耗时 ~100 秒），
中间夹着 `E wifi:sta is connecting, cannot set config` —— 那是关联还在进行时又喊了一遍 `WiFi.begin()`。

修法：加个状态机，**30 秒内只 `WiFi.begin()` 一次**；每轮最多等 8 秒就先回去扫 BLE，下一轮接着等
（关联在后台继续）。这台 CAM 的 WiFi 只有 -70~-86 dBm，本来就慢，不能再自己捣乱。

> 教训（给我自己）：改完一定要**先编译再发**。这两个改动我改了没编，直接把源码交给你，
> 结果你 IDE 里报 `'wifiStarted' was not declared` —— 补上声明后才编过（1,791,659 B）。

## 2. 它什么时候干什么

```
开机 → 连 WiFi（2.4G）→ 取 Open-Meteo（顺便从 HTTP Date 头拿 UTC 时间）
     → 扫 BLE 找 "ZK42V-EPD"（6 秒一轮，每轮之间歇 3 秒）
     → 扫到就连上去：
          ① 写 0x20 <UTC秒> <时区> <模式=1日历>   （刚上电/超过 1 天没校时，才发）
          ② 写 0x71 <天气码> <温度>               （**值变了才发**，省一次 16 秒全刷）
          ③ 停 1.5 秒收价签的通知（t= / wx=），断开
     → 价签自己画整页农历月历并刷屏
```

关键设计（跟 Mac 版一致，理由见 `../README.md` 第 4 节）：

- **每条命令都会让价签整页重画一次（约 16 秒）**，所以时间按"重新出现 + 每天一次"发，天气按"值变了"发。
- **时间用 HTTP 响应的 `Date:` 头**，不用 NTP —— 家里那条代理链路 UDP 123 不一定通，80/443 是实测通的。
- WiFi 与 BLE 共用一个 2.4G 射频（ESP32 是分时的），所以流程上"先联网取数据、再扫 BLE"，
  避免一边下载一边扫描丢广播包。

## 3. 怎么烧（ESP32-CAM）

### 3.1 先改两行配置

打开 `zk_base_esp32/zk_base_esp32.ino`，改最上面的：

```c
#define WIFI_SSID       ""      // ← 填家里 2.4G 的 SSID（ESP32 只认 2.4G）
#define WIFI_PASS       ""      // ← 密码
#define BASE_MODE       1       // ← 第一次建议先填 0，只扫描不做任何写入
```

坐标已经填好深圳公明广场（`22.7809 / 113.8861`，来源见 `../README.md` 2.4 节），时区 +8。

### 3.2 Arduino IDE（图形界面）

1. 打开 `zk_base_esp32.ino`；
2. 开发板选 **AI Thinker ESP32-CAM**；端口选 ESP32 那个（`/dev/cu.usbserial-XXXX` / `wchusbserial`，
   **不是**价签的 `usbserial-210`）；
3. ESP32-CAM 按住板上 **IO0** 再按一下 **RST** 进下载模式（有 ESP32-CAM-MB 底板的一般自动）；
4. 点上传，完了再按一下 RST；
5. 串口监视器 **115200**。

### 3.3 或者命令行（Mac 上直接粘）

```bash
CLI="/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"

# 看有哪些串口
ls /dev/cu.usbserial-* /dev/cu.wchusbserial-* 2>/dev/null

# 直接上传（把 PORT 换成上面看到的）
PORT=/dev/cu.usbserial-XXXX
"$CLI" upload -p "$PORT" --fqbn esp32:esp32:esp32cam \
  /Users/mac/Documents/Codex/2026-09-15/a/outputs/ble-base/esp32/zk_base_esp32
```

### 3.4 不想编译：直接烧现成的合并镜像

`build/zk_base_esp32.ino.merged.bin` 是整片镜像（含 bootloader + 分区表 + app），烧到 `0x0` 即可：

```bash
ESPTOOL=~/Library/Arduino15/packages/esp32/tools/esptool_py/5.3.1/esptool
PORT=/dev/cu.usbserial-XXXX
"$ESPTOOL" --chip esp32 --port "$PORT" --baud 460800 \
  write_flash -z 0x0 \
  /Users/mac/Documents/Codex/2026-09-15/a/outputs/ble-base/esp32/build/zk_base_esp32.ino.merged.bin
```

（注意：这个烧的是**默认配置**——WiFi 是空的、BASE_MODE=1。要改配置得自己编译。）

## 4. 烧完应该看到什么

**第一次（`BASE_MODE 0`，只扫描）**：

```
=================================================
 ZK42V 价签基站 (ESP32) build-1
 芯片: ESP32-D0WD-V3 rev3 2 核 @240MHz  Flash 4MB  PSRAM 有
 时间源: HTTP Date 头   天气: Open-Meteo
 坐标: 22.7809, 113.8861   时区: UTC+8   模式: 0 只扫描
=================================================
[BASE 1s] BLE 起来了，开始只扫描（不做任何写入）
[BASE 14s]   扫到 (无名)           rssi= -71  5c:xx:xx:xx:xx:xx
[BASE 14s]   扫到 ZK42V-EPD        rssi= -48  3c:xx:xx:xx:xx:xx
[BASE 14s] → 命中价签：ZK42V-EPD  rssi=-48  addr=3c:xx:xx:xx:xx:xx
[BASE 15s] 连接中 ...
[BASE 16s] 连上了
[BASE 16s] （只扫描模式）连接验证通过，主动断开
```

看到 `rssi` 就是射频这关过了 —— **-48 很棒，-70 以上能用，-85 以下就别指望了**（换位置或换板子）。

**然后（`BASE_MODE 1`，真发数据）**：

```
[BASE 3s] 连 WiFi "xxxx" ...
[BASE 6s] WiFi 好了，IP = 192.168.100.xxx  rssi = -52 dBm
[BASE 7s] 时间（来自 HTTP Date 头）= UTC 1790737436 → 屏上应显示 2026-09-30 11:03:56
[BASE 7s] 天气取好了（22.7809,113.8861）：晴 34℃  （WMO=0 原始温度=33.6）
[BASE 10s] → 命中价签：ZK42V-EPD  rssi=-48  addr=3c:xx:xx:xx:xx:xx
[BASE 11s] 连接中 ...
[BASE 12s] 连上了
[BASE 12s] 固件协议版本 = 0x1A（>=0x16 就是新流程）
[BASE 12s] → 时间 UTC 1790737436 + 时区8 → 价签应显示 2026-09-30 11:03:56  载荷=20 6A BC 7C 1C 08 01
        <- 价签: "t=1790766236"  (74 3d 31 37 39 30 37 36 36 32 33 36)
[BASE 12s] → 天气 晴 34℃  载荷=71 01 22
        <- 价签: "wx=1 t=34"  (77 78 3d 31 20 74 3d 33 34)
[BASE 14s] 同步完成，已断开（价签这时在刷屏，约 16 秒）
```

价签回的那串 `t=` 是**已经加过时区**的当地时间戳：`1790766236 - 1790737436 = 28800 = 8 小时`，
跟 Mac 版同一个对账办法。屏上这时应该从旧画面变成"晴 34℃"的整页月历。

## 5. 坑（都踩过或者是有依据的）

1. **GPIO4 那根线**：ESP32-CAM 的 GPIO4 就是板载补光灯，`outputs/esp32-rst/` 那套把它接到价签的 RST 上了。
   当基站之前**必须把那根线从价签上拔掉**，否则板子一启动/闪灯就可能把价签按在复位里。
   拔了之后刷价签改用 TTL 复位：`cd outputs/pyocd && RST_VIA=ttl bash flash-app.sh`。
2. **只认 2.4G**：填 5G 的 SSID 会一直连不上（日志里会打 `WiFi 没连上（状态 6/201）`）。
3. **没填 WiFi 也能用**：这时只做蓝牙（扫描/连接/写天气会被跳过，时间也没得校）。日志会明确提示。
4. **每写一条命令 = 价签整页刷一次（约 16 秒）**：脚本已经做了节流，别把间隔调到几分钟以内。
5. **天线**：ESP32-CAM 是 PCB 板载天线，最好和价签在同一间屋；实在远就换 S3 板或者加个 U.FL 天线。
6. **供电**：ESP32-CAM 对 5V 挑剔（日志里 `esp_reset_reason` 会报 BROWNOUT），用 1A 以上、线别太细。
7. 摄像头/PSRAM **完全没用到**（固件里没有一行 camera 代码），摄像头坏不影响。
8. **WiFi 信号别太弱**：实测这块 CAM 连 AP 只有 **-86 dBm**（心跳里 `rssi=` 那个数就是它）——
   要靠好几轮握手的 HTTPS 基本没戏，纯 HTTP 能凑合但也不稳。能把板子挪得离 AP 近一点最好。
9. **`HTTP -1` 怎么读**：ESP32 的 `HTTPClient` 里 `-1` 是"连接失败"，不是 HTTP 状态码
   （HTTP 状态码是 200/404 那种正数）。失败时固件会自动打 `—— 网络诊断 ——` 几行，照着看即可。

## 6. 文件清单

| 文件 | 说明 |
|---|---|
| `zk_base_esp32/zk_base_esp32.ino` | 固件本体（WiFi + HTTP 天气 + BLE central + 命令写入） |
| `build/zk_base_esp32.ino.merged.bin` | 整片镜像（4MB，烧 `0x0`） |
| `build/zk_base_esp32.ino.bin` | 只有 app（1.13MB，OTA/单段烧录用） |

## 7. 还没做（下一步的候选）

1. **上 S3 常驻**：同一份代码换 `esp32:esp32:esp32s3` 编译即可；S3 有原生 USB，烧写不用 IO0 那套。
2. **推图**：价签支持 RLE 推整页位图（244 字节/块）——ESP32 侧要先 `setMTU()` 协商，再按 Mac 版
   `zk_ble_base.py`/网页那套逻辑切块。现在没做，因为日历是价签自己画的。
3. **位置文字**：固件侧要加 `0x73 <utf8>` 命令 + 字库（每个汉字 32 字节），见 `../README.md` 第 4.1/上一轮讨论。
4. **深度睡眠省电**：现在是一直开着（插 USB 供电），如果以后要用电池驱动基站，可以改成
   `esp_deep_sleep_start()` + 定时唤醒扫描。
