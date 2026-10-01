#!/bin/bash
# =============================================================================
#  和风天气 JWT 的密钥生成器（在 Mac 上跑，全程不需要联网）
#
#      bash qweather-jwt-keygen.sh            # 默认生成到 ./qweather-ed25519.pem
#      bash qweather-jwt-keygen.sh my.pem      # 指定文件名
#
#  它做四件事：
#    ① 生成 Ed25519 私钥（PEM）
#    ② 打印**公钥**（十六进制 + base64）—— 粘到和风控制台「凭据」里
#    ③ 打印**私钥 seed 十六进制**（64 个字符）—— 填进 ESP32 的 QWEATHER_JWT_HEX
#    ④ 自检：从刚才那串 seed 重新推出公钥，确认跟 ② 一致（能推出来就说明
#       这串 hex 就是"能把 JWT 签对"的那一串）
#
#  控制台那边：dev.qweather.com → 项目管理 → 新建项目 → 创建凭据
#              （类型选 **JSON Web Token**）→ 上传公钥 → 记下两个 ID：
#        · 凭据 ID → ESP32 的 QWEATHER_JWT_KID
#        # 项目 ID → ESP32 的 QWEATHER_JWT_SUB
# =============================================================================
set -euo pipefail

PEM="${1:-qweather-ed25519.pem}"

if ! command -v openssl >/dev/null 2>&1; then
    echo "找不到 openssl（macOS 自带一个）"; exit 1
fi
if [ -e "$PEM" ]; then
    echo "⚠ $PEM 已存在 —— 直接用它（想换新的先删掉或换个文件名）"
else
    openssl genpkey -algorithm ed25519 -out "$PEM"
    echo "① 私钥已生成：$PEM"
fi
chmod 600 "$PEM"

hex_of_last32() { tail -c 32 | xxd -p -c 64; }

PUB_HEX="$(openssl pkey -in "$PEM" -pubout -outform DER | hex_of_last32)"
SEED_HEX="$(openssl pkey -in "$PEM" -outform DER | hex_of_last32)"
PUB_B64="$(openssl pkey -in "$PEM" -pubout -outform DER | tail -c 32 | base64)"

echo
echo "② 公钥（粘到和风控制台的「凭据」里）："
echo "     十六进制: $PUB_HEX"
echo "     base64  : $PUB_B64"
echo
echo "③ 私钥 seed（填进 ESP32 的 #define QWEATHER_JWT_HEX）："
echo "     $SEED_HEX"
echo

# ④ 自检：用 seed 造一个 PKCS#8，再推公钥，必须跟 ② 一样
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
python3 - "$SEED_HEX" > "$TMP/k.der.hex" <<'PY'
import sys
seed = bytes.fromhex(sys.argv[1])
# PKCS#8 Ed25519 私钥：固定前缀 + 32 字节 seed
der = bytes.fromhex("302e020100300506032b657004220420") + seed
sys.stdout.write(der.hex())
PY
xxd -r -p "$TMP/k.der.hex" > "$TMP/k.der"
openssl pkey -inform DER -in "$TMP/k.der" -out "$TMP/k.pem" 2>/dev/null
CHECK="$(openssl pkey -in "$TMP/k.pem" -pubout -outform DER | hex_of_last32)"

if [ "$CHECK" = "$PUB_HEX" ]; then
    echo "④ 自检通过 ✅ 这串 seed 推出来的公钥跟 ② 完全一致 —— JWT 会签对"
else
    echo "④ 自检**失败** ❌（推出来的公钥是 $CHECK）—— 把这两行发我看看"
    exit 1
fi

echo
echo "接下来："
echo "  1) 控制台里上传公钥、建好凭据，拿到 kid（凭据 ID）+ sub（项目 ID）"
echo "  2) ESP32 的 zk_base_esp32.ino 里填三个宏："
echo "       #define QWEATHER_JWT_KID  \"你拿到的凭据ID\""
echo "       #define QWEATHER_JWT_SUB  \"你拿到的项目ID\""
echo "       #define QWEATHER_JWT_HEX  \"上面 ③ 那串\""
echo "  3) 刷基站，串口应出现「天气源 = **和风天气（实况）**」"
echo "  （私钥文件 $PEM 别进 git、别乱发；要换钥匙就重跑这个脚本 + 控制台重传公钥）"
