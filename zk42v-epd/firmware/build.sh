#!/bin/bash
# =====================================================================
#  一键：编译自研固件 -> 打包成能过原厂 bootloader 校验的整片镜像
#
#      bash build.sh
#
#  产物：
#      zk42v-epd-app/GCC/out/zk42v_epd.bin     编译出来的 APP 原始镜像
#      zk42v-custom-512k.bin                   整片 512KB（拿去 flash-app.sh）
#
#  只编译不打包：NO_PACK=1 bash build.sh
# =====================================================================
set -e
cd "$(dirname "$0")"

echo "================ 1/2 编译 ================"
make -C zk42v-epd-app/GCC

if [ "${NO_PACK:-0}" = "1" ]; then
    echo
    echo "（NO_PACK=1，跳过打包）"
    exit 0
fi

echo
echo "================ 2/2 打包 ================"
python3 tools/fwpack.py "$@"
