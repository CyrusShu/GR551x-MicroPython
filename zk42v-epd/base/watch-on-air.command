#!/bin/sh
# 让 Mac 开机/登录后自动当基站：把「系统设置 → 通用 → 登录项」的加号指向这个文件。
#
# 为什么用 .command 而不是 LaunchAgent：终端窗口里的进程继承 Terminal 的蓝牙授权，
# 而 LaunchAgent 拉起来的进程得自己过一遍 TCC（常见结果是 state=3 unauthorized）。
#
# 关掉窗口 = 基站停。想改频率/位置就改下面这行参数。
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR"

echo "== ZK42V 基站模式（关掉这个窗口就停）=="
exec "$DIR/run.sh" watch --tz 8 \
    --lat 22.7809 --lon 113.8861 \
    --time-interval 86400 --weather-interval 21600 \
    --log /tmp/zk-base.log
