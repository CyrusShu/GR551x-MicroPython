# B2-A：BLE 推图（协议对齐 tsl0922/EPD-nRF5）

> ## 进展（2026-09-27，build 22）—— **A.1 通，A.2 已实现，服务可见性修好了**
>
> **build 21 的实测**：手机连上 GATT 后报
> `No Services matching UUID 62750001-… found in Device`，而固件侧
> `ble_gatts_srvc_db_create` 返回 0、还分配了句柄 —— 也就是说**光调这个 ROM 函数
> 是不够的**：SDK 里 service 是当作 profile 注册的，得走
> `ble_gatts_prf_add()`（它内部 `ble_server_prf_add()` + `gapm_set_profile_added_flag()`），
> 协议栈随后回来加载 profile（`ble_service_load_cb`）时才真正建库并回调
> `BLE_GATTS_EVT_DATABASE_INITED_IND` 告诉我们句柄范围。
> build 22 改走这条路，并把 GATTS 读写事件收进 profile 自己的回调
> （`ble_event_handle` 的分发顺序：先问 profile，认领了就不往下传）。
>
> ## 进展（2026-09-27，build 21）—— A.1 / A.2 都实现了
>
> **A.1 达成**（build 20 手机实测）：名字 `ZK42V-EPD`、厂商数据
> `<FFFF> 5A4B 3432 56`、服务 UUID、广播间隔实测 103~108ms（我们设的 100ms）全对。
>
> **A.2 实现**（build 21）：`Src/ble/zk_epd_svc.c`
>
> * 服务/特征 UUID 跟他原厂**一字不差**（`62750001/2/3-…`），
>   版本特征回 `0x1a`（网页 `<0x16` 会提示固件太旧）；
> * 命令全实现：`INIT` `CLEAR` `REFRESH` `SLEEP` `WRITE_IMAGE`（RLE + 原始两种）、
>   `SEND_CMD/SEND_DATA`、`SET_TIME`（回显）、其余收下忽略、`SYS_RESET` 真复位；
> * 通知协议按他原厂的顺序：CCCD 打开时先推 13 字节配置（网页按「第 0 条通知」
>   解析出引脚/驱动型号，型号 0x03 = 4.2" 三色 UC8176，预览就是 400x300 三色），
>   `INIT` 后再推 `mtu=<可写长度> rle=1` —— **网页靠这条才打开 RLE**；
> * 图块收进 `ZK_IMG_BUF`（= B2-B 那块 30000 字节），黑白面/红面各 15000；
>   收到 `REFRESH` 后在空闲循环里写屏 + 刷新，刷完放开屏脚；
> * 兜底：万一网页没收到通知退回 v1.5 老 flags 格式，固件认得出来
>   （`ble_legacy`）并照老规矩解。
>
> **还没做**：他原厂那套日历/时钟模式的 GUI 绘制（我们只推图）。
>
> ## 进展（2026-09-27，build 20）
>
> **A.1 达成**：手机 nRF Connect 里能看到我们的设备了 —— 厂商数据
> `<FFFF> 5A4B 3432 56…`、服务 UUID `62750001-D828-918D-FB46-B6C11C675AEC`、
> RSSI -57 dBm。也就是**空中真的有包，而且能被发现**。
>
> 同一张截图还揪出一个我们自己的 bug：**设备名显示成 N/A**。
> 根因是厂商数据那条 AD 结构的**长度字节写成 0x09（应该 0x08）**，
> 多算了 1 个字节 → 把下一条结构的长度字节 0x0A 吞了 → 后面的「完整名字」
> 结构错位、被读成 `type=0x5A`。症状很隐蔽：`ADV_START` 照样 `status=0`，
> 固件状态块里一切正常，只有手机那边名字没了（所以按名字过滤一个都搜不到）。
> build 20 修好了，并加了 `tools/test_adv_data.py` 专门盯 AD 结构长度。
>
> build 20 另外做了两件事：**关掉开机扫描实验**（`ZK_BLE_SCAN_TEST 0`，
> 直接广播；扫描那条路留着开关，以后做「附近有什么」还能用），以及
> **记录并自愈 `ADV_STOP`**（以前广播要是停了，状态块一个数都不变）。
>
> **下一步就是 A.2**：建服务 + 两个特征 + 命令分发（`INIT/CLEAR/REFRESH/
> WRITE_IMAGE`），让 tsl0922 的网页能连上推图。还有一个待确认的小点：
> 手机详情页里我们的广播包**有没有 `Flags`**（协议栈按 `disc_mode` 决定加不加；
> Android 无所谓，iOS / Web Bluetooth 更挑）。
>
> ## 当时的证据链（build 19，保留）
>
> **实机结果（build 19，17:34 那次 `status.sh`）：**
>
> * **扫描听得一清二楚**：4 秒收到 395 条广播、去重 16 个设备（去重表 16 满了、
>   还溢出 48 次）、最强 -39 dBm ⇒ **射频和收通路都是好的**。
> * **广播数据变体 #0（厂商数据 + 名字，不带 `Flags`）= `ADV_START status 0` ✅**
>   ⇒ 发送侧的病根就是**广播数据里那条 `Flags(0x01)`**：协议栈会按 `disc_mode`
>   自己加，我们再塞一条就被判「duplicate/invalid」（`0x4A`）。
>   build 18 那套数据（带 Flags）正好是变体 #1，回的就是 `0x4A` —— A/B 对照闭合。
> * 事件账目分毫不差：1 + 1 + 395 + 1 + 1 = 399 = `ble_evt_count`
>   ⇒ 上一轮那个天文数字确实只是"没清零的 RAM"，不是控制器不吭声。
> * 旁证全绿：`AON PWR_RET01` 显示 comm core/timer 已上电、已放开复位；
>   `NVIC ISER0` 里 `IRQ1 BLE_SDK` / `IRQ2 BLE` 都使能；AON 定时器在走。
>
> 于是 `zk_ble.c` 里 `ZK_BLE_SCAN_TEST` 这条实验路径的任务完成了：结论已经落地，
> build 20 已按它收尾（固化变体 #0、关掉扫描、补 `ADV_STOP` 记录与自愈）。
>
> **再往回一层：为什么当时要设计这一版实验**
>
> **A.1（起协议栈 + 广播）卡住了，但性质变了 —— 找到了硬证据。**
>
> 状态块里 `ble_evt_id = 0x207` = `BLE_GAPM_EVT_ADV_START`、
> `ble_evt_status = 0x4A` = `BLE_GAP_ERR_ADV_DATA_INVALID`
> （「广播数据重复或非法」）。也就是命令全被接受、控制器也回了事件，
> 但**广播数据被协议栈判非法**，链路层于是没开始广播，空中没有包。
> （以前那句「从不回 ADV_START」是被没清零的 `ble_evt_count` 误导的读数。）
> 首要嫌疑：我们自己往广播包里塞了 `Flags(0x01)`，而 SDK 例程从来不塞、
> 由协议栈按 `disc_mode` 自己加 —— 两条 Flags 就是 "duplicate"。
>
> build 19 这一版一次 flash 同时做两件事（细节见 `../README.md` 附四）：
>
> 1. **扫描实验**：`ble_gap_scan_param_set` + `ble_gap_scan_start` +
>    `BLE_GAPM_EVT_ADV_REPORT`，数设备、记 RSSI -> 判定「射频活没活」；
> 2. **广播数据变体实验**：6 种广播数据组合（含去掉 Flags 的那版）逐个试，
>    每个的 ADV_START status 都记进状态块 -> 一眼看出哪种数据控制器认。
>
> **判据**：`bash status.sh` 里「B2-A.2 实验一/实验二」两段。
> 实验一听得到 + 实验二有变体被接受 = A.1 通，接着做下面的 A.2 服务与命令。
> 实验一听不到 = 射频没起来，转去反汇编原厂固件的 BLE 使能路径。

