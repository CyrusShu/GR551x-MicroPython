#!/bin/bash
# 一分钟自检：算 SHA-256，还拿真固件当标准答案把这份备份验一遍
#   bash verify-backup.sh
cd "$(dirname "$0")" || exit 1

echo "== 备份文件的 SHA-256 =="
shasum -a 256 zk42v-factory-backup-run1.bin zk42v-live-512k.bin
echo
echo "（两个应该一模一样：1dfab92f558352642f5c477b22d1e66d12efc1be959d5e676ae27fe05cedb704）"
echo

python3 test-sanity.py
