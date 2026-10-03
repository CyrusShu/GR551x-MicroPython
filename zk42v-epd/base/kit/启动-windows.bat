@echo off
chcp 65001 >nul
rem =====================================================================
rem  双击我就能启动（Windows 10 / 11）
rem
rem  第一次会自己装一个隔离的 Python 环境（只装 bleak 这一个库）。
rem  关掉这个黑窗口 = 停止工作。
rem
rem  ⚠ 前提：这台电脑有蓝牙（笔记本基本都有；台式机要插个蓝牙适配器），
rem    而且 Windows 设置里蓝牙是打开的。要求 Windows 10 1709 以上。
rem =====================================================================
cd /d "%~dp0"
cd ..

echo ===================================================
echo  ZK42V 价签 —— 电脑基站
echo ===================================================

where python >nul 2>nul
if errorlevel 1 (
    echo ❌ 没找到 Python。
    echo    去 https://www.python.org/downloads/ 下载安装，
    echo    安装时**务必勾上最下面那个 "Add python.exe to PATH"**，然后重新双击我。
    echo.
    pause
    exit /b 1
)

if not exist .venv\Scripts\python.exe (
    echo 第一次运行：正在准备环境（大约 30 秒，只做这一次）…
    python -m venv .venv || ( echo ❌ 建虚拟环境失败 & pause & exit /b 1 )
)

.venv\Scripts\python.exe -c "import bleak" >nul 2>nul
if errorlevel 1 (
    echo 正在安装蓝牙库 bleak …
    .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    .venv\Scripts\python.exe -m pip install --quiet bleak || (
        echo ❌ 装 bleak 失败（是不是没联网？） & pause & exit /b 1 )
)

echo 正在启动服务…（浏览器会自动打开 http://localhost:8777/）
echo 这个窗口别关，关了就停止工作。
echo.
.venv\Scripts\python.exe serve.py --config kit\配置.json --open
pause
