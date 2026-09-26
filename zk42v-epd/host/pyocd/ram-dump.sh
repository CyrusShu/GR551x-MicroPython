#!/bin/bash
# =====================================================================
#  CPU 搬运式整片备份（只在 flashlab2 的路 3 试通之后才用）
#
#  做法：每一轮让 CPU 从 flash 抄 32KB 到 RAM，我们再从 RAM 读回来写进文件。
#  全程不写 flash、不擦 flash。中途断了再跑一次会从上次进度接着读。
#
#  用法：
#      MANUAL=hold bash ram-dump.sh
#      DUMP_TOTAL=0x80000 DUMP_RESTART=1 MANUAL=hold bash ram-dump.sh
#      DUMP_OUT=/tmp/zk42v-cpu.bin MANUAL=hold bash ram-dump.sh
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
export DUMP_DIR="${DUMP_DIR:-$PWD}"
export DUMP_OUT="${DUMP_OUT:-$DUMP_DIR/zk42v-cpu-512k.bin}"
export DUMP_TOTAL="${DUMP_TOTAL:-0x80000}"
export TRAMP_RUN_MS="${TRAMP_RUN_MS:-300}"

LOG="$PWD/ram-dump-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "速度  : $SPD"
echo "目标文件: $DUMP_OUT"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c ramdump 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
echo "Nothing was written to the chip (flash)."
