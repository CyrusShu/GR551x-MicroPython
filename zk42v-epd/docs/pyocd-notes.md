# pyOCD 版：按价签指示灯时序抢 SWD 窗口

## 现状（2026-09-22）

ZK42V 价签（GR5513BEND）这一段已经收尾：

| 事项 | 状态 | 文档 |
|---|---|---|
| 原厂固件备份 | 完成，`SHA-256 1dfab92f...cedb704` | `BACKUP-zk42v.md` |
| 读路径（绕开 ST-Link 克隆版的假数据） | 完成 | 本文档 |
| 写路径（Goodix FLM 算法，擦/写/验收） | **完成**：`restore` 128 颗全做完，`verify` 报 `VERIFY_OK` | `WRITE-zk42v.md` |
| 芯片现状 | 原厂固件在片，能正常启动，串口日志完好 | — |

要往这颗芯片写自己的固件，直接照 `WRITE-zk42v.md` 那四步走。

## 【重要】假数据事件与读法自检（2026-09-22）

上一轮 `dump-resume.sh` 报 `DUMP_COMPLETE`、写满 512KB、SHA-256 也打出来了，
**但整片是同一个字**（`0x4034C8F7` 反复），搜不到任何字符串 —— 是假数据。

### 根因（在 pyOCD 源码里找到的）

pyOCD 建 AHB-AP 时会问调试器"有没有加速内存接口"，ST-Link 说有，于是把 AP
的内存方法**换绑**到调试器自己的实现上（`pyocd/coresight/ap.py` 第 636-642 行）：

| 你调的 | 实际走的 | 地址自增由谁做 |
| --- | --- | --- |
| `target.read_memory_block8()` | `ap._accelerated_read_memory_block8` | **ST-Link 固件**（READMEM 命令） |
| `ap.read_memory_block32()` | `ap._accelerated_read_memory_block32` | 同上 |

而 `target.read_memory_block8` → `cortex_m.py` → `self.ap.read_memory_block8`。
所以之前每一次读，走的都是 ST-Link 固件那条加速路。那条路的地址自增是固件
自己实现的，克隆版固件（V2J37）没做对，就表现成"整片读成同一个字"。

经典路径（pyOCD 自己发 TAR + 连续读 DRW 事务）一直都在，只是被顶掉了：
`ap._read_memory(addr)`、`ap._read_memory_block32(addr, n)`。

### 现在的做法

`dumpresume` 连上以后先做**读法自检**：三条路各读同一批地址，按
CPUID（`0xE000ED00` 应为 `0x410FC241`）、`app_info` magic、数据变化度打分，
自动挑能读出真数据的那条。

| READMODE | 走哪条路 | 说明 |
| --- | --- | --- |
| `auto`（默认） | 自检后自动挑 | 推荐 |
| `apid` | `ap._read_memory_block32` | 经典 AP 块读，快 |
| `apiw` | `ap._read_memory` 逐字 | 最保守，最慢 |
| `probe` | `target.read_memory_block8` | ST-Link 加速路径（出假数据那条） |

整片读完后还会跑**真伪校验**：查 `app_info` magic、在文件里搜
`ZKC42V` / `GR551` / `ZKONG`、统计"前 128KB 有多少个不同的 32 位字"。
通过打印 `SANITY_OK`，不通过打印 `SANITY_FAIL` 并拒绝把文件当备份。

### 怎么跑

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
MANUAL=hold bash dump-resume.sh                                # 倒数 → 松手 → 自动选读法 → 整片读
DUMP_RESTART=1 READMODE=apiw MANUAL=hold bash dump-resume.sh   # 最保守的兜底
```

离线验证这套自检逻辑（不需要硬件）：

```bash
python3 /private/tmp/zk42v-testlogs/test-dumpresume-modes.py
```

### 结果：换读法也救不了 → 往下挖一层（DAP 体检）

2026-09-22 实测：`READMODE=apiw`（经典逐字读）读完整片，**仍然是一个字**
（`0xF7C83444`，和加速路的 `0x4034C8F7` 其实同一个 4 字节），`SANITY_FAIL`。
说明问题不在"哪条读法"，而在更底层。

在源码里又挖到一条关键事实：

```python
# pyocd/probe/stlink_probe.py 第 183 行
def connect(self, protocol=None):
    self._link.enter_debug(STLink.Protocol.SWD)   # 只发"进入 SWD"命令，不读、不校验 IDCODE
    self._is_connected = True
