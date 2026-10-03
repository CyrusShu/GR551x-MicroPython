#!/bin/bash
# =====================================================================
#  双击我就能启动（macOS）
#
#  第一次会自己装一个隔离的 Python 环境（只装 bleak 这一个库），
#  之后每次双击都是直接跑。关掉这个窗口 = 停止。
#
#  ⚠ 第一次运行 macOS 会弹「"终端"想使用蓝牙」—— 必须点「好」，
#    否则它连价签都找不到（系统设置 → 隐私与安全性 → 蓝牙 里能改）。
# =====================================================================
cd "$(dirname "$0")" || exit 1
cd ..            # 回到 ble-base 目录（基站程序在那儿）

echo "==================================================="
echo " ZK42V 价签 —— 电脑基站"
echo "==================================================="

# 找 Python。⚠ 顺序有讲究：/usr/bin/python3 是 macOS 的**桩程序**，
# 机器上没装过开发者工具时它会弹"要安装命令行开发者工具"—— 那不是我们想要的。
# 所以优先用 python.org 装的（在 /Library/Frameworks 下），再考虑 PATH / Homebrew。
PY=""
for c in /Library/Frameworks/Python.framework/Versions/*/bin/python3 \
         "$(command -v python3 2>/dev/null)" /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    case "$c" in ""|/usr/bin/python3) continue ;; esac
    if [ -x "$c" ]; then PY="$c"; break; fi
done
if [ -z "$PY" ] && [ -x /usr/bin/python3 ]; then
    if ! xcode-select -p >/dev/null 2>&1; then
        echo "⚠ 只找到 macOS 自带的 python3 —— 它可能弹「安装命令行开发者工具」。"
        echo "  更省事的做法：去 https://www.python.org/downloads/ 装一个 Python 3，"
        echo "  再重新双击我。现在先按自带的试。"
    fi
    PY=/usr/bin/python3
fi
if [ -z "$PY" ]; then
    echo "❌ 没找到 python3。"
    echo "   去 https://www.python.org/downloads/ 下载安装 Python 3，然后重新双击我。"
    echo ""
    read -r -p "按回车关闭…" _
    exit 1
fi

if [ ! -x .venv/bin/python3 ]; then
    echo "第一次运行：正在准备环境（大约 30 秒，只做这一次）…"
    "$PY" -m venv .venv || { echo "❌ 建虚拟环境失败"; read -r -p "按回车关闭…" _; exit 1; }
fi
if ! .venv/bin/python3 -c "import bleak" >/dev/null 2>&1; then
    echo "正在安装蓝牙库 bleak …"
    .venv/bin/python3 -m pip install --quiet --upgrade pip
    .venv/bin/python3 -m pip install --quiet bleak || {
        echo "❌ 装 bleak 失败（是不是没联网？）"; read -r -p "按回车关闭…" _; exit 1; }
fi

echo "正在启动服务…（浏览器会自动打开）"
echo "  · 网页：推图 / 切页面 / 发命令"
echo "  · 面板：基站状态 / 改配置   ← 顶部那个链接"
echo "这个窗口别关，关了就停止工作。"
echo ""
exec .venv/bin/python3 serve.py --config kit/配置.json --open
