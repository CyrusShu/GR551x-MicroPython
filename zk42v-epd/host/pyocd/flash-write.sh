#!/bin/bash
# =====================================================================
#  把备份刷回去（写 flash）—— 分四步走，先探后写，别跳步
#
#      MODE=probe     bash flash-write.sh   # 只 Init，不擦不写（先跑这个）
#      MODE=pagetest  bash flash-write.sh   # 一颗空白扇区上真写一次
#      MODE=peek      bash flash-write.sh   # 只读：干净重连后看一眼试写那颗扇区
#      MODE=restore   bash flash-write.sh   # 把备份整片刷回 0x01000000
#      MODE=verify    bash flash-write.sh   # 只读，跟备份逐字节比
#
#  写用的是 Goodix 自己的 FLM 算法（SDK 里那份），QSPI/XIP 由算法自己配。
#  动手前有一道硬联锁：算法认的基地址必须等于 FW_BASE，对不上就拒绝写。
#
#  ⚠️ pagetest 的"读回"只能算参考：算法一 Init，QSPI 就进了它自己的命令模式，
#     这会儿从 flash 窗口读回来的是个死值（实机上是 0xF7C03400 反复），
#     看上去像"没写进去"。真验收是 peek —— 它干净重连、等应用把 XIP 打开再读。
#     顺序：pagetest -> 按 RST -> peek。peek 认出图案了才准往下跑 restore。
#
#  多按几下 RST 没坏处：这个流程每一轮都要重新抢一次 SWD 窗口。
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
export HOLD_WAIT_SECS="${HOLD_WAIT_SECS:-3}"
export DUMP_WINDOW_MS="${WINDOW_MS:-8000}"
export RST_VIA="${RST_VIA:-ttl}"
export TTL_PORT="${TTL_PORT:-/dev/cu.usbserial-210}"

export MODE="${MODE:-probe}"
export FW_FILE="${FW_FILE:-$PWD/zk42v-factory-backup-run1.bin}"
export FW_BASE="${FW_BASE:-0x01000000}"
export FW_TOTAL="${FW_TOTAL:-0x80000}"
export FLM="${FLM:-$ROOT/GR551x-SDK/build/keil/GR5xxx_16MB_Flash.FLM}"

LOG="$PWD/flash-write-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "MODE  : $MODE"
echo "备份  : $FW_FILE"
echo "算法  : $FLM"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c flashwrite 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
