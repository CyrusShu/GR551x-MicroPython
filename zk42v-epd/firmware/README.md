# ZK42V 自研固件（方向 B）第一版：把屏点亮

> **2026-09-27 build 26 —— 日历 / 时钟模式（网页上那俩按钮终于有反应了）**
>
> 原厂的分工是：**网页只发"时间戳 + 模式字节"，页面由固件画**
> （他 `syncTime(1)`=日历、`syncTime(2)`=时钟；MCU 端 GUI/DrawCalendar + DrawClock）。
> 我们之前只做了"收图 → 刷屏"，所以点那两个按钮屏上没反应 —— 现在补上了。
>
> 两个页面（预览图在 `docs/preview/`，是用宿主机编同一份 `zkgui.c` 渲染出来的，
> 跟屏上像素一致）：
>
> * **日历模式**：左红面板 = 表盘 + 数字时间 + 大日期 + 星期/月份；右白面板 =
>   年月 + 月牙 + 月历（**周一开头**、今天红圈）。重画节奏照抄原厂：
>   **换天（00:00）重画一次**。
> * **时钟模式**：整屏一个大表盘 + 黑底数字时间。原厂这模式是**每分钟全刷**
>   （他页面自己也提醒"主要用于除残影"），我们照做 —— 但一分钟一次 16 秒的全刷
>   对屏和电都不友好，当调试工具用就好。
> * 推图（WRITE_IMAGE）或清屏会**自动切回图片模式**，不然时钟会把图盖掉。
>
> 版式是照用户给的参考图做的，月历那部分的像素借了
> `BuyudarenC/Lnk-bottle-calendar-based-on-ESP32`（同样是 400x300 那块 4.2 寸屏，
> 周一开头 / 格宽 ~27px / 行距 39px / 今天用红圈），左半边的大日期+农历结构
> 参考了 `lxrmido/node-paper-calendar`。
>
> 还没做：**农历**（字模我已经先画进去了：正/冬/腊/初/廿 等 9 个字；算法本地就有
> —— 原厂 `GUI/Lunar.c` 是自包含 C，搬过来即可）、天气/温度（原厂靠 WiFi 取，
> 我们是 BLE 基站，得想别的来源）。

> # ✅ B2-A 打通了（2026-09-27 18:33，build 22 实测）
>
> **手机网页 → BLE → 价签 → 屏**,整条链第一次跑通。实测日志:
>
> ```
> 网页侧：⇑01(INIT) → 收到配置 → ⇓mtu=244 rle=1 → 已开启 RLE
>         ⇑30 …(97 块 WRITE_IMAGE) → ⇑05(REFRESH) → 发送完成！耗时 0.921s
> 固件侧：命令 100 条   黑白面 15000/15000   红面 15000/15000
>         RLE 解出 30000 字节（压缩那条路）   屏：刷完一帧了
> ```
>
> 几个值得记住的结论:
>
> * **97 块里绝大多数是"无响应的写"**（网页 `确认间隔` 默认 50），两个面照样
>   一个字节不差 ⇒ 「Write Command 会被协议栈丢掉」这个担心不成立，
>   `确认间隔` 保持默认 50 就行，不用填 0。
> * 0.9 秒发完一整帧 —— 靠的是网页的 RLE（30000 字节压到约 97×242 字节）
>   加上 244 的 MTU。固件这边只是把解出来的字节按面塞进 `ZK_IMG_BUF`。
> * 屏的写图 + 全刷是在**空闲循环**里做的（命令一到只记账），所以那 0.9 秒
>   里协议栈一直在正常收包。
>
> 这条路子上的三个"当初要是不知道就会踩"的点，现在都有据可查:
> 广播数据不能自己写 `Flags`（0x4A）、AD 结构长度字节要对（否则设备名 N/A）、
> 服务必须走 `ble_gatts_prf_add` 注册（否则手机看不到服务）。

> # 🎉 图片上屏成功（2026-09-27 18:42，build 24）
>
> **网页推图 → 屏上出现图片**,从 build 22 的"数据到了屏不动"到这一版真正画出来,
> 就是那一处修改:**别在"初始化"和"写图+刷新"之间放开屏的供电脚**。
> 实测数字(对照 B1.6 成功刷屏那几次):
>
> | 指标 | 这次推图(build 24) | B1.6 成功刷屏 |
> |---|---|---|
> | BUSY 轮询次数 | 80860(≈16.2 秒) | 82953 / 93684(≈16.6/18.7 秒) |
> | 黑白面 / 红面 | 15000/15000 两个面都满 | — |
> | RLE 解出 | 30000 字节(压缩率约 2:1) | — |
> | 命令总数 | 101(INIT 2 + 图块 98 + REFRESH 1) | — |
>
> 也就是说:**一次全刷 ≈ 16 秒**,网页那边 0.9 秒发完 + 我们这边 16 秒刷,
> 屏会明显闪一阵 —— 这是墨水屏全刷的正常表现。
>
> ## build 25(只是把"计时"修准,不影响功能,不用专门为它刷)
>
> build 24 那份日志里 `写图+刷新用了 5537 ms` 是**假的**,原因很"自己人":
> `delay_init()` 里有一句 `DWT->CYCCNT = 0`,而 `epd_refresh_ex()` 内部又会调
> `epd_gpio_init()` → 计数器被清零 → 我拿"清零前后的两个绝对值相减"得到垃圾。
> 真正的时间看 **BUSY 轮询次数**(80860 × 200us ≈ 16.2 秒)才准 —— 跟 B1.6 的
> 16.9 秒完全对得上,说明这次全刷是实打实的。
>
> build 25 把 `DWT->CYCCNT = 0` 去掉了(自由计数器不清零,差值用无符号减法算,
> 16MHz 时 268 秒才绕一圈),以后所有 ms 都可信。顺带 `status.sh` 加了一段
> **时基自检**:直接读 DWT 跟宿主机的 250ms 卡一下,报出真实主频,
> 再和固件用的 `SystemCoreClock` 对比 —— 以后"时间对不上"这类问题一眼可判。

