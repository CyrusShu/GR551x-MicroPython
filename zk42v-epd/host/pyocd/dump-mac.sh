#!/bin/bash
# =====================================================================
#  GR551x (ZK42V) 原厂固件备份 -- pyOCD 版
#
#  READ ONLY. 只连、只读，什么都不写。
#
#  用法（在你自己的终端里跑）：
#      bash dump-mac.sh                       # 默认模式
#      UNDER_RESET=1 bash dump-mac.sh         # RST 线接了才用
#      SPEED=240k bash dump-mac.sh            # 降速
#      PYOCD=/path/to/pyocd bash dump-mac.sh  # 换 pyocd 路径
# =====================================================================

cd "$(dirname "$0")" || exit 1

PYOCD="${PYOCD:-/Users/mac/Documents/Codex/2026-09-15/a/tools/pyocd/pyocd}"
SPEED="${SPEED:-500k}"
OUT="${OUT:-$PWD}"

if [ ! -x "$PYOCD" ]; then
    echo "找不到 pyocd: $PYOCD"
    echo "用 PYOCD=/你的路径/pyocd bash dump-mac.sh 指定一个"
    exit 1
fi

MODE=()
if [ -n "${UNDER_RESET:-}" ]; then
    MODE=(-M under-reset)
fi

mkdir -p "$OUT"
LOG="$OUT/pyocd-$(date +%Y%m%d-%H%M%S).log"

echo "pyocd   : $PYOCD"
"$PYOCD" --version 2>&1 | head -1
echo "速度    : $SPEED"
echo "模式    : ${MODE[*]:-halt（默认，不需要 RST 线）}"
echo "输出目录: $OUT"
echo "日志    : $LOG"
echo

FACTORY="$OUT/zk42v-factory-512k.bin"
NVDS="$OUT/zk42v-nvds-4k.bin"

{
    echo "=== 探测到的调试器 ==="
    "$PYOCD" list -p
    echo
    echo "=== 连接 + 状态 ==="
    # -W = 没有调试器时立刻报错，不要傻等
    "$PYOCD" commander -W -t cortex_m -f "$SPEED" "${MODE[@]}" \
        -c "status" \
        -c "savemem 0x01000000 0x80000 $FACTORY" \
        -c "savemem 0x0107F000 0x1000 $NVDS"
} 2>&1 | tee "$LOG"

echo
echo "===== 结果 ====="
if [ -f "$FACTORY" ]; then
    ls -l "$FACTORY" "$NVDS" 2>/dev/null
    echo
    shasum -a 256 "$FACTORY" "$NVDS" 2>/dev/null
    echo
    echo "文件头 32 字节（正常的话前 4 字节是栈指针，形如 3x xx xx xx）："
    xxd -l 32 "$FACTORY" 2>/dev/null || od -A x -t x1 -N 32 "$FACTORY"
    echo
    echo "再跑一次这个脚本，SHA-256 必须一模一样才算可信。"
else
    echo "没有产出文件 —— 连接没成功，把上面日志里的错误发我。"
fi
echo "完整日志: $LOG"
