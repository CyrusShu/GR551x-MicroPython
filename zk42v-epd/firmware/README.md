# ZK42V 自研固件（方向 B）第一版：把屏点亮

> **2026-09-27 B1 实测结果 → B1.1 → B1.2（真凶找到了）**
>
> 写入和验收都过了（两次 `MODE=app` 各写 19+1 颗扇区、约 11 秒；
> `MODE=appverify` 报 `APP_VERIFY_OK`），但**屏没亮，而且 3 秒后调试口就没了**。
>
> **真凶（B1.2 修的）**：芯片卡在 SDK 的
> `platform_clock_init() → platform_disable_sleep_timer()` 等待 **AON PSC** 空闲的
> 死循环里（PC 精确落在 `0x0100CEAE/0x0100CEB0`，还有一次落在被它轮询的那段 RAM 代码
> `0x30004E18`）。原因是：SDK 默认要求**低功耗时钟走 RTC / 外部 32.768kHz 晶振**
> （PSC 要执行 `RTC_CLK` 命令才切得过去），而这颗价签**没有那个晶振**，命令永远完不成，
> `MCU_PWR_BUSY` 一直置位。原厂固件在同一处传的是 `r1=2`（`RNG_OSC_CLK2`，内部 RC）
> —— 见 `outputs/analysis/app.asm` VA `0x0101F0F4`。
>
> **B1.2 的改法**：`custom_config.h` 里 `CFG_LPCLK_INTERNAL_EN` 从 0 改成 **1**
> （内部 RC 当低功耗时钟），编译产物里 `platform_init` 已经改成调
> `platform_clock_init_rng(..., RNG_OSC_CLK2, ...)`、不再调 `platform_set_rtc_crystal_delay`。
>
> ⚠️ **刷完 B1.2 必须断电重上电**（不是按 RST）：卡住的 PSC 状态在 AON 域，
> 系统复位清不掉，只有彻底断电能清。断电 10 秒再上电，屏上就该出现那四条横带。

> 2026-09-26 · 前置状态：原厂固件已备份验真，写入路径已验通（`restore`/`verify` OK），
> 屏的参数已全部逆向出来（见 `../analysis/PANEL-zk42v.md`）。

## 这一版干了什么

**原厂 bootloader 一个字节都不动**，只把 flash `0x0100A000` 那个 APP 换成我们自己写的
固件。它跑起来做三件事：把屏初始化、画一张「体检图」、刷新显示，然后停在原地
保持 SWD 开机（原厂 APP 会关掉 SWD，这版反过来）。

这一版的价值在于：它一次就能回答三个之前只能靠猜的问题

1. **屏到底能不能被驱动** —— 能，就看到图；不能，调试状态块会告诉我们卡在哪一步；
2. **黑/白/红的极性** —— 图上有四条用不同数据组合画出来的横带，颜色对应关系一眼就定；
3. **方向** —— 图左上角有一条定位小条，能看出 buffer 的行列跟屏的上下左右怎么对应。

## 安全网（任何时候都能回厂）

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
MODE=restore bash flash-write.sh
```

`restore` 会把出厂备份整片刷回去（128 颗扇区，约 3 分钟），刷完 `MODE=verify` 打
`VERIFY_OK` 就彻底回厂。**这一步从第一版起就一直有效，跟自研固件无关。**

---

## 一、你要跑的命令

全部在 Mac 终端里跑，工作目录固定：

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a
```

### 第 0 步（可选）重新编译 + 打包

我已经编好并打包好了，想自己重来一遍就跑：

```bash
cd outputs/firmware && bash build.sh
```

**判据**：最后几行出现

```
[OK  ] 可以刷了。
```

以及 `zk42v-custom-512k.bin` 的 SHA-256。（B1.2 这一版是
`1ab3aeea9c5fd88b1655dcc875a4cde24b69b036443f61a2b38e2a0c8fd5d2c6`，
你重编出来的应该一样；不一样也不要紧，只要"可以刷了"这句在。）

