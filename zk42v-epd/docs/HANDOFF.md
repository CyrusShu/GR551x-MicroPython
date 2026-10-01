# ZK42V 价签自研固件 —— 交接说明（2026-10-01）

## ⚡ 2026-10-01：**build 59 —— 毫秒时基换成 AON 定时器**（先看这段）

**这一版修的是"日历每 ~8 小时跨一天"的老大难**：时基原来拿 `DWT->CYCCNT`（CPU 周期）
折算毫秒，但 GR5513 的主频会变（空闲 16 MHz、刷屏/连 BLE 时高得多），于是毫秒
**快 3~4.8 倍**。现在改用 **AON 定时器** `AON->TIMER_VAL`（0xA000C594，低功耗时钟域，
与主频无关），开机动用 DWT 标定它的频率（实测 **~28 kHz，而且是递减计数**），
标不出来依次退到 SDK 的 `sys_lpclk_get()`、名义值 28000 —— **AON 完全不涨才退回老做法**。

| build | 内容 | 镜像 |
|---|---|---|
| 59 | AON 时基 + 开机标定 + 回退 + 状态块 104~111 号字 | `bin_size 130224 / check_sum 0x00CAE38F`，SHA-256 `fd4e55cb…` |

* 改了：`Src/main.c`（`zk_timebase_init()` / `tick_ms64_raw()`）· `Src/board/zk_tick.h`
  （新增 `zk_tick_aon_step()`）· `Src/board/zk_dbg.h`（`ZK_DBG_WORDS` 104→112）·
  `outputs/pyocd/led-window-user.py`（译码 + **时基实测**）· `tools/test_tick.py`（+5 条用例）。
* 完整来龙去脉（症状 → 证据 → 根因 → 修法 → 副作用）在
  `outputs/firmware/docs/feature-backlog.md` 的「✅ 已修：毫秒时基换成 AON 定时器」一节。
* **验收就一条**：`cd outputs/pyocd && MODE=app bash flash-app.sh` 之后
  `STATUS_SETTLE_MS=0 bash status.sh`，看新加的那段「时基实测」——
  `价签走了 X 秒 / 真实 Y 秒 ⇒ 比值 ≈ 1.00`（修之前是 3~4.8）。
  旁边「毫秒时基（build 59 起用 AON 定时器）」那一组会印出频率和来源。
* 副作用（可接受、其实是"回到本来该有的值"）：所有拿 `tick_ms()` 计时的逻辑现在走真实
  时间 —— 电池每 60 秒读一次（以前 ~15 秒）、日历重画间隔按真时间走。
  `epd_wait_busy` 的 30 秒超时不受影响（它数的是 200µs 步数，不是 tick）。
* 安全网不变：刷坏了 `MODE=app bash flash-app.sh` 刷回 build 58（镜像在 git 里），
  或者 `MODE=restore bash flash-write.sh` 整片回出厂。

## ⚡ 2026-09-30 下午：新增两块（先看这段）

**1. 价签固件 build 48 / 49 / 50**（分支还是 `zk42v-boot-calendar`）：

| build | 内容 | 镜像 |
|---|---|---|
| 48 | **写图+刷新"整轮重试"** —— 修掉"固件说刷了、屏没动" | `bin_size 128828 / check_sum 0x00C80A2D` |
| 49 | 调试命令 **`75 <ctrl> [temp]`**（局刷实验开关，走 48 那条能工作的路径） | `128928 / 0x00C85541` |
| 50 | **时间+天气合并成一条命令**：`20 <utc4> <tz> <mode> [wx] [temp]`（向后兼容） | `128992 / 0x00C857E8`，SHA-256 `7bf3c4b6…` |

* build 48 的来龙去脉（症状 → 证据表 → 根因 → 修法）在
  `outputs/firmware/docs/feature-backlog.md`，**必读**：
  `epd_pins_release()` 会把 `P1_8(AUX)` 屏供电也松开，冷启动的第一次激活被屏忽略
  （BUSY 只忙 0.3~0.5 秒），只有"整轮重来"才真刷（17 秒 / 8 万+ 次轮询）。