> **2026-09-27 build 24 —— 「图发进去了、屏却没动」的元凶：我把刷屏会话拆成了两段**
>
> build 22 推图那次：网页说发完了、`status.sh` 也说
> `黑白面 15000/15000  红面 15000/15000`、`屏：刷完一帧了`，
> 但**屏上一动不动**。`status.sh` 里的 BUSY 轮询次数是铁证：
>
> | 日志 | BUSY 轮询 | 意思 |
> |---|---|---|
> | B1.6 刷成功那几次 | 82953 / 93684 / 469030 | 屏真忙了 17~94 秒（全刷） |
> | build 22 推图 | **2277** | 屏只"忙"了约 0.45 秒 ⇒ 压根没做全刷 |
>
> 原因：那 7 根脚里 **AUX = P1_8 是屏的供电/使能**。原厂（和我们 B1.6 那版）是
> **一次会话把活干完**：`pins_init → 复位 → 初始化 → 写图 → 刷新 → pins_release`
> —— 最后那步放开脚就是给屏断电，原厂每次刷完都这么干。
> 我在 build 22 把流程拆成两段（INIT 一段、REFRESH 一段），
> INIT 干完就把脚松了 → 屏掉电；等 REFRESH 再来时屏已经不是"初始化好"的状态，
> 写 RAM + 发 0x22/0x20 只让它象征性忙了 0.45 秒。
>
> 修法：**初始化完脚一直攥着，直到一次"写图+刷新"做完才放开**，
> 放开的同时把"已初始化"状态作废（下次要用就重新初始化）。这跟原厂的节奏一致。
>
> 另外加了一个**以后一眼能看出这类问题**的字段：`ble_busy_delta`
> = 这一轮刷新期间 BUSY 被轮询了多少次。真刷是几万~几十万（17 秒以上），
> 只有几百就是没刷 —— `status.sh` 会直接喊「屏压根没做全刷」并列出历史对照。
>
> （这一版同时带上了 build 23 那两个显示小修：属性个数 7→6、
> 耗时不再恒为 0。build 23 的镜像没有刷过，直接刷 24 就行。）
> 镜像 SHA-256 `8bd2a11f…c94f`，`check_sum = 0x00E6D724`，写 APP 37 颗 + 信息 1 颗。
>
> **2026-09-27 build 23（小修，已被 24 取代）**
>
> 手机那边的握手已经**全通了**（build 22 实测日志）：
> `找到 EPD Service` → `找到 Characteristic` → `固件版本: 0x1a` → `⇑01`(INIT)
> → `收到配置：040302050706ff03ffffff0000`（第 0 条通知，引脚+驱动型号 0x03=4.2"三色）
> → `⇓mtu=244 rle=1` → 网页自动设分块 244、打开 RLE。`status.sh` 也确认
> `INIT` 收到了、屏初始化完了、两条通知都发成功了。
>
> build 23 只修两个**显示**问题（不影响推图）：
>
> 1. `status.sh` 原来把服务报成"7 个属性" —— SDK 那个框架给的 `end_hdl`
>    是**开区间**（`start + 属性个数`），现在减 1 还原成 **6 个属性**
>    （真实句柄 = `start ~ start+5`）；
> 2. `初始化用了 0 ms / 写图+刷新用了 0 ms` —— 那是拿"进 poll 时的时间"算的，
>    而屏的活整个跑在一次 poll 里，差值当然是 0。现在现场读 DWT，能报真数了
>    （这个数有用：能看出一次全刷到底十几秒还是二十几秒）。
>
> ~~镜像 SHA-256 `ae8c52f5…3213`~~ → **这一版没刷，直接刷 build 24**（见上面那条，
> 里面包含了这两处修正，还修了"屏不刷新"那个真问题）。
>
> 📌 **一个免刷机的保险**：网页上那个 **「确认间隔」(interleavedcount) 默认是 50**，
> 意思是"每 50 块才有一块带响应"。要是推图时发现图缺一块块的（或者干脆想最稳），
> 把它填 **0** → 每一块都等确认，一块都不会丢（慢一点，但可靠）。