## 为什么对齐它

`tsl0922/EPD-nRF5` 已经把「手机浏览器 → BLE → 墨水屏价签」这条链路全做完了：
网页端有 5 种抖动算法、裁剪、涂鸦、加字，还有 RLE 压缩，MCU 端只负责解码 + 写屏 RAM。
我们只要让这块价签**会讲它那套协议**，现成的网页就能直接给它推图，不用自己写上位机。

（它的 MCU 是 nRF51/nRF52，我们是 GR5513 —— 字节级的固件不通用，**协议通用**。）

## 对端（网页）要求我们实现什么

从 `html/js/main.js` 和 `EPD/EPD_service.h` 里读出来的：

| 项目 | 值 |
|---|---|
| 服务 UUID | `62750001-d828-918d-fb46-b6c11c675aec` |
| 写/通知特征 | `62750002-d828-918d-fb46-b6c11c675aec` |
| 版本特征（读） | `62750003-d828-918d-fb46-b6c11c675aec`，读回 `APP_VERSION = 0x1a` |
| 广播 | 必须带上面那个服务 UUID（Web Bluetooth 按服务过滤） |
| 通知 | 客户端会 `startNotifications()` —— 支持 notify 最好，不支持它只是记条日志继续 |

命令（write 的第一个字节）：

| code | 命令 | 我们怎么处理 |
|---|---|---|
| `0x00` | SET_PINS | 忽略（我们的引脚是固定的） |
| `0x01` | INIT | 屏复位 + 整套初始化序列 |
| `0x02` | CLEAR | 全白一帧 + 刷新 |
| `0x03`/`0x04` | SEND_CMD / SEND_DATA | 直接转发给屏（调试用，留着） |
| `0x05` | REFRESH | 把缓冲写进屏 RAM + `0x22=0xC7` 刷新 |
| `0x06` | SLEEP | 屏进深睡 |
| `0x30` | WRITE_IMAGE | 收图（见下） |
| `0x90` | SET_CONFIG | 接受、忽略（我们的屏型固定 400x300 三色） |
| `0x91`/`0x92`/`0x99` | SYS_RESET / SYS_SLEEP / CFG_ERASE | 最小实现（复位可以用 `NVIC_SystemReset`） |
| `0x20`/`0x21` | SET_TIME / SET_WEEK_START | 忽略（那是它内置日历模式的） |