### 第 1 步 刷进去（只写 APP 那一段）

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
MODE=app bash flash-app.sh
```

它会：

1. 数到 3，**这 3 秒里把价签的 RST 按住接地**，听到"叮"就松手；
2. 抢 SWD 窗口连上（试几百次是正常的）；
3. 只擦写 `0x0100A000` 起那 19 颗扇区 + 最后改 `0x01002000` 那颗镜像信息扇区；
4. **不碰** bootloader（`0x01003000`）和 NVDS（`0x0107F000`）。

**判据**：看到这两行就算成功

```
>>> 写完了：APP 21 颗 + 信息 1 颗，一共 22 颗扇区。
  芯片里那段 APP 的逐字节和 = 0x008084C8（跟声明的对上了）
```

> 中途掉线/失败也是安全的：这时 `0x01002000` 那条镜像信息还是旧的，原厂 bootloader
> 算完会发现 APP 对不上，于是去走 DFU，而**不会**跳进一个半截的 APP。重跑同一条命令即可。

### 第 2 步 只读验收（不看这个不许往下走）

先把价签的 RST 碰一下 GND 再松开（让它带着新固件重新启动），然后：

```bash
MODE=appverify bash flash-app.sh
```

（同样会数到 3 让你按住 RST。）

**判据**：

```
>>> 芯片里的 APP 跟本地镜像一致，而且自洽  APP_VERIFY_OK
```

看到 `APP_VERIFY_FAIL` 就把整段输出发我，别继续折腾。

### 第 3 步 体检（相当于 printf + 示波器）

```bash
bash status.sh
```

它会隔一会儿采几个点，每次都**先把 CPU 停住再读**，打印成一张表：

```
  #1  PC=0x0100C70D [我们的 APP]                magic=0x5A4B3401 stage=9    boot=1 uds=1
  #2  PC=0x0100C70D [我们的 APP]                magic=0x5A4B3401 stage=9    boot=1 uds=1
