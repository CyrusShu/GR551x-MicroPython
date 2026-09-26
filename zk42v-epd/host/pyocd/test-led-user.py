# 用假 probe 跑 led-window-user.py，验证逻辑（不需要真硬件）
import os
import sys
import time
import tempfile

SCRIPT = '/Users/mac/Documents/Codex/2026-09-15/a/outputs/pyocd/led-window-user.py'


class FakeLink:
    def get_target_voltage(self):
        pass
    target_voltage = 3.211


class FakeProbe:
    def __init__(self, fail_ms=0):
        self.fail_ms = fail_ms          # 复位后多少 ms 才开始能连上
        self.t0 = None
        self.reset_calls = []
        self.connect_tries = 0

    _link = FakeLink()

    def assert_reset(self, asserted):
        self.reset_calls.append((asserted, round(time.monotonic(), 3)))
        if asserted:
            # 复位那一刻 = 新的时间基准
            self.t0 = None

    def connect(self):
        self.connect_tries += 1
        if self.t0 is None:
            self.t0 = time.monotonic()
        el = (time.monotonic() - self.t0) * 1000.0
        if el < self.fail_ms:
            raise RuntimeError('STLink error (9): Get IDCODE error')
        return None


class FakeTarget:
    def __init__(self):
        self.halted = False
    def halt(self):
        self.halted = True
    def read_memory(self, addr, size):
        return {0x01000000: 0x30008000, 0x01003000: 0x20000000,
                0x0100A000: 0x20000010, 0x0100A200: 0x47525858,
                0x0107F000: 0xFFFFFFFF}.get(addr, 0)

    def read_memory_block8(self, addr, size):
        # 假数据：按地址给个可预测的花样，方便验证写文件对不对
        return [(addr + i) & 0xff for i in range(size)]


class FakeBoard:
    def __init__(self, target):
        self.target = target
        self.inited = False
    def init(self):
        self.inited = True


class FakeSession:
    def __init__(self, probe, target):
        self.probe = probe
        self.target = target
        self.board = FakeBoard(target)


def run_case(name, fail_ms, outdir, env_extra):
    os.environ.update({
        'GR551X_OUTDIR': outdir,
        'BEEP': '0',
        'LED_DELAY_MS': '300',
        'LED_GREEN_MS': '200',
        'LED_BLUE_MS': '200',
        'ROUNDS': '2',
        'WINDOW_MS': '1200',
        'HOLD_MS': '100',
        'GR551X_PULSES': '3',
        'GR551X_PULSE_GAP': '50',
    })
    os.environ.update(env_extra)

    probe = FakeProbe(fail_ms=fail_ms)
    target = FakeTarget()
    sess = FakeSession(probe, target)

    reg = {}

    def command(name_, help=''):
        def deco(fn):
            reg[name_] = fn
            return fn
        return deco

    ns = {'command': command, 'session': sess, '__name__': 'test'}
    src = open(SCRIPT, encoding='utf-8').read()
    exec(compile(src, SCRIPT, 'exec'), ns)

    print("=========== 用例: %s ===========" % name)
    assert 'rstpulse' in reg and 'ledwindow' in reg, "命令没注册上: %s" % list(reg)
    print("  注册到的命令: %s" % sorted(reg))

    if name.startswith('rsthold'):
        t = time.monotonic()
        reg['rsthold']()
        print("  >>> rsthold 用了 %.2f 秒" % (time.monotonic() - t))
        print("  >>> assert_reset 调用 %d 次，序列: %s"
              % (len(probe.reset_calls),
                 ''.join('L' if a else 'H' for a, _ in probe.reset_calls)))
        print()
        return

    if name.startswith('pulse'):
        t = time.monotonic()
        reg['rstpulse']()
        print("  >>> rstpulse 用了 %.2f 秒，assert_reset 调用 %d 次"
              % (time.monotonic() - t, len(probe.reset_calls)))
        print("  >>> 拉低/松开序列: %s"
              % ''.join('L' if a else 'H' for a, _ in probe.reset_calls))
    else:
        t = time.monotonic()
        reg['ledwindow']()
        print("  >>> ledwindow 用了 %.2f 秒" % (time.monotonic() - t))
        print("  >>> connect 尝试次数: %d" % probe.connect_tries)
        print("  >>> target halt 了吗: %s   board init 了吗: %s"
              % (target.halted, sess.board.inited))
        for f in ('zk42v-factory-512k.bin', 'zk42v-nvds-4k.bin'):
            p = os.path.join(outdir, f)
            print("  >>> %s: %s" % (f, ('%d 字节' % os.path.getsize(p)) if os.path.exists(p) else '没生成'))
    print()


tmp1 = tempfile.mkdtemp(prefix='lw-fail-')
tmp2 = tempfile.mkdtemp(prefix='lw-hit-')
run_case('pulse', 0, tempfile.mkdtemp(prefix='lw-pulse-'), {})
run_case('ledwindow-fail', 10 ** 9, tmp1, {})
run_case('ledwindow-hit', 500, tmp2, {})
run_case('ledwindow-manual', 500, tempfile.mkdtemp(prefix='lw-man-'),
         {'MANUAL': '1', 'BEEP': '0', 'WINDOW_MS': '900'})
run_case('rsthold-low', 0, tempfile.mkdtemp(prefix='lw-h1-'),
         {'RST_STATE': 'low', 'RST_HOLD_SECS': '3'})
run_case('rsthold-toggle', 0, tempfile.mkdtemp(prefix='lw-h2-'),
         {'RST_STATE': 'toggle', 'RST_HOLD_SECS': '5', 'RST_TOGGLE_MS': '800'})
