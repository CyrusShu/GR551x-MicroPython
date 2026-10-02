# 把天气中继装到 NAS 上（常驻）

## 0. 实测结论（2026-10-02，登进那台飞牛 NAS 盘过之后）

那台 NAS 的实际情况决定了这里该用哪套：

| 项 | 实测 |
|---|---|
| 现有容器 | **3 个**（`devnet-homekit` / `lucky` / `homebox`），全是 compose 管的 |
| 目录习惯 | `/vol1/1000/docker/<项目>/` + `docker-compose.yml` + `.env` |
| 自建 systemd 服务 | 无（跑着的都是 fnOS 自带：ai_manager / imagesrv / mediasrv / postgresql@15 …） |
| Python | ✅ 系统自带 3.11.2（`/usr/bin/python3`）—— systemd 那条路**不用装任何东西** |
| sudo | ❌ **没有免密 sudo**（systemd 要写 `/etc/systemd/system`，每步都得输密码） |
| 资源 | 内存 15GB / 空闲 11GB；数据盘空闲 12TB；负载 0.17；根盘剩 9.8GB |

⇒ **那台 NAS 上建议用 Docker/compose**：① 跟现有三个服务一套管法、fnOS 界面能看日志
② docker 对 `cyrus` 直接可用，不用 sudo ③ 资源差（~10MB 内存 + ~120MB 镜像）在这台机器上是噪声。
**纯论资源仍然是 systemd 更省**（一个进程、磁盘 0），只是要输密码、且风格不统一。

## 1. 资源上的取舍（两种方式的差别）

| | **systemd**（推荐） | Docker |
|---|---|---|
| 常驻进程 | 1 个 `python3` | python + 容器 shim + veth 网络命名空间 |
| 内存 | ~15 MB（就是解释器本身） | 同上 **+5~15 MB** |
| 磁盘 | 脚本 11 KB + `python3`（NAS 自带） | 还要拉 python 镜像（slim ≈ 120 MB） |
| CPU | 每 30 分钟醒一次，平时 ≈ 0 | 同上 |
| 启动 | <1 秒 | 几秒（容器起 + python 起） |
| 好处 | 最省、随系统起、崩了自动重启、日志进 journald | 隔离干净、fnOS 图形界面能管、升级镜像即可 |

这个中继就是"收到请求 → 可能有缓存 → 调一次和风 → 回一行 JSON"，**没理由再套一层容器**。
除非你更看重"图形界面里点一下就能看日志/重启"，那就用 Docker 那份。

## 路线 A：systemd（省资源）

在 NAS 上（root）：

```bash
install -d -m 0755 /opt/wx-relay
cp wx-relay.py wx-compare.py /opt/wx-relay/         # ⚠ 两个都要：中继复用对比脚本里的取数逻辑
cp ed25519-private.pem /opt/wx-relay/
chmod 600 /opt/wx-relay/ed25519-private.pem         # 私钥只有 root 能读

cp wx-relay.env.example /etc/wx-relay.env
vi /etc/wx-relay.env                                # 改 Host / KID / SUB / DEV_ID（私钥路径已写好）
chmod 600 /etc/wx-relay.env                         # 里面有凭据

cp wx-relay.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wx-relay
systemctl status wx-relay --no-pager                # active (running)
```

验证：

```bash
curl -s http://127.0.0.1:8788/wx        # {"code":3,"temp":29,"text":"阴","src":"qweather",...}
ss -lntp | grep 8788                    # 确认在监听
journalctl -u wx-relay -n 20 --no-pager # 看取数日志
```

最后在 ESP32 的 `zk_base_esp32.ino` 里填：

```c
#define WX_RELAY_URL "http://<NAS 的局域网IP>:8788/wx"
```

## 路线 B：Docker（**这台 NAS 上推荐**，跟现有服务一套管法）

目录就照 NAS 上现有习惯放 `/vol1/1000/docker/wx-relay/`：

```bash
# NAS 上（cyrus 身份，docker 直用、不用 sudo）
mkdir -p /vol1/1000/docker/wx-relay && cd /vol1/1000/docker/wx-relay
# 把 wx-relay.py / wx-compare.py / ed25519-private.pem / docker-compose.yml / wx-relay.env.example
# 拷进来（从 Mac 上 scp，或 fnOS 文件管理器拖）
chmod 600 ed25519-private.pem
cp wx-relay.env.example .env && vi .env && chmod 600 .env   # 改 Host/KID/SUB/DEV_ID

docker compose up -d
docker compose logs -f          # 看到"和风中继启动：http://0.0.0.0:8788/wx"就对了
curl -s http://127.0.0.1:8788/wx    # 应回一行 JSON
```

fnOS 的「Docker → 项目 → 新建」也能直接把 `docker-compose.yml` 粘进去（一样的效果）。

## 两件要紧的事

1. **私钥权限**：`ed25519-private.pem` 在 NAS 上 `chmod 600`，别放进任何同步/备份到公网的地方。
   它一旦泄露，别人配合你的 API Host 就能冒充你调接口（虽然 JWT 每次 15 分钟过期、可随时
   在控制台换钥匙，但还是别泄露）。
2. **ESP32 那边不用放私钥了**：配了 `WX_RELAY_URL` 之后，价签基站只跟局域网说话，
   连和风凭据都不需要 —— 这也是选这条路的额外好处。

## 故障对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| `curl /wx` 返回 502 + 错误体 | 中继自己取不到（和风凭据/网络） | 看错误体：`Invalid Host` → Host 写错；`token/kid` → 凭据不对 |
| `curl /wx` 连不上 | 服务没起 / 端口没通 | `systemctl status wx-relay`；NAS 防火墙放行 8788（局域网内） |
| ESP32 日志 `中继：HTTP -1` | ESP32 到 NAS 不通 | 确认 NAS 的 IP 写对、8788 端口可达（先在 Mac 上 curl 试） |
| 温度一直不变 | 缓存命中（默认 120 秒）或 ESP32 没重发 | 正常现象；和风实况本身也是 10~30 分钟更新一次 |

## 2026-10-02 追加：中继现在也取「天气预警」，并且有了外部域名

- **预警**：`wx-relay.py` 在取实况的同时取和风的**实时天气预警**
  （`GET /weatheralert/v1/current/{纬度}/{经度}` —— 老的 `/v7/warning/now` 已被和风下架，
  回 403 Deprecated）。JSON 里多四个平铺字段 `alert/alevel/atype/aend`。
  预警取不到**不影响天气**，只是那几个字段不出现。
- **域名**：Lucky 上加了 `weather.swimbirds.com` → `http://127.0.0.1:8788`（NAS 上 Lucky 是 host 网络）。
  内网可用：`curl -s https://weather.swimbirds.com/wx`（走 NAS 自己的公网 IPv6，证书是
  `*.swimbirds.com` 的通配符，2026-11-11 到期）。
  ⚠ **公网进不来**：拿 `https://r.jina.ai/<url>` 做外网抓取实测超时，而同一时刻
  `r.jina.ai/https://ipv6.google.com/` 返回 200（说明那个抓取服务有 IPv6 出口）→
  运营商侧不放行入站。人在外面要看数据走 tailnet：`http://192.168.100.221:8788/wx`。
- **NAS 静态租约**：路由器上加了 `fnNAS / 86:8F:D3:90:86:90 / 192.168.100.221` 的预留
  （NAS 其实是自己手配的静态 IP，不是 DHCP 客户端；这条的作用是把 .221 从 DHCP 池里锁住）。
