#!/bin/sh
# 用 venv 里的 python 跑基站脚本。例子：
#   ./run.sh probe
#   ./run.sh sync  --tz 8
#   ./run.sh watch --tz 8 --interval 1800
DIR=$(cd "$(dirname "$0")" && pwd)
PY="$DIR/.venv/bin/python3"

if [ ! -x "$PY" ]; then
    echo "还没建 venv。先跑： $DIR/setup.sh" >&2
    exit 1
fi

exec "$PY" "$DIR/zk_ble_base.py" "$@"
