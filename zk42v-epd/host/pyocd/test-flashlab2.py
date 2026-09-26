# 离线跑 flashlab2 / flashlab3 / flashlab4 / rstcheck / ramdump
#
#   A 别名 0x03000000 能读到真 flash（CSW 全死）
#   B 别名也死、CSW 全死，但 CPU 自己能读 flash -> CPU 搬运成功
#   C 三条路全死（连 CPU 读 flash 都是死值）
#
# 假芯片里装的就是真 dump（zk42v-live-512k.bin），所以「读回的东西对不对」
# 是用真固件当标准答案判的，不是拿某个猜出来的 magic。
import io
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'led-window-user.py')
REAL_DUMP = os.path.join(HERE, 'zk42v-live-512k.bin')
DEAD = 0xF7C03404
BOOT_VEC = 0x0100A2B9
FLASH_LEN = 0x80000
FLASH_BASE = 0x01000000
ALIAS_BASE = 0x03000000

FLASH = bytearray(FLASH_LEN)
with open(REAL_DUMP, 'rb') as _f:
    _real = _f.read()
FLASH[:len(_real)] = _real
assert struct.unpack_from('<I', FLASH, 0xA004)[0] == BOOT_VEC


class FakeAddr:
    address = 0x00000000


class FakeAP:
    short_description = 'AHB-AP#0'
    address = FakeAddr()

    def __init__(self, scen):
        self.scen = scen
        self.ram = {}
        self.reads = 0
        self._cached_csw = -1

    def _read_memory(self, addr, transfer_size=32):
        if addr == 0xE000ED00:
            return 0x410FC241
        if addr == 0xE00FF000:
            return 0xFFF0F003
        if FLASH_BASE <= addr < FLASH_BASE + FLASH_LEN:
            self.reads += 1
            if self.scen.get('live_after') and self.reads > self.scen['live_after']:
                return struct.unpack_from('<I', FLASH, addr - FLASH_BASE)[0]
            return DEAD
        if ALIAS_BASE <= addr < ALIAS_BASE + FLASH_LEN:
            if self.scen.get('alias_ok'):
                return struct.unpack_from('<I', FLASH, addr - ALIAS_BASE)[0]
            return DEAD
        if 0 <= addr < 0x80000:
            return (0x10000 + addr) & 0xFFFFFFFF
        if 0x00800000 <= addr < 0x00820000:
            return self.ram.get(addr, 0)
        if 0xA0000000 <= addr < 0xA0020000:
            if not self.scen.get('periph', True):
                raise Exception('Memory transfer fault')
            return (0xA0000000 + (addr & 0xFFF)) & 0xFFFFFFFF
        if addr == 0x30000000:
            return 0
        return 0

    def cpu_read(self, addr):
        if FLASH_BASE <= addr < FLASH_BASE + FLASH_LEN:
            if not self.scen.get('cpu_can_read', True):
                return DEAD
            return struct.unpack_from('<I', FLASH, addr - FLASH_BASE)[0]
        if ALIAS_BASE <= addr < ALIAS_BASE + FLASH_LEN:
            return struct.unpack_from('<I', FLASH, addr - ALIAS_BASE)[0]
        return self._read_memory(addr)

    def _write_memory(self, addr, data, transfer_size=32):
        if 0x00800000 <= addr < 0x00820000:
            self.ram[addr] = data & 0xFFFFFFFF
            return
        raise Exception('Memory transfer fault')

    def _read_memory_block32(self, addr, size):
        return [self._read_memory(addr + 4 * i) for i in range(size)]

    def _write_memory_block32(self, addr, data):
        for i, v in enumerate(data):
            self._write_memory(addr + 4 * i, v)


class FakeDP:
    def __init__(self, ap, scen):
        self.ap = ap
        self.scen = scen
        self.csw = 0
        self.tar = 0

    def write_ap(self, addr, data):
        off = addr & 0xFFFF
        if off == 0x00:
            self.csw = data & 0xFFFFFFFF
        elif off == 0x04:
            self.tar = data & 0xFFFFFFFF

    def read_ap(self, addr):
        off = addr & 0xFFFF
        if off == 0x00:
            return self.csw
        if off != 0x0C:
            return 0
        a = self.tar
        if FLASH_BASE <= a < FLASH_BASE + FLASH_LEN:
            if self.scen.get('csw_ok') and ((self.csw >> 29) & 1) == 0:
                return struct.unpack_from('<I', FLASH, a - FLASH_BASE)[0]
            return DEAD
        if ALIAS_BASE <= a < ALIAS_BASE + FLASH_LEN and self.scen.get('alias_ok'):
            return struct.unpack_from('<I', FLASH, a - ALIAS_BASE)[0]
        try:
            return self.ap._read_memory(a)
        except Exception:
            return DEAD


