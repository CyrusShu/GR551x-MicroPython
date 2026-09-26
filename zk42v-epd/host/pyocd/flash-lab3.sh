#!/bin/bash
# =====================================================================
#  钉死「CPU 到底看到什么」（承接 flash-lab2 那个有歧义的结果）
#
#  flash-lab2 的路 3 搬回来是死值，但有两种可能：
#    (a) CPU 确实执行了我们的代码，而 flash 窗口连 CPU 的数据读也只给死值
#    (b) 我们的代码压根没跑起来
#
#  这一轮：
#    1) 打印 XQSPI 的 CACHE / QSPI / XIP 全部寄存器（只读）
#    2) 让 CPU 跑 200ms 再停住，看那个死值会不会变（只读，但有点冒险）
#    3) 搬运小程序里加了个"标记"：跑过就会改写它 —— 铁证
#       并且从 CPUID（一定等于 0x410FC241）和从 flash 各抄一遍做对照
#
#  用法：
#      MANUAL=hold TRAMP=yes bash flash-lab3.sh
#      MANUAL=hold bash flash-lab3.sh          # 第 3 步会问一句
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
export TRAMP="${TRAMP:-ask}"
export TRAMP_BYTES="${TRAMP_BYTES:-0x40}"
export RESUME_MS="${RESUME_MS:-200}"

LOG="$PWD/flash-lab3-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "速度  : $SPD"
echo "CPU 搬运试验(TRAMP): $TRAMP"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c flashlab3 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
echo "Nothing was written to the chip (flash)."
