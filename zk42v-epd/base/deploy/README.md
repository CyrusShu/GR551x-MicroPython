# 把天气中继装到 NAS 上（常驻）

## 先用一句话回答：**systemd 更省**

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

## 路线 B：Docker（fnOS 图形界面友好）

```bash
mkdir -p ~/wx-relay && cd ~/wx-relay
cp /path/to/wx-relay.py /path/to/wx-compare.py ./
cp /path/to/ed25519-private.pem . && chmod 600 ed25519-private.pem
cp /path/to/wx-relay.env.example ../wx-relay.env   # 按注释改好
# 把 docker-compose.yml 放到 ~/wx-relay 的上一层，然后：
docker compose up -d
docker logs -f wx-relay
```

fnOS 的「Docker → 项目 → 新建」也能直接把 `docker-compose.yml` 粘进去。

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
