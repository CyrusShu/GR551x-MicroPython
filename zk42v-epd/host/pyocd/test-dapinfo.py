# 离线跑 dapinfo：模拟"DP IDR 是垃圾 + 所有地址读回同一个值"
import io
import os
import sys
import tempfile
import time

SCRIPT = '/Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd/led-window-user.py'
JUNK = 0x4034C8F7


class FakeLink:
    _jtag_version = 'J37'
    _hw_version = 'V2'
    _protocol = 'SWD'
    _swd_freq = 240000
    target_voltage = 3.2


class FakeProbe:
    unique_id = '34FF74064D56383350302243'
    description = 'STM32 STLink (fake)'
    _link = FakeLink()

    def connect(self):
        return None

    def read_dp(self, addr, now=True):
        # 坏掉的读通路：不管问什么寄存器，都给同一个字
        return JUNK


class FakeAP:
    short_description = 'AHB-AP (fake)'

    class _Addr:
        address = 0x00000000

    address = _Addr()

    def read_reg(self, off, now=True):
        return JUNK

    def _read_memory(self, addr, transfer_size=32, now=True):
        return JUNK


class FakeDPIDR:
    idr = JUNK
    partno = JUNK & 0x3
    version = (JUNK >> 12) & 0xF
    revision = (JUNK >> 28) & 0xF


class FakeTarget:
    def __init__(self):
        self.ap = FakeAP()
        self.dp = type('DP', (), {'dpidr': FakeDPIDR})()
        self.halt_called = False

    def init(self):
        pass

    def halt(self):
        self.halt_called = True

    def get_state(self):
        return 'RUNNING'

    def read_memory_block8(self, addr, size):
        return [0x44] * size


class FakeSession:
    def __init__(self, target):
        self.target = target
        self.probe = FakeProbe()


def main():
    os.environ.update({
        'GR551X_OUTDIR': tempfile.mkdtemp(prefix='dapinfo-'),
        'BEEP': '0',
        'MANUAL': '1',
        'DUMP_WINDOW_MS': '300',
    })
    target = FakeTarget()
    sess = FakeSession(target)
    reg = {}

    def command(n, help=''):
        def deco(fn):
            reg[n] = fn
            return fn
        return deco

    ns = {'command': command, 'session': sess, '__name__': 'test'}
    exec(compile(open(SCRIPT, encoding='utf-8').read(), SCRIPT, 'exec'), ns)

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        reg['dapinfo']()
    finally:
        sys.stdout = old
    out = buf.getvalue()
    print(out)

    checks = [
        ("注册了 dapinfo", 'dapinfo' in reg),
        ("打印了 DP IDR", 'probe.read_dp(0x0)' in out),
        ("连读 8 次的分辨试验在", '8 次里有 1 个不同的值' in out),
        ("判定为读命令坏了", '读寄存器这条命令坏了' in out),
        ("识别出 version 不正常", '不像正常值' in out),
        ("halt 前后 DHCSR 都读了", 'halt 前 DHCSR' in out and 'halt 后 DHCSR' in out),
        ("判定读通路没反映状态", '压根没反映芯片状态' in out),
        ("给了结论对照表", '结论怎么读' in out),
        ("真的调了 halt", target.halt_called),
    ]
    bad = 0
    for label, ok in checks:
        print("  [%s] %s" % ('PASS' if ok else '**FAIL**', label))
        bad += 0 if ok else 1
    print("全部通过 ✅" if bad == 0 else "有 %d 项失败 ❌" % bad)
    return bad


sys.exit(1 if main() else 0)
