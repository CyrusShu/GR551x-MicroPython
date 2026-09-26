#!/usr/bin/env python3
"""Reconstruct the e-paper command/data sequences out of the ZK42V app image."""
import re
import sys

import dislib

CMD_FN = 0x10115D8
DAT_FN = 0x1011648
OTHER_FNS = {
    0x10116C0: 'DATX2',
    0x10116E8: 'W2',
    0x101176C: 'W3',
}

asm = dislib.Asm(sys.argv[1] if len(sys.argv) > 1 else '../app.asm',
                 sys.argv[2] if len(sys.argv) > 2 else '../app.bin')

callers = asm.call_targets()
func_starts = sorted(callers.keys())


def func_of(va):
    lo = None
    for f in func_starts:
        if f <= va:
            lo = f
        else:
            break
    return lo


def reg_val(ops, j):
    """Return ('imm'|'lit'|'reg'|None, value)."""
    m = re.match(r'(r\d+|ip|sl),\s*#(-?\d+|0x[0-9a-fA-F]+)', ops)
    if m:
        v = m.group(2)
        return ('imm', int(v, 16) if v.startswith('0x') else int(v, 10), m.group(1))
    m = re.match(r'(r\d+|ip|sl),\s*(r\d+|ip|sl)\s*$', ops)
    if m:
        return ('reg', m.group(2), m.group(1))
    return None


def walk_function(start, stop):
    """Yield ('CMD'|'DATA'|'DATX2'|..., arg, va) in program order."""
    regs = {}
    out = []
    for i in range(asm.by_va[start], min(asm.by_va[stop] if stop else len(asm.insns), len(asm.insns))):
        va, mnem, ops, ln = asm.insns[i]
        ln = ln.strip()
        # literal pool loads
        lit = asm.literals(va)
        if lit:
            for r, (v, addr) in lit.items():
                regs[r] = ('lit', v, addr)
        elif mnem.startswith('movw'):
            m = re.match(r'(r\d+|ip|sl),\s*#(\d+|0x[0-9a-fA-F]+)', ops)
            if m:
                v = m.group(2)
                regs[m.group(1)] = ('imm', int(v, 16) if v.startswith('0x') else int(v, 10), 'movw')
        elif mnem.startswith('movt'):
            m = re.match(r'(r\d+|ip|sl),\s*#(\d+|0x[0-9a-fA-F]+)', ops)
            if m:
                r = m.group(1)
                v = m.group(2)
                v = int(v, 16) if v.startswith('0x') else int(v, 10)
                prev = regs.get(r)
                if prev and prev[0] == 'imm':
                    regs[r] = ('imm', (v << 16) | (prev[1] & 0xFFFF), 'movw/movt')
                else:
                    regs[r] = ('imm', v << 16, 'movt')
        elif mnem in ('movs', 'mov', 'mov.w'):
            rv = reg_val(ops, i)
            if rv:
                if rv[0] == 'reg':
                    regs[rv[2]] = regs.get(rv[1], ('unk', None, rv[1]))
                else:
                    regs[rv[2]] = ('imm', rv[1], 'movs')
        elif mnem in ('bl', 'b.w', 'blx') and ops.startswith('0x'):
            tgt = int(ops.split()[0], 16)
            arg = regs.get('r0')
            if tgt == CMD_FN:
                out.append(('CMD', arg, va, ln))
            elif tgt == DAT_FN:
                out.append(('DATA', arg, va, ln))
            elif tgt in OTHER_FNS:
                out.append((OTHER_FNS[tgt], arg, va, ln))
            # a real call clobbers r0..r3
            for r in ('r0', 'r1', 'r2', 'r3'):
                regs.pop(r, None)
        elif mnem in ('push', 'pop', 'stmia', 'ldmia', 'str', 'strb', 'strh', 'ldr', 'ldrb', 'ldrh'):
            pass
        elif re.match(r'(adds?|subs?|ands?|orrs?|eors?|lsls?|lsrs?|asrs?|muls?)\s+(r\d+|ip|sl),', mnem + ' ' + ops):
            r = ops.split(',')[0].strip()
            if r in regs:
                regs[r] = ('unk', None, ops)
        elif mnem.startswith(('cmp', 'b', 'it', 'cbz', 'cbnz', 'uxtb', 'uxth', 'sxtb', 'sxth', 'bfi', 'ubfx', 'and', 'orr', 'eor', 'lsl', 'lsr', 'adr', 'add.w', 'sub.w', 'rsb', 'mvn', 'nop', 'svc', 'dsb', 'isb', 'wfi')):
            pass
        else:
            r = ops.split(',')[0].strip()
            if r in regs:
                regs[r] = ('unk', None, ops)
    return out


def fmt_arg(a):
    if a is None:
        return '?'
    k, v, extra = a
    if k == 'imm':
        return '0x%02X (%d)' % (v, v) if v <= 0xFF else '0x%X (%d)' % (v, v)
    if k == 'lit':
        return 'lit@0x%X=0x%s' % (extra, ('%X' % v) if v is not None else '??')
    if k == 'reg':
        return 'r%s' % v
    return '?'


blocks = {}
for i, (va, mnem, ops, ln) in enumerate(asm.insns):
    if mnem in ('bl', 'b.w') and ops.startswith('0x'):
        tgt = int(ops.split()[0], 16)
        if tgt in (CMD_FN, DAT_FN) or tgt in OTHER_FNS:
            f = func_of(va)
            blocks.setdefault(f, []).append(va)

print('functions that push EPD traffic: %d' % len(blocks))
starts = sorted(blocks.keys())
for n, f in enumerate(starts):
    stop = starts[n + 1] if n + 1 < len(starts) else None
    seq = walk_function(f, stop)
    if not seq:
        continue
    print('')
    print('=' * 72)
    print('func 0x%08X   %d records' % (f, len(seq)))
    print('=' * 72)
    for kind, arg, va, ln in seq:
        print('  %08X  %-6s %s' % (va, kind, fmt_arg(arg)))
