#!/bin/sh
# 双击（或在 Terminal 里跑）这个文件 —— 目的只有一个：
# **在 Terminal 的权限下**扫一次 BLE，确认 Mac 自己能看见价签。
# Codex 的沙箱里跑 CoreBluetooth 会拿到 state=2(unsupported)，见 README。
DIR=$(cd "$(dirname "$0")" && pwd)
LOG=/tmp/zk_base_probe.log

echo "== ZK42V 价签扫描（10 秒）=="
"$DIR/.venv/bin/python3" "$DIR/zk_ble_base.py" probe --scan 10 >"$LOG" 2>&1
echo "== 结果 =="
cat "$LOG"
echo
echo "（这份结果同时留在 $LOG）"