```

再往下是细节（CPUID / DHCSR / PC 落在哪 / CFSR 硬件异常标志 / AON 寄存器 /
状态块的逐字段解读）和一段结论。**判据**：

| 看到什么 | 意思 |
|---|---|
| PC 一直落在「我们的 APP」，stage = 9 | 固件整条路都走完了，屏上应该有图 |
| PC 在 ROM 和 APP 之间来回跳，或者 boot 一直涨 | **芯片在反复复位** |
| AON `SOFTWARE_1` 低 16 位 = `0xF175` | 抓到那个「超深睡唤醒」死循环了 |
| 连不上 / 读不到 | 先重连一次（脚本自己会做），还不行就把输出发我 |

顺带说一句：这条命令能**不复位直接连上**，本身就是个好消息 —— 说明那个状态下
调试口是开的（原厂 APP 跑起来会关掉它）。

### 第 4 步 看屏

正常应该看到（从上往下）：

| 位置 | 高度 | 内容 |
|---|---|---|
| 最上面 | 8 行 | **左半边**一条细带，右半边跟下面第 2 条一样 |
| 第 2 条 | 100 行 | 一整条 |
| 第 3 条 | 80 行 | 一整条 |
| 第 4 条 | 70 行 | 一整条 |
| 最下面 | 42 行 | 一整条 |

四条带的颜色组合**各不相同**，把它们从上到下报给我（比如"黑、白、红、红"），
再加上"那条细带在左上角还是右上角"，我就能把极性表和方向钉死，
下一版直接画正常图片。

### 第 5 步（想回厂的时候）

```bash
MODE=restore bash flash-write.sh
# 完了再按一下 RST，跑 MODE=verify bash flash-write.sh，看到 VERIFY_OK 就回厂了
```

---

## 二、刷完之后有些现象是**正常的**，别误会成砖

| 现象 | 为什么 |
|---|---|
| 串口不再打 `version:` / `model :` 那些日志 | 那是原厂 APP 打的。我们这版**没初始化 UART**（这块板的 UART 引脚还没挖出来，不敢乱配）。要日志就看 `status.sh` |
| RGB 灯不再走"绿→蓝→红" | 那也是原厂 APP 点的。我们这版没去点灯 |
| 复位后 SWD 窗口不再只有 2 秒 | 恰恰相反 —— 我们的固件进 `main()` 第一件事就是 `sys_swd_enable()`，**随时都能连** |
| 板子比原厂状态费电 | 我们这版不进睡眠，一直在空闲循环里转。**测试时用外部供电，别拿电池长期挂着** |

---

## 三、这条路为什么是通的（证据，不是推测）

### 1) 校验和公式是反向验证过的

原厂串口日志里明文打着两个镜像的 `CheckSum`。我用"逐字节求和取低 32 位"拿备份文件
算回去，两个都对上了：

| 镜像 | 地址/长度 | 日志里的 CheckSum | 我算出来的 |
|---|---|---|---|
| bootloader | `0x01003000` + `0x3B00` | `0x00173927` | `0x00173927` ✅ |
| 原厂 APP | `0x0100A000` + `0x1F8F0` | `0x00CCE71B` | `0x00CCE71B` ✅ |

所以只要把新 APP 的 `bin_size` 和 `check_sum` 写对，原厂 bootloader 的验签就会过。

### 2) APP 在哪儿是明摆着的

`0x01002000` 那条 40 字节记录里就写着 `load_addr = run_addr = 0x0100A000`，
bootloader 就是按它找 APP 的。我们保留 `pattern`/`comments`/`load`/`run`，只改
`bin_size` 和 `check_sum`。

### 3) 屏的每一个字节都是从原厂固件里读出来的

引脚、初始化序列、缓冲区布局、刷新序列，全部对应原厂代码地址（见
`../analysis/PANEL-zk42v.md`）。这一版是**原样重放**，没有一处是猜的。

---

## 四、调试状态块 `0x3001F000`

固定地址，16 个字。固件每走完一步就往里写一个数。`status.sh` 会把它翻译成人话。

| 偏移 | 名字 | 含义 |
|---|---|---|
| +0x00 | magic | `0x5A4B3401` —— 值对了就说明我们的固件真的跑起来了 |
| +0x04 | heart | 空闲循环里自增，连读两次不一样 = CPU 在跑 |
| +0x08 | stage | 走到第几步（见下表） |
| +0x0C | flags | bit0 BUSY 超时过 / bit1 GPIO 初始化失败 / bit2 DWT 可用 / bit3 SWD 已开 / bit4 清过超深睡标志 |
| +0x10 | busy_levels | bit0 见过 BUSY 低 / bit1 见过 BUSY 高 |
| +0x14 | busy_polls | 等 BUSY 一共轮询了多少次 |
| +0x18 | busy_timeouts | 等 BUSY 超时了几次 |
| +0x1C | gpio_err | `app_io_init` 返回非 0 的次数 |
| +0x20 | ms_init | 复位 + 初始化序列耗时（毫秒） |
| +0x24 | ms_write | 传 30000 字节耗时（毫秒） |
| +0x28 | ms_refresh | 刷新到 BUSY 松开耗时（毫秒） |
| +0x2C | build_id | 固件构造号 |
| +0x30 | boot_count | 进 `main_init()` 的次数。这块 RAM 是 NOLOAD、**软复位不清**，所以它一直涨就是芯片在反复复位 |
| +0x34 | uds_seen | 见到并清掉 AON `SOFTWARE_1 == 0xF175` 的次数（> 0 = 之前那个死循环就是它） |
| +0x38 | boot_magic | `0xB007C0DE`，用来认领上面两个数（别的值说明还没被我们写过） |

stage 取值：

| stage | 含义 |
|---|---|
| 64 | `main_init()` 跑到了（SDK 的初始化还没走完 —— 卡在 `soc_init`/`hal_flash_init` 这一带） |
| 1 | `main()` 进来了 |
| 2 | `sys_swd_enable()` 调过了 |
| 3 | 7 根脚配好了（**卡这里 = GPIO 配不动**） |
| 4 | 屏复位脉冲发完 |
| 5 | 整条初始化序列发完（**卡这里 = 屏不应答 / BUSY 不对**） |
| 6 | 测试图在 RAM 里拼好了 |
| 7 | 30000 字节传进屏了 |
| 8 | 刷新完 —— 屏上这时候就该有图了 |
| 9 | 空闲循环（心跳在涨） |

为什么放 `0x3001F000`：GR5513 的 RAM 是 `0x30000000` + 128KB，链接脚本把顶上 4KB
单独划成 `RAM_DBG`，栈顶也钉在 `0x3001F000` 往下长，所以栈永远踩不到它；
而且地址是写死的，读的时候不用去查 map 文件。

---

## 五、目录结构

```
outputs/firmware/
├── README.md                     ← 本文件
├── build.sh                      ← 一键：编译 + 打包
├── zk42v-custom-512k.bin         ← 打包产物（整片 512KB，拿去 flash-app.sh）
├── zk42v-epd-app/
│   ├── GCC/
│   │   ├── Makefile              ← 用 SDK 的编译参数，目标 zk42v_epd
│   │   ├── gcc_linker_zk42v.lds  ← APP 落在 0x0100A000、栈顶 0x3001F000
│   │   └── out/                  ← 编译产物（zk42v_epd.bin / .elf / .map）
│   └── Src/
│       ├── main.c                ← 主流程：开 SWD → 配脚 → 初始化 → 传图 → 刷新
│       ├── board/zk42v_board.h   ← 引脚表、屏参数（都带出处）
│       ├── board/zk_dbg.h        ← 调试状态块定义
│       ├── epd/epd_zk42v.[ch]    ← 屏驱动（重放原厂的序列）
│       ├── img/testimg.[ch]      ← 体检图
│       └── config/custom_config.h← SDK 配置（GR5513BEND、APP 在 0x0100A000）
└── tools/
    ├── fwpack.py                 ← 把 APP 打包成整片镜像（含自检）
    └── test_fwpack.py            ← 打包器的离线自测