```

所以脚本里那句"连上了"**并不代表读通路是好的**。而 ST-Link 有两条完全不同的命令：
连接命令 `JTAG_ENTER2/JTAG_ENTER_SWD`（固件自己会读 IDCODE，读不到就报
`Get IDCODE error`）和读寄存器命令 `JTAG_READ_DAP_REG`（我们读 DP/AP 全靠它）。
连接能过、读回来却是死值，就说明问题在后一条。

于是有了 `dap-info.sh`：

```bash
MANUAL=hold bash dap-info.sh
```

它会打印：调试器信息、DP IDR 连读 8 次（看值变不变）、DP/AP 的原始寄存器
（CSW/TAR/DRW/IDR）、以及一个"值会不会变"的试验 —— **halt 前后读 DHCSR
（0xE000EDF0），看 S_HALT 位变不变**；只要有一个 bit 会变，读通路就是活的。
最后给一张结论对照表，直接对应四种可能。

### 体检结果（2026-09-22 实测）

| 看什么 | 读到的 | 判断 |
| --- | --- | --- |
| DP IDR | `0x2BA01477`（连读 8 次都一样） | ✅ 正常 SW-DP |
| AP IDR / BASE | `0x24770011` / `0xE00FF003` | ✅ 正常 AHB-AP + ROM 表 |
| CPUID `0xE000ED00` | `0x410FC241` | ✅ Cortex-M4，读得到 |
| ROM 表 `0xE00FF000` | `0xFFF0F003` | ✅ 正常 ROM 表项 |
| `halt` 前后 DHCSR | `0x01010001` → `0x01030003`，**S_HALT 0→1** | ✅ **读通路完全是活的** |
| 没映射的 `0x40000000` | 正常报 Memory transfer fault | ✅ 总线错误也正常 |
| `0x01000000` 整块 | 一个死值（两次跑分别是 `0xB7C83440` / `0xF7C83400`） | ❌ **只有 flash 区间不吐真数据** |

**结论：调试器没问题、读通路没问题，是这颗芯片的 flash 区间（0x01000000）
对调试器不返回真数据**，而且那个死值每次会话还不一样（像"总线上的残留数据"）。
换调试器也没用 —— ST-Link 自己固件的加速读路径和 pyOCD 的经典 DAP 路径
给出的是同一个死值，两条独立实现都这样，说明是芯片这一侧的行为。

### 下一步：`flash-lab.sh` 找 flash 到底在哪块地址能读到

```bash
MANUAL=hold bash flash-lab.sh
```

它看四件事：CPU running vs halted 时读值变不变、字读 vs 逐字节读、
一排候选地址窗口（`0x00000000` / `0x00020000` / `0x00800000` / `0x08000000`
/ `0x10000000` …）哪个有变化、以及**别名猜想** —— 如果 flash 在
`0x00000000` 也有别名，那么 `app_info` 应该在 `0x0000A200` 读到
`0x47525858`，应用入口 `0x0000A000` 的第二个字应该是 `0x0100Axxx` 这种奇数地址
（Cortex-M 的复位向量）。

如果别名成立，读整片就改成：

```bash
DUMP_BASE=0 DUMP_TOTAL=0x80000 DUMP_RESTART=1 MANUAL=hold bash dump-resume.sh
```

（原来那份假数据已挪到 `/private/tmp/zk42v-testlogs/garbage/`，
`.progress` 一起挪走了，所以下一遍会从 0 开始读。）

## 量线用：`rst-hold.sh`（把 RST 钉住，方便万用表量）

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
bash rst-hold.sh                # 持续拉低 RST，直到 Ctrl-C
SECS=60 bash rst-hold.sh        # 只保持 60 秒
STATE=high bash rst-hold.sh     # 持续放开（拉高）
STATE=toggle bash rst-hold.sh   # 每 2 秒翻一次 —— 用来找出哪根针是 nRST
STATE=toggle TOGGLE=1000 bash rst-hold.sh   # 翻快一点
```

