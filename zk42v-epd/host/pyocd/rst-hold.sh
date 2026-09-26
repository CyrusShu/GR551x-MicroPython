#!/bin/bash
# =====================================================================
#  把价签的 RST 钉住，方便你用万用表量线  —— 只驱动引脚，不碰 SWD
#
#  用法：
#      bash rst-hold.sh              # 持续拉低 RST，直到 Ctrl-C
#      SECS=60 bash rst-hold.sh      # 只保持 60 秒
#      STATE=high bash rst-hold.sh   # 持续放开（拉高）
#      STATE=toggle bash rst-hold.sh # 每 2 秒翻一次：用来找出哪根针是 nRST
#      STATE=toggle TOGGLE=1000 bash rst-hold.sh   # 翻快一点，1 秒一次
#
#  为什么用 STATE=toggle：把 ST-Link 插在 USB 上、价签先别接，
#  万用表黑笔碰 ST-Link 的 GND，红笔挨个点 10 根针 ——
#  在 0V 和 3.3V 之间来回跳的那根，就是 nRST。
# =====================================================================

cd "$(dirname "$0")" || exit 1

ROOT="$(cd ../.. && pwd)"

if [ -n "$PYOCD" ]; then
    :
elif command -v pyocd >/dev/null 2>&1; then
    PYOCD="$(command -v pyocd)"
elif [ -x "$ROOT/tools/pyocd/pyocd" ]; then
    PYOCD="$ROOT/tools/pyocd/pyocd"
else
    echo "找不到 pyocd。"
    exit 1
fi

SCRIPT="$PWD/led-window-user.py"
if [ ! -f "$SCRIPT" ]; then
    echo "找不到 $SCRIPT"
    exit 1
fi

export RST_STATE="${STATE:-low}"
export RST_HOLD_SECS="${SECS:-0}"
export RST_TOGGLE_MS="${TOGGLE:-2000}"
export RST_ENTER_SWD="${RST_ENTER_SWD:-1}"
export RST_VIA="${RST_VIA:-stlink}"          # stlink | ttl
export TTL_PORT="${TTL_PORT:-/dev/cu.usbserial-210}"
export TTL_LINE="${TTL_LINE:-rts}"
export TTL_INVERT="${TTL_INVERT:-0}"
export ESP32_PORT="${ESP32_PORT:-}"      # RST_VIA=esp32 时填 ESP32 的串口
SPD="${SPD:-500k}"

LOG="$PWD/rst-hold-$(date +%Y%m%d-%H%M%S).log"

echo "pyocd   : $PYOCD"
echo "模式    : $RST_STATE    保持: ${RST_HOLD_SECS}s (0=到 Ctrl-C)    翻转周期: ${RST_TOGGLE_MS}ms"
echo "速度    : $SPD"
echo "日志    : $LOG"
echo

# -c 放最后：pyOCD 的 -c 是 nargs='+'，会把后面的参数吃掉
"$PYOCD" commander \
    -W -N -M halt -t cortex_m -f "$SPD" --no-config \
    --script "$SCRIPT" \
    -c rsthold 2>&1 | tee -a "$LOG"

echo
echo "日志: $LOG"
echo "Nothing was written to the chip."
