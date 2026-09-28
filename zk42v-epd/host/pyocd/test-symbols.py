#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线验：status.sh 读「RAM 变量」时用的是 .map 里的真地址，而不是写死的旧地址。

为什么单独盯这一条 —— build 28 那一轮真栽过（而且是"假告警"，最容易把人带偏）：

    status 里打印过
        SystemCoreClock = 1
        → 真实主频是 SystemCoreClock 的 15991430 倍

    看着像固件时基全错，其实固件一点问题没有：脚本里
    `ZK_SYSCLK_VAR = 0x300042C8` 是照 build 22 的 .map 写死的，而 build 28 里
    SystemCoreClock 已经挪到 0x300041C0 —— 0x300042C8 那个位置现在是
    `s_app_timer_info`，读出来的 1 是它的头 4 字节。

    同理 `ZK_CYC_PER_US` 也过期了（读到 0）。

现在这两个符号都改成 **按名字从 .map 查**（`_zk_sym_addr`），
这个测试就盯着「解析结果 == .map 里文本写的地址」，以及「读的时候不再直接用常量」。

    python3 test-symbols.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'led-window-user.py')
MAP = ("/Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware/"
       "zk42v-epd-app/GCC/out/lst/zk42v_epd.map")

FAIL = []


def check(label, ok, detail=''):
    print(('  PASS  ' if ok else '  FAIL  ') + label)
    if not ok:
        FAIL.append(label)
        if detail:
            print('        · %s' % detail)


def load_script():
    """把 led-window-user.py 当普通模块载入（stub 掉 pyOCD 注入的 command 装饰器）"""
    def command(name, help=''):
        def deco(fn):
            return fn
        return deco

    ns = {'__name__': 'ledwin', 'command': command, 'print': print}
    src = open(SCRIPT, encoding='utf-8').read()
    exec(compile(src, SCRIPT, 'exec'), ns)
    return ns, src


def map_addr(path, sym):
    """从 .map 文本里直接抠出某个符号的地址（跟脚本无关的"第二意见"）

    .map 里静态变量是两行排版（段名一行、地址一行）：
         .bss.s_cyc_per_us
                        0x3000b1e8        0x4 out/obj/epd_zk42v.o
    全局符号则是「地址 + 名字」单独一行。
    """
    pat_sec_one = re.compile(r'^\s*\.(?:text|data|bss)\.%s\s+0x([0-9a-fA-F]+)' % sym)
    pat_sec = re.compile(r'^\s*\.(?:text|data|bss)\.%s\s*$' % sym)
    pat_addr = re.compile(r'^\s*0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+\S+\.o\s*$')
    pat_name = re.compile(r'^\s*0x([0-9a-fA-F]+)\s+%s\s*$' % sym)
    pending = False
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = pat_sec_one.match(line) or pat_name.match(line)
            if m:
                return int(m.group(1), 16)
            if pat_sec.match(line):
                pending = True
                continue
            if pending:
                m = pat_addr.match(line)
                if m:
                    return int(m.group(1), 16)
                pending = False
    return None


def main():
    ns, src = load_script()

    print('== 1. .map 在不在 ==')
    if not os.path.exists(MAP):
        print('  没有 .map（%s）—— 先 `bash build.sh` 编一次再来跑这个测试' % MAP)
        return 1
    check('找得到本机编译出来的 .map', True)

    print()
    print('== 2. 按名字查符号（脚本的解析 == .map 文本）==')
    sym_addr = ns['_zk_sym_addr']
    for sym in ('SystemCoreClock', 's_cyc_per_us'):
        want = map_addr(MAP, sym)
        got = sym_addr(sym)
        check('%s：脚本查到 0x%08X，.map 里写的是 0x%08X'
              % (sym, got or 0, want or 0), got == want)

    print()
    print('== 3. 兜底常量没跟着过期 ==')
    for sym, const in (('SystemCoreClock', ns['ZK_SYSCLK_VAR']),
                       ('s_cyc_per_us', ns['ZK_CYC_PER_US'])):
        want = map_addr(MAP, sym)
        check('%s 的兜底常量 0x%08X == .map 的 0x%08X'
              % (sym, const, want or 0), const == want,
              '兜底常量过期了（虽然正常情况下走不到它，但别留在那儿骗人）')

    print()
    print('== 4. 读的时候不再直接用写死的常量 ==')
    body = src
    for sym, const in (('SystemCoreClock', 'ZK_SYSCLK_VAR'),
                       ('s_cyc_per_us', 'ZK_CYC_PER_US')):
        bad = re.search(r'rd\(\s*%s\s*\)' % const, body)
        check('没有 rd(%s) 这种写法' % const, bad is None,
              '又回到写死地址的老路上了')
        ok = re.search(r"_zk_sym_addr\(\s*'%s'" % sym, body)
        check("是按名字查的 _zk_sym_addr('%s')" % sym, ok is not None)

    print()
    print('== 5. 顺带：状态块那个符号也在（不然 PC 反查/状态块解读会静默失效）==')
    want = map_addr(MAP, 'g_dbg')
    check('g_dbg 在 .map 里、而且落在 0x3001F000（RAM_DBG 那块）',
          want == 0x3001F000, '查到的地址是 %s' % (hex(want) if want else None))

    print()
    if FAIL:
        print('有 %d 项失败 ❌' % len(FAIL))
        return 1
    print('全部通过 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
