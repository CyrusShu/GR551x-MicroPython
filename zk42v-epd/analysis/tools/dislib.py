"""Small Thumb disassembly helper for the ZK42V app image."""
import re
import struct

BASE = 0x0100A000


class Asm(object):
    def __init__(self, asm_path, bin_path, base=BASE):
        self.base = base
        self.raw = open(bin_path, 'rb').read()
        self.insns = []
        self.by_va = {}
        for ln in open(asm_path, errors='replace').read().split('\n'):
            p = ln.split('\t')
            if len(p) < 3:
                continue
            head = p[0].strip()
            if not head.endswith(':'):
                continue
            try:
                va = int(head[:-1], 16)
            except ValueError:
                continue
            mnem = p[2].strip()
            ops = '\t'.join(p[3:]).strip() if len(p) > 3 else ''
            self.by_va[va] = len(self.insns)
            self.insns.append((va, mnem, ops, ln))

    def rd32(self, va):
        off = va - self.base
        if 0 <= off <= len(self.raw) - 4:
            return struct.unpack_from('<I', self.raw, off)[0]
        return None

    def rd(self, va, n):
        off = va - self.base
        if 0 <= off <= len(self.raw) - n:
            return self.raw[off:off + n]
        return None

    def text(self, va):
        i = self.by_va.get(va)
        if i is None:
            return None
        return self.insns[i][2]

    def ops(self, va):
        return self.text(va)

    def call_targets(self):
        t = {}
        for va, mnem, ops, ln in self.insns:
            if mnem in ('bl', 'b.w', 'blx') and ops.startswith('0x'):
                try:
                    tgt = int(ops.split()[0], 16)
                except ValueError:
                    continue
                t.setdefault(tgt, []).append(va)
        return t

    def literal_slot(self, va):
        """If [pc,#N] load, return (reg, slot_addr, value)."""
        ops = self.text(va)
        if not ops or 'pc,' not in ops:
            return None
        m = re.match(r'(r\d+|ip|sl|fp),\s*\[pc,\s*#(-?\d+)\]', ops.strip())
        if not m:
            return None
        a = re.search(r'@\s*\((0x[0-9a-fA-F]+)\)', ops)
        if not a:
            return None
        slot = int(a.group(1), 16)
        return (m.group(1), slot, self.rd32(slot))

    def literals(self, va):
        r = self.literal_slot(va)
        if not r:
            return {}
        reg, slot, val = r
        return {reg: (val, slot)}


def parse_imm(ops):
    m = re.match(r'(r\d+|ip|sl|fp),\s*#(-?\d+|0x[0-9a-fA-F]+)', ops)
    if not m:
        return None
    v = m.group(2)
    return int(v, 16) if v.startswith('0x') else int(v, 10)