* 判据：`status.sh` 里 `写图+刷新 ≈24000ms` + `BUSY ≈8 万+` = 真刷；
  `≈7000ms` + `一两千` = 假刷；`panel_state=3` = 三轮都没真刷。

**2. Mac / ESP32 基站**（新目录 `outputs/ble-base/`，README 在 `esp32/README.md`）：

* Mac 版 `zk_ble_base.py`：`probe / sync / watch / raw`，坐标已改**深圳公明广场 22.7809,113.8861**。
  `raw` 发原始字节给面板（`03`=命令 `04`=数据）。
* ESP32 版 `esp32/zk_base_esp32/`：**build-6**，WiFi→Open-Meteo（纯 HTTP，绕开 TLS 吃堆）→
  BLE central 写时间/天气。已实机跑通（屏真的刷出来过）。
  坑：HTTP 只能用纯 HTTP（`WX_USE_TLS 0`）；温度别被 JSON 的 `current_units` 骗；
  价签"重新出现"要把"已发过"缓存作废；发之前超过 5 分钟要重新对表。
* ESP32-CAM（经典 ESP32）**跑不了 OEPL 官方 AP 固件**（2.92 只有 S3 16/8 等镜像），
  但当"我们自己的基站"足够（1.79MB / 56% 分区）。

**3. 局刷 —— 已判定"可行但暂不做"（2026-09-30 决定，完整线索见 `docs/feature-backlog.md`）**：
原厂 `0xD7 + 短延时（0.16~0.64 秒，查表 0x0100D8A0）` 就是**快刷**（实测 7.3 秒 / BUSY≈0，
对比全刷 24 秒 / 8.5 万次），但**整屏用必发灰**、`0x1A` 调大甚至全白；
社区（qbsg）那个「校准局刷参数」是**他们固件的私有命令 `E6 <param>`**（不是面板寄存器），
照抄无用。真要做只能照 UC8176 datasheet 啃 partial 窗口 + 局刷 LUT。
实验开关 `76/77/78` 已经加进固件、**默认关闭**（注意它们是黏的，实验完要关掉 + 全刷洗画面）。

> 复制这一整份到新会话即可。所有路径都是**绝对路径**，命令可以直接粘。

## 0. 一句话现状

ZKONG ZK42V（GR5513BEND）4.2 寸三色价签，**保留原厂 bootloader、只换 APP**；
屏已点亮、SWD 推图/网页 BLE 推图都通，固件自己画**整页农历月历 + 天气 + 电量**。
当前固件 **build 59**（分支 `zk42v-boot-calendar`；开工默认日历模式，
**毫秒时基已换成 AON 定时器**）。**机器上刷到哪一版要看 `status.sh` 里的 `build=`**
（2026-10-01 中午最后一次已知是 build 58；build 59 还没刷上去）。

## 1. 硬件

| 项 | 值 |
|---|---|
| 整机 | ZKONG ZKC42V-N，PCB 丝印 `ZK42V_V1.1` |
| 主控 | **GOODIX GR5513BEND**（Cortex-M4F + BLE 5.1，128KB RAM / 512KB flash，NVDS @0x0107F000） |
| 屏 | 4.2" 400×300 **三色**（UC8176），7 根脚：CS/SCLK/SDI/DC/BUSY/RST + AUX(P1_8) |
| 供电 | **2× CR2450 电池夹**（J2/J3，3.0V 标称，并联） |
| 调试 | SWD 焊盘（GND/IO/SWCLK/SWDIO/UTX/RST/VCC/URX），另有 NFC |
| flash 布局 | `0x01000000` boot_info · `0x01002000` APP 镜像信息(bin_size+check_sum) · `0x01003000` 原厂 bootloader · **`0x0100A000` 我们的 APP** · `0x0107F000` NVDS（**别动**） |

## 2. 工作目录与仓库

```
工作根目录      /Users/mac/Documents/Codex/2026-09-15/a
固件工程        outputs/firmware/（zk42v-epd-app = 源码，tools = 生成器+自测，docs = 文档+预览图）
上位机脚本      outputs/pyocd/（status.sh / flash-app.sh / flash-write.sh / led-window-user.py）
网页            work/github/epd-nrf5-user/（tsl0922 那套的本地魔改版，本地起 http.server 打开）
留档仓库        work/github/gr551x-mp-push/（远端 git@github.com:CyrusShu/GR551x-MicroPython.git，
                我们这个项目的所有快照都在它的 zk42v-epd/ 子目录里）
反汇编产物      outputs/analysis/app.asm（原厂 APP）、work/ble-dis/
```