> **2026-09-27 B2-A.2 修正（build 22）—— 「手机看不到服务」那个 bug 修好了**
>
> build 21 的实测结果很干脆：手机连上 GATT 之后报
> `connect: No Services matching UUID 62750001-… found in Device`，
> 而固件这边 `status.sh` 说建库**成功**（返回 0、还分配了句柄 0x0001）。
>
> 把 SDK 里的 `ble_gatts_prf_add` / `ble_service_load_cb` 反汇编出来一看就明白了：
> 注册一个 profile 要走三步 —— ①`ble_server_prf_add()` 在协议栈里登记一个槽；
> ②`gapm_set_config_bit(1)` + `gapm_set_profile_added_flag(0)` 告诉 GAPM「加好了」；
> ③协议栈随后**回来加载 profile**（`ble_service_load_cb`）时才真正调
> `ble_gatts_srvc_db_create()` 建库，建完给我们发
> `BLE_GATTS_EVT_DATABASE_INITED_IND`。
>
> 也就是说：**光调 ROM 那个 `ble_gatts_srvc_db_create` 是没用的** —— 它只把
> "描述"建出来（所以返回 0、还给句柄 0x0001），协议栈那边的 ATT 库从头到尾没建，
> 手机什么服务都看不到。build 21 就栽在这一步。
>
> build 22 改成**走 `ble_gatts_prf_add`**（SDK 里每个 profile 都这么建），
> 并把 GATTS 读写事件挪进我们自己的 profile 回调 —— SDK 的分发顺序是
> 「先问 profile，谁认领就不再往下传」（`ble_event_handle` 反汇编证实），
> 全局回调里那两个 GATTS case 本来是死的，现在删掉，免得两边都回包。
>
> ```bash
> cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
> MODE=app bash flash-app.sh          # 数到 3 时按住 RST 接地，听到"叮"松手
> MODE=appverify bash flash-app.sh    # 只读验收 → APP_VERIFY_OK
> bash status.sh                      # 应看到「注册返回 = 0  建库返回 = 0  句柄 = 0x0006~0x000B」
> ```
>
> （镜像 SHA-256 `99fba98e…6371`，`check_sum = 0x00E69E40`，写 APP 37 颗 + 信息 1 颗。）
>
> `status.sh` 现在把「服务」拆成两步报：**注册返回**（`ble_gatts_prf_add`）
> 和**建库返回**（协议栈回调里那一步）。build 21 那种「注册 0、句柄却还是 0」
> 的情况会被直接点名：「profile 登记上了，但协议栈还没回来建库」。
> **2026-09-27 B2-A.2（build 21）—— 服务建好了，网页可以推图了**
>
> 上一版（build 20）手机确认了：名字 `ZK42V-EPD`、厂商数据 `5A4B 3432 56`、服务 UUID、
> 100ms 广播间隔（实测 103~108ms）全对。A.1 收工。
>
> 这一版把 tsl0922/EPD-nRF5 那套**网页协议**实现上了（UUID 跟他原厂一字不差）：
>
> | 特征 | 干什么 |
> |---|---|
> | `62750001-…` | 主服务 |
> | `62750002-…` | 写 + 通知：网页往这儿写命令和图块 |
> | `62750003-…` | 只读版本，回 `0x1a`（网页 `< 0x16` 会提示固件太旧） |
>
> 命令都实现了：`INIT` `CLEAR` `REFRESH` `SLEEP` `WRITE_IMAGE`（含 RLE）、
> `SEND_CMD/SEND_DATA`（调试直通）、`SET_TIME`（把时间回给网页）、
> `SET_PINS/SET_CONFIG/SET_WEEK_START/SYS_SLEEP/CFG_ERASE`（收下忽略，我们配置写死）、
> `SYS_RESET`（真复位）。**没做**的是他原厂那套「日历/时钟模式」的 GUI 绘制 —— 我们只推图。
>
> 三个容易踩、我特意对齐了原厂的细节：
>
> 1. **通知的顺序**：网页把「收到的第 0 条通知」当配置结构体解析、第 1 条才当文本。
>    所以他原厂是在客户端开通知(CCCD)时推配置、`INIT` 时推 `mtu=… rle=1`。我们照做 ——
>    顺序错了网页会把 `mtu=…` 当配置读，而且**永远不会打开 RLE**（传输慢十倍）。
> 2. **`WRITE_IMAGE` 的 flags 位**：bit0 = 黑白/红面，bit1 = 本面第一块，bit2 = RLE。
>    万一通知没送到、网页退回 v1.5 老格式（bit0-3=0x0F 表示黑白面），我们也能认出来
>    （`ble_legacy`）并照老规矩解，不至于画出一堆乱码。
> 3. **不把屏的重活堵在 BLE 回调里**：一次刷新要等 BUSY 十几秒。命令一到只记账，
>    真正的初始化/写图/刷新放在空闲循环里做，写完再通知。
>
> ```bash
> cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
> MODE=app bash flash-app.sh          # 数到 3 时按住 RST 接地，听到"叮"松手
> MODE=appverify bash flash-app.sh    # 只读验收，看到 APP_VERIFY_OK 再往下
> bash status.sh                      # 先看一眼服务建起来没有
> ```
>
> （这一版的镜像：SHA-256 `9177e419…98b3c`，`check_sum = 0x00E53D93`，
> 写 APP **37 颗** + 信息 1 颗 —— 加了服务之后比上一版多一颗扇区。）
>
> ⚠ **红面极性那件事**：网页（和他原厂固件）的红面约定是「bit=0 才是红」，
> 而**我们这块屏实测是反的**（bit=1 才是红，见 `img/testimg.c` 和 `tools/img2epd.py`）。
> 所以固件从网页收下来的红面数据会**整片取反**再进缓冲（`ZK_WEB_RED_IS_ACTIVE_LOW`）。
> 万一点「清屏」出来的是**一整片红**、或者推的图红蓝反了，把那个宏改成 0 重编就行。
>
> 然后手机上用 **Chrome / Edge（安卓）或 Bluefy（iOS）** 打开
> <https://tsl0922.github.io/EPD-nRF5/> → 「连接」→ 选 `ZK42V-EPD` →
> 挑一张图 → 「推送」。网页日志里应该依次出现
> `固件版本: 0x1a`、`MTU 已更新为: 244`、`已开启 RLE 压缩传输支持`。
>
> **判据**（`status.sh` 里「B2-A.2：GATT 服务 / 推图」那一段）：
>
> | 看到什么 | 意思 |
> |---|---|
> | `服务：注册返回 = 0  建库返回 = 0  句柄 = 0x…` | 服务真建起来了，手机能看到它 |
> | `命令：一共收到 N 条`，N 在涨 | 网页的写真的递到固件了 |
> | `黑白面 15000/15000  红面 15000/15000` | 两个面都收全了 |
> | `RLE 解出 … 字节` | 网页走了压缩那条路（说明它收到了 `rle=1`） |
> | `屏：刷完一帧了` + `写图+刷新用了 xxxxx ms` | 屏真的画完了 |
> | `服务没建起来：…返回 = 0x10` | 协议栈堆不够放这张表 —— 把这段发我 |
> | `收到图块了但没凑满` | 多半是「无响应的写」没被递上来 —— 把这段发我 |
>
> 两个最快的目视自检：点 **「清屏」** → 屏应该变**全白**（如果变全红，就是红面极性反了，
> 见上面的 ⚠）；推一张**有红色**的图 → 红色应该只出现在该红的地方。

