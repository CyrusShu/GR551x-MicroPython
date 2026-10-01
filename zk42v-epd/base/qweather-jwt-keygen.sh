#!/bin/bash
# =============================================================================
#  和风天气 JWT 的密钥生成器（在 Mac 上跑，全程不需要联网）
#
#      bash qweather-jwt-keygen.sh            # 默认生成 ed25519-private.pem / ed25519-public.pem
#      bash qweather-jwt-keygen.sh my.pem      # 指定文件名
#
#  它做四件事：
#    ① 生成 Ed25519 私钥（PEM）
#    ② 写出**公钥 PEM 文件**（ed25519-public.pem）—— **这个文件上传到和风控制台**
#    ③ 打印**私钥 seed 十六进制**（64 个字符）—— 填进 ESP32 的 QWEATHER_JWT_HEX
#    ④ 自检：从刚才那串 seed 重新推出公钥，确认跟 ② 一致（能推出来就说明
#       这串 hex 就是"能把 JWT 签对"的那一串）
#
#  控制台那边：dev.qweather.com → 项目管理 → 新建项目 → 创建凭据
#              （类型选 **JSON Web Token**）→ 上传上面那个公钥文件 → 记下两个 ID：
#        · 凭据 ID → ESP32 的 QWEATHER_JWT_KID
#        # 项目 ID → ESP32 的 QWEATHER_JWT_SUB
# =============================================================================
set -euo pipefail

# 文件名跟和风文档里那套保持一致，省得看着两套名字发懵：
#   ed25519-private.pem  私钥（自己留着，签名用）
#   ed25519-public.pem   公钥（**上传到和风控制台**的就是这个文件）
PEM="${1:-ed25519-private.pem}"
PUB_PEM="${PEM%.pem}-public.pem"

# ⚠ 必须找一个**支持 Ed25519** 的 openssl：
#   macOS 自带的 /usr/bin/openssl 是 **LibreSSL**，不支持 ed25519
#   （报错就是 "Algorithm ed25519 not found"）。
#   而 `bash 脚本.sh` 这种跑法**不会**读你的 zsh 配置 —— PATH 里可能只有 /usr/bin，
#   于是即使你装了 Homebrew 的 openssl@3 也用不上。所以这里自己按顺序找。
find_openssl() {
    local c
    for c in "${OPENSSL:-}" \
             /opt/homebrew/bin/openssl \
             /usr/local/bin/openssl \
             /opt/homebrew/opt/openssl@3/bin/openssl \
             /usr/local/opt/openssl@3/bin/openssl \
             "$(command -v openssl 2>/dev/null || true)" \
             /usr/bin/openssl; do
        [ -n "$c" ] && [ -x "$c" ] || continue
        if "$c" genpkey -algorithm ed25519 -out /dev/null >/dev/null 2>&1; then
            printf '%s\n' "$c"
            return 0
        fi
    done
    return 1
}

if ! OPENSSL_BIN="$(find_openssl)"; then
    echo "找不到支持 Ed25519 的 openssl —— 没法生成 JWT 的密钥。"
    echo
    echo "  macOS 自带的 /usr/bin/openssl 是 LibreSSL，**不支持 ed25519**"
    echo "  （报错 'Algorithm ed25519 not found' 就是它）。装一个再跑："
    echo
    echo "      brew install openssl@3"
    echo
    echo "  或者手动指定："
    echo "      OPENSSL=\"/opt/homebrew/opt/openssl@3/bin/openssl\" bash $0"
    exit 1
fi
echo "用的 openssl：$OPENSSL_BIN  （$("$OPENSSL_BIN" version)）"

if [ -e "$PEM" ]; then
    echo "⚠ $PEM 已存在 —— 直接用它（想换新的先删掉或换个文件名）"
else
    "$OPENSSL_BIN" genpkey -algorithm ed25519 -out "$PEM"
    echo "① 私钥已生成：$PEM"
fi
chmod 600 "$PEM"

hex_of_last32() { tail -c 32 | xxd -p -c 64; }

PUB_HEX="$("$OPENSSL_BIN" pkey -in "$PEM" -pubout -outform DER | hex_of_last32)"
SEED_HEX="$("$OPENSSL_BIN" pkey -in "$PEM" -outform DER | hex_of_last32)"
PUB_B64="$("$OPENSSL_BIN" pkey -in "$PEM" -pubout -outform DER | tail -c 32 | base64)"

# ⚠ 和风控制台要的是**标准公钥 PEM**（-----BEGIN PUBLIC KEY-----）——就是文档里
#   `openssl pkey -pubout -in ed25519-private.pem > ed25519-public.pem` 那个文件。
#   第一版我给的是 32 字节裸公钥的 hex/base64，控制台直接报"无效的公钥"。
"$OPENSSL_BIN" pkey -in "$PEM" -pubout -out "$PUB_PEM"

echo
echo "② 公钥文件：$PUB_PEM   ← **把这个文件上传到控制台**（凭据类型选 JSON Web Token）"
echo "   文件内容如下，控制台要是只让粘贴文本，就粘这个："
echo
sed 's/^/       /' "$PUB_PEM"
echo
echo "   （同一把公钥的其它写法，备用）"
echo "     十六进制: $PUB_HEX"
echo "     base64  : $PUB_B64"
echo
echo "③ 私钥 seed（填进 ESP32 的 #define QWEATHER_JWT_HEX）："
echo "     $SEED_HEX"
echo

# ④ 自检：用 seed 造一个 PKCS#8，再推公钥，必须跟 ② 一样
# ⚠ 自检只用 shell + openssl + xxd（**不依赖 python3** —— 用 bash 跑脚本时 PATH 里
#   可能没有 python3，之前就是这么踩的）
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
printf '%s%s' "302e020100300506032b657004220420" "$SEED_HEX" | xxd -r -p > "$TMP/k.der"
"$OPENSSL_BIN" pkey -inform DER -in "$TMP/k.der" -out "$TMP/k.pem" 2>/dev/null
CHECK="$("$OPENSSL_BIN" pkey -in "$TMP/k.pem" -pubout -outform DER | hex_of_last32)"

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
