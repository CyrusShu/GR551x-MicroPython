#!/bin/bash
# =====================================================================
#  等 XIP 打开（连着 SWD 不放，让 CPU 自己往下跑）
#
#  假设：0x01000000 这个窗口背后是 XQSPI 的缓存/XIP 引擎。ROM/bootloader
#  阶段控制器还在"间接模式"（用 QSPI 寄存器一条条读），XIP 没开 —— 这时候
#  谁去读那个地址都只拿到锁存住的死值。等 bootloader 校验完镜像、打开 XIP、
#  跳到 0x0100A000 跑应用之后，窗口才是"真"的。
#
#  所以这一轮不 halt：连着 SWD 让 CPU 照常跑，每 5ms 去戳一次 0x0100A200，
#  一变活就立刻 halt（冻住 CPU，免得应用跑去睡觉/抢调试口），然后趁窗口
#  活着整片读回。
#
#  用法：
#      MANUAL=hold bash flash-lab4.sh
#      POLL_MS=1 POLL_TOTAL_MS=6000 MANUAL=hold bash flash-lab4.sh
#      AUTODUMP=0 MANUAL=hold bash flash-lab4.sh     # 只看窗口活不活，不整片读
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
export POLL_MS="${POLL_MS:-5}"
export POLL_TOTAL_MS="${POLL_TOTAL_MS:-4000}"
export DUMP_DIR="${DUMP_DIR:-$PWD}"
export DUMP_OUT="${DUMP_OUT:-$DUMP_DIR/zk42v-live-512k.bin}"
export DUMP_BASE="${DUMP_BASE:-0x01000000}"
export DUMP_TOTAL="${DUMP_TOTAL:-0x80000}"
export AUTODUMP="${AUTODUMP:-1}"

LOG="$PWD/flash-lab4-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "速度  : $SPD"
echo "盯到  : ${POLL_TOTAL_MS}ms（每 ${POLL_MS}ms 一次）"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c flashlab4 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
echo "Nothing was written to the chip (flash)."