它会先把 `RST` 钉住，然后一直保持（每 10 秒报一次证明还钉着），
同时把该量哪里、应该读到多少直接打在屏幕上；Ctrl-C 之后自动放开。

万用表直流电压档，黑表笔接**价签 GND**，红表笔依次点：

| 量哪里 | 应该是 | 说明 |
| --- | --- | --- |
| ST-Link 上你插 RST 的那根排针 | ~0V | 这根针确实被拉低了 |
| 价签的 RST 焊盘 | ~0V | 线通，ST-Link 真的在驱动它 |
| 价签的 SWCLK 焊盘 | ~3.3V | 空闲应为高 |
| 价签的 SWDIO 焊盘 | ~3.3V | 空闲应为高 |

判读：

- 排针 ~0V + 价签 RST 也 ~0V → 线是通的，别再怀疑 RST 线。
- 排针 ~0V + 价签 RST 还是 ~3.3V → 线没接通 / 接错针（最常见）。
- 排针还是 ~3.3V → **那根针根本不是 nRST**。用 `STATE=toggle` 找：
  把价签先摘掉，红笔挨个点 10 根针，在 0V 和 3.3V 之间来回跳的才是 nRST。

## 先跑这个

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
bash led-window.sh
```

两个文件：

| 文件 | 作用 |
| --- | --- |
| `led-window.sh` | 一键脚本：跑第 0 步自检、问你灯重启几次、再抢窗口 |
| `led-window-user.py` | pyOCD 用户脚本，注册 `rstpulse` 和 `ledwindow` 两条命令 |

## 时序依据

你实测的现象：

```
RST 接地松开 ──> 约 2000 ms 后【绿】亮 1 秒 ──>【蓝】亮 1 秒 ──>【红】
```

| 复位后时间 | 指示灯 | 对应阶段 | SWD |
| --- | --- | --- | --- |
| 0 ~ 2000 ms | 不亮 | ROM / BootROM + bootloader | **理论上一定活着** |
| 2000 ~ 3000 ms | 绿 | 应用开始接管 | 可能被关掉 |
| 3000 ~ 4000 ms | 蓝 | 应用完全跑了 | 大概率已关 |
| 4000 ms 以后 | 红 | 等 AP / 睡觉 | 大概率已关 |

所以真正要抢的窗口是**复位后的头 2000ms**。工具就围着它打。

## 它到底做了什么

**第 0 步 `rstpulse`** —— 只驱动 RST 拉 5 下，每下间隔 1.2 秒，**完全不碰 SWD**。
屏幕一定会打出 5 行「拉低 RST」，灯每下走一遍绿→蓝→红就说明 RST 线是通的。
看到 0 行才是工具的问题；打出 5 行而灯不动，才是线的问题。

**第 1 步 `ledwindow`** —— 一轮一轮地：拉低 RST 200ms → 松开记 t=0 →
从 t=0 起以最高频率反复 `probe.connect()`，一直试到 t=4.6s。
t=2s/3s/4s 各播一声不同的音（绿=叮 蓝=叮咚 红=咚），
让你用耳朵把三盏灯和时间轴对上。
命中就报「第几轮、复位后多少毫秒、当时是哪盏灯」，然后整片读回 512KB。

## 为什么要 `-N`

```bash
pyocd commander -W -N -M halt -t cortex_m -f 500k --no-config \
    --script led-window-user.py -c ledwindow