```

配套（在 `outputs/pyocd/`）：

| 文件 | 作用 |
|---|---|
| `flash-app.sh` | `MODE=app` 写入 / `MODE=appverify` 只读验收 |
| `status.sh` | 读 `0x3001F000` 的调试状态块 |
| `led-window-user.py` | 新增了 `flashwrite` 的 `app`/`appverify` 两个模式，和 `status` 命令 |

---

## 六、离线测试（不用硬件，随时可跑）

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware
python3 tools/test_fwpack.py            # 打包器：18 项

cd ../pyocd
python3 test-flashwrite.py              # 烧写脚本：含 app/appverify/status，70+ 项
python3 test-dumpresume-modes.py
python3 test-sanity.py
python3 test-flashlab.py
python3 test-flashlab2.py
python3 test-dapinfo.py
python3 test-led-user.py
```

`test-flashwrite.py` 里跟这一版直接相关的用例：

| 用例 | 验的是什么 |
|---|---|
| L | `MODE=app` 只擦 APP 那几颗 + 一颗信息扇区，bootloader/NVDS 不动 |
| L2 | 镜像校验和对不上 → 拒刷，一个字节不动 |
| L3 | 复位向量不在 APP 段里 → 拒刷 |
| L4 | 中途写失败 → 不写信息扇区（保持旧账，bootloader 会走 DFU） |
| M/M2/M3 | `MODE=appverify` 一致时报 OK、被改过报 FAIL、没刷过报 FAIL，且全程只读 |
| N/N2/N3 | `status` 能认出自研固件、报出 stage、心跳、BUSY 电平，且只读 |

---

## 七、这一版还有哪些不确定（诚实列出来）

1. **黑/白/红极性**：只能靠第 1 步那张体检图实测确定。同一份固件里
   `0x24`（黑白面）和 `0x26`（红面）的数据含义只差这个。
2. **BUSY 的有效电平**：按 SSD1680/UC81xx 那一族的惯例写成"高=忙"，
   但驱动里做了双向兜底（两种电平都记进状态块），万一反了不会死等。
3. **P0_24（pins[7]）**：原厂 `pins_init` 把它配成输出并拉高，但后面再没动过。
   我们照抄（输出+高）。如果屏一点反应都没有，这是第一个要试反的脚。
4. **SDK 的 `soc_init()`**：它会 `platform_flash_enable_quad()`（把 XIP 切成四线）。
   原厂 APP 是同一套 SDK，所以我们认为它在这块板上没问题；万一挂在这里，
   状态块会停在 **64**（`main_init` 写了 magic，但 `main()` 还没进去）——
   这正是我们把 magic 提前到 `main_init` 里写的原因：能区分"根本没跑"和"跑了一半"。
   另外 SDK 那份 `main_init` 会先问 `pwr_mgmt_get_wakeup_flag()`，
   判成"深睡唤醒"就直接睡死、`main()` 永不执行 —— 我们把 `main_init` 整个接管了，
   跳过这一问（这块板是 bootloader 直接跳过来的，永远该按冷启动走）。
5. **UART 引脚**：还没挖出来，所以这版不打日志。下次可以顺手从原厂固件的
   引脚复用寄存器写入里找出来。

## 八、下一版打算做什么（方向 B 的第二步）

