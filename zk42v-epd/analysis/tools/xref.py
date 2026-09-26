#!/usr/bin/env python3
"""Find code that loads a given absolute address out of the literal pool."""
import re
import struct
import sys

import dislib

asm = dislib.Asm(sys.argv[1] if len(sys.argv) > 2 else '../app.asm',
                 bin_path=sys.argv[2] if len(sys.argv) > 2 else '../app.bin')


def pool_refs(addr):
    """Return list of (code_va, reg, insn) whose [pc,#N] literal == addr."""
    out = []
    for va, mnem, ops, ln in asm.insns:
        if 'pc,' not in ops or '@ (' not in ops:
            continue
        m = re.search(r'@\s*\((0x[0-9a-fA-F]+)\)', ops)
        if not m:
            continue
        poola = int(m.group(1), 16)
        if asm.rd32(poola) == addr:
            out.append((va, ops.split(',')[0].strip(), ln.strip()))
    return out


def func_start(va):
    starts = sorted(asm.call_targets().keys())
    lo = None
    for f in starts:
        if f <= va:
            lo = f
        else:
            break
    return lo


if __name__ == '__main__':
    for a in sys.argv[1:]:
        addr = int(a, 16)
        print('== refs to 0x%08X ==' % addr)
        for va, reg, insn in pool_refs(addr):
            print('  %08X  func~%08X  %s' % (va, func_start(va) or 0, insn))