```

`-N`（`--no-init`）让 pyOCD **只打开适配器、设好时钟，但不去初始化目标**。
不加它，pyOCD 一上来就初始化，芯片不应答就报错退出
（就是你之前看到的 `STLink error (9): Get IDCODE error`），
我们连「拉复位再重试」的机会都没有。

加了 `-N` 之后，pyOCD 把 `session`（里面有 `probe`）交给我们自己的脚本，
由我们决定什么时候连、怎么连。命中之后再调 `session.board.init()`
让 pyOCD 把标准初始化流程走完，然后正常读内存。

## 两个已经踩过的坑（都修了）

1. **`session.probe` 不是 probe，是一层带引用计数的代理。**
   它的 `connect()` 只在计数为 0 的那一次真的下去连 SWD，之后就只把计数 +1
   直接返回。拿它做高频重试等于自己骗自己。脚本里用 `raw_probe()`
   取 `.probe` 拿到底层对象，每一次调用都是一次真实的连接尝试。

2. **`-c CMD [CMD ...]` 会吃掉后面的参数**（`nargs='+'`）。
   所以 `-c` 永远放在命令行最后。

## 另外记一笔：OpenOCD 那条路在 Mac 上是真坏了

`outputs/openocd/rst-pulse.cfg` 在 Mac 上会直接崩：

```
Assertion failed: (handle), function stlink_usb_assert_srst, file stlink_usb.c, line 2166.
```

因为 `init` 阶段连不上目标就失败，底层 ST-Link 句柄是空的，
这时候去 `assert srst` 正好踩中 OpenOCD 自己的断言（abort）。
pyOCD 没这个问题：它的 `-N` 只打开适配器，句柄是好的，
`probe.assert_reset()` 能正常驱动 nRST。所以量线、抢窗口都用 pyOCD 这套。

实测数据（2026-09-22 17:07 那次）：pyOCD 的 `rstpulse` 正常发出 5 下；
`ledwindow` 以 **约 360 次/秒** 试了 4 轮共 6713 次，全部失败。

## 常用开关

```bash
SPD=240    bash led-window.sh   # 降到 240kHz 重试
RND=8      bash led-window.sh   # 轮数，默认 4
WIN=6000   bash led-window.sh   # 每轮抓多久，默认 4600ms
LED_MS=2500 bash led-window.sh  # 如果实际不是 2 秒才转绿
HOLD=500   bash led-window.sh   # 复位拉低多久，默认 200ms
BEEP=0     bash led-window.sh   # 静音
SKIP_RSTCHECK=1 bash led-window.sh  # 跳过第 0 步
ONLY_PULSE=1    bash led-window.sh  # 只做第 0 步
PYOCD=/path/to/pyocd bash led-window.sh   # 指定 pyocd
```

## 怎么读结果

- `RESULT_HIT round=2 elapsed_ms=812 phase=rom` —— 抢到了，而且是在灯还没亮的
  ROM 阶段抢到的，链路是好的。后面会自动读整片并算 SHA-256。
- `RESULT_FAIL total_attempts=...` —— 连 ROM 那 2 秒都抢不到，
  **这不是时机问题而是链路问题**：RST 线 → SWCLK/SWDIO 接反 → 共地 → 降速。
- `RESULT_PULSE_OK count=5` —— RST 线自检发出去了 5 下。

读完之后**再跑一遍比对 SHA-256 一致**，这份备份才算可信。

## 如果 `-c ledwindow` 不被认

用交互模式进去手动敲命令名：

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
../../tools/pyocd/pyocd commander -W -N -M halt -t cortex_m -f 500k \
    --no-config --script led-window-user.py -i
# 然后敲:  rstpulse    或    ledwindow
```

## 老工具还在

`dump-mac.sh` 是原来那条「正常 pyOCD 路径」的备份脚本（不抢窗口，直接连）。
现在链路连不上，先用 `led-window.sh` 把窗口抢下来。

---

## ⚠️ ST-Link 的 RST 针：先看这个

2026-09-22 实测：`rsthold` / `rstpulse` 都报成功（ST-Link 收下了 DRIVE_NRST 命令、
回了 OK），但万用表量那根针一直是 3.3V，一动不动。

两个已知原因：

1. **DRIVE_NRST 是 ST-Link 的 JTAG 命令**，有的固件在「没进 SWD/JTAG 模式」时
   会把命令收下但不驱动引脚。所以 `rstpulse` / `rsthold` 现在会**先尝试进一次
   SWD 模式**再拉复位（不想这样：`RST_ENTER_SWD=0`）。