class FakeTarget:
    def __init__(self, ap, dp):
        self.ap = ap
        self.dp = dp
        self.regs = {}
        self.running = False

    def init(self):
        pass

    def resume(self):
        self.running = True

    def halt(self):
        self.running = False
        pc = self.regs.get('pc', 0) & ~1
        if self.ap.ram.get(pc) == 0x6803601A:
            src = self.regs.get('r0', 0)
            dst = self.regs.get('r1', 0)
            n = self.regs.get('r2', 0)
            mark = self.regs.get('r3', 0)
            if mark:
                self.ap.ram[mark] = n
            for i in range(n):
                self.ap.ram[dst + 4 * i] = self.ap.cpu_read(src + 4 * i)

    def get_state(self):
        return 'RUNNING' if self.running else 'HALTED'

    def write_core_register(self, name, value):
        self.regs[name] = value

    def read_core_register(self, name):
        return self.regs.get(name, 0x01000000)

    def read_memory_block8(self, addr, size):
        return [0] * size


PROBE_FAIL = {'v': False}


class FakeProbe:
    unique_id = 'FAKE'
    description = 'fake probe'

    def connect(self):
        if PROBE_FAIL['v']:
            raise Exception('could not connect to target')
        return None

    def read_dp(self, addr, now=True):
        return 0x2BA01477


class FakeSession:
    def __init__(self, target):
        self.target = target
        self.probe = FakeProbe()


def run(cmd, scen, env=None):
    ap = FakeAP(scen)
    dp = FakeDP(ap, scen)
    target = FakeTarget(ap, dp)
    os.environ.update({
        'GR551X_OUTDIR': tempfile.mkdtemp(prefix='flashlab2-'),
        'BEEP': '0',
        'MANUAL': '1',
        'DUMP_WINDOW_MS': '300',
        'TRAMP': 'no',
    })
    if env:
        os.environ.update(env)
    PROBE_FAIL['v'] = bool(scen.get('probe_fail'))
    reg = {}

    def command(name, help=''):
        def deco(fn):
            reg[name] = fn
            return fn
        return deco

    ns = {'command': command, 'session': FakeSession(target), '__name__': 'test'}
    exec(compile(open(SCRIPT, encoding='utf-8').read(), SCRIPT, 'exec'), ns)
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        reg[cmd]()
    finally:
        sys.stdout = old
    return buf.getvalue(), ap, target


CHECKS = []


def check(label, ok):
    CHECKS.append((label, bool(ok)))