> **2026-09-27 B2-A.3（build 20）—— 手机已经在空中看到我们了；顺手修掉一个真 bug**
>
> 上一版（build 19）的结论经手机验证是成立的：nRF Connect 里出现了我们的设备，
> 厂商数据 `<FFFF> 5A4B 3432 56…` 和服务 UUID
> `62750001-D828-918D-FB46-B6C11C675AEC` 都在，RSSI -57 dBm。
> **A.1「能被发现」这一环通了。**
>
> 但同一张截图也暴露了一个**我们自己的 bug**：设备名显示成 **N/A**。原因是
> 广播数据里那条「厂商数据」的**长度字节写错了**（写 `0x09`，实际只有 8 个字节），
> 于是解析器把下一条结构的长度字节 `0x0A` 也吞了进去 —— 手机显示
> `<FFFF> 5A4B 3432 560A`（末尾那个 `0A` 就是赃物），后面那条「完整名字」
> 从此错位、被读成 `type=0x5A`，名字就丢了。（这也解释了为什么用名字过滤
> 一个都搜不到。）正确值应该是 `0x08` = 1(type) + 2(公司ID) + 5("ZK42V")。
> **这个 bug 不会让广播起不来**（`ADV_START` 照样 `status=0`），状态块里一切正常，
> 只有手机那边名字没了 —— 所以这一版专门加了个离线自测
> `tools/test_adv_data.py` 盯着 AD 结构的长度字节。
>
> build 20 干了三件事：
>
> 1. **修长度字节**（三处：变体 #0/#1/#3），名字现在能正确发出去；
> 2. **关掉扫描实验**（`ZK_BLE_SCAN_TEST 0`）—— 开机直接广播，不再先听 4 秒；
> 3. **补上 `ADV_STOP` 记录与自愈**：以前完全没有这条记录，广播要是中途停了，
>    状态块里一个数都不变、我们还以为它在广播。现在每停一次记一笔
>    （`ble_adv_stop_cnt` / `ble_adv_stop_rsn`），非「被连接打断」的原因还会自动重开
>    （`ble_adv_restart_cnt`）。
>
> ```bash
> cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
> MODE=app bash flash-app.sh          # 数到 3 时按住 RST 接地，听到"叮"松手
> MODE=appverify bash flash-app.sh    # 只读验收，看到 APP_VERIFY_OK 再往下
> bash status.sh
> ```
>
> **判据**：手机 nRF Connect 里那一栏的名字应该从 `N/A` 变成 **`ZK42V-EPD`**，
> 厂商数据变成 `<FFFF> 5A4B 3432 56`（末尾不再有那个多余的 `0A`）。
> `status.sh` 里会多一条 `ADV_STOP（广播自己停）：一共 0 次` —— 0 次就对了。

> **2026-09-27 B2-A.2（build 19）—— 这一版先回答两个问题**
>
> 上一轮把「BLE 广播起不来」的性质改变了：**控制器其实是回事件的**。
> 状态块里 `ble_evt_id = 0x207`（`BLE_GAPM_EVT_ADV_START`）、
> `ble_evt_status = 0x4A`。0x4A 就是
> `BLE_GAP_ERR_ADV_DATA_INVALID` ——「广播数据重复/非法」。
> 也就是：广播**参数**没问题、命令条条被接受，但广播**数据**在 adv_start
> 那一刻被协议栈判非法，链路层于是压根没开始广播，空中一个包都没有。
> （之前之所以觉得"从不回事件"，是被那个没清零的 `ble_evt_count` 带偏了：
> 那块 RAM 是 NOLOAD，读出来是个天文数字。build 19 一并修掉。）
>
> 这一版一次 flash 干两件事：
>
> 1. **扫描实验**：开机先当 4 秒观察者，数周围能听到几个 BLE 设备。
>    听得到 = 射频活着、收通路也是好的（问题在发送侧）；
>    听不到 = 射频没起来，下一步转去反汇编原厂固件的 BLE 使能路径。
> 2. **广播数据变体实验**：扫描结束后，把 6 种广播数据组合**挨个试一遍**，
>    每种被 ADV_START 事件带回来的状态码都记进状态块 —— 哪种数据控制器认，
>    一眼就看出来（第 0 种就是把 `Flags(0x01)` 那条 AD 结构摘掉的版本，
>    也是最可能直接修好的）。
>
> ```bash
> cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
> MODE=app bash flash-app.sh          # 数到 3 时按住 RST 接地，听到"叮"松手
> MODE=appverify bash flash-app.sh    # 只读验收，看到 APP_VERIFY_OK 再往下
> # 断电重上电（这一步必须做，不是按 RST），然后等 10 秒左右
> bash status.sh
> ```
>
> **判据**：`status.sh` 的输出里会多出两段「B2-A.2 实验一/实验二」：
>
> ### 实测结果（2026-09-27 17:34，就是这一版）
>
> ```
> B2-A.2 实验一：ble_scan_state=4（扫描结束）  SCAN_START status=0  SCAN_STOP reason=0（超时）
>   → 听到 原始 395 条广播，去重后 16 个设备（去重表溢出 48 次）
>   RSSI：最后 -83 dBm，最强 -39 dBm
>   ▶ 射频是活的、收通路也是好的 —— 问题在发送侧
> B2-A.2 实验二：#0 = 0 ✅ 被接受了（广播=厂商数据+名字，不带 Flags）
>   （#1..#5 写「没等到事件」是**故意的** —— #0 一成功就停，后面没试）
> BLE 最后事件: ADV_START（status=0 成功）  共收到 399 个事件
> ```
>
> **两条结论都拿到了**：
>
> 1. **射频是好的** —— 395 条上报、16 个设备、-39 dBm，收通路一点问题没有。
> 2. **发送侧的病根找到了** —— 把 `Flags(0x01)` 那条 AD 结构从我们的广播数据里
>    拿掉（变体 #0），`ADV_START` 立刻变成 `status=0`。对照组也成立：原来带 Flags
>    的那套数据（变体 #1，就是 build 18 用的那份）回的是 `0x4A`。
>    也就是**广播数据里不能自己写 Flags —— 协议栈按 `disc_mode` 自己会加**
>    （SDK 自带例程一份都不写，就是这个道理）。
>
> 顺带，事件账目**分毫不差**：`1`(STACK_INIT) + `1`(SCAN_START) + `395`(ADV_REPORT)
> + `1`(SCAN_STOP) + `1`(ADV_START) = `399` = 状态块里的 `ble_evt_count`。
> 这说明 build 19 的清零修好了，整条状态机一步不漏地走完了 ——
> 上一轮那个"天文数字"纯属没清零的 RAM 垃圾。
>
> **做完这一步别急着刷**：现在这块板子**正在用变体 #0 那套数据持续广播**
> （`duration=0` 一直广播），拿手机 nRF Connect 搜 `ZK42V-EPD` 就能验证 A.1。
> 想更仔细点：看它的广播包里有没有 `Flags: 0x06`（协议栈自己加的）、
> `Complete Local Name: ZK42V-EPD`，scan response 里有没有那个 128 位服务 UUID。
>
> | 看到什么 | 意思 |
> |---|---|
> | 实验一：`听到 原始 N 条广播，去重后 M 个设备`，M ≥ 1 | **射频是活的**，问题在发送侧（看实验二的变体表） |
> | 实验一：`一条广播都没听到` + `SCAN_START 事件也没回来` | 射频/链路层没在干活 → 转去反汇编原厂的 BLE 使能路径 |
> | 实验一：`一条广播都没听到` 但 SCAN_START 事件是好的 | 命令通路通、收信通路没出东西（附近真没 BLE 设备？） |
> | 实验二：某个变体 `0 ✅ 被接受了` | 这套广播数据控制器认 —— 下一版就固化它，然后用手机/nRF Connect 搜 `ZK42V-EPD` |
> | 实验二：全是 `0x4A ADV_DATA_INVALID` | 广播数据这条路上还有别的规矩没满足 |
> | 实验二：全是 `没等到事件` | 链路层压根没处理 adv_start（回到实验一的结论） |
>
> 实验二里要是还有几行 `没等到事件`，说明那一步还没跑完（每个变体最多等 3 秒）
> —— **隔十几秒再跑一次 `bash status.sh`** 就行：结果一直留在 RAM 里，不会丢
> （只是那次读的时候还没轮到它）。想一次看到底就 `STATUS_SETTLE_MS=30000 bash status.sh`。
>
> 回厂永远是这一条：
> ```bash
> cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
> MODE=restore bash flash-write.sh
> ```
>
> 细节（证据链、新字段表、离线自测）见文末**附四**。

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
3. 只擦写 `0x0100A000` 起 APP 那一段（扇区数按镜像里的 `bin_size` 算 ——
   B1 那版是 19 颗，**build 19 这版带了 BLE 协议栈，是 36 颗**）
   + 最后改 `0x01002000` 那颗镜像信息扇区；
