#!/bin/bash
# =====================================================================
#  ZK42V / GR5513 原厂固件备份 —— 分段读 + 断点续读（全程只读）
#
#  为什么不是一把梭：芯片只在复位后的头 1~2 秒（ROM/bootloader）里老实
#  应答 SWD，之后应用接管、又去睡了。一次读 512KB 中间断一下，pyOCD 就
#  整条命令作废、一个字节都不给。所以：分块读 + 进度文件 + 外层反复重来，
#  每次进程都是干净的重新初始化，读到哪算哪。
#
#  用法：
#      bash dump-resume.sh                        # 默认用 TTL 的 RTS 打复位
#      RST_VIA=esp32 ESP32_PORT=/dev/cu.usbserial-XXXX bash dump-resume.sh
#      RST_VIA=stlink bash dump-resume.sh         # 用 ST-Link 的 nRST（这颗推不动）
#      MANUAL=hold bash dump-resume.sh            # 先按住 RST，我倒数、喊"松手"你才松（推荐）
#      MANUAL=1 bash dump-resume.sh               # 碰一下模式：20 秒窗口里随便碰，抢到就停
#        手动模式每轮都会从断点接着读，能读多少读多少。
#      SPD=240k ROUNDS=40 bash dump-resume.sh
#      DUMP_RESTART=1 bash dump-resume.sh         # 从头重读
#      VERIFY=0 bash dump-resume.sh               # 跳过第二遍校验
#
#  读完了会看到 DUMP_COMPLETE 和 SHA-256。
#
#  【2026-09-22 新增】读法自检
#  上一次读完的 512KB 全是同一个字（0x4034C8F7 反复）—— 因为 pyOCD 默认
#  把 AP 的内存接口绑到了 ST-Link 的"加速接口"，地址自增交给 ST-Link 固件
#  做，克隆版固件没做对。现在连上后会自检三条路，自动挑能读出真数据的那条，
#  读完还会拿参照点验一遍文件（SANITY_OK / SANITY_FAIL）。
#      READMODE=auto    默认：自检后自动挑
#      READMODE=apid    经典 AP 块读（pyOCD 自己发 DAP 事务，最可能是这条救场）
#      READMODE=apiw    经典 AP 逐字读（最保守，最慢）
#      READMODE=probe   目标层（走 ST-Link 加速，默认不再用）
# =====================================================================

cd "$(dirname "$0")" || exit 1
ROOT="$(cd ../.. && pwd)"

if [ -n "$PYOCD" ]; then :;
elif [ -x "$ROOT/tools/pyocd/pyocd" ]; then PYOCD="$ROOT/tools/pyocd/pyocd"
elif command -v pyocd >/dev/null 2>&1; then PYOCD="$(command -v pyocd)"
else echo "找不到 pyocd，用 PYOCD=/路径/pyocd 指定。"; exit 1; fi

SCRIPT="$PWD/led-window-user.py"
[ -f "$SCRIPT" ] || { echo "找不到 $SCRIPT"; exit 1; }

SPD="${SPD:-240k}"
ROUNDS="${ROUNDS:-60}"
export OUTDIR="${OUTDIR:-/Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd}"
export GR551X_OUTDIR="$OUTDIR"

# 复位源
export RST_VIA="${RST_VIA:-ttl}"
export TTL_PORT="${TTL_PORT:-/dev/cu.usbserial-210}"
export TTL_LINE="${TTL_LINE:-rts}"
export TTL_INVERT="${TTL_INVERT:-0}"
export ESP32_PORT="${ESP32_PORT:-}"
export MANUAL="${MANUAL:-0}"
export RST_ENTER_SWD="${RST_ENTER_SWD:-1}"

# 读取参数
export DUMP_BASE="${DUMP_BASE:-0x01000000}"
export DUMP_TOTAL="${DUMP_TOTAL:-0x80000}"
export DUMP_CHUNK="${DUMP_CHUNK:-0x1000}"
# 手动模式默认给 20 秒：你可以在窗口里反复碰 RST，不用卡毫秒
if [ "${WINDOW_MS:-}" != "" ]; then
    export DUMP_WINDOW_MS="$WINDOW_MS"
elif [ "${MANUAL:-0}" = "1" ]; then
    export DUMP_WINDOW_MS=20000
else
    export DUMP_WINDOW_MS=3000
fi
export DUMP_VERIFY="${VERIFY:-1}"
export DUMP_RESTART="${DUMP_RESTART:-0}"
export HOLD_MS="${HOLD:-200}"
export HOLD_WAIT_SECS="${HOLD_WAIT_SECS:-8}"   # MANUAL=hold 时的倒数秒数
export READMODE="${READMODE:-auto}"

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$OUTDIR/dump-resume-$STAMP.log"
BIN="$OUTDIR/zk42v-factory-512k.bin"

{
echo "pyocd    : $PYOCD"
"$PYOCD" --version 2>&1 | head -1
echo "速度     : $SPD"
if [ "$MANUAL" = "hold" ]; then
    echo "复位源   : 手动（先按住 RST，倒数 $HOLD_WAIT_SECS 秒后提示松手）"
elif [ "$MANUAL" != "0" ]; then
    echo "复位源   : 手动（窗口内随便碰 RST）"
else
    echo "复位源   : $RST_VIA $( [ "$RST_VIA" = ttl ] && echo "($TTL_PORT $TTL_LINE)" )"
fi
echo "目标文件 : $BIN"
echo "日志     : $LOG"
echo "最多     : $ROUNDS 轮（每轮都是干净的一次重连）"
echo "读法     : $READMODE （auto = 连上后自检三条路，挑能读出真数据的）"
echo
} | tee "$LOG"

done_ok=0
for i in $(seq 1 "$ROUNDS"); do
    echo "==================================================================" | tee -a "$LOG"
    echo "  第 $i/$ROUNDS 轮   $(date +%H:%M:%S)" | tee -a "$LOG"
    echo "==================================================================" | tee -a "$LOG"

    # -N：只开适配器，不初始化目标；-c 放最后（nargs='+' 会吃掉后面的参数）
    "$PYOCD" commander \
        -W -N -t cortex_m -f "$SPD" --no-config \
        --script "$SCRIPT" \
        -c dumpresume 2>&1 | tee -a "$LOG"

    if grep -q "DUMP_COMPLETE" "$LOG"; then done_ok=1; break; fi

    # 每轮之间歇一下，让价签自己走完开机流程
    sleep 1
done

echo
echo "===== 结果 =====" | tee -a "$LOG"
if [ "$done_ok" = 1 ]; then
    if grep -q "SANITY_OK" "$LOG"; then
        echo "读完了，而且真伪校验通过（SANITY_OK）。" | tee -a "$LOG"
    else
        echo "读完了，但真伪校验没过（SANITY_FAIL）—— 这份先别当备份！" | tee -a "$LOG"
        echo "换最保守的读法重来：" | tee -a "$LOG"
        echo "    DUMP_RESTART=1 READMODE=apiw MANUAL=hold bash dump-resume.sh" | tee -a "$LOG"
    fi
else
    echo "跑了 $ROUNDS 轮还没读满 —— 看日志里每次卡在哪个偏移。" | tee -a "$LOG"
fi
ls -l "$BIN" "$BIN.progress" 2>/dev/null | tee -a "$LOG"
if [ -f "$BIN" ]; then
    echo "文件大小: $(stat -f %z "$BIN") 字节" | tee -a "$LOG"
    echo "SHA-256 : $(shasum -a 256 "$BIN" | awk '{print $1}')" | tee -a "$LOG"
fi
echo
echo "完整日志: $LOG"
echo "Nothing was written to the chip."