1. 把极性/方向写死进驱动，画一张正常图片（比如一张 400x300 的黑白红图）；
2. 把「基站」那一半接上：先用 USB-TTL 串口做一条最小协议
   （`长度 + 数据 + 校验`）把任意图片推给价签，价签收到就刷新；
3. 再往后才是无线（BLE / 2.4G），那一步要看你想不想继续用原厂那套私有协议。

---

# 附：B1 实测复盘（2026-09-27 凌晨）

## 日志里确认的事实

| 事实 | 依据 |
|---|---|
| 写入路径 100% 正常 | `MODE=app` 跑了两次都成功（各 11 秒写完 19+1 颗扇区）；`MODE=appverify` 逐字节对账 `APP_VERIFY_OK` |
| 00:04–00:05 三次失败与芯片无关 | 三次日志都是 `No connected debug probes` —— ST-Link 没插好 |
| 连上的那一刻调试口是好的 | `status.sh` 报"直接连上了，而且 CPUID 读得出来（试了 1 次）" |
| 当时 CPU 是**停住**的 | `DHCSR=0x00030003`，S_HALT=1 —— 这是上一轮 `appverify` 留下的状态（它 halt 之后没 resume） |
| 3 秒后调试口没了 | resume + 等 3 秒后连 CPUID 都读不到 |

## 关键判断：那不是"读法问题"

2026-09-22 的 `flash-lab4` 已经证明过**CPU 跑着的时候也能读内存**：

```
  +   7ms  0x0100A200 = 0xF7C03400 ...      ← 这会儿 XIP 还没开，读到的是死值
  + 892ms  0x0100A200 = 0x6803825A ...      ← CPU 还在跑，已经读到真数据了
```

所以 `status.sh` 那次"读不到"只能解释成：**调试口在这 3 秒里被重置或关掉了**。
而 v1 的 `status.sh` 却把"读不到"直接说成"跑的还是原厂固件" —— 这是误判，
已经在 v2 里改掉（现在的规矩是：读不到先重连，再看 PC 落在哪）。

## 最可疑的病根（代码依据）

`platform/soc/src/gr_soc.c`：

```c
#define SOFTWARE_REG1_ULTRA_DEEP_SLEEP_MAGIC 0xF175

static void ultra_deep_sleep_wakeup_handle(void)
{
    if (SOFTWARE_REG1_ULTRA_DEEP_SLEEP_MAGIC == (AON->SOFTWARE_1 & 0xFFFF))
    {
        hal_nvic_system_reset();      // 复位整个系统
        while (true);
    }
}

void soc_init(void)
{
    ultra_deep_sleep_wakeup_handle();   // 每次启动都会走这里
    ...
}
```

`AON` 在 `0xA000C500`，`SOFTWARE_1` 在 `+0x60` = `0xA000C560`，属于**永远在线域
（软复位不清）**。也就是说：只要这个标志还在，每次启动都会在 `soc_init()` 里
再复位一次 —— 表现就是"刷完不亮、过一两秒调试口消失、反复重启"。

这条能解释全部现象（写入 OK / 一开始能连 / 3 秒后连不上 / 屏不亮），
但**当时还没读到寄存器实证**。所以 B1.1 两手都做了：

1. `main_init()` 里直接把这个标志清掉（我们从 bootloader 直接跳进来，永远该当冷启动）；
2. 同时记 `uds_seen`（清过几次）和 `boot_count`（进 main_init 几次）——
   修复生效之后，`uds_seen > 0` 就反过来证明刚才那个死循环确实是它；
   如果 `uds_seen` 一直是 0 而问题依旧，那嫌疑就排除，看 `boot_count`/PC 继续查。

## 下一轮要看的三个数

```
bash status.sh
```

* **PC** 落在哪 —— 一直在「我们的 APP」里 = 活得好；在 ROM/APP 之间跳 = 还在复位；
* **AON SOFTWARE_1** —— 低 16 位还是 `0xF175` 就说明标志没清掉（或者又被写回去了）；
* **boot_count / uds_seen** —— 前者一直涨 = 还在重启；后者 > 0 = 病根就是那个标志。

---

# 附二：B1.2 —— 真凶是「低功耗时钟选错了源」

## 证据链（每一步都能复现）

1. **`status.sh` 采样**：PC 五次里三次落在 `0x0100CEAE / 0x0100CEB0`，一次落在
   `0x30004E18`。都在很小的范围内 → 不是乱跳，是**卡在某个循环**。
