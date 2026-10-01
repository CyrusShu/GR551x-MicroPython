#!/bin/sh
# 建一个带 bleak（macOS 走 CoreBluetooth）的 venv。只需跑一次。
# 注意：**在 Terminal 里跑**，不要在 Codex 沙箱里跑 —— 蓝牙权限认的是 Terminal。
set -e
DIR=$(cd "$(dirname "$0")" && pwd)

python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade pip
"$DIR/.venv/bin/pip" install --quiet bleak

echo "环境就绪：$DIR/.venv"
"$DIR/.venv/bin/python3" -c "import importlib.metadata as m; print('bleak', m.version('bleak'))"
echo
echo "下一步： $DIR/run.sh probe"
echo "（第一次会弹「Terminal 想使用蓝牙」，点允许）"
