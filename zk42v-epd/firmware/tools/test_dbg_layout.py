#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测「调试状态块的布局」—— 不需要硬件。

为什么要有这个：状态块的 word 号是**固件和上位机之间的硬约定**。
固件那边加一个字段、上位机那边数错一格，读出来的就是一堆看似合理、
其实错位的数据 —— 这一轮（build 18 -> 19）就吃过一次亏：
ble_evt_count 没清零，读出来是个天文数字，把「事件到底回没回」这个
最关键判断带偏了整整一轮。

所以拿 zk_dbg.h 当唯一真相来源，机械地对一遍：

  1  zk_dbg.h 的 ZK_DBG_WORDS == led-window-user.py 的 ZK_DBG_WORDS
  2  struct 里每个字段的实际下标，跟它注释里写的「N:」一致
  3  ble_evt_count 正好是第 23 号字（B2-A.2 那一组从第 24 号开始）
  4  固件 s_variants[] 的条数 == 上位机 ZK_ADV_VARIANT_DESC 的条数
  5  固件 s_variants[] 的条数 <= 状态块里 ble_adv_stN 的格子数（6）
  6  ble_adv_stN 六个格子必须是连续的（上位机是当下标数组用的）
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)                       # outputs/firmware
ZK_DBG_H = os.path.join(FW, 'zk42v-epd-app', 'Src', 'board', 'zk_dbg.h')
ZK_BLE_C = os.path.join(FW, 'zk42v-epd-app', 'Src', 'ble', 'zk_ble.c')
LED_PY = os.path.join(os.path.dirname(FW), 'pyocd', 'led-window-user.py')

CHECKS = []


def check(label, ok):
    CHECKS.append((label, bool(ok)))


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def match_num(text, pattern, label, flags=0):
    m = re.search(pattern, text, flags)
    if not m:
        check(label, False)
        return None
    return int(m.group(1), 0)


def struct_fields(src):
    """把 zk_dbg_t 的字段按顺序抽出来 -> [(名字, 占几个字, 注释里写的下标 or None)]

    只认 uint32_t 开头的字段行；数组按元素个数折算成字数。
    rsv[...] 用表达式算字数（ZK_DBG_WORDS - N）。
    """
    # 注意：文件里不止一个 struct（前面还有个 zk_mailbox_t）。
    # 用 [^{}]* 卡住「字段体里没有花括号」，这样非贪婪匹配只能落在
    # 紧挨着 zk_dbg_t 的那个 typedef 上，不会把邮箱那个也吞进来。
    body = re.search(r'typedef struct\s*\{([^{}]*)\}\s*zk_dbg_t\s*;', src, re.S)
    if not body:
        return None
    out = []
    for line in body.group(1).splitlines():
        m = re.match(r'\s*uint32_t\s+(\w+)\s*(\[[^\]]*\])?\s*;', line)
        if not m:
            continue
        name, arr = m.group(1), m.group(2)
        n = 1
        if arr:
            inner = arr.strip('[]')
            if inner.isdigit():
                n = int(inner)
            else:
                mm = re.search(r'ZK_DBG_WORDS\s*-\s*(\d+)', inner)
                if mm:
                    words = header_words
                    n = words - int(mm.group(1))
                else:
                    n = None
        cm = re.search(r'/\*\s*(\d+)\s*:', line)
        out.append((name, n, int(cm.group(1)) if cm else None))
    return out


def variant_count(src):
    body = re.search(r's_variants\[\]\s*=\s*\{(.*?)\};', src, re.S)
    if not body:
        return None
    return len(re.findall(r'^\s*\{', body.group(1), re.M))


def main():
    src = read(ZK_DBG_H)
    ble = read(ZK_BLE_C)
    py = read(LED_PY)

    global header_words
    header_words = match_num(src, r'#define\s+ZK_DBG_WORDS\s+(\d+)', '1: 头文件里有 ZK_DBG_WORDS')
    py_words = match_num(py, r'^ZK_DBG_WORDS\s*=\s*(\d+)',
                         '1: 上位机里有 ZK_DBG_WORDS', re.M)
    check('1: 固件 / 上位机的 ZK_DBG_WORDS 一致（%s vs %s）'
          % (header_words, py_words), header_words == py_words)

    fields = struct_fields(src)
    check('2: 能解析出 zk_dbg_t 的字段', bool(fields))
    if not fields:
        return report()

    idx = 0
    starts = {}
    bad = []
    for name, n, cm in fields:
        if n is None:
            bad.append('%s: 数组长度算不出来' % name)
            idx = None
            break
        if cm is not None and cm != idx:
            bad.append('%s: 注释写 %d，实际是第 %d 号' % (name, cm, idx))
        starts[name] = idx
        idx += n
    check('2: 每个字段注释里的下标跟实际顺序一致', not bad)
    if bad:
        for b in bad[:6]:
            print('      · %s' % b)

    check('3: ble_evt_count 正好是第 23 号字',
          starts.get('ble_evt_count') == 23)
    check('3: B2-A.2 那一组从第 24 号字开始（ble_scan_state）',
          starts.get('ble_scan_state') == 24)
    check('3: 状态块总字数够用（字段占 %d <= ZK_DBG_WORDS %d）'
          % (idx, header_words), idx <= header_words)

    vc = variant_count(ble)
    desc = len(re.findall(r"^\s{4}'", re.search(
        r'ZK_ADV_VARIANT_DESC\s*=\s*\[(.*?)\]', py, re.S).group(1), re.M))
    check('4: 广播变体条数一致（固件 %s vs 上位机 %s）' % (vc, desc), vc == desc)
    check('5: 变体条数不超过状态块里的格子数（6）', vc is not None and vc <= 6)

    # ble_adv_st0..st5 必须是连续的：上位机/固件都是当数组用的
    st = [starts['ble_adv_st%d' % i] for i in range(6) if 'ble_adv_st%d' % i in starts]
    check('6: ble_adv_st0..st5 六个格子都在、而且连续（%s）' % st,
          len(st) == 6 and st == list(range(st[0], st[0] + 6)))

    return report()


def report():
    bad = 0
    for label, ok in CHECKS:
        print('  [%s] %s' % ('PASS' if ok else '**FAIL**', label))
        bad += 0 if ok else 1
    print('全部通过 ✅' if bad == 0 else '有 %d 项失败 ❌' % bad)
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