2. **按 `.map` 反查**（我在临时目录里把 B1 原样重建了一份，复位向量 `0x0100C6A9`、
   74428 字节，跟芯片里跑的那份一致）：
   `0x0100CEAE → platform_disable_sleep_timer+0x6`，`0x30004E18 → ramfunc+0x1C`
   （`libble_sdk.a(platform_clock.o)`）。
3. **反汇编**：
   ```
   0100cea8 <platform_disable_sleep_timer>:
    100ceaa: ldr r4,[pc]   @ r4 = 0x30004E19 → 调 RAM 里那个函数
    100ceac: blx r4
    100ceae: cmp r0,#1     ← 采样停在这
    100ceb0: beq 100ceac   ← 采样也停在这：返回 1 就再调一次
   ```
   而 `0x30004E18` 那个 RAM 函数是：`ldr r3,[pc] (=0xA000C500 AON 基址); ldr r0,[r3,#0x80]; ubfx r0,r0,#1,#1`
   —— 读 **`AON_PSC_CMD` 的 bit1**。查头文件：`AON_PSC_CMD_MCU_PWR_BUSY` 就是 bit1。
4. **谁把它逼到这一步**：`platform_clock_init()` 的分支里先 `platform_set_psc_clk(1)`
   （内部：等 PSC 空闲 → 发 **opcode 7 = `RTC_CLK`**）→ 紧接着
   `platform_disable_sleep_timer()` 又要等 PSC 空闲，**这时候 busy 已经是 1**。
   也就是说：**那条 RTC_CLK 命令发出去以后一直没完成。**
5. **为什么完不成**：`platform/soc/src/gr_soc.c` 的 `platform_init()`：
   ```c
   #if CFG_LPCLK_INTERNAL_EN
       platform_clock_init_rng(SYSTEM_CLOCK, RNG_OSC_CLK2, 500, 0);   // 内部 RC
   #else
       platform_set_rtc_crystal_delay(CFG_CRYSTAL_DELAY);
       platform_clock_init(SYSTEM_CLOCK, RTC_OSC_CLK, ...);           // 要 32.768k 晶振
   #endif
   ```
   我们的 `CFG_LPCLK_INTERNAL_EN` 是 **0** → 走 RTC → 而这块板子没有那个晶振。
6. **原厂怎么做的**：`outputs/analysis/app.asm` VA `0x0101F0F4`：
   ```
   movs r3,#0 ; mov.w r2,#500 ; movs r1,#2 ; movs r0,#4 ; bl ...
   ```
   `r1=2` = `RNG_OSC_CLK2` = **原厂用的就是内部 RC**（`r0=4` 是 16MHz 系统时钟）。
   跟我们的结论完全一致。

## 修法

`Src/config/custom_config.h`：

```c
#define CFG_LPCLK_INTERNAL_EN   1      // 原来是 0
```

改完编译产物里 `platform_init` 变成调 `platform_clock_init_rng(..., RNG_OSC_CLK2, ...)`，
`platform_set_rtc_crystal_delay` 也不再被调用 —— 和原厂一致。

**刷完必须断电重上电**：卡住的 PSC 状态在 AON 域，按 RST 清不掉。

## 顺手修掉的一个工具坑

`zk42v-epd-app/GCC/Makefile` 里虽然有 `-MD`（生成依赖文件），但从来没 `-include` 进来，
所以**只改头文件不会触发重编**——我第一次改 `CFG_LPCLK_INTERNAL_EN` 时，
重编出来的 SHA 跟上一版一模一样，就是这个原因。现在加了
`-include $(wildcard $(BUILD_OBJ)/*.d)`。

## 这一轮学到的两条（写进工具里了）

* 「读不到」**不能**当结论。v1 的 `status.sh` 把「读不到」直接说成「跑的还是原厂固件」，
  是误判；现在的规矩是：先重连，再看 PC 落在哪。
* PC 在 ROM 和 APP 之间跳**不等于**芯片在复位 —— 那次采样里的 ROM 值其实是上一轮
  `appverify` 把 CPU 停在启动阶段留下的。真正的复位判据是 **DHCSR 的 `S_RESET_ST`**
  （`0x01030003` 里它是 0 → 没复位）。现在每次采样都会打印这个位。