4. **不碰** bootloader（`0x01003000`）和 NVDS（`0x0107F000`）。

**判据**：看到这两行就算成功

```
>>> 写完了：APP 36 颗 + 信息 1 颗，一共 37 颗扇区。
  芯片里那段 APP 的逐字节和 = 0x00DEA848（跟声明的对上了）
```

（build 19 这份镜像的 `check_sum` 就是 `0x00DEA848`、`bin_size` = `0x233BC`；
 你要是自己重编了，这两个数会变 —— 对得上就行。）

（扇区数会跟着固件大小变；要紧的是「写完了」+「逐字节和对上了」。
 它俩是从镜像自己那条 `bin_size`/`check_sum` 记录里读出来的，镜像自检不过就不写。）

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

它默认**先等 15 秒**（`STATUS_SETTLE_MS`，等固件把扫描和广播变体实验跑完），
然后隔一会儿采几个点，每次都**先把 CPU 停住再读**，打印成一张表：

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
│       ├── ble/zk_ble.[ch]       ← B2-A：协议栈 / 扫描实验 / 广播数据变体
│       ├── ble/zk_epd_svc.[ch]   ← B2-A.2：GATT 服务 + 网页那套命令（推图）
│       └── config/custom_config.h← SDK 配置（GR5513BEND、APP 在 0x0100A000）
└── tools/
    ├── fwpack.py                 ← 把 APP 打包成整片镜像（含自检）
    ├── img2epd.py                ← 图片 -> 30000 字节三色数据（+ 预览 PNG）
    ├── test_fwpack.py            ← 打包器的离线自测
    ├── test_img2epd.py           ← 图片转换的离线自测
    ├── test_dbg_layout.py        ← 状态块布局的离线自测（固件/上位机别错位）
    └── test_adv_data.py          ← 广播数据 AD 结构的离线自测（build 20 新增）
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
python3 tools/test_img2epd.py           # 图片转换：11 项
python3 tools/test_dbg_layout.py        # 状态块布局：9 项（B2-A.2 新增）
python3 tools/test_adv_data.py          # 广播数据 AD 结构：11 项（build 20 新增）

cd ../pyocd
python3 test-flashwrite.py              # 烧写脚本：含 app/appverify/status，155 项
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
| N6 | build 19 的扫描结果能翻译出来（设备数/RSSI/广播数据字节），并判「射频是活的」 |
| N6b | `NVIC ISER0` 没使能 / comm core 没上电时，必须点名这两条怀疑 |
| N7 | 一条广播都没听到、`SCAN_START` 事件也没回来 -> 指向「反汇编原厂的 BLE 使能路径」 |
| N8 | 广播变体全被拒时，把 `0x4A` 翻成 `GAP_ERR_ADV_DATA_INVALID` |
| N9 | 复刻 2026-09-27 17:34 那次真实结果（395 条/16 设备/变体 #0 被接受） |
| N10/N10b | PC 落在空闲延时里说「正常打转」，落在别处才说「卡住」 |
| N11/N12 | build 20：不把「没跑扫描实验」报成扫描失败；广播被连接打断不算异常 |
| N13~N16 | build 21：服务/推图那一段的解码（服务没建起来、命令没进来、图没凑满各有各的说法） |

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

# 附三：B2-B —— 从电脑推一张图到屏上（SWD + 共享内存信箱）

