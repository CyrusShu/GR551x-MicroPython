#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测「广播数据里的 AD 结构」—— 不需要硬件，也不用真机。

为什么要有这个（2026-09-27 手机截图那次）：
    广播数据里那条「厂商数据」的长度字节写成了 0x09，实际只有 8 个字节，
    于是解析器把下一条结构的长度字节 0x0A 也吞了 —— 手机 nRF Connect 上
    看到的是 `<FFFF> 5A4B 3432 560A`（末尾那个 0A 就是赃物），而后面那条
    「完整名字」从此错位、被读成 type=0x5A，设备名直接显示成 **N/A**。
    这类错误**不会**让广播起不来（ADV_START 照样 status=0），所以固件状态块
    里一切正常、只有手机那边名字没了 —— 靠人眼看字节太容易漏。

所以拿 zk_ble.c 里的数组当真相来源，机械地按 AD 结构走一遍：

  1  每条结构「长度字节」跟实际字节数严丝合缝，正好铺满整个数组
  2  每条的 AD type 都在白名单里（Flags/128位UUID/完整名字/厂商数据）
  3  「完整名字」结构里必须是可打印 ASCII
  4  「厂商数据」至少要能放下 2 字节公司 ID
  5  128 位 UUID 结构的长度必须是 1 + 16 的整数倍
  6  广播数组和 scan response 数组里**都不许出现 Flags(0x01)**
     （build 18 那个 0x4A = BLE_GAP_ERR_ADV_DATA_INVALID 就是它惹的）
  7  每个数组总长 <= 31 字节（传统广播的硬上限）
  8  至少有一个数组带完整名字 "ZK42V-EPD"（否则手机那边就是 N/A）
  9  带服务 UUID 的结构里，UUID 必须是 62750001-d828-918d-fb46-b6c11c675aec
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
ZK_BLE_C = os.path.join(FW, 'zk42v-epd-app', 'Src', 'ble', 'zk_ble.c')

AD_TYPE_FLAGS = 0x01
AD_TYPE_UUID128 = 0x07
AD_TYPE_NAME = 0x09
AD_TYPE_MANU = 0xFF
AD_TYPE_WHITELIST = (AD_TYPE_FLAGS, AD_TYPE_UUID128, AD_TYPE_NAME, AD_TYPE_MANU)

# 62750001-d828-918d-fb46-b6c11c675aec 的小端 16 字节
EPD_UUID = bytes([0xEC, 0x5A, 0x67, 0x1C, 0xC1, 0xB6, 0x46, 0xFB,
                  0x8D, 0x91, 0x28, 0xD8, 0x01, 0x00, 0x75, 0x62])
NAME_WANT = 'ZK42V-EPD'

# 实验用「对照组」：这两条**故意**带 Flags，用来复现 build 18 那个 0x4A。
# 除了它们，别的广播数据里出现 Flags 就是错的（协议栈会自己加）。
FLAGS_CONTROL_ARRAYS = ('s_adv_v1', 's_adv_v4')

CHECKS = []


def check(label, ok, detail=''):
    CHECKS.append((label, bool(ok), detail))


def parse_arrays(src):
    """把 zk_ble.c 里所有 `static const uint8_t xxx[] = { ... };` 抽成 {名字: bytes}"""
    out = {}
    pat = re.compile(
        r'static const uint8_t\s+(\w+)\s*\[\s*\]\s*=\s*\{(.*?)\}\s*;', re.S)
    for m in pat.finditer(src):
        name, body = m.group(1), m.group(2)
        body = re.sub(r'/\*.*?\*/', '', body, flags=re.S)     # 去掉块注释
        body = re.sub(r'//[^\n]*', '', body)                  # 去掉行注释
        toks = re.findall(r"0x[0-9A-Fa-f]{1,2}|'(?:\\.|[^'])'|\b\d{1,3}\b", body)
        data = bytearray()
        for t in toks:
            if t.startswith('0x'):
                data.append(int(t, 16))
            elif t.startswith("'"):
                ch = t[1:-1]
                if ch.startswith('\\'):
                    ch = {'\\0': '\0', '\\n': '\n', '\\r': '\r',
                          '\\\\': '\\', "\\'": "'"}.get(ch, ch[1:])
                data.append(ord(ch) & 0xFF)
            else:
                data.append(int(t) & 0xFF)
        out[name] = bytes(data)
    return out


