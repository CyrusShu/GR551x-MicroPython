#!/bin/bash
# =====================================================================
#  flash 三路会审 —— 换三条完全不同的路问："flash 里的字节到底是什么"
#
#  路 1  CSW 实验：把 AHB-AP 的 CSW 换成十几种组合（重点 bit29 MSTRTYPE、
#        bit30 HNONSEC、HPROT[4:0]），每种都手搓一次 TAR/DRW 事务。
#        参照点：0x0100A200 应该是 app_info magic 0x47525858。
#  路 2  别名窗口 0x03000000：Goodix SDK 的 EXFLASH_ALIAS_ADDR
#        （= 0x01000000 + 0x02000000），之前从来没扫过这个地址。
#  路 3  CPU 搬运：让 CPU 自己把 flash 抄进 RAM，我们再从 RAM 读回来。
#        这一步会往 RAM 写十几个字节并让 CPU 跑 0.25 秒（不碰 flash），
#        所以会在中途问你一句要不要做。
#
#  用法：
#      MANUAL=hold bash flash-lab2.sh
#      MANUAL=hold TRAMP=no bash flash-lab2.sh      # 只跑只读的两路
#      MANUAL=hold TRAMP=yes bash flash-lab2.sh     # 三路一次跑完，不中途问
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
export TRAMP_SRC="${TRAMP_SRC:-0x0100A000}"
export TRAMP_BYTES="${TRAMP_BYTES:-0x1000}"

LOG="$PWD/flash-lab2-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "速度  : $SPD"
echo "CPU 搬运试验(TRAMP): $TRAMP"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c flashlab2 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
echo "路 1 / 路 2 / 外设 / eFuse 全程只读；路 3 只写 RAM，从不碰 flash。"