B1 收尾之后先做的这一步，目标是**验证图片管线**：任意图片 → 400×300 三色 →
上屏。传输走 SWD（不是串口）：这条路我们从写 flash 到读内存全验过，零硬件未知；
串口的 RX 脚还没挖出来，等做真基站（BLE）时再上无线。

## 怎么用

```bash
cd outputs/pyocd
IMG=~/Desktop/你的图.png bash push-image.sh          # 三色（默认）
IMG=~/Desktop/照片.jpg MODE=bw bash push-image.sh    # 只要黑白（照片通常更清楚）
```

它干两件事：

1. `tools/img2epd.py` 把图转成 30000 字节，并写一张**预览 PNG**
   （`outputs/pyocd/last-push.preview.png`）——先看预览，觉得行再推；
2. pyOCD 用 SWD 把那 30000 字节塞进固件的**共享内存信箱**，然后等固件刷完。

**判据**：

| 输出 | 意思 |
|---|---|
| `像素 : 黑 x%  白 y%  红 z%` | 转换成功；顺手看一眼预览图 |
| `>>> 刷完了（status=3，用了 xxxxx ms）` | 固件收下并刷完了，看屏 |
| `>>> 固件说校验不过（status=0xFF）` | 图没写全，重跑一次即可 |

## 图是怎么到屏上的

```
电脑                                    价签（GR5513）
────                                    ──────────────
img2epd.py   图片 -> 30000 字节          空闲循环里每 20ms 轮询信箱：
             （前 15000 黑白面，          magic 对？seq 变了？
               后 15000 红面）             -> 校验 len 和累加和
pushimg      SWD 写 RAM：                  -> memcpy 到画面缓冲
             1) 图像区 30000 字节         -> epd_write_image + refresh(0xC7)
             2) len / sum                 -> 回写 ack_seq
             3) magic
             4) **seq 最后写** ←关键
             然后轮询 ack_seq
```

**`seq` 必须最后写**：固件看到 `seq != ack_seq` 才认为"这一帧到齐了"。
要不然它可能在图像只写了一半的时候就动手刷屏。

## 内存布局（谁占哪块）

| 地址 | 大小 | 用途 |
|---|---|---|
| `0x30004000` | 64KB | 应用 RAM：`.data` / `.bss` / heap / 栈（栈顶 `0x30014000`） |
| `0x30014000` | 64B | 信箱控制块：magic / seq / len / sum / status / ack_seq / ms_refresh |
| `0x30014100` | 30000B | 图像数据（到 `0x3001B630`） |
| `0x3001F000` | 4KB | 调试状态块（`status.sh` 读它） |

信箱这块地址是**写死**的（`board/zk_dbg.h` 的 `ZK_MB_ADDR`），链接脚本把
应用 RAM 收到 `0x30014000` 为止，保证不会撞上。

## 离线自测

```bash
cd outputs/firmware && python3 tools/test_img2epd.py    # 图片转换：11 项
cd ../pyocd && python3 test-flashwrite.py               # 含 pushimg 的信箱写序用例
```

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

### 实机物证（2026-09-27 00:40 那次 status.sh，日志在 `docs/runs/2026-09-27-b1/status-004040.log`）

## B1.4 / B1.5：辅助脚是 P1_8，不是 P0_24（这是屏不亮的真正原因）

`status.sh` 里出现 `GPIO 错=1` / `flags bit1` 时就要警觉 —— 有一个脚 `app_io_init`
被拒了。查 SDK `drivers/src/app_io.c` 的 `APP_IO_TYPE_NORMAL` 分支：

> GR551X 的引脚编号是**全局 0~31**：0~15 → GPIO0 的 bit0~15；16~31 → **GPIO1** 的 bit0~15。

原厂引脚表里 `pins[7] = 0x18 = 24` 因此是 **GPIO1 bit8 = P1_8**，不是 P0_24。
我们一开始用 `APP_IO_TYPE_GPIOA + APP_IO_PIN_24`，被那句
`if (!(pin & APP_IO_PINS_0_15)) return INVALID_PARAM;` 直接拒掉 ——
**这根屏的供电/使能脚从头到尾没被驱动过**，于是"命令发得出去、屏也刷了 21 秒，
但写进 RAM 的东西不生效"。改用 `APP_IO_TYPE_NORMAL + APP_IO_PIN_24`（落到 P1_8）
并在拉高后多等 50ms，屏立刻开始跟着我们的内容变。

顺带钉死一条：**`0x3001F000` 那块调试 RAM 不能用来跨复位存东西** ——
每次启动都会被动过（`boot_count` 一直是 1、`test_step` 读出来是随机值），
所以"按 RST 换一步"的做法作废，B1.5 改成一次启动自动把 5 步走完。

```
  PSC_CMD     = 0x00000002   MCU_PWR_REQ=0  MCU_PWR_BUSY=1   ← 一直在忙！
  PSC_CMD_OPC = 0x00000007   opcode=0x07 (RTC_CLK)
  AON 定时器（跑在低功耗时钟上）：0 -> 0（差 0）→ 没走
```

三条对上：**卡住的命令就是 RTC_CLK（0x07）**，而且**低功耗时钟确实没在跑** ——
命令发起后永远得不到时钟去完成，于是死在等待循环里。`RST_ST` 全 0 也再次确认：
不是复位，是卡住。

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

---

# 附四：B2-A.2 —— 广播被 `0x4A` 拒了，于是先扫描、再逐个试广播数据

## 一、硬证据：ADV_START 事件**回来了**，带着错误码 0x4A

`status.sh` 在 2026-09-27 14:53 那次（`outputs/pyocd/status-20260927-145313.log`）读到：

```
BLE: state=1（在广播，等连接）  ble_err=0  mtu=0
BLE MAC: A0:A3:B8:2D:96:EA
BLE 最后事件: id=519  status=74（0=成功）  共收到 3383669617 个事件
```

把这三个数翻译一下：