**Git 分支（都在 gr551x-mp-push 仓库）**

| 分支 | 内容 | 最新提交 |
|---|---|---|
| `zk42v-epd` | 主线，最后一次是 **build 45** | `e350767` |
| `zk42v-boot-calendar` | 新分支，**build 46 开机默认日历** | `93cb617`（当前 HEAD） |
| `master` | 原始镜像仓库，跟本项目无关 | — |

改完代码的固定流程：

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/work/github/gr551x-mp-push/zk42v-epd
python3 tools/make-snapshot.py          # 把 outputs/ 里的文件拷进仓库（FILES 表要加新文件）
cd .. && git add zk42v-epd && git commit -m "..." && git push origin <分支名>
```

## 3. 镜像与自测现状

| build | 内容 | check_sum | 大小 | 谁在跑 |
|---|---|---|---|---|
| 45 | 时区诊断（`status.sh` 印网页给的时区） | `0x00C7ED8A` | 128748 B | **机器上刷的就是它**（2026-09-30 09:45 刷的） |
| 46 | 开机默认日历模式（**还没刷**） | `0x00C7F706` | 128764 B | — |

* 镜像上限：bootloader 硬要求 `bin_size ≤ 0x24FC0 = 151488` 字节，超了会停在 `0x01003720` 死循环
  （`tools/fwpack.py` 会拦）。现在用掉 128764，**余 ~22.7KB**。
* 离线自测 **11 套**（不需要硬件）：`test_fwpack / test_img2epd / test_dbg_layout / test_adv_data /
  test_lunar / test_jieqi / test_weather / test_bat / test_opt / test_tick / test_gui`，
  另有上位机的 `pyocd/test-symbols.py`、`test-flashwrite.py`。
* 只编译不打包：`NO_PACK=1 bash build.sh`。

## 4. 常用命令（都能直接粘）

```bash
# 编译 + 打包（产物 outputs/firmware/zk42v-custom-512k.bin）
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware && bash build.sh

# 跑全套离线自测（11 套）
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware
for t in test_fwpack test_img2epd test_dbg_layout test_adv_data test_lunar test_jieqi \
         test_weather test_bat test_opt test_tick test_gui; do printf "%-18s " $t; \
  python3 tools/$t.py >/tmp/o.txt 2>&1 && tail -1 /tmp/o.txt || echo 失败; done

# 看屏幕/固件状态（先 halt 再读、隔一会儿采 5 个点，最后恢复运行）
#   build 59 起还会多打两段：AON 定时器频率 + 「时基实测」（比值应 ≈1.00）
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd && bash status.sh

# 刷机：日常只写 APP 段
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd && MODE=app bash flash-app.sh
# 安全网：整片刷回出厂备份（~3 分钟）
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd && MODE=restore bash flash-write.sh

# 网页（手机 Chrome 打开 http://<你的电脑IP>:8777 或本机 localhost）
cd /Users/mac/Documents/Codex/2026-09-15/a/work/github/epd-nrf5-user/html
python3 -m http.server 8777

