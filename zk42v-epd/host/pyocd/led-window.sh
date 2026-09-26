#!/bin/bash
# =====================================================================
#  按价签指示灯的实际时序去抢 SWD 窗口 —— pyOCD 版，只读不写
#
#  你的实测时序：
#      RST 接地松开 ──> 约 2 秒后【绿】亮 1 秒 ──>【蓝】亮 1 秒 ──>【红】
#
#  工具围着这个时序来：
#    第 0 步  只驱动 RST 拉 5 下（每下间隔 1.2 秒），完全不碰 SWD
#             —— 屏幕一定会打出 5 行「拉低 RST」，灯跟着动就说明线通
#    第 1 步  一轮一轮地：拉复位 -> 从 t=0 起高频重试 SWD -> 试到 4.6 秒
#             t=2s/3s/4s 各播一声不同的音（绿=叮 蓝=叮咚 红=咚）
#    命中就报「第几轮、复位后多少毫秒、当时是哪盏灯」，然后整片读回
#
#  为什么用 -N（--no-init）：pyOCD 平时一上来就初始化目标，芯片不应答
#  就报错退出。加 -N 后它只打开适配器、设好时钟，把 session/probe 交给
#  我们自己的脚本 led-window-user.py，由我们去决定什么时候连、怎么连。
#
#  用法（在你自己的 Terminal 里跑）：
#      cd /Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd
#      bash led-window.sh
#
#  常用开关：
#      SPD=240   bash led-window.sh      # 降到 240kHz 重试
#      RND=8     bash led-window.sh      # 轮数（默认 4）
#      WIN=6000  bash led-window.sh      # 每轮抓多久，默认 4600ms
#      LED_MS=2500 bash led-window.sh    # 如果实际不是 2 秒才转绿
#      BEEP=0    bash led-window.sh      # 静音
#      SKIP_RSTCHECK=1 bash led-window.sh   # 跳过第 0 步
#      ONLY_PULSE=1    bash led-window.sh   # 只做第 0 步
#      MANUAL=1        bash led-window.sh   # RST 线不通时：你用手碰 RST，工具只管锤
# =====================================================================

cd "$(dirname "$0")" || exit 1

ROOT="$(cd ../.. && pwd)"

if [ -n "$PYOCD" ]; then
    :
elif command -v pyocd >/dev/null 2>&1; then
    PYOCD="$(command -v pyocd)"
elif [ -x "$ROOT/tools/pyocd/pyocd" ]; then
    PYOCD="$ROOT/tools/pyocd/pyocd"
else
    echo "找不到 pyocd。"
    echo "本目录里应该有一份束在 $ROOT/tools/pyocd/pyocd，或者 pip install pyocd"
    exit 1
fi

SCRIPT="$PWD/led-window-user.py"
if [ ! -f "$SCRIPT" ]; then
    echo "找不到 $SCRIPT"
    exit 1
fi

OUTDIR="${GR551X_OUTDIR:-${OUTDIR:-/Users/mac/Documents/Codex/2026-09-15/a/outputs}}"
SPD="${SPD:-500k}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$PWD/led-window-$STAMP.log"

export GR551X_OUTDIR="$OUTDIR"
export LED_DELAY_MS="${LED_MS:-${LED_DELAY_MS:-2000}}"
export WINDOW_MS="${WIN:-${WINDOW_MS:-4600}}"
export ROUNDS="${RND:-${ROUNDS:-4}}"
export HOLD_MS="${HOLD:-${HOLD_MS:-200}}"
export GR551X_PULSES="${PULSES:-${GR551X_PULSES:-5}}"
export GR551X_PULSE_GAP="${GAP:-${GR551X_PULSE_GAP:-1200}}"
export BEEP="${BEEP:-1}"
export MANUAL="${MANUAL:-0}"
export RST_ENTER_SWD="${RST_ENTER_SWD:-1}"
export RST_VIA="${RST_VIA:-stlink}"          # stlink | ttl
export TTL_PORT="${TTL_PORT:-/dev/cu.usbserial-210}"
export TTL_LINE="${TTL_LINE:-rts}"
export TTL_INVERT="${TTL_INVERT:-0}"
export ESP32_PORT="${ESP32_PORT:-}"      # RST_VIA=esp32 时填 ESP32 的串口