| 读到的 | 真相 | 依据 |
|---|---|---|
| `id=519` = `0x207` | **`BLE_GAPM_EVT_ADV_START`** | `ble_event.h`：`BLE_GAPM_EVT_BASE=0x200`，枚举里 `ADV_START` 正好第 8 个；固件里那行 `movw r2, #519 @ 0x207` 就是 `case BLE_GAPM_EVT_ADV_START:` |
| `status=74` = `0x4A` | **`BLE_GAP_ERR_ADV_DATA_INVALID`「广播数据重复/非法」** | `ble_error.h`：`#define BLE_GAP_ERR_ADV_DATA_INVALID 0x4A` |
| `共收到 3383669617 个事件` | **假的**，是没清零的 RAM 垃圾 | `main()` 原来只清了 `ble_state/ble_err/ble_mtu`，没清 `ble_evt_count`；而 `0x3001F000` 那块是 NOLOAD、上电不清零 |

所以之前那句「控制器从不回 ADV_START 事件」是**读数的锅**，不是控制器的锅。
真实情况是：命令全被接受（`ble_gap_adv_param_set` / `adv_data_set` / `adv_start`
返回全是 0），控制器也回了 `ADV_START`，但**带了个错误码** ——
它认为我们的广播数据非法，于是没开始广播，空中自然一个包都没有。

**为什么最可疑的是那条 `Flags`：** SDK 自带例程的广播数据里**从来不自己放
`Flags(0x01)`**（去看 `projects/ble/ble_peripheral/*/Src/user/user_app.c`：
用 `0x11,0x07,<128位UUID>` + 厂商数据，或者 `0x02,0x01,0x06` 也不是它写的，
名字放 scan response）。协议栈会按 `disc_mode` 自己加 Flags。我们那份数据第一个
AD 结构就是 `02 01 06`，两条 Flags 撞在一起正是「duplicate」。

## 二、这一版（build 19）干了什么

改的四个地方：

| 文件 | 改了什么 |
|---|---|
| `Src/ble/zk_ble.c` | 重写：**扫描实验** + **6 个广播数据变体**逐个试 + 全套记录进状态块 |
| `Src/board/zk_dbg.h` | `ZK_DBG_WORDS` 24 → 56，加 B2-A.2 那一组字段；`ZK_BUILD_ID` 18 → 19 |
| `Src/main.c` | 把状态块**用到的字段一个不漏地清零**（这次栽的就是漏清）；空闲循环里加 `zk_ble_poll(tick_ms())`；循环延时 20ms → 5ms |
| `Src/epd/epd_zk42v.[ch]` | 新增 `epd_timer_init()`：单独把 DWT 时基打开（不碰屏的引脚），这样 BLE-only 的固件也有准的毫秒时基 |

流程（都在空闲循环的驱动下跑，不阻塞）：

```
协议栈起来
  └─ 实验一：扫描 4 秒（ble_gap_scan_param_set + scan_start）
        · ADV_REPORT 事件：数条数、去重数设备、记 RSSI/地址/广播数据前 16 字节
        · SCAN_STOP（超时）或者兜底超时 -> 进实验二
  └─ 实验二：广播数据变体 0..5 逐个试
        每种：adv_data_set(DATA) + adv_data_set(SCAN_RSP) + adv_start
        等 ADV_START 事件把 status 记进 ble_adv_stN
        status == 0  -> 认了，停在这儿当广播机
        status != 0  -> 换下一种；3 秒等不到事件也换下一种
```

**变体表**（索引就是状态块里的 `ble_adv_stN`，顺序按嫌疑大小排）：

| # | 广播包数据 | scan response |
|---|---|---|
| 0 | 厂商数据 + 名字（**不带 Flags**） | 128 位服务 UUID |
| 1 | Flags + 厂商数据 + 名字（旧版那一套，对照组） | 128 位服务 UUID |
| 2 | 只有名字 | 128 位服务 UUID |
| 3 | 128 位 UUID + 厂商数据（照抄 SDK 例程的形状） | 名字 |
| 4 | 只有 Flags | 128 位服务 UUID |
| 5 | 厂商数据 + 名字 | 空 |

## 三、状态块 0x3001F000 新增的字段（build 19 起，word 24 起）

`bash status.sh` 会自动读出来翻译成人话，这里是原始对照表（改代码时别乱序）：

| word | 字节偏移 | 名字 | 含义 |
|---|---|---|---|
| 24 | +0x60 | `ble_scan_state` | 0 没发起 / 1 发起过没事件 / 2 收到 SCAN_START / 3 在收上报 / 4 结束 |
| 25 | +0x64 | `ble_scan_param_err` | `ble_gap_scan_param_set` 返回码 |
| 26 | +0x68 | `ble_scan_start_err` | `ble_gap_scan_start` 返回码 |
| 27 | +0x6C | `ble_scan_start_st` | SCAN_START 事件的 status（`0xFFFFFFFF` = 没收到） |
| 28 | +0x70 | `ble_scan_stop_rsn` | SCAN_STOP 的 reason（0 超时/1 主机停/2 连上了；`0xFFFFFFFE` = 我们兜底强停） |
| 29 | +0x74 | `ble_scan_rpts` | ADV_REPORT 一共几条（含重复设备） |
| 30 | +0x78 | `ble_scan_devs` | 去重后听到几个设备（上限 16） |
| 31 | +0x7C | `ble_scan_ovf` | 去重表溢出次数（>0 = 真实数量比 16 还多） |
| 32 | +0x80 | `ble_scan_rssi_last` | 最后一条上报的 RSSI（有符号） |
| 33 | +0x84 | `ble_scan_rssi_best` | 听到过的最强 RSSI |
| 34/35 | +0x88/+0x8C | `ble_scan_addr0/1` | 最后一条上报的设备地址 |
| 36 | +0x90 | `ble_scan_last_len` | 第一条广播数据的长度 |
| 37..40 | +0x94..+0xA0 | `ble_scan_data0..3` | 第一条广播数据的前 16 字节（拿它看别人怎么写 Flags/名字） |
| 41 | +0xA4 | `ble_adv_try` | 试到第几个变体 |
| 42 | +0xA8 | `ble_adv_try_status` | 最近一次 ADV_START 的 status（`0xFFFFFFFF` = 没等到） |
| 43 | +0xAC | `ble_adv_ok_variant` | 第一个被接受的变体号（`0xFFFFFFFF` = 都失败） |
| 44/45 | +0xB0/+0xB4 | `ble_adv_ds_err` / `ble_adv_ds2_err` | `adv_data_set`(DATA / SCAN_RSP) 的返回码 |
| 46 | +0xB8 | `ble_adv_start_err` | `ble_gap_adv_start` 的返回码 |
| 47..52 | +0xBC..+0xD0 | `ble_adv_st0..5` | 每个变体的 ADV_START status（`0` = 被接受） |

