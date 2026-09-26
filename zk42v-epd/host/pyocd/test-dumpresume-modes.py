# 验证 dumpresume 的"读法自检"：模拟 ST-Link 加速路径坏掉 / 经典路径坏掉
import io
import os
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'led-window-user.py')
BASE = 0x01000000
SIZE = 0x80000

# 假芯片里装的就是真 dump —— 真伪校验是用真固件当标准答案判的
with open(os.path.join(HERE, 'zk42v-live-512k.bin'), 'rb') as _f:
    REAL = bytearray(_f.read())
assert len(REAL) == SIZE


class FakeAP:
    """经典 AP 路径（pyOCD 自己发 TAR/DRW 事务）"""
    def __init__(self, broken=False):
        self.broken = broken
        self.short_description = 'AHB-AP (fake)'

    def _word(self, addr):
        if self.broken:
            return 0x4034C8F7
        if addr == 0xE000ED00:
            return 0x410FC241
        off = addr - BASE
        if 0 <= off <= len(REAL) - 4:
            return struct.unpack_from('<I', REAL, off)[0]
        return 0xDEADBEEF

    def _read_memory(self, addr, transfer_size=32, now=True):
        return self._word(addr)

    def _read_memory_block32(self, addr, size):
        if self.broken:
            return [0x4034C8F7] * size
        return [self._word(addr + 4 * i) for i in range(size)]


class FakeTarget:
    """target 层的 read_memory_block8 == ST-Link 加速路径"""
    def __init__(self, ap, probe_broken=False):
        self.ap = ap
        self.probe_broken = probe_broken
        self.halted = False

    def read_memory_block8(self, addr, size):
        if self.probe_broken:
            return [0xF7, 0xC8, 0x34, 0x40] * (size // 4)
        out = []
        for i in range(size):
            a = addr + i
            off = a - BASE
            if 0 <= off < len(REAL):
                out.append(REAL[off])
            elif a == 0xE000ED00:
                out.append(0x41)
            else:
                out.append(0)
        return out

    def halt(self):
        self.halted = True

    def init(self):
        pass

    def get_state(self):
        return 'RUNNING'


class FakeProbe:
    target_voltage = 3.2

    def connect(self):
        return None


class FakeSession:
    def __init__(self, target):
        self.target = target
        self.probe = FakeProbe()


def run(name, probe_broken, classic_broken, expect_mode, expect_sanity):
    outdir = tempfile.mkdtemp(prefix='dm-')
    os.environ.update({
        'GR551X_OUTDIR': outdir,
        'BEEP': '0',
        'MANUAL': '1',
        'DUMP_WINDOW_MS': '300',
        'DUMP_BASE': hex(BASE),
        'DUMP_TOTAL': hex(SIZE),
        'DUMP_CHUNK': '0x1000',
        'DUMP_RESTART': '1',
        'DUMP_VERIFY': '0',
        'READMODE': 'auto',
    })
    ap = FakeAP(broken=classic_broken)
    target = FakeTarget(ap, probe_broken=probe_broken)
    sess = FakeSession(target)

    reg = {}

    def command(n, help=''):
        def deco(fn):
            reg[n] = fn
            return fn
        return deco

    ns = {'command': command, 'session': sess, '__name__': 'test'}
    src = open(SCRIPT, encoding='utf-8').read()
    exec(compile(src, SCRIPT, 'exec'), ns)

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        reg['dumpresume']()
    finally:
        sys.stdout = old
    out = buf.getvalue()

    print("========== 用例：%s ==========" % name)
    for line in out.splitlines():
        if any(k in line for k in ('选定读法', '读法自检', '经典AP', '目标层',
                                   'SANITY', '真伪校验', '搜 ', '不同 32 位字',
                                   'DUMP_COMPLETE', '整片读完了')):
            print("  " + line.strip())

    path = os.path.join(outdir, 'zk42v-factory-512k.bin')
    data = open(path, 'rb').read()
    got_mode = None
    for line in out.splitlines():
        if '选定读法' in line:
            got_mode = line.split('：')[1].split('（')[0].strip()

    checks = [
        ("文件大小对", len(data) == SIZE),
        ("选中的读法 = %s" % expect_mode, got_mode == expect_mode),
        ("SANITY 结果 = %s" % expect_sanity,
         ('SANITY_OK' in out) == (expect_sanity == 'OK')),
    ]
    # 只有"有能用的读法"的场景，才该读出真数据
    if expect_sanity == 'OK':
        checks += [
            ("文件里搜得到 ZKC42V", b'ZKC42V' in data),
            ("app 镜像校验和对得上",
             (sum(data[0xA000:0xA000 + 0x1F8F0]) & 0xFFFFFFFF) == 0x00CCE71B),
            ("dump 内容跟真固件一模一样", bytes(data) == bytes(REAL)),
            ("没有任何一个 4 字节字重复成一片", len(set(
                struct.unpack_from('<%dI' % (len(data) // 4), data, 0))) > 1000),
        ]
    else:
        checks += [("（预期）整片就是同一个字", len(set(
            struct.unpack_from('<%dI' % (len(data) // 4), data, 0))) == 1)]
    bad = 0
    for label, ok in checks:
        print("  [%s] %s" % ('PASS' if ok else '**FAIL**', label))
        bad += 0 if ok else 1
    print()
    return bad


fails = 0
fails += run('加速路径坏掉（复现 pyOCD 假数据）', True, False, 'apid', 'OK')
fails += run('经典路径坏掉、加速路径正常', False, True, 'probe', 'OK')
fails += run('两条都坏（应该报可疑）', True, True, 'probe', 'FAIL')
print("全部通过 ✅" if fails == 0 else "有 %d 项失败 ❌" % fails)
sys.exit(1 if fails else 0)
