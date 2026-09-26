# 离线跑 flashlab：模拟"0x01000000 是死值，0x0000xxxx 有真数据"
import io
import os
import struct
import sys
import tempfile

SCRIPT = '/Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd/led-window-user.py'
DEAD = 0xB7C83440

# 假 flash：放在 0x00000000 这个"别名"上
CONTENT = bytearray(0x20000)
for i in range(0, len(CONTENT), 4):
    struct.pack_into('<I', CONTENT, i, (0x512D0000 + i // 4) & 0xFFFFFFFF)
struct.pack_into('<I', CONTENT, 0x0000, 0x00804000)
struct.pack_into('<I', CONTENT, 0x3000, 0x00804101)
struct.pack_into('<I', CONTENT, 0xA200, 0x47525858)


class FakeAP:
    short_description = 'AHB-AP#0'

    def _read_memory(self, addr, transfer_size=32, now=True):
        if addr == 0xE000ED00:
            return 0x410FC241
        if addr == 0xE00FF000:
            return 0xFFF0F003
        if 0x01000000 <= addr < 0x01080000:
            return DEAD                      # flash 官方区间：死值
        off = addr
        if 0 <= off <= len(CONTENT) - 4:
            return struct.unpack_from('<I', CONTENT, off)[0]
        return 0


class FakeTarget:
    def __init__(self):
        self.ap = FakeAP()
        self.halted = False
        self.dp = type('DP', (), {'dpidr': None})()

    def init(self):
        pass

    def halt(self):
        self.halted = True

    def get_state(self):
        return 'RUNNING'

    def read_memory_block8(self, addr, size):
        return [0] * size


class FakeProbe:
    unique_id = 'FAKE'
    description = 'fake probe'

    def connect(self):
        return None


class FakeSession:
    def __init__(self, target):
        self.target = target
        self.probe = FakeProbe()


def main():
    os.environ.update({
        'GR551X_OUTDIR': tempfile.mkdtemp(prefix='flashlab-'),
        'BEEP': '0',
        'MANUAL': '1',
        'DUMP_WINDOW_MS': '300',
    })
    target = FakeTarget()
    reg = {}

    def command(n, help=''):
        def deco(fn):
            reg[n] = fn
            return fn
        return deco

    ns = {'command': command, 'session': FakeSession(target), '__name__': 'test'}
    exec(compile(open(SCRIPT, encoding='utf-8').read(), SCRIPT, 'exec'), ns)

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        reg['flashlab']()
    finally:
        sys.stdout = old
    out = buf.getvalue()
    print(out)

    checks = [
        ("注册了 flashlab", 'flashlab' in reg),
        ("扫到了别名窗口 0x00000000", '0x00000000 : 0x00804000' in out),
        ("标出了 flash 死值区", '0x01000000 : 0x%08X' % DEAD in out),
        ("读到别名的 app_info magic", '0x0000A200 = 0x47525858' in out),
        ("halt 被调用了", target.halted),
    ]
    bad = 0
    for label, ok in checks:
        print("  [%s] %s" % ('PASS' if ok else '**FAIL**', label))
        bad += 0 if ok else 1
    print("全部通过 ✅" if bad == 0 else "有 %d 项失败 ❌" % bad)
    return bad


sys.exit(1 if main() else 0)
