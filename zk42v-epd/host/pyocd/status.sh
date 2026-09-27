#!/bin/bash
# =====================================================================
#  读自研固件写在 0x3001F000 的调试状态块
#
#      bash status.sh
#
#  这块价签的 UART 引脚我们还没挖出来，看不到 printf。
#  但我们的固件**不关 SWD**（原厂固件会关），所以调试器随时能连上，
#  于是固件每走完一步就往固定地址 0x3001F000 写一个数 —— 相当于 printf。
#
#  判据（正常应该长这样）：
#      自研固件在跑 ✅  magic=0x5A4B3401
#      stage = 9  ->  空闲循环里（心跳应该一直在涨）
#      心跳：N -> M，在涨 ✅
#      ☑ sys_swd_enable() 调用成功
#  如果 stage 停在 3/4/5，说明卡在初始化，把整段发我。
#
#  B2-A.2（build 19 起）还会多打两段：
#      「B2-A.2 实验一：扫描」 -> 听到几个设备 / 最后和最强的 RSSI
#      「B2-A.2 实验二：广播数据变体」 -> 6 种广播数据各自的 ADV_START 状态码
#  刚上电时这两段还没跑完（扫描要 4 秒，广播变体再各等一会儿），所以
#  这条脚本默认先等 STATUS_SETTLE_MS=15000 毫秒再采。只想快速看一眼
#  stage/boot_count 的话，用 STATUS_SETTLE_MS=0 bash status.sh 跳过等待。
#
#  注意：脚本会先试「不复位直接连」，连不上再抢复位窗口 ——
#  连上之后会等 3 秒让固件把该做的做完（用 STATUS_WAIT_MS= 改）。
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
export STATUS_WAIT_MS="${STATUS_WAIT_MS:-3000}"
export STATUS_SETTLE_MS="${STATUS_SETTLE_MS:-15000}"

LOG="$PWD/status-$(date +%Y%m%d-%H%M%S).log"
"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c status 2>&1 | tee "$LOG"

echo
echo "日志: $LOG"