# 看页面版式（不用硬件）：整页 / 表头放大 4 倍
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware
python3 tools/gui_preview.py 1 1790625600 /tmp/cal.png 8 3970 264 0 2 26
python3 tools/header_zoom.py 1 1790625600 /tmp/hdr.png 8 3970 264 0 2 26
python3 tools/bat_sheet.py                 # 电池图标随电压的对照图
```

## 5. 已经做完的（里程碑）

| build | 做了什么 |
|---|---|
| B1 | 屏点亮（原厂 bootloader 不动，只换 APP） |
| B2-B | SWD 共享内存推图 |
| B2-A | 网页 **BLE 推图**（tsl0922 那套协议，MTU 244 + RLE） |
| 26~31 | 整页农历月历 + 时钟页；时基修成 64 位（`zk_tick_ms64()`）；电池/片内温度真读数 |
| **40** | **UI 方案 v1 定稿**（tag `zk42v-ui-v1`）：表头红年月/黑农历/红生肖，黑星期条，大日号+小农历，今天红圆 |
| 41 | 天气：表头「星期」撤掉，改画**图标 + 文字**（手机经 `0x71` 下发） |
| 42 | 电压显示 2.59V 的真相：ADC 通道被温度 init 抢走 → 每次读之前重新 init；参考换 1.28V |
| 43 | 表头收尾：温度跟天气走（**2 倍字 10×14**），电池只留图标；加 `0x72` 立刻读电池；表头放大图工具 |
| 44 | 电量曲线换 **CR2450**（3.0V=100% / 2.5V=0%，表在 `Src/board/zk_bat_curve.h`） |
| 45 | 网页时区记进状态块（`status.sh` 会印），查"23:05 跳到 30 号"那件事 |
| **46** | **开机默认日历模式**（新分支 `zk42v-boot-calendar`） |

## 6. 踩过的坑（**新会话必读**）

1. **镜像上限** 151488 字节（`bin_size`），超了刷进去也起不来；`-specs=nano.specs` 已在用。
2. **时基**：一切"定时"逻辑必须用 `zk_tick_ms64()`（`Src/board/zk_tick.h`）。
   老代码用 CYCCNT 直除，268 秒绕一次圈，屏每 4.5 分钟自己刷一页。
3. **ADC 只有一个配置寄存器** `AON->SNSADC_CFG`（通道+参考+使能都在里面）：
   SDK 的 `hal_adc_vbat_read()` 只翻 `VBAT_EN`、**不重选通道**，所以**每次读之前必须重新
   `hal_adc_vbat_init()` / `hal_adc_temp_init()`**，否则读到的是别人的通道
   （这就是"喂 3.3V 却显示 2.59V"的原因：其实在读温度二极管）。
   顺带：0.85V 参考满量程只有 3.28V，会削顶，已换 1.28V；那条曲线表是
   `zk_bat_curve.h`，固件/主机预览/对照图脚本共用同一份。
4. **字模三套来源**：原厂 u8g2 子集（`tools/gen_vendor_font.py`）· 文泉驿点阵宋体 BDF
   （`tools/gen_wqy_bitmap.py`）· TTF 栅格化兜底（`tools/cjk_from_ttf.py`，要 Pillow，
   venv 在 `/tmp/zkvenv`）。汇总生成 `tools/gen_font.py` → `Src/img/zkgui_font.h`；
   **`CJK_ORDER` 的顺序就是 C 里的索引，改顺序必须同步改 `zkgui.c` 的 `CJK_xxx`**。
5. **农历/节气**表和算法来自**原厂固件**（`tools/gen_lunar.py` / `gen_jieqi.py`），
   有锚点自测（2025-08-07 立秋、08-23 处暑 等）。
6. **网页发的时间必须带对时区**：SET_TIME = UTC 秒 + 时区小时（有符号）。
   遇到过浏览器报 `+9`，结果屏上比北京时间快 1 小时、23:05 就跳到第二天。
   网页上已加「时区」输入框（留空=用浏览器报的，填 8 强制北京时间），
   `status.sh` 也会印"网页给的时区"。
7. **墨水屏只在重画时更新**：不重画就一直留旧画面（换电池/刷机后要点一下网页「日历模式」）。
8. **zkgui 的天气图标**（build 41 起）来自开源字体渲染，不是手画：
   `tools/gen_weather.py`（8× 超采样 → 阈值 → 去孤立点）→ `tools/weather_img.py` → `Src/img/weather.c`。
   现在这套是 **mix**：太阳/多云 = Material Symbols，云/雨/雷/雪/雾/风 = Font Awesome。
9. **qbsg 那个"12 光芒红太阳"抠不出来**（试过三份固件：`PP_da14585_4.2_RCH.img` 加密、
   `PP_da14585_4.2_CH.img` 与 `PP5513_ 4.2.bin` 明文但**没有整页位图/图标位图**，
   它们的画面是代码画的）。用户已说"不用画了"，**别再折腾这个图标**。
   （`docs/preview/preview-sun-*.png` 是那次实验的遗留图，可以删。）
10. **网页仓库** `work/github/epd-nrf5-user` 的 `origin` 是 HTTPS 且本机没凭据，
    我给它加了个 `ssh` remote；推的时候用 `git push ssh main`。

## 7. 私有 BLE 命令（网页「发送命令」框里敲十六进制）

| 命令 | 作用 |
|---|---|
| `70 00` / `70 01` / `70 02` / `70 04` / `70 08` | 画面选项：全关 / 反色 / 旋转 180° / 不画农历 / 节气加粗（可叠加） |
| `71 <码> [温度]` | 天气（码 1晴 2多云 3阴 4小雨 5大雨 6雷阵雨 7雪 8雾 9风；温度有符号整度）。例 `7102 1A` = 多云 26℃；`7100` 取消 |
| `72` | **立刻重读一次电池 + 重画一页**（扫电压/挑电量图标用），回通知 `bat=.. pct=..` |
| 时间 | 网页「日历模式 / 时钟模式」按钮 = SET_TIME（UTC 秒 + 时区小时 + 模式） |

## 8. 待办 / 待你拍板

1. **时间/模式持久化**（backlog 第 15 条）—— 做完才是真正的"装电池就显示日历"：
   把时间写进 APP 区后面那颗**空扇区**（候选 `0x0107E000`，先用出厂备份确认整颗是 `0xFF`，
   **绝不碰 0x0107F000 的 NVDS**），上电读回来直接画一页。
   ⚠ 现在 build 46 只做到"默认日历模式"，时间没同步过就不会自动画。
2. **天气自动更新**（走 A：手机端取好再发）：建议在网页面板加一排天气按钮 + 温度输入 +
   一键从 Open-Meteo（免 key）取实时天气再发 `0x71`；打开网页自动同步一次。
   （固件侧可选加一条"值没变就不重画"，省 16 秒全刷。）
3. **电池**：CR2450 曲线已换成 3.0V=100%/2.5V=0%（`zk_bat_curve.h`），要不要拿可调电源
   扫 2.5/2.6/2.7/2.8/2.9/3.0V 各跑一次 `status.sh`，把曲线再核准一遍。
4. 其余 backlog：局刷校准（风险最高）、休眠时段（`0x75`）、倒计时、停车牌、字体/字号。

## 9. 用户偏好 / 硬约束（**照做**）

* 命令**必须能直接粘**；安全网永远是 `cd outputs/pyocd && MODE=restore bash flash-write.sh`。
* 改文件用 `apply_patch`；不要 `rm -f`；不要用 `cat >` 写文件。
* 文档写**中文**，要**证据链**（地址 / 日志 / 实测数字），别只给结论。
* 每轮结束报告：镜像 SHA-256 / check_sum / 扇区数 / 自测情况。
* 用户很在意**像素级证据**（"按像素级推理"），改版式先出预览图（`tools/gui_preview.py`、
  `tools/header_zoom.py`）让他看，再落地。
* 联网每轮要 `request_permissions {"network":{"enabled":true}}`（有时效，过期就重新申请）。
* 不要手画图标（他不认）；要"从网上搞好看的"或者用现成字库。

## 10. 证据/产物都在哪

```
outputs/firmware/README.md              逐版记录（每版为什么改、镜像哈希、自测数字）—— 最重要的一份
outputs/firmware/docs/feature-backlog.md 功能清单 + 控制通道 + 风险
outputs/firmware/docs/preview/          所有预览图（日历页 / 表头放大 / 天气图标 / 电池档位）
outputs/pyocd/status-*.log              status.sh 的历史体检日志（排查都靠它）
outputs/pyocd/flash-app-*.log           刷机日志（里面有 check_sum，能对上刷的是哪一版）
outputs/analysis/qbsg-生态调研.md        社区那套（qbsg/tsl0922）的调研：版式来源、像素测量
outputs/ZK42V-*.md                      硬件判定、原厂固件布局、备份步骤
work/github/gr551x-mp-push/zk42v-epd/   上面这些东西的 git 快照（含完整历史）
```