### WRITE_IMAGE 的帧格式

```
[0x30][flags][data...]
```

| flags 位 | 含义 |
|---|---|
| bit0 | 0 = 黑白面，1 = 红面 |
| bit1 | 1 = **本面的第一块**（MCU 要重设 RAM 窗口/游标） |
| bit2 | 1 = 这一块是 RLE 压缩的 |

RLE 解码（网页端 `rle.js` 的编码规则，反过来）：

```
控制字节 c：
  c & 0x80 != 0  ->  重复下一个字节 (c & 0x7F) + 3 次
  否则           ->  后面跟 c + 1 个原文字节
```

一帧图像 = 两个面各 15000 字节（400x300 每行 50 字节，MSB first）：
黑白面 bit=1 白 / 0 黑；红面 bit=1 红（红盖过黑白面）。

客户端**不等 ack**：它靠 `writeValueWithResponse` 限流，每 `interleavedCount` 块插一次
带响应的写。所以我们收完一块就处理，不用回包。

## 固件侧实现计划

| 步 | 做什么 | 判据 |
|---|---|---|
| **A.1** | 起协议栈 + 广播（名字 `ZK42V-EPD`，广播数据里带服务 UUID） | Chrome 打开 tsl0922 的网页，点连接能看到这个设备 |
| **A.2** | 加服务 + 两个特征；写事件里按命令分发（INIT / CLEAR / REFRESH / SLEEP） | 网页点"清屏"，屏变白 |
| **A.3** | WRITE_IMAGE 收块 + RLE 解码 + 填缓冲；REFRESH 时刷屏；传完发个通知 | 网页推一张图，屏上出现 |

要点：

- 图像缓冲就用 B2-B 那块 `s_img[30000]`，驱动/刷新完全复用；
- **RLE 是按块传的**，每块里的 RLE 码是完整的（网页端切块时就保证了），所以可以边收边解；
- BLE 的 MTU 交换要开（默认 23，网页会按 `mtusize` 切块，交换到 247 更快）；
- 广播间隔先用 100ms 左右；连上就停广播，断开再开。

## 与 B2-B 的关系

B2-B 的 SWD 共享内存信箱**留着当后路**：BLE 万一哪天不灵，或者要批量灌图，还能走 SWD。
两边的入口都落到同一套 `s_img` + `epd_write_image()` + `epd_refresh()`。

## 待你确认

1. 设备名用 `ZK42V-EPD` 行不行（网页列表里显示的就是它）？
2. 网页端你要用哪一份：官方托管的 `tsl0922.github.io/EPD-nRF5`，还是本地双击它仓库里的
   `html/index.html`？（后者能自己改，也不涉及分发）

（已确认：设备名用 `ZK42V-EPD`。）

---

## SDK API 侦察结果（写代码前先对准，免得写出来编译不过）

GR551x 这份 SDK 的 GATT 服务和常见的 `ble_gatts_service_add` + `characteristic_add`
不一样，是**一次性建库**的写法：

```c
#include "gr_includes.h"

STACK_HEAP_INIT(heaps_table);                 /* main 里放一份堆表 */
ble_stack_init(ble_evt_handler, &heaps_table); /* 启动协议栈，回调由我们提供 */

/* 建服务（128 位 UUID）：结构体是 ble_gatts_create_db_t */
gatts_create_db_t db;
memset(&db, 0, sizeof(db));
db.uuid          = epd_svc_uuid_lsb_first;      /* 16 字节 LSB first */
db.srvc_perm     = SRVC_UUID_TYPE_SET(UUID_TYPE_128);
db.shdl          = &s_start_hdl;
db.attr_tab_cfg  = NULL;                        /* NULL = 全表都加 */
db.max_nb_attr   = N;
db.attr_tab_type = SERVICE_TABLE_TYPE_128;
db.attr_tab.attr_tab_128 = s_epd_attr_tab;      /* ble_gatts_attm_desc_128_t[] */
db.inc_srvc_num  = 0;
ble_gatts_srvc_db_create(&db);
```

属性表每项是 `ble_gatts_attm_desc_128_t { uuid[16]; perm; ext_perm; max_size; }`，
标准属性（主服务 0x2800、特征声明 0x2803、CCCD 0x2902）也在同一张表里按顺序排。

通知：`ble_gatts_noti_ind(conn_idx, &ble_gatts_noti_ind_t{...})`
写确认：`ble_gatts_write_cfm(conn_idx, &ble_gatts_write_cfm_t{...})`

**可以直接抄的模板**（SDK 自带，把建库、属性表、事件处理都示范了一遍）：

```
components/libraries/ble/ble_gatt_service/ble_gatt_service.c
projects/ble/ble_peripheral/ble_app_uart/Src/user/user_app.c   ← 广播/GAP 的写法
```

广播侧（已确认）：`ble_gap_device_name_set` / `ble_gap_adv_param_set` /
`ble_gap_adv_data_set`（带服务 UUID）/ `ble_gap_adv_start`，连上停广播、断开重开。
