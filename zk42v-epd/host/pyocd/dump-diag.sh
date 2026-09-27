#!/bin/bash
# =====================================================================
#  读法诊断：同一个地址用四种读法各读一遍，看哪种能读到真数据
#
#  背景：dump-resume 读完的 512KB 全是同一个 4 字节（40 34 c8 f7 反复），
#  说明块读的地址自增没生效 / 读的不是那块地方。先诊断，别瞎读。
#
#  用法：
#      MANUAL=hold bash dump-diag.sh
#      MANUAL=hold HOLD_WAIT_SECS=5 bash dump-diag.sh
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
export TTL_LINE="${TTL_LINE:-rts}"
export ESP32_PORT="${ESP32_PORT:-}"

LOG="$PWD/dump-diag-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "速度  : $SPD"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c dumpdiag 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
echo "Nothing was written to the chip."
