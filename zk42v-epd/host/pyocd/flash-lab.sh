#!/bin/bash
# =====================================================================
#  flash 定位实验（全程只读）
#
#  dap-info 已经证明：读通路是好的（CPUID 对、ROM 表对、halt 后 DHCSR
#  的 S_HALT 位变了），但 0x01000000 那一整块返回死值。
#  这个实验去找"哪儿能读到真数据"：
#    · CPU running vs halted 时读，值变不变
#    · 同一个地址 字读 vs 逐字节读
#    · 一排候选地址窗口，看哪个有变化
#    · 别名猜想：flash 在 0x00000000 也有别名的话，app_info 应该在
#      0x0000A200 读到 0x47525858
#
#  用法：
#      MANUAL=hold bash flash-lab.sh
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

LOG="$PWD/flash-lab-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "速度  : $SPD"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c flashlab 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
echo "Nothing was written to the chip."