# -c 后面跟的是本脚本注册的两条命令（rstpulse / ledwindow）。
# 放在最后，免得 -c 把后面的选项当成它的参数吃掉。
run_pyocd() {
    "$PYOCD" commander \
        -W -N -M halt -t cortex_m -f "$SPD" --no-config \
        --script "$SCRIPT" \
        -c "$1" 2>&1 | tee -a "$LOG"
}

echo "pyocd   : $PYOCD"
echo "脚本    : $SCRIPT"
echo "速度    : $SPD   轮数: $ROUNDS   每轮窗口: $WINDOW_MS ms"
echo "时序基准: 复位后 $LED_DELAY_MS ms 转绿"
echo "产物到  : $OUTDIR"
echo "日志    : $LOG"
echo

if [ -z "$SKIP_RSTCHECK" ]; then
    echo "=================================================================="
    echo "  第 0 步：只拉 RST，完全不碰 SWD"
    echo "------------------------------------------------------------------"
    echo "  要拉 $GR551X_PULSES 下，每下间隔 $GR551X_PULSE_GAP ms。"
    echo "  屏幕上一定会打出 $GR551X_PULSES 行「拉低 RST」——"
    echo "  没看到就是工具没发出去，不是你线的问题。"
    echo ""
    echo "  盯着价签的指示灯：每下拉完松开，约 2 秒后灯应该走 绿-蓝-红。"
    echo "=================================================================="
    echo
    sleep 2

    run_pyocd rstpulse

    echo
    if grep -q "RESULT_PULSE_FAIL" "$LOG"; then
        echo "!!! 脉冲没发出去（上面有原因）。这不是接线问题。"
        echo "    先看上面的报错，接口能打开才谈得上拉线。"
        echo
        exit 2
    fi
    echo "刚才那 $GR551X_PULSES 次拉复位，灯重启了几次？"
    echo "  $GR551X_PULSES 次 = RST 线通的，接着抢窗口"
    echo "  0 次   = 脉冲确实发出去了但灯没动 -> RST 线没接通或接错脚"
    echo

    if [ -z "$ONLY_PULSE" ]; then
        ans=""
        if [ -t 0 ]; then
            printf "看到几次重启？(回车=继续) : "
            read -r ans
        fi
        echo
    fi
fi

if [ -n "$ONLY_PULSE" ]; then
    echo "ONLY_PULSE=1，只做第 0 步，收工。"
    echo "日志: $LOG"
    exit 0
fi

echo "=================================================================="
echo "  第 1 步：抢窗口"
echo "------------------------------------------------------------------"
echo "  每轮开始，ST-Link 会把 RST 拉低 ${HOLD_MS}ms 再松开，那一刻灯会重启。"
echo "  接着三声不同的提示音分别对应："
echo "      叮    -> 绿灯应该亮（复位后 2.0s，应用开始接管）"
echo "      叮咚  -> 蓝灯应该亮（复位后 3.0s）"
echo "      咚    -> 红灯（复位后 4.0s）"
echo "  命中会立刻报出来，并自动整片读回 512KB。"
echo "=================================================================="
echo
sleep 2

run_pyocd ledwindow
RC=$?

echo
echo "=================================================================="
echo "  结果"
echo "------------------------------------------------------------------"
if grep -q "^RESULT_HIT" "$LOG"; then
    grep "^RESULT_HIT" "$LOG" | tail -1
    echo
    ls -l "$OUTDIR/zk42v-factory-512k.bin" "$OUTDIR/zk42v-nvds-4k.bin" 2>/dev/null
    echo
    echo "  再跑一遍比对 SHA-256 一致，这份备份才算可信。"
elif grep -q "^RESULT_FAIL" "$LOG"; then
    grep "^RESULT_FAIL" "$LOG" | tail -1
    echo
    echo "  连 ROM 那 2 秒都抢不到 —— 问题不在时机，在链路。"
    echo "  下一个 30 秒就能验证的动作：把 SWCLK 和 SWDIO 两根线对调。"
else
    echo "  没跑完（退出码 $RC）。看日志最后 30 行："
    echo
    tail -30 "$LOG" 2>/dev/null
fi
echo
echo "完整日志: $LOG"
echo "Nothing was written to the chip."
exit $RC
