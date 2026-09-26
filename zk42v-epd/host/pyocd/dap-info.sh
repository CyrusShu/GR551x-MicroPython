#!/bin/bash
# =====================================================================
#  DAP 体检：判断"读通路"到底通不通（全程只读）
#
#  背景：dumpresume 的读法自检发现三条读法读回来都是同一个字
#  （加速路 0x4034C8F7 / 经典路 0xF7C83444），换读法救不了，
#  所以要往下挖一层：直接看 DP / AP 的原始寄存器，并且做一个
#  "值会不会变"的试验（halt 前后读 DHCSR，看 S_HALT 位变不变）。
#
#  用法：
#      MANUAL=hold bash dap-info.sh
#      MANUAL=hold HOLD_WAIT_SECS=5 bash dap-info.sh
#      MANUAL=1 WINDOW_MS=20000 bash dap-info.sh    # 窗口里随便碰 RST
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

LOG="$PWD/dap-info-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "速度  : $SPD"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c dapinfo 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
echo "Nothing was written to the chip."