顺带：`status.sh` 现在还直接读三个寄存器当旁证，**射频/中断到底活没活**一眼可判：

* `AON PWR_RET01`（`0xA000C504`）：BLE comm core / comm timer 的**上电与复位闸**
  （bit6/7 = 电源，bit11/12 = 复位放开）。没上电/还在复位 → 链路层什么都不会执行。
* `NVIC ISER0`（`0xE000E100`）：协议栈的调度中断 `IRQ1 BLE_SDK` / `IRQ2 BLE`
  有没有使能。**没使能 = 命令收下、队列没人处理、事件永不递上来** ——
  跟「命令全接受、事件全不回」的症状一模一样，值得一直盯着。

## 四、离线自测（不用硬件）

```bash
cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware
python3 tools/test_dbg_layout.py     # 状态块布局自测：9 项
python3 tools/test_fwpack.py         # 打包器：18 项
python3 tools/test_img2epd.py        # 图片转换：11 项

cd ../pyocd
python3 test-flashwrite.py           # 烧写脚本：123 项（含 build 19 的新解码）
```

`tools/test_dbg_layout.py` 是这一轮新加的：拿 `zk_dbg.h` 当唯一真相来源，
机械地核对「头文件字段顺序 == 注释里写的 word 号 == 上位机 `ZK_DBG_WORDS`」，
以及「固件 `s_variants[]` 的条数 == 上位机 `ZK_ADV_VARIANT_DESC` 的条数」。
**这类错位这次真栽过一回**（`ble_evt_count` 没清零读成天文数字），所以给它配个哨兵。

## 五、这一版的不确定（诚实列出来）

1. ~~**变体 0 也不一定成**~~：**已实测通过** —— 变体 #0 拿到 `ADV_START status=0`，
   带 Flags 的变体 #1（= build 18 那套）是 `0x4A`，A/B 对照闭合。
   （原来的担心是 `0x4A` 里那个「或非法」可能指总长之类的别的规矩；
   现在证明就是 Flags 那一条。）
2. **失败之后能不能重设广播数据**：Goodix 那边如果拒绝了一半就锁住 adv 活动，
   后面几个变体会连带失败（`adv_data_set` 的返回码会记在 `ble_adv_ds_err`，
   能区分是"数据被拒"还是"命令被拒"）。
   一样的道理，第 5 号变体（广播=厂商数据+名字、scan response 空）是想测
   「能不能把 scan response 清掉」—— 万一协议栈不接受长度为 0 的设置，
   它实际用的还是上一个变体留下的 UUID（这时 `ble_adv_ds2_err` 非 0，
   看状态块就能分辨）。
3. ~~**扫描的 4 秒够不够**~~：**够了** —— 4 秒 395 条、16 个设备、
   去重表 16 格直接打满（还溢出 48 次）。真要数全，把 `ZK_SCAN_DEV_MAX`
   从 16 调到 64 就行（只是 RAM 里多两百来字节）。
4. **空闲循环的兜底超时**：靠 `tick_ms()`（DWT）。万一 DWT 不可用，
   会退回"数圈数"（扫描 2400 圈 / 每个变体 600 圈），结论一样，只是慢点。

## 六、下一步（按结果分叉）

* ~~实验一听到东西 + 实验二有变体被接受 →~~ **A.1、A.2 都落地了，build 21 已就绪**：
  1. build 19 手机验证：设备在空中有包、能被发现（A.1 达成）。
  2. build 20 修掉「厂商数据长度字节写错 → 设备名 N/A」，关掉扫描实验，
     加 `ADV_STOP` 记录与自愈；手机确认名字/厂商数据/100ms 间隔全对。
  3. build 21 把网页协议实现上了（服务 + 两个特征 + 全部命令 + RLE 推图）。
  4. 接着要做的：**手机上真推一张图**。如果成功，接下来是三件可选的活：
     * 把屏幕拉进产品形态（休眠：`SYS_SLEEP` 那条路现在是收下忽略，我们是不睡的基站）；
     * 原厂那套「日历/时钟模式」的 GUI 绘制（他要的是基站推图的话可以先不做）；
     * P1_8（屏使能/射频前端那个可疑脚）到底会不会影响 BLE —— 现在屏的脚
       只在刷图那一小段被我们占着，刷完立刻放开，所以影响面很小。
  5. 还没确认的小事：手机点进设备详情页看广播包里**有没有 `Flags`**
     （`LE General Discoverable`）。Android 怎么都能看到，iOS / Web Bluetooth
     更挑；如果详情页里确实没有，我们再专门处理。
* 实验一听不到东西 →
  拿 `outputs/analysis/app.asm`（原厂固件反汇编）找它的 BLE 使能路径：
  重点看它有没有额外的「comm core 上电/时钟/射频校准」调用是我们漏掉的
  （`AON PWR_RET01` 和 `NVIC ISER0` 两个旁证会先告诉我们缺的是电源还是中断）。
  （这一条现在用不上了，但留着 —— 换块板子/换颗芯片时还是这个查法。）
* 实验二全是 `0x4A` → 老老实实按 AD 结构二分：先只放 `Flags`，
  再加名字、再加厂商数据、再加服务 UUID，看是从哪一条开始被拒的
  （变体表就是为这个准备的，加两个变体再刷一次即可）。
