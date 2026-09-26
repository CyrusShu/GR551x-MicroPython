#!/bin/bash
# =====================================================================
#  只回答一个问题：RST 松开之后，SWD 能不能重新握上手？
#
#  为什么要单独做这个：价签那颗 RGB 灯是【应用固件】点亮的，应用状态存在
#  NVDS 里。灯不亮 ≠ 复位失效。真正的判据只有两个：
#     · 串口：按 RST 后开机日志会不会重新打一遍
#     · SWD ：按 RST 后调试器能不能重新握上手（复位前是握不上的）
#  这条命令就是第二个判据，顺便报一下 DP IDR / CPUID / CPU 状态。
#
#  用法：
#      MANUAL=hold bash rst-check.sh
# =====================================================================
cd "$(dirname "$0")" || exit 1
ROOT="$(cd ../.. && pwd)"
if [ -n "$PYOCD" ]; then :;
elif [ -x "$ROOT/tools/pyocd/pyocd" ]; then PYOCD="$ROOT/tools/pyocd/pyocd"
elif command -v pyocd >/dev/null 2>&1; then PYOCD="$(command -v pyocd)"
else echo "找不到 pyocd"; exit 1; fi

SPD="${SPD:-240k}"
export GR551X_OUTDIR="${OUTDIR:-$PWD/../}"
export BEEP="${BEEP:-1}"
export MANUAL="${MANUAL:-hold}"
export HOLD_WAIT_SECS="${HOLD_WAIT_SECS:-8}"
export DUMP_WINDOW_MS="${WINDOW_MS:-8000}"
export RST_VIA="${RST_VIA:-ttl}"
export TTL_PORT="${TTL_PORT:-/dev/cu.usbserial-210}"

LOG="$PWD/rst-check-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "速度  : $SPD"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c rstcheck 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
