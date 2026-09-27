#!/usr/bin/env python3
"""Dump a VA range from the app disassembly with literal-pool annotations."""
import sys
import dislib

asm = dislib.Asm('../app.asm', '../app.bin')

lo = int(sys.argv[1], 16)
hi = int(sys.argv[2], 16) if len(sys.argv) > 2 else lo + 0x100
for va, mnem, ops, ln in asm.insns:
    if va < lo:
        continue
    if va >= hi:
        break
    r = asm.literal_slot(va)
    extra = ''
    if r and r[2] is not None:
        w = r[2]
        s = ''
        if 0x0100A000 <= w < 0x0102C000:
            b = asm.rd(w, 24)
            if b and all(0x20 <= c < 0x7f for c in b[:8]):
                s = ' STR=' + repr(b.split(b'\0')[0])
        extra = '   ; <- 0x%08X%s' % (w, s)
    print('%08X  %-8s %s%s' % (va, mnem, ops, extra))