def main():
    out, ap, t = run('flashlab2', {'alias_ok': True, 'csw_ok': False})
    check('A: 别名窗口读到真数据', '别名窗口读得到真数据' in out)
    check('A: 结论指向别名', 'flash 走 0x03000000 能读' in out)
    check('A: 下一步给 DUMP_BASE=0x03000000', 'DUMP_BASE=0x03000000' in out)
    check('A: CSW 表 14 行都印了', out.count('HPROT=') + out.count('SPIDEN=1') >= 7
          and '去掉 MSTRTYPE' in out and '8 位传输' in out)
    check('A: 外设探查有输出', 'XQSPI' in out)
    check('A: eFuse 两个钥匙地址都读了', '0xA0017060' in out and '0xA00170E0' in out)
    check('A: 只写 RAM 不碰 flash', all(0x00800000 <= a < 0x00820000 for a in ap.ram))

    out, ap, t = run('flashlab2', {'alias_ok': False, 'csw_ok': False, 'cpu_can_read': True},
                     {'TRAMP': 'yes'})
    check('B: 别名判定为死值', '跟 0x01000000 一样是死值' in out)
    check('B: 路 3 报成功', 'CPU 搬运        : 成功' in out)
    check('B: 下一步给 ram-dump.sh', 'ram-dump.sh' in out)
    check('B: 搬运后 RAM 里就是真 flash 内容',
          any(v == BOOT_VEC for a, v in ap.ram.items() if a > 0x00810000))
    check('B: 搬运后 RAM 内容跟真 dump 对得上',
          b''.join(struct.pack('<I', ap.ram[0x00817000 + 4 * i]) for i in range(16))
          == bytes(FLASH[0xA000:0xA040]))

    out, ap, t = run('flashlab2', {'alias_ok': False, 'csw_ok': False, 'cpu_can_read': False},
                     {'TRAMP': 'yes'})
    check('C: 路 3 没成', '搬回来还是死值' in out)
    check('C: 三条路都不通时指向 flash-lab4', 'flash-lab4.sh' in out)

    out, ap, t = run('flashlab2', {'alias_ok': False, 'csw_ok': True}, {'TRAMP': 'no'})
    check('D: CSW 那一路有命中', 'flash 窗口被这种 CSW 读通了' in out)

    out, ap, t = run('flashlab3', {'alias_ok': False, 'csw_ok': False, 'cpu_can_read': False},
                     {'TRAMP': 'yes'})
    check('F: 标记被改写（代码真的跑了）', '代码真的跑了 ✅' in out)
    check('F: CPUID 抄对了', '0x410FC241' in out)
    check('F: 结论指向 QSPI 控制器那条路', '直接驱动 QSPI 控制器读 flash' in out)
    check('F: XQSPI 寄存器清单有 XIP CTRL3', 'XIP 使能请求' in out)

    out, ap, t = run('flashlab3', {'alias_ok': False, 'csw_ok': False, 'cpu_can_read': True},
                     {'TRAMP': 'yes'})
    check('G: CPU 能从 flash 抄到真数据', '搬运这条路可以走' in out)

    out, ap, t = run('flashlab4', {'alias_ok': False, 'csw_ok': False, 'live_after': 3},
                     {'POLL_MS': '1', 'POLL_TOTAL_MS': '500'})
    check('H: 等到窗口变活', '窗口活了' in out)
    check('H: 变活后把 CPU 冻住了', t.running is False)
    check('H: 整片读并过了真伪校验', 'SANITY_OK' in out)
    hdst = os.path.join(os.environ.get('GR551X_OUTDIR', '/tmp'), 'x')  # 只占位
    check('H: 提示了再对一次 SHA-256', 'SHA-256' in out)

    out, ap, t = run('flashlab4', {'alias_ok': False, 'csw_ok': False},
                     {'POLL_MS': '1', 'POLL_TOTAL_MS': '200'})
    check('H2: 没等到时给出 a/b/c 三种可能', '没等到窗口变活' in out and 'POLL_MS=1' in out)

    out, ap, t = run('rstcheck', {})
    check('I: RST 握手报有效', 'RST 有效 ✅' in out)
    check('I: 报了 DP IDR 和 CPUID', 'DP IDR' in out and '0x410FC241' in out)

    out, ap, t = run('rstcheck', {'probe_fail': True}, {'DUMP_WINDOW_MS': '50'})
    check('I2: 握不上时说清原因', '内都没握上手' in out and '彻底断电' in out)

    tmp = tempfile.mkdtemp(prefix='ramdump-')
    out, ap, t = run('ramdump', {'alias_ok': False, 'cpu_can_read': True},
                     {'DUMP_DIR': tmp, 'DUMP_RESTART': '1', 'TRAMP_RUN_MS': '10'})
    dst = os.path.join(tmp, 'zk42v-cpu-512k.bin')
    size = os.path.getsize(dst) if os.path.exists(dst) else -1
    check('E: 文件大小 = 512KB', size == FLASH_LEN)
    if size == FLASH_LEN:
        check('E: 文件内容 = 真 flash', open(dst, 'rb').read() == bytes(FLASH))
    check('E: 真伪校验过了', 'SANITY_OK' in out)

    bad = 0
    for label, ok in CHECKS:
        print("  [%s] %s" % ('PASS' if ok else '**FAIL**', label))
        bad += 0 if ok else 1
    print("全部通过 ✅" if bad == 0 else "有 %d 项失败 ❌" % bad)
    return bad


sys.exit(1 if main() else 0)
