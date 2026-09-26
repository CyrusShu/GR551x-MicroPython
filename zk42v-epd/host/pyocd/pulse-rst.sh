#!/bin/bash
# =====================================================================
#  只驱动 RST 打几下复位脉冲，完全不碰 SWD  —— 不用万用表就能验复位线
#
#  价签要接好：GND / RST（SWDIO、SWCLK 接不接都行，本命令不碰它们）。
#  串口开着更好（tio / screen），既看灯也看开机日志。
#
#  用法：
#      bash pulse-rst.sh              # 默认 5 下，每下 200ms，间隔 1.2s
#      PULSES=8 bash pulse-rst.sh     # 打 8 下
#      RST_ENTER_SWD=0 bash pulse-rst.sh   # 不让 ST-Link 先进 SWD 模式
#
#  判据：
#      灯跟着走「绿-蓝-红」5 次、串口蹦 5 段 "loader info:"  -> 复位线通了
#      灯一下都不动、串口没动静                               -> 复位没送到芯片
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
    echo "找不到 pyocd，用 PYOCD=/路径/pyocd 指定一个。"
    exit 1
fi

SCRIPT="$PWD/led-window-user.py"
[ -f "$SCRIPT" ] || { echo "找不到 $SCRIPT"; exit 1; }

SPD="${SPD:-500k}"
export GR551X_PULSES="${PULSES:-5}"
export HOLD_MS="${HOLD:-200}"
export GR551X_PULSE_GAP="${GAP:-1200}"
export RST_ENTER_SWD="${RST_ENTER_SWD:-1}"

LOG="$PWD/pulse-rst-$(date +%Y%m%d-%H%M%S).log"

echo "pyocd : $PYOCD"
echo "脉冲  : $GR551X_PULSES 下，每下 ${HOLD_MS}ms，间隔 ${GR551X_PULSE_GAP}ms"
echo "日志  : $LOG"
echo

# -c 放最后：pyOCD 的 -c 是 nargs='+'，会把后面的参数吃掉
"$PYOCD" commander \
    -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$SCRIPT" \
    -c rstpulse 2>&1 | tee -a "$LOG"

echo
echo "日志: $LOG"
echo "Nothing was written to the chip."
