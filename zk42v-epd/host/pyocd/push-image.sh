#!/bin/bash
# =====================================================================
#  B2-B：把一张图片推到价签上（SWD -> 共享内存信箱 -> 固件刷屏）
#
#      IMG=~/Desktop/a.png bash push-image.sh
#      IMG=~/Desktop/photo.jpg MODE=bw bash push-image.sh     # 只出黑白，照片更清楚
#
#  它干两件事：
#    1) outputs/firmware/tools/img2epd.py 把图转成 30000 字节（顺带出一张预览图）
#    2) pyOCD 通过 SWD 把这块数据塞进固件的共享内存信箱，然后等固件刷完
#
#  判据：
#    - 转换那步会打印「像素 : 黑 x% 白 y% 红 z%」，预览图也在（自己看一眼）
#    - 推送那步打印「>>> 刷完了（status=3，用了 xxxxx ms）」
#    - 屏上出现这张图
#
#  安全网：随时 cd outputs/pyocd && MODE=restore bash flash-write.sh 回厂。
# =====================================================================
cd "$(dirname "$0")" || exit 1
ROOT="$(cd ../.. && pwd)"

IMG="${IMG:-}"
if [ -z "$IMG" ]; then
    echo "用法: IMG=<图片路径> bash push-image.sh"
    echo "例  : IMG=~/Desktop/a.png bash push-image.sh"
    exit 1
fi
if [ ! -f "$IMG" ]; then
    echo "找不到图片：$IMG"
    exit 1
fi

EPD="${EPD:-$PWD/last-push.epd}"
PREVIEW="${PREVIEW:-$PWD/last-push.preview.png}"
MODE="${MODE:-bwr}"
DITHER_OPT=""
[ "${NO_DITHER:-0}" = "1" ] && DITHER_OPT="--no-dither"

if [ -n "$PYOCD" ]; then :;
elif [ -x "$ROOT/tools/pyocd/pyocd" ]; then PYOCD="$ROOT/tools/pyocd/pyocd"
elif command -v pyocd >/dev/null 2>&1; then PYOCD="$(command -v pyocd)"
else echo "找不到 pyocd"; exit 1; fi

SPD="${SPD:-240k}"
export GR551X_OUTDIR="${OUTDIR:-$PWD/../}"
export BEEP="${BEEP:-1}"
export MANUAL="${MANUAL:-0}"          # 我们固件不关 SWD，默认不复位直接连
export HOLD_WAIT_SECS="${HOLD_WAIT_SECS:-3}"
export DUMP_WINDOW_MS="${WINDOW_MS:-8000}"
export RST_VIA="${RST_VIA:-ttl}"
export TTL_PORT="${TTL_PORT:-/dev/cu.usbserial-210}"
export EPD_FILE="$EPD"

echo "================================================================"
echo "  第 1 步：转图  ->  $EPD"
echo "================================================================"
python3 "$ROOT/outputs/firmware/tools/img2epd.py" "$IMG" \
        --out "$EPD" --preview "$PREVIEW" --mode "$MODE" $DITHER_OPT || exit 1
echo
echo "预览图（先看看效果对不对，再决定要不要推）：$PREVIEW"
echo
echo "================================================================"
echo "  第 2 步：推给价签（SWD）"
echo "================================================================"
LOG="$PWD/push-image-$(date +%Y%m%d-%H%M%S).log"
"$PYOCD" commander -W -N -t cortex_m -f "$SPD" --no-config \
    --script "$PWD/led-window-user.py" \
    -c pushimg 2>&1 | tee "$LOG"
echo
echo "日志: $LOG"
echo "回厂: MODE=restore bash flash-write.sh"
