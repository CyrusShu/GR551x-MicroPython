#!/bin/bash
# =====================================================================
#  启动（Linux）—— 命令行跑：  bash 启动-linux.sh
#
#  ⚠ Linux 上用蓝牙需要权限，二选一：
#     · 把自己加进 bluetooth 组：  sudo usermod -aG bluetooth $USER   （重新登录生效）
#     · 或者用 sudo 跑这个脚本
#  另外要确保蓝牙服务开着：  systemctl status bluetooth
# =====================================================================
cd "$(dirname "$0")" || exit 1
cd ..            # 回到 ble-base 目录

echo "==================================================="
echo " ZK42V 价签 —— 电脑基站"
echo "==================================================="

PY="$(command -v python3 || true)"
[ -z "$PY" ] && { echo "❌ 没找到 python3（Ubuntu/Debian: sudo apt install python3 python3-venv）"; exit 1; }

if [ ! -x .venv/bin/python3 ]; then
    echo "第一次运行：正在准备环境…"
    "$PY" -m venv .venv || exit 1
fi
if ! .venv/bin/python3 -c "import bleak" >/dev/null 2>&1; then
    echo "正在安装 bleak …"
    .venv/bin/python3 -m pip install --quiet --upgrade pip
    .venv/bin/python3 -m pip install --quiet bleak || exit 1
fi

echo "找价签中…（价签要上电；这个窗口别关，关了就停止工作）"
exec .venv/bin/python3 zk_ble_base.py watch --config kit/配置.json --log kit/运行日志.txt