2. **克隆版的排针定义可能和标准 ARM 10 针不一样**。手上这颗金色 ST-Link V2
   克隆版，壳上印的是：

   | 针 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
   |---|---|---|---|---|---|---|---|---|---|----|
   | 标注 | RST | SWCLK | SWIM | SWDIO | GND | GND | 3.3V | 3.3V | 5.0V | 5.0V |

   标准 ARM 10 针是 **2=SWDIO、4=SWCLK**，这里正好**反了**。照「标准 ST-Link
   接线图」接的话，SWDIO/SWCLK 就是反的 —— 三个工具全都连不上，症状完全对得上。

   别信图，量出来：黑笔接 ST-Link 的 USB 金属壳（GND），红笔点 10 根针，
   两根 ~5.0V、两根 ~3.3V、两根 0V —— 对得上上面这张表就说明丝印是对的。
   再跑 `STATE=toggle bash rst-hold.sh`，在 0V / 3.3V 之间来回跳的那根才是 nRST。

## 不用万用表验复位线：pulse-rst.sh

```bash
bash pulse-rst.sh          # 只打 RST，完全不碰 SWD
PULSES=8 bash pulse-rst.sh
```

串口开着一起看：灯跟着走「绿-蓝-红」N 次、串口蹦 N 段 `loader info:` = 复位线通了。

---

## 结论（2026-09-22 17:30 实测）：这颗 ST-Link 的 RST 针救不回来

```
ST-Link 固件: V2J37M7
进 SWD 模式失败：STLink error (9): Get IDCODE error
```

`DRIVE_NRST` 要适配器先进 SWD/JTAG 模式才真的推引脚，而芯片在 SWD 上不应答
就进不去 → 命令空发 → 表上始终 3.3V。所以**换复位源**，别再跟这根针耗。

现在的复位源选择（`RST_VIA`）：

| RST_VIA | 复位源 | 说明 |
|---|---|---|
| `stlink`（默认） | ST-Link 的 nRST 针 | 这颗克隆版实测推不动 |
| `ttl` | USB-TTL 的 RTS/DTR | 需要模块把 RTS/DTR 引出来；先跑 `rst-ttl.py` 量 |
| `esp32` | ESP32 的 GPIO | **推荐**，硬件保证 0V，见 `../esp32-rst/` |
| `MANUAL=1` | 你用手碰 RST | 什么都不用接 |

```bash
# 用 ESP32 当复位源（推荐）
ESP32_PORT=/dev/cu.usbserial-XXXX RST_VIA=esp32 bash led-window.sh

# 用 USB-TTL 的 RTS 当复位源
RST_VIA=ttl bash led-window.sh

# 用手碰
MANUAL=1 bash led-window.sh

# 只想打复位、不抢 SWD（验证复位线）
bash pulse-rst.sh
```

---

## 2026-09-22 18:43 实测：SWD 其实是通的，问题在读取方式

```
SPEED=240k bash dump-mac.sh
  → Core 0 (None): Running          <-- 连上了！芯片在应答
  → Error: memory transfer failed   <-- 一把梭读 512KB，中途断一次整条命令作废
```

两个原因叠在一起：

1. **一把梭**：`savemem 0x01000000 0x80000` 是一次 512KB 的传输，中间任何一次
   SWD 失败，pyOCD 整条命令作废，一个字节都不给你。
2. **价签平时在睡**：串口日志里 `no AP, sleep 30000s`。只有复位后头 1~2 秒
   （ROM/bootloader）SWD 才老实应答。

### 所以用 dump-resume.sh

分块 4KB + 进度文件 + 每轮重新拉复位抢窗口，断了从断点续读：

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
RST_VIA=ttl bash dump-resume.sh          # 默认就是这个（TTL 的 RTS 打复位）
RST_VIA=ttl SPEED=240k ROUNDS=60 bash dump-resume.sh
```

- 进度存在 `zk42v-factory-512k.bin.progress`，随时 Ctrl-C 都不丢
- 读完打 `DUMP_COMPLETE` + `SHA-256` + 文件大小
- 想再确认一次：`DUMP_RESTART=1 bash dump-resume.sh`，两次 SHA-256 一样才算可信备份
- NVDS（0x0107F000，4KB）在最后一块里，一起读到了

---

## 2026-09-22 18:53 真机日志：手动「按住→松手」流程有效，但踩了一个 pyOCD API 坑

```
MANUAL=hold bash dump-resume.sh
  >>> 现在松手！ <<<
  连上了（复位后 1039 ms，试了 245 次）        <-- 手动复位这条路是通的
  halt 失败（不致命，照读）：'Board' object has no attribute 'halt'
  0x01000000 处读失败：'Board' object has no attribute 'read_memory_block8'
