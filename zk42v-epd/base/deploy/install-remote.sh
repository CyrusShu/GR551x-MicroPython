#!/bin/bash
# =============================================================================
#  在 **Mac 上**跑这个脚本，把中继推到 NAS 上（文件都在 Mac 这边，NAS 里没有！）
#
#      bash deploy/install-remote.sh root@192.168.100.50
#      bash deploy/install-remote.sh root@nas.local /opt/wx-relay   # 也可以换目标目录
#
#  它做三件事：
#    ① rsync/scp 把 wx-relay.py、wx-compare.py、私钥、systemd 单元、env 模板送过去
#    ② 在远端建好目录、把私钥 chmod 600
#    ③ 打印"接下来在 NAS 上要敲的那几行"（改 env、装服务、验证）
# =============================================================================
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "用法: bash deploy/install-remote.sh <user@host> [远端目录]"
    echo "例：  bash deploy/install-remote.sh root@192.168.100.50"
    exit 1
fi

DEST_HOST="$1"
DEST_DIR="${2:-/opt/wx-relay}"
HERE="$(cd "$(dirname "$0")" && pwd)"        # .../ble-base/deploy
BASE="$(cd "$HERE/.." && pwd)"               # .../ble-base

# 私钥可能叫这两个名字之一（keygen 早期版本生成的名字不同）
KEY=""
for k in "$BASE/ed25519-private.pem" "$BASE/qweather-ed25519.pem"; do
    [ -f "$k" ] && KEY="$k" && break
done

for f in "$BASE/wx-relay.py" "$BASE/wx-compare.py" "$HERE/wx-relay.service" \
         "$HERE/wx-relay.env.example"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done
if [ -z "$KEY" ]; then
    echo "⚠ 没找到私钥（ed25519-private.pem / qweather-ed25519.pem）——"
    echo "  先跑 bash qweather-jwt-keygen.sh 生成，或用 --key 手动指定。"
    echo "  （只用 API key 的话可以忽略，但那样就别把 QWEATHER_JWT_* 填进 env）"
fi

echo "① 在 $DEST_HOST 上建目录 $DEST_DIR ..."
ssh "$DEST_HOST" "mkdir -p '$DEST_DIR' && chmod 755 '$DEST_DIR'"

echo "② 传文件（脚本 + 私钥 + 部署文件）..."
scp "$BASE/wx-relay.py" "$BASE/wx-compare.py" "$HERE/wx-relay.service" \
    "$HERE/wx-relay.env.example" "$DEST_HOST:$DEST_DIR/"
if [ -n "$KEY" ]; then
    scp "$KEY" "$DEST_HOST:$DEST_DIR/ed25519-private.pem"
    ssh "$DEST_HOST" "chmod 600 '$DEST_DIR/ed25519-private.pem'"
fi

cat <<EOF

③ 接下来**在那台机器上**（NAS）敲这几行：

    cd $DEST_DIR
    cp wx-relay.env.example /etc/wx-relay.env
    vi /etc/wx-relay.env            # 改 QWEATHER_HOST / KID / SUB / DEV_ID（私钥路径已经是 $DEST_DIR/ed25519-private.pem）
    chmod 600 /etc/wx-relay.env

    # 先手动跑一次，确认能取到数（Ctrl-C 退出）
    set -a; . /etc/wx-relay.env; set +a; python3 $DEST_DIR/wx-relay.py
    # 另开一个终端：curl -s http://127.0.0.1:8788/wx   → 应回一行 JSON

    # 没问题就装成常驻服务
    cp wx-relay.service /etc/systemd/system/
    systemctl daemon-reload && systemctl enable --now wx-relay
    systemctl status wx-relay --no-pager

④ ESP32 那边填这台的局域网 IP：

    #define WX_RELAY_URL "http://$(ssh "$DEST_HOST" "hostname -I 2>/dev/null | awk '{print \$1}'" || echo '<NAS_IP>'):8788/wx"

（Docker 版见 deploy/docker-compose.yml —— 不过按资源算，systemd 那份更省。）
EOF
