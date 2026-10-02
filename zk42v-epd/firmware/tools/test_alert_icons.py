#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预警图标表的离线自测（不需要硬件）：

    python3 tools/test_alert_icons.py

盯这几件事（都是实机踩过的坑的同类）：
  1. 每张图 20x20、有墨（全黑/全白都是坏图），而且没糊成一块（墨量上限）；
  2. 查表：每个收录的和风编号都指到自己那张（不是都指到兜底）；
  3. 认不出的编号走"通用预警"那张（下标 0），不能是空指针；
  4. 编号在 C 里是 16 位（>255 的要能存下：1003 这种）。

数据直接从生成的 alert_icons.c 里读 —— 测的就是固件里那份，不是另抄一份。
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
C_FILE = os.path.join(HERE, '..', 'zk42v-epd-app', 'Src', 'img', 'alert_icons.c')

W = H = 20
STRIDE = (W + 7) // 8            # 3 字节/行


def load():
    src = open(C_FILE, encoding='utf-8').read()
    body = src.split('[ZK_ALERT_ICON_NUM][60] = {', 1)[1].split('\n};', 1)[0]
    blocks = re.findall(r'\{(.*?)\},', body, re.S)
    names = re.findall(r'\{   /\* (.+?) \*/', body)
    icons = []
    for b in blocks:
        icons.append([int(x, 16) for x in re.findall(r'0x([0-9A-Fa-f]{2})', b)])
    m = re.search(r's_alert_map\[\] = \{(.*?)\n\};', src, re.S)
    pairs = re.findall(r'\{\s*(\d+),\s*(\d+)\s*\}', m.group(1))
    return icons, names, [(int(a), int(b)) for a, b in pairs]


def main():
    icons, names, table = load()
    fails = []

    def ck(cond, msg):
        print(('  OK   ' if cond else '  FAIL ') + msg)
        if not cond:
            fails.append(msg)

    print('预警图标表：%d 张，映射 %d 条' % (len(icons), len(table)))
    ck(len(icons) == 6, '张数 = 6（1 张通用 + 5 张和风的）')
    for i, v in enumerate(icons):
        ink = sum(bin(b).count('1') for b in v)
        nm = names[i] if i < len(names) else '?'
        ck(len(v) == 60, '%s：每张 60 字节（20 行 x 3 字节）' % nm)
        ck(8 <= ink <= 250, '%s：墨量 %d（有内容、没糊成一块）' % (nm, ink))

    seen = {}
    for code, idx in table:
        ck(idx != 0, '编号 %d 指到自己那张（不是兜底）' % code)
        seen.setdefault(idx, []).append(code)
        print('       %-6d -> 下标 %d（%s）' % (code, idx, names[idx]))
    ck(not {k: v for k, v in seen.items() if len(v) > 1}, '没有两个编号共用同一张图')
    ck(len(table) == 5, '收录 5 个编号（1003/1014/1001/1015/1009）')
    ck(all(c <= 0xFFFF for c, _ in table), '编号都在 16 位范围内')
    ck(any(c > 255 for c, _ in table), '有 >255 的编号（1003 这种）→ 真的按 16 位存')

    print('')
    print('全部通过 ✅' if not fails else '有 %d 项失败 ❌' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