```

**坑**：pyOCD 0.45 重构过，`session.board` 是 `Board`（板子信息对象），
**不是 CPU 目标**。真目标在 `session.board.target` / `session.target` 上，
`halt()` / `read_memory_block8()` 这些方法都在它身上。

已修：新增 `resolve_target()`，三种情况都兜（新老版本、board 是不是 target），
`dumpresume` 和 `ledwindow` 都改用它，并且会打印出来：

```
  目标     : GR5513 (cortex_m)
  CPU 状态 : running
```

## 2026-09-22 20:00 新工具：`flash-lab2.sh`（三路会审）与 `ram-dump.sh`

`flash-lab.sh` 的结果是：0x00000000 那一片（芯片掩膜 ROM）能读出真代码，
只有 0x01000000（flash 窗口）整块返回同一个死值。于是换三条完全不同的路再问一遍：

| 路 | 做什么 | 依据 |
|---|---|---|
| 路 1 | 把 AHB-AP 的 CSW 换成 14 种组合（MSTRTYPE/HNONSEC/HPROT/传输宽度），每种都手搓 TAR/DRW | pyOCD 读内存永远带 MSTRTYPE=1，芯片可能就靠这个位挡调试器 |
| 路 2 | 扫别名窗口 0x03000000 | Goodix SDK：`EXFLASH_ALIAS_OFFSET = 0x02000000`，即 `EXFLASH_ALIAS_ADDR = 0x03000000` |
| 路 3 | 往 RAM 塞 14 字节 Thumb 代码，让 CPU 自己把 flash 抄进 RAM，再从 RAM 读回 | CPU 读 flash 是"正常访问"，只挡调试器的防火墙挡不住 |

```bash
MANUAL=hold bash flash-lab2.sh          # 路 1/2/3（路 3 会先问一句）
MANUAL=hold TRAMP=no bash flash-lab2.sh # 只跑只读的两路
```

路 3 的代码（`arm-none-eabi-as` 对过，逐字节一致）：

```
6803 ldr  r3,[r0]   600b str r3,[r1]   3004 adds r0,#4
3104 adds r1,#4     3a01 subs r2,#1    d1f9 bne     e7fe b .
```

哪条路通了就走哪条：

```bash
# 路 2 通：整片从别名窗口读
DUMP_RESTART=1 DUMP_BASE=0x03000000 MANUAL=hold bash dump-resume.sh

