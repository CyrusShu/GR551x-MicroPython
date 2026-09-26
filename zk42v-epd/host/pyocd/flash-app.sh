#!/bin/bash
# =====================================================================
#  只写「自研 APP 那一段」—— 不动 bootloader、不动 NVDS
#
#      MODE=app        bash flash-app.sh   # 写：APP 那几颗扇区 + 最后改镜像信息
#      MODE=appverify  bash flash-app.sh   # 只读：把 APP 段读回来跟本地镜像对账
#
#  跟 flash-write.sh 的关系：
#    flash-write.sh 的 MODE=restore 是「整片 512KB 全刷」（128 颗扇区，约 3 分钟），
#    用来回厂；这条是日常迭代用的，只写 APP 那几颗（自研固件才 19 颗），
#    而且 bootloader 一个字节都不碰。
#
#  要刷的东西默认是打包好的整片镜像：
#      outputs/firmware/zk42v-custom-512k.bin
#  （它是「出厂备份 + 换掉 APP 段 + 更新 0x2000 那条镜像信息」拼出来的，
#    用 FW_FILE= 可以换。）
#
#  判据：
#    MODE=app        -> 看到「写完了：APP 19 颗 + 信息 1 颗」和「自洽」
#    MODE=appverify  -> 看到「APP_VERIFY_OK」
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

export MODE="${MODE:-app}"
export FW_FILE="${FW_FILE:-$ROOT/outputs/firmware/zk42v-custom-512k.bin}"
export FW_BASE="${FW_BASE:-0x01000000}"
export FW_TOTAL="${FW_TOTAL:-0x80000}"
export FLM="${FLM:-$ROOT/GR551x-SDK/build/keil/GR5xxx_16MB_Flash.FLM}"

LOG="$PWD/flash-app-$(date +%Y%m%d-%H%M%S).log"
echo "pyocd : $PYOCD"
echo "MODE  : $MODE"
echo "镜像  : $FW_FILE"
echo "算法  : $FLM"
echo "日志  : $LOG"
echo

"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c flashwrite 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