def walk_ad(data):
    """按 AD 结构走一遍 -> [(type, payload, 长度字节)]，走不通就抛异常"""
    out = []
    i = 0
    while i < len(data):
        ln = data[i]
        if ln == 0:
            raise ValueError('偏移 %d: 长度字节是 0（非法，解析器会当场停）' % i)
        if i + 1 + ln > len(data):
            raise ValueError('偏移 %d: 长度字节 0x%02X 声称后面有 %d 字节，'
                             '但数组只剩 %d 字节' % (i, ln, ln, len(data) - i - 1))
        typ = data[i + 1]
        payload = data[i + 2:i + 1 + ln]
        out.append((typ, payload, ln))
        i += 1 + ln
    return out


def parse_variants(src):
    """解析 s_variants[] 的顺序 -> [(广播数组名, scan response 数组名), ...]"""
    m = re.search(r's_variants\[\]\s*=\s*\{(.*?)\n\};', src, re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        mm = re.match(r'\s*\{\s*(\w+)\s*,.*?,\s*(\w+)\s*,', line)
        if mm:
            out.append((mm.group(1), mm.group(2)))
    return out


def main():
    src = open(ZK_BLE_C, encoding='utf-8').read()
    arrays = parse_arrays(src)

    # 只看「广播包/扫描响应」那几个数组，跳过 UUID 常量那种辅助数组
    adv_names = sorted(n for n in arrays if n.startswith('s_adv_'))
    rsp_names = sorted(n for n in arrays if n.startswith('s_rsp_'))
    all_names = adv_names + rsp_names
    check('找得到广播/扫描响应数组（adv=%d, rsp=%d）'
          % (len(adv_names), len(rsp_names)),
          len(adv_names) >= 1 and len(rsp_names) >= 1)

    bad_frame, bad_type, bad_len, flags_seen = [], [], [], []
    uuid_seen = set()
    name_seen = set()

    for name in all_names:
        data = arrays[name]
        if len(data) > 31:
            bad_len.append('%s 有 %d 字节（>31）' % (name, len(data)))
        try:
            structs = walk_ad(data)
        except ValueError as e:
            bad_frame.append('%s: %s' % (name, e))
            continue

        # 结构必须正好铺满：walk_ad 保证不越界，这里保证不会少走
        covered = sum(1 + ln for _, _, ln in structs)
        if covered != len(data):
            bad_frame.append('%s: 结构只覆盖 %d/%d 字节'
                             % (name, covered, len(data)))

        for typ, payload, ln in structs:
            if typ not in AD_TYPE_WHITELIST:
                bad_type.append('%s: type=0x%02X 不是我们认识的 AD 类型'
                                '（十有八九是长度字节写错、结构错位了）' % (name, typ))
                continue
            if typ == AD_TYPE_FLAGS:
                flags_seen.append(name)
            elif typ == AD_TYPE_UUID128:
                if (ln - 1) % 16 != 0:
                    bad_len.append('%s: 128 位 UUID 结构的长度字节 0x%02X 不对'
                                   '（应是 1+16n）' % (name, ln))
                for k in range((ln - 1) // 16):
                    uuid_seen.add(bytes(payload[16 * k:16 * (k + 1)]))
            elif typ == AD_TYPE_NAME:
                if not all(32 <= b < 127 for b in payload):
                    bad_len.append('%s: 名字结构里有非打印字节 —— 很可能又是错位'
                                   % name)
                try:
                    name_seen.add(payload.decode('ascii'))
                except Exception:
                    pass
            elif typ == AD_TYPE_MANU:
                if len(payload) < 2:
                    bad_len.append('%s: 厂商数据结构装不下 2 字节公司 ID' % name)

    check('1: 每条 AD 结构的长度字节都跟实际字节数对得上、且正好铺满',
          not bad_frame, '；'.join(bad_frame))
    check('2: 每条的 AD type 都在白名单里（Flags/UUID128/名字/厂商数据）',
          not bad_type, '；'.join(bad_type))
    check('3/4/5: 名字是可打印 ASCII、厂商数据能装下公司 ID、UUID 长度合规',
          not bad_len, '；'.join(bad_len))
    # ---- Flags 这件事要分两层说 -------------------------------------------
    #   · 产品实际要用的那一条（s_variants[0]）里**绝对不能**有 Flags：
    #     协议栈会自己按 disc_mode 加，我们再塞一条就被判 0x4A（build 18 的坑）。
    #   · 但实验的对照组（s_adv_v1 / s_adv_v4）必须**保留** Flags ——
    #     那是用来复现 0x4A 的，删了就没法做 A/B 对照了。
    variants = parse_variants(src)
    check('6a: 能解析出 s_variants[] 的顺序（%d 条）' % len(variants),
          len(variants) >= 1)
    first = variants[0] if variants else ('?', '?')
    flags_in_first = [n for n in first if n in flags_seen]
    check('6b: 产品实际用的那条（第 0 条：%s + %s）里没有 Flags(0x01)'
          % first, not flags_in_first,
          '出现在：' + '、'.join(flags_in_first))
    stray = [n for n in flags_seen if n not in FLAGS_CONTROL_ARRAYS]
    check('6c: 除了对照组 %s，别处都不许出现 Flags'
          % '、'.join(FLAGS_CONTROL_ARRAYS), not stray,
          '出现在：' + '、'.join(stray))
    missing_ctrl = [n for n in FLAGS_CONTROL_ARRAYS
                    if n in arrays and n not in flags_seen]
    check('6d: 对照组 %s 仍然带着 Flags（它们是复现 0x4A 用的，别顺手删）'
          % '、'.join(n for n in FLAGS_CONTROL_ARRAYS if n in arrays),
          not missing_ctrl,
          '这些对照组里已经没 Flags 了：' + '、'.join(missing_ctrl))
    check('7: 每个数组都 <= 31 字节', not [x for x in bad_len if '>31' in x],
          '；'.join(x for x in bad_len if '>31' in x))
    check('8: 至少有一个数组带完整名字 "%s"（否则手机那边就是 N/A）' % NAME_WANT,
          NAME_WANT in name_seen,
          '实际见到的名字：%s' % ('、'.join(sorted(name_seen)) or '（一个都没有）'))
    check('9: 服务 UUID 就是 62750001-d828-918d-fb46-b6c11c675aec',
          EPD_UUID in uuid_seen,
          '实际见到的 UUID：%s'
          % ('、'.join(u[::-1].hex() for u in sorted(uuid_seen)) or '（一个都没有）'))

    # 第 0 条（产品实际用的那条）的**广播包自己**必须带完整名字：
    # 名字要是只放 scan response，iOS / 某些扫描器不一定会去取，列表里就是 N/A
    # —— 2026-09-27 手机截图那次就是这么显示的（虽然那次的主因是长度字节写错）。
    first_adv_names = set()
    if variants and variants[0][0] in arrays:
        for typ, payload, ln in walk_ad(arrays[variants[0][0]]):
            if typ == AD_TYPE_NAME:
                first_adv_names.add(payload.decode('ascii', 'replace'))
    check('10: 第 0 条的**广播包**里自带完整名字（不靠 scan response）',
          NAME_WANT in first_adv_names,
          '第 0 条广播包里见到的名字：%s'
          % ('、'.join(sorted(first_adv_names)) or '（一个都没有）'))

    bad = 0
    for label, ok, detail in CHECKS:
        print('  [%s] %s' % ('PASS' if ok else '**FAIL**', label))
        if not ok and detail:
            print('       · %s' % detail)
        bad += 0 if ok else 1
    print('全部通过 ✅' if bad == 0 else '有 %d 项失败 ❌' % bad)
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