# 路 3 通：CPU 搬运式整片读（不写 flash，断了能续）
MANUAL=hold bash ram-dump.sh
```

## 2026-09-22 20:10 实测 flash-lab2 的结果，以及新假设（flash-lab3 / flash-lab4）

flash-lab2 实测：路 1（14 种 CSW）全死、路 2（别名 0x03000000）也全死、
路 3（CPU 抄）搬回来也是那个死值 `0xB7C83400`。

**新假设**：`0x01000000` 这个窗口背后是 XQSPI 的缓存/XIP 引擎。ROM/bootloader
阶段控制器还在「间接模式」（用 QSPI 寄存器一条条读，串口日志里那个 CheckSum
就是这么算出来的），XIP 还没开 —— 这时候谁去读那个地址都只拿到锁存住的死值。
等 bootloader 校验完镜像、打开 XIP、跳到 `0x0100A000` 跑应用之后，窗口才是真的。
（这也解释了为什么死值每次都不一样：它是控制器里那个锁存/预取寄存器。）

两条新命令：

| 命令 | 干什么 |
|---|---|
| `flash-lab3.sh` | 打印 XQSPI 的 CACHE/QSPI/XIP 全部寄存器；给搬运小程序加「标记」证明代码真跑了；从 CPUID 和 flash 各抄一遍做对照 |
| `flash-lab4.sh` | **连着 SWD 让 CPU 自己往下跑**，每 5ms 戳一次 `0x0100A200`，一变活立刻 halt 并整片读回 |

```bash
MANUAL=hold bash flash-lab4.sh                          # 最可能一把成
MANUAL=hold TRAMP=yes bash flash-lab3.sh                # 钉死结论 + 寄存器清单
POLL_MS=1 POLL_TOTAL_MS=6000 MANUAL=hold bash flash-lab4.sh   # 窗口太短时
```

搬运小程序的机器码（加了标记那一条，`arm-none-eabi-as` 逐字节对过）：

```
601a str r2,[r3]  ← 先把"要搬几个字"写进标记地址：跑过就是铁证
6803 ldr r3,[r0]  600b str r3,[r1]  3004 adds r0,#4
3104 adds r1,#4   3a01 subs r2,#1   d1f9 bne   e7fe b .
```

## 2026-09-22 20:20 `rst-check.sh`：只验「RST 松开后 SWD 有没有握上手」

价签那颗 RGB 灯是**应用固件**点的（应用状态存在 NVDS 里），所以
**灯不亮 ≠ 复位失效**。判 RST 有没有用的两个真判据：

```bash
MANUAL=hold bash rst-check.sh      # SWD 判据：复位后能不能重新握上手
```

另一个判据是串口：按 RST 之后开机日志会不会重新打一遍（USB-TTL 接价签 TX）。

实测口径：正常应该是「松开后约 1 秒、试一两百次才握上」—— 这个延迟正好是
ROM/bootloader 阶段。如果 0ms 就握上，多半是上一次会话没断干净，重跑一次。

---

# 2026-09-26 · 进入方向 B：刷自研固件（屏驱动已逆向完）

前置状态全部落定：原厂固件备份验真、写入路径验通（`restore`/`verify` 都过）、
屏的引脚/初始化序列/图像格式/刷新序列全部从原厂固件里挖出来
（见 `../analysis/PANEL-zk42v.md`）。于是开始往这颗芯片写自己的固件。

自研固件的源码、构建、文档都在 **`../firmware/`**（先读那份 README）。
这边只记跟「硬件访问」有关的三件事：

## 1. 路由：原厂 bootloader 留着，只换 APP

```
0x01000000  boot_info          ROM 按它找 bootloader      ← 不动
0x01000040  镜像信息("second_boot_")                       ← 不动
0x01002000  APP 的镜像信息      bootloader 就按它找 APP     ← 要改
0x01003000  原厂 bootloader                                 ← 不动
0x0100A000  原厂 APP                                        ← 换成我们的
0x0107F000  NVDS                                            ← 不动
```

`0x01002000` 那条 40 字节记录里，`check_sum` 就是「APP 镜像逐字节求和取低 32 位」。
这个公式是**反向验证过**的：拿备份文件按它算，bootloader 段得 `0x00173927`、
APP 段得 `0x00CCE71B`，跟串口日志里原样打出来的两个数一字不差。

所以刷自研固件只需要：把 APP 写进 `0x0100A000`，再把那条记录的 `bin_size`
和 `check_sum` 改对。bootloader 的验签自然就过。

## 2. 新增两条 MODE 和一条命令（都在 `flash-write.sh` 的同一个脚本里）

| 用法 | 干什么 |
|---|---|
| `MODE=app bash flash-app.sh` | 只擦写 APP 那一段 + **最后**改 `0x01002000` 那颗扇区；bootloader/NVDS 一个字节不碰 |
| `MODE=appverify bash flash-app.sh` | 只读：把 APP 段读回来，跟本地镜像逐字节比 + 自洽验校验和 |
| `bash status.sh` | 读自研固件写在 `0x3001F000` 的调试状态块 |

从 build 19（B2-A.2）起，`status.sh` 还会多读两块东西：

* 状态块里 **word 24..52** 那一组：扫描实验的结果（听到几个设备 / RSSI /
  第一条广播数据的字节）和 6 种广播数据变体各自的 `ADV_START` 状态码；
* 三个直接读的寄存器当旁证：`AON PWR_RET01`（`0xA000C504`，BLE 子系统
  有没有上电/还在复位）、`NVIC ISER0`（`0xE000E100`，协议栈的调度中断
  `IRQ1 BLE_SDK` / `IRQ2 BLE` 有没有使能）、以及原来的 AON 那一片。

它默认先等 `STATUS_SETTLE_MS=15000` 毫秒（让固件把扫描和广播变体实验跑完）
再采；只想快速看一眼 stage/boot_count 就用 `STATUS_SETTLE_MS=0 bash status.sh`。

配网页推图时还有一条**不用刷机**的保险：网页上 `确认间隔`（interleavedcount）
默认 50 = 每 50 块才有一块"带响应的写"。要是推出来的图缺块，把它填 **0**
（每块都带响应，一块不丢，慢一点）。

从 build 20（B2-A.3）起：

* 固件**不再开机先扫描**（`ZK_BLE_SCAN_TEST 0`，直接广播），所以实验一那几格
  会是「没跑过」；`status.sh` 会明说「这一版没跑扫描实验」，别当成扫描失败。
  想再跑扫描实验 `STATUS_SETTLE_MS=15000 bash status.sh` 之前先把固件里那个开关改回 1。
* 多一行 `ADV_STOP（广播自己停）：一共 N 次   我们重开了 M 次`。
  正常应该是 0 次；被手机连上时会出现 1 次、原因是「被连接打断（正常）」。
  **非**连接原因停掉且没重开，脚本会直接喊「空中可能已经没有我们的包了」。

从 build 21（B2-A.2 网页推图）起，还会多一段
`B2-A.2：GATT 服务 / 推图`，读的是状态块 word 56..76：

* 服务建起来没有（`ble_svc_hdl` / `ble_svc_err`）、手机连过几次、CCCD 开没开；
  从 build 22 起这里是**两步**：`ble_gatts_prf_add` 的返回（注册）和
  `ble_gatts_srvc_db_create` 的返回（协议栈回调里真正建库）分开报 ——
  build 21 就是"注册的东西没走到栈里"，手机报
  `No Services matching UUID … found in Device`，而固件这边却显示建库成功。
* 收到多少条命令、最后一条是什么、`INIT`/`REFRESH` 各收到几次；
* `WRITE_IMAGE` 收到多少块、黑白面和红面各收了多少字节（满 = 15000/15000）、
  RLE 解出多少字节、最后一块的 flags、有没有退回 v1.5 老格式（`ble_legacy`）；
* 屏的状态（没动过/初始化完/刷完一帧）和耗时、发出去几条通知。

这一段自带判据：服务没建起来会说「服务没建起来」，命令一条没收到会说
「写事件没进来」，图块没凑满会说「无响应的写可能没递上来」。

`MODE=app` 的顺序是故意这么排的：**镜像信息扇区最后写**。万一写到一半掉线，
bootloader 手里还是旧账，它算完发现 APP 对不上 -> 去走 DFU，而**不会**
跳进一个半截的 APP。

## 3. 自研固件不关 SWD —— 以后的调试变简单了

原厂 APP 一接管就把 SWD 关了，所以之前每次看芯片都得抢复位后那 2 秒。
我们的固件进 `main()` 第一件事就是 `sys_swd_enable()`，而且不睡觉，
所以：

* 调试器**随时**能连上，不用再算时间；
* 固件往固定地址 `0x3001F000` 写进度（相当于 printf），`status.sh` 翻译成人话；
* `status.sh` 里「不复位就直接连上」本身就是判据 —— 连得上就说明跑的是我们的固件。

## 4. 回厂还是那一条

```bash
MODE=restore bash flash-write.sh     # 整片刷回出厂备份（128 颗扇区）
MODE=verify  bash flash-write.sh     # 刷完按一下 RST，看到 VERIFY_OK 就回厂了
```

自研固件这一路**没有动** bootloader、boot_info、NVDS 和任何 OTP，所以回厂
永远只是「把 APP 段覆盖回去」而已。
