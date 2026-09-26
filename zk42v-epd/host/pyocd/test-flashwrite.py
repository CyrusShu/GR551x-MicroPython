# 离线跑 flashwrite：用假 pyOCD 模块 + 假芯片，验控制流
#
#   A probe      : 只 Init、不擦不写，并打印算法认的地址范围
#   B pagetest   : 一颗扇区 擦->写->读回 对得上
#   C restore    : 整片刷回，结束状态 = 备份，且顺序是从高地址往低地址
#   D 联锁       : 算法认的基地址不对 -> 必须拒绝写，一个字节都不许动
#   E verify     : 读回跟备份一致 -> VERIFY_OK；被改过 -> VERIFY_FAIL
#   F 中途失败   : 停在那颗扇区，低地址那段（含 bootloader）原封不动
#   G/H          : 算法装不进 RAM / Init 被拒 -> 明确报错，不硬来
#   J  peek      : 干净重连（XIP 活着）之后，能认出 pagetest 写下的图案
#   K  死窗口    : 算法卸完窗口也回不来时，不许把"读不到"误报成"写失败"
import io
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'led-window-user.py')
REAL_DUMP = os.path.join(HERE, 'zk42v-live-512k.bin')
BASE = 0x01000000
TOTAL = 0x80000
SECTOR = 0x1000

REAL = open(REAL_DUMP, 'rb').read()
assert len(REAL) == TOTAL

FW_START = 0x01000000          # 算法「正常」时会算出来的基地址
LOAD_ADDR = 0x0081F000
CODE_START = LOAD_ADDR + 4
RW_START = 0x20
STATIC_BASE = CODE_START + RW_START
SYM_OFF = {'first_packet_data': 0x8, 'fw_start_addr': 0xC,
           'mirror_addr_offset': 0x10, 'verify_size': 0x14}


def make_fake_pyocd(root):
    pkg = os.path.join(root, 'pyocd')
    for d in ('target/pack', 'core', 'flash', 'debug/elf'):
        os.makedirs(os.path.join(pkg, d), exist_ok=True)
    for d in ('', 'target', 'target/pack', 'core', 'flash', 'debug', 'debug/elf'):
        open(os.path.join(pkg, d, '__init__.py'), 'w').write('')
    elf_src = '''
class _Sym:
    def __init__(self, address):
        self.address = address


class _Decoder:
    def __init__(self, table):
        self.table = table

    def get_symbol_for_name(self, name):
        v = self.table.get(name)
        return None if v is None else _Sym(v)


class ELFBinaryFile:
    TABLE = {table}

    def __init__(self, path):
        self.symbol_decoder = _Decoder(dict(self.TABLE))
'''.replace('{table}', repr(SYM_OFF))
    open(os.path.join(pkg, 'debug/elf/elf.py'), 'w').write(elf_src)
    open(os.path.join(pkg, 'core/memory_map.py'), 'w').write('''
class RamRegion:
    def __init__(self, start=0, length=0, **kw):
        self.start = start
        self.length = length
        self.end = start + length - 1


class FlashRegion:
    def __init__(self, start=0, end=0, length=None, **attrs):
        self.start = start
        self.end = end
        self.attrs = attrs
        self.sector_size = attrs.get('sector_size', 0x1000)
        self.page_size = attrs.get('page_size', 0x1000)
        self.erased_byte_value = attrs.get('erased_byte_value', 0xFF)
''')
    open(os.path.join(pkg, 'target/pack/flash_algo.py'), 'w').write('''
import os


class _Info:
    def __init__(self, name, start, size, page_size, value_empty):
        self.name = name
        self.start = start
        self.size = size
        self.page_size = page_size
        self.value_empty = value_empty


class PackFlashAlgo:
    def __init__(self, path):
        self.path = path
        self.flash_info = _Info(b'GR5xxx_16MB_Flash', 0x00100000, 0x1000000,
                                0x1000, 0xFF)
        self.rw_start = {rw}
        self.rw_size = 0x100
        self.zi_start = 0
        self.zi_size = 0

    def get_pyocd_flash_algo(self, page_size, ram_region):
        if os.environ.get('FAKE_ALGO_NORAM') == '1':
            return None
        return dict(
            load_address={load},
            instructions=[0xE7FDBE00] * 8,
            pc_init={load} + 4,
            pc_unInit={load} + 8,
            pc_erase_sector={load} + 12,
            pc_program_page={load} + 16,
            page_buffers=[0x0081E000, 0x0081D000],
            begin_stack=0x0081F000,
            end_stack=0x0081C000,
            static_base={static},
            min_program_length=page_size,
            analyzer_supported=False,
        )
'''.replace('{rw}', hex(RW_START)).replace('{load}', hex(LOAD_ADDR))
        .replace('{static}', hex(STATIC_BASE)))
    open(os.path.join(pkg, 'flash/flash.py'), 'w').write('''
from enum import IntEnum


class _Op(IntEnum):
    ERASE = 1
    PROGRAM = 2
    VERIFY = 3


class Flash:
    Operation = _Op
    LOG = []

    def __init__(self, target, algo):
        self.target = target
        self.algo = algo
        self._region = None
        self.op = None

    @property
    def region(self):
        return self._region

    @region.setter
    def region(self, r):
        self._region = r

    def init(self, operation, address=None, clock=0, reset=False):
        Flash.LOG.append(('init', operation.name))
        self.op = operation
        # 照实机来：算法一 Init，QSPI 就进了它自己的命令模式，
        # 这时候从 flash 窗口读回来的是个死值，不是真数据。
        self.target.xip_on = False
        if self.target.fail_init:
            raise RuntimeError('fake init refused')

    def uninit(self):
        Flash.LOG.append(('uninit', None))
        # 照实机来：UnInit 会把 QSPI/XIP 交还给芯片，交还完才读得到真数据。
        # 交还之后算法代码还留在 RAM 里，可以接着再 init。
        if not self.target.xip_stuck:
            self.target.xip_on = True

    def cleanup(self):
        Flash.LOG.append(('cleanup', None))
        self.uninit()

    def erase_sector(self, address):
        Flash.LOG.append(('erase', address))
        if self.target.fail_erase_at == address:
            raise RuntimeError('fake erase failed')
        n = self._region.sector_size
        off = address - %d
        self.target.flash[off:off + n] = b'\\xff' * n

    def program_page(self, address, data):
        Flash.LOG.append(('program', address, len(data)))
        if (self.target.fail_program_at is not None
                and self.target.fail_program_at <= address
                < self.target.fail_program_at + %d):
            raise RuntimeError('fake program failed')
        off = address - %d
        self.target.flash[off:off + len(data)] = data
''' % (BASE, SECTOR, BASE))


class FakeAddr:
    address = 0


class FakeAP:
    short_description = 'AHB-AP#0'
    address = FakeAddr()

    def __init__(self, target):
        self.target = target

    def _read_memory(self, addr):
        return self._read_memory_block32(addr, 1)[0]

    def _read_memory_block32(self, addr, n):
        out = []
        for i in range(n):
            a = addr + 4 * i
            if CODE_START <= a < CODE_START + 0x100:
                out.append(self.target.ram.get(a, 0))
            elif 0x3001F000 <= a < 0x3001F040:
                # 自研固件的调试状态块（status 命令读它）
                if a == 0x3001F004 and self.target.heart_live:
                    self.target.heart += 1
                    out.append(self.target.heart & 0xFFFFFFFF)
                elif a == 0x3001F030 and self.target.boot_grow:
                    self.target.boot += 1
                    out.append(self.target.boot & 0xFFFFFFFF)
                else:
                    out.append(self.target.dbg.get(a, 0))
            elif a == 0xA000C560:
                out.append(self.target.aon_sw1)      # AON SOFTWARE_1
            elif BASE <= a < BASE + TOTAL:
                if self.target.xip_on:
                    out.append(struct.unpack_from('<I', bytes(self.target.flash),
                                                  a - BASE)[0])
                else:
                    # 实机上量到的死值就是这一串（flash-lab4 里 +5ms 那次）。
                    out.append(self.target.dead_value)
            else:
                out.append(0)
        return out


class FakeProbe:
    unique_id = 'FAKE'
    target_voltage = 3.2

    def connect(self):
        return None

    def read_dp(self, addr, now=True):
        return 0x2BA01477


class FakeSession:
    def __init__(self, target):
        self.target = target
        self.probe = FakeProbe()


class FakeTarget:
    def __init__(self, flash, ram):
        self.flash = flash
        self.ram = ram
        self.halted = False
        self.fail_init = False
        self.fail_erase_at = None
        self.fail_program_at = None
        self.xip_on = True
        self.xip_stuck = False
        self.dead_value = 0xF7C03400
        self.dbg = {}
        self.heart = 0
        self.heart_live = True
        self.boot = 0
        self.boot_grow = False
        self.aon_sw1 = 0
        self.pc_seq = [0x0100C6A9]
        self.pc_idx = 0
        self.ap = FakeAP(self)

    def init(self):
        pass

    def halt(self):
        self.halted = True

    def resume(self):
        self.halted = False

    def read_core_register(self, name):
        if name == 'pc':
            v = self.pc_seq[min(self.pc_idx, len(self.pc_seq) - 1)]
            self.pc_idx += 1
            return v
        return {'lr': 0x0100C6B1, 'sp': 0x3001E000}.get(name, 0)

    def read_memory_block8(self, addr, size):
        return [0] * size

    def read_memory32(self, addr):
        return self.ap._read_memory(addr)


def default_ram(fw_start):
    ram = {}
    # 照实机来：真机上这三个都是 0，基地址只在 g_chip_spec_info[0] 里。
    # 谁要是又拿 fw_start_addr 当判据，这里就会露馅。
    ram[CODE_START + SYM_OFF['first_packet_data']] = 0x00000001
    ram[CODE_START + SYM_OFF['fw_start_addr']] = 0x00000000
    ram[CODE_START + SYM_OFF['mirror_addr_offset']] = 0x00000000
    ram[CODE_START + SYM_OFF['verify_size']] = 0x00000000
    ram[STATIC_BASE + 0x44] = fw_start
    ram[STATIC_BASE + 0x48] = TOTAL
    for i in range(1, 5):
        ram[STATIC_BASE + 0x44 + 8 * i] = 0
        ram[STATIC_BASE + 0x48 + 8 * i] = 0
    return ram


_REAL_COPY = []


def real_copy():
    if not _REAL_COPY:
        p = os.path.join(tempfile.mkdtemp(prefix='fwfile-'), 'backup.bin')
        open(p, 'wb').write(REAL)
        _REAL_COPY.append(p)
    return _REAL_COPY[0]


def run(cmd, env, flash=None, ram=None, fail_init=False, fail_erase_at=None,
        fail_program_at=None, fake_root=None, xip_stuck=False,
        dbg=None, heart_live=True, aon_sw1=0, pc_seq=None, boot_grow=False):
    from pyocd.flash.flash import Flash
    Flash.LOG = []
    tgt = FakeTarget(bytearray(REAL if flash is None else flash),
                     default_ram(FW_START) if ram is None else ram)
    tgt.fail_init = fail_init
    tgt.fail_erase_at = fail_erase_at
    tgt.fail_program_at = fail_program_at
    tgt.xip_stuck = xip_stuck
    if dbg is not None:
        tgt.dbg = dict(dbg)
    tgt.heart_live = heart_live
    tgt.aon_sw1 = aon_sw1
    tgt.boot_grow = boot_grow
    if pc_seq:
        tgt.pc_seq = list(pc_seq)
    os.environ.update({
        'GR551X_OUTDIR': tempfile.mkdtemp(prefix='fwout-'),
        'BEEP': '0',
        'MANUAL': 'hold',
        'HOLD_WAIT_SECS': '0',
        'DUMP_WINDOW_MS': '300',
        'MODE': 'probe',
        'FW_FILE': real_copy(),
        'FW_BASE': hex(BASE),
        'FW_TOTAL': hex(TOTAL),
        'FLM': os.path.join(fake_root, 'fake.FLM'),
        'WAIT_LIVE_MS': '200',
        'FORCE': '0',
    })
    os.environ.update(env)
    reg = {}

    def command(name, help=''):
        def deco(fn):
            reg[name] = fn
            return fn
        return deco

    ns = {'command': command, 'session': FakeSession(tgt), '__name__': 'test'}
    exec(compile(open(SCRIPT, encoding='utf-8').read(), SCRIPT, 'exec'), ns)
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        reg[cmd]()
    finally:
        sys.stdout = old
    return buf.getvalue(), tgt, Flash.LOG


CHECKS = []


def check(label, ok):
    CHECKS.append((label, bool(ok)))


def wrote(log):
    return any(s[0] in ('erase', 'program') for s in log)


def main():
    root = tempfile.mkdtemp(prefix='fakepyocd-')
    make_fake_pyocd(root)
    sys.path.insert(0, root)
    open(os.path.join(root, 'fake.FLM'), 'w').write('not a real flm')

    out, tgt, log = run('flashwrite', {'MODE': 'probe'}, fake_root=root)
    check('A: 报 Init 成功', 'Init(addr=0x01000000, op=擦除) 返回 0' in out)
    check('A: 读回了算偏移用的那个基地址',
          'g_chip_spec_info[0].start = 0x01000000' in out)
    check('A: 读回了算法认的地址范围', 'start=0x01000000  size=0x80000' in out)
    check('A: 没有任何擦/写', not wrote(log))
    check('A: 收尾说没擦没写', '没擦也没写' in out)
    check('A: fw_start_addr 是 0 也没关系', 'fw_start_addr' in out)
    check('A: probe 也顺便报了联锁结论', '联锁通过' in out)
    check('A: probe 末尾提示下一步', 'MODE=pagetest' in out)

    out, tgt, log = run('flashwrite', {'MODE': 'pagetest'}, fake_root=root)
    check('B: 联锁通过', '联锁通过' in out)
    check('B: 擦了一颗扇区', [s for s in log if s[0] == 'erase']
          == [('erase', BASE + TOTAL - SECTOR)])
    check('B: 读回比对全对上', '字节全对上' in out)
    check('B: 只动了那一颗扇区',
          bytes(tgt.flash[:TOTAL - SECTOR]) == REAL[:TOTAL - SECTOR])
    check('B: 那一颗扇区确实被改了',
          bytes(tgt.flash[TOTAL - SECTOR:]) != REAL[TOTAL - SECTOR:])

    out, tgt, log = run('flashwrite', {'MODE': 'restore'}, fake_root=root)
    erases = [s[1] for s in log if s[0] == 'erase']
    check('C: 128 颗扇区都擦了', len(erases) == TOTAL // SECTOR)
    check('C: 顺序是从高地址往低地址', erases == sorted(erases, reverse=True))
    check('C: 最后擦的是含 bootloader 那颗', erases[-1] == BASE)
    check('C: 结束状态 = 备份', bytes(tgt.flash) == REAL)
    check('C: 报写完了', '写完了' in out)
    check('C: 提示接着做 verify', 'MODE=verify bash flash-write.sh' in out)
    check('C: 扇区数真的印出来了，不是裸的 %d',
          '开始刷：128 颗扇区' in out and '%d' not in out)

    out, tgt, log = run('flashwrite', {'MODE': 'restore'},
                        ram=default_ram(0x00100000), fake_root=root)
    check('D: 联锁没过', '联锁没过' in out)
    check('D: 一个字节都没动', not wrote(log))
    check('D: 芯片内容原封不动', bytes(tgt.flash) == REAL)
    check('D: 告诉用户改 FW_BASE', '把 FW_BASE 改成 0x00100000' in out)

    out, tgt, log = run('flashwrite', {'MODE': 'restore', 'FW_TOTAL': hex(TOTAL),
                                       'FW_BASE': hex(BASE)}, fake_root=root)
    check('D3: 范围内照常放行', '联锁通过' in out)
    ram_big = default_ram(FW_START)
    ram_big[STATIC_BASE + 0x48] = 0x40000          # 算法说这颗 flash 只有 256KB
    out, tgt, log = run('flashwrite', {'MODE': 'restore'}, ram=ram_big, fake_root=root)
    check('D4: 超出算法认的范围也要拦', '超出了算法认的范围' in out)
    check('D4: 拦住时没写', not wrote(log))

    out, tgt, log = run('flashwrite', {'MODE': 'probe', 'FORCE': '1'},
                        ram=default_ram(0x00100000), fake_root=root)
    check('D2: probe 模式照样不写', '没擦也没写' in out)

    out, tgt, log = run('flashwrite', {'MODE': 'verify'}, fake_root=root)
    check('E: 一致时报 VERIFY_OK', 'VERIFY_OK' in out)
    flip = bytearray(REAL)
    flip[0x1234] ^= 0x01
    out, tgt, log = run('flashwrite', {'MODE': 'verify'}, flash=flip, fake_root=root)
    check('E: 被改过时报 VERIFY_FAIL', 'VERIFY_FAIL' in out)
    check('E: 指出第一个不一样的偏移', '第一个不一样的偏移 0x1234' in out)
    check('E: verify 不写', not wrote(log))

    out, tgt, log = run('flashwrite', {'MODE': 'restore'},
                        fail_erase_at=BASE + 16 * SECTOR, fake_root=root)
    check('F: 说没写完', '没写完' in out)
    check('F: 停在那颗扇区', '0x%08X 这颗失败' % (BASE + 16 * SECTOR) in out)
    check('F: 低地址那段（含 bootloader）没动',
          bytes(tgt.flash[:16 * SECTOR]) == REAL[:16 * SECTOR])
    check('F: 高地址那段已经写好了',
          bytes(tgt.flash[17 * SECTOR:]) == REAL[17 * SECTOR:])

    os.environ['FAKE_ALGO_NORAM'] = '1'
    out, tgt, log = run('flashwrite', {'MODE': 'probe'}, fake_root=root)
    del os.environ['FAKE_ALGO_NORAM']
    check('G: 装不进 RAM 时明确报错且不写',
          '算法装不进' in out and not wrote(log))

    out, tgt, log = run('flashwrite', {'MODE': 'restore'}, fail_init=True,
                        fake_root=root)
    check('H: Init 失败就说清楚、不写',
          'Init 失败' in out and not wrote(log))

    bad_file = os.path.join(tempfile.mkdtemp(prefix='fwbad-'), 'bad.bin')
    _tmp = bytearray(REAL)
    _tmp[0x20004] ^= 0xFF
    open(bad_file, 'wb').write(bytes(_tmp))
    out, tgt, log = run('flashwrite', {'MODE': 'restore', 'FW_FILE': bad_file},
                        fake_root=root)
    check('I: 备份本身坏了就拒刷', '没通过真伪校验' in out)
    check('I: 拒刷时一个字节没动', not wrote(log) and bytes(tgt.flash) == REAL)

    out, tgt, log = run('flashwrite',
                        {'MODE': 'restore', 'FW_FILE': bad_file,
                         'FW_SKIP_SANITY': '1'}, fake_root=root)
    check('I2: 加了 FW_SKIP_SANITY=1 才放行', '写完了' in out)

    # J: pagetest 写下的图案，干净重连（peek，XIP 活着）之后要能认出来。
    #    这就是实机上要跑的那两步 —— pagetest 试写，peek 验收。
    out, tgt, log = run('flashwrite', {'MODE': 'pagetest'}, fake_root=root)
    check('J0: pagetest 自己说全对上', '字节全对上' in out)
    check('J0: 试写只动了那一颗扇区',
          bytes(tgt.flash[:TOTAL - SECTOR]) == REAL[:TOTAL - SECTOR])
    out2, _t2, log2 = run('flashwrite', {'MODE': 'peek'},
                          flash=bytes(tgt.flash), fake_root=root)
    check('J: peek 干净重连之后认得出试写图案',
          '跟试写图案一模一样' in out2)
    check('J: peek 全程只读', not wrote(log2))

    # K: 万一算法卸完窗口也回不来（xip_stuck），读到的是死值。
    #    这时候绝不许把"读不到"说成"写得不一样"。
    out, tgt, log = run('flashwrite', {'MODE': 'pagetest'}, fake_root=root,
                        xip_stuck=True)
    check('K: pagetest 读不到时明说是死值', '死值' in out)
    check('K: pagetest 不把它说成写失败', '写没进去' not in out)
    check('K: pagetest 指向 peek 做真验收', 'MODE=peek' in out)

    out, tgt, log = run('flashwrite', {'MODE': 'restore'}, fake_root=root,
                        xip_stuck=True)
    check('K: restore 全读不到也照常做完', '写完了' in out)
    check('K: restore 不误报"读回来跟备份不一样"',
          '读回来跟备份不一样' not in out)
    check('K: restore 结束时芯片内容 = 备份', bytes(tgt.flash) == REAL)

    # ---------------------------------------------------------------
    # L: MODE=app —— 只写自研 APP 那一段 + 最后一颗镜像信息扇区
    # ---------------------------------------------------------------
    def make_custom_image(app_len=0x4000, app_base=0x0100A000):
        """造假的自研镜像：APP 换掉、0x2000 那条信息跟着改，
        其余（bootloader / NVDS）一字节不动。"""
        img = bytearray(REAL)
        app = bytearray(app_len)
        struct.pack_into('<II', app, 0, 0x3001F000, app_base + 0x101)
        tag = b'ZK42V-EPD-CUSTOM-FW-B1'
        app[0x200:0x200 + len(tag)] = tag
        o = app_base - BASE
        # 扇区尾巴补 0xFF（flash 擦完就是 0xFF）
        img[o:o + app_len] = app
        tail = ((-app_len) % SECTOR)
        if tail:
            img[o + app_len:o + app_len + tail] = b'\xff' * tail
        off = 0x2000
        struct.pack_into('<I', img, off + 4, app_len)
        struct.pack_into('<I', img, off + 8, sum(app) & 0xFFFFFFFF)
        return bytes(img)

    custom = make_custom_image()
    custom_path = os.path.join(tempfile.mkdtemp(prefix='fwcustom-'), 'custom.bin')
    open(custom_path, 'wb').write(custom)

    out, tgt, log = run('flashwrite', {'MODE': 'app', 'FW_FILE': custom_path},
                        fake_root=root)
    erases = [s[1] for s in log if s[0] == 'erase']
    check('L: 报了自研 APP 的账面', '自洽，可以刷' in out)
    check('L: 只擦了 APP 那几颗 + 一颗镜像信息（共 5 颗）',
          len(erases) == 5)
    check('L: APP 从低地址往高地址写',
          erases[:4] == sorted(erases[:4]))
    check('L: 镜像信息那颗最后写', erases[-1] == BASE + 0x2000)
    check('L: bootloader 那段一个字节没动',
          bytes(tgt.flash[0x3000:0x3000 + 0x3B00]) == REAL[0x3000:0x3000 + 0x3B00])
    check('L: NVDS 没动', bytes(tgt.flash[0x7F000:]) == REAL[0x7F000:])
    check('L: APP 区 = 自研镜像那段',
          bytes(tgt.flash[0xA000:0xA000 + 0x4000]) == custom[0xA000:0xA000 + 0x4000])
    check('L: 0x2000 那条信息 = 自研镜像那条',
          bytes(tgt.flash[0x2000:0x2000 + 40]) == custom[0x2000:0x2000 + 40])
    check('L: 收尾报了写了几颗', '一共 5 颗扇区' in out)
    check('L: 收尾指向 appverify', 'MODE=appverify' in out)

    # L2: 镜像里校验和对不上 -> 必须拒刷、一个字节都不动
    bad_custom = bytearray(make_custom_image())
    bad_custom[0xA000 + 0x10] ^= 0xFF          # 改一个字节，校验和就对不上了
    bad_path = os.path.join(tempfile.mkdtemp(prefix='fwbadsig-'), 'bad.bin')
    open(bad_path, 'wb').write(bytes(bad_custom))
    out, tgt, log = run('flashwrite', {'MODE': 'app', 'FW_FILE': bad_path},
                        fake_root=root)
    check('L2: 校验和对不上就拒刷', '不能刷' in out or '没通过自检' in out)
    check('L2: 拒刷时一个字节没动', not wrote(log) and bytes(tgt.flash) == REAL)

    # L3: 复位向量不在 APP 段里 -> 也拒刷
    bad_rv = bytearray(make_custom_image())
    struct.pack_into('<I', bad_rv, 0xA000 + 4, 0x01003000)   # 指到 bootloader 去了
    # 重新算校验和，让它过了 checksum 这一关，专测复位向量那条判据
    _body = bytes(bad_rv[0xA000:0xA000 + 0x4000])
    struct.pack_into('<I', bad_rv, 0x2000 + 8, sum(_body) & 0xFFFFFFFF)
    rv_path = os.path.join(tempfile.mkdtemp(prefix='fwbadvr-'), 'bad.bin')
    open(rv_path, 'wb').write(bytes(bad_rv))
    out, tgt, log = run('flashwrite', {'MODE': 'app', 'FW_FILE': rv_path},
                        fake_root=root)
    check('L3: 复位向量不在 APP 段里也拒刷', '复位向量没落在' in out)
    check('L3: 拒刷时一个字节没动', not wrote(log))

    # L4: 中途写失败 -> 镜像信息那扇区不许写（保持旧账，bootloader 会走 DFU）
    out, tgt, log = run('flashwrite', {'MODE': 'app', 'FW_FILE': custom_path},
                        fail_erase_at=0x0100A000 + 2 * SECTOR, fake_root=root)
    check('L4: 中途失败会停下来并说明', '没写完' in out)
    check('L4: 失败时不写镜像信息扇区',
          (BASE + 0x2000) not in [s[1] for s in log if s[0] == 'erase'])
    check('L4: bootloader 还是好的',
          bytes(tgt.flash[0x3000:0x3000 + 0x3B00]) == REAL[0x3000:0x3000 + 0x3B00])

    # ---------------------------------------------------------------
    # M: MODE=appverify —— 只读，把芯片里的 APP 段跟本地镜像对账
    # ---------------------------------------------------------------
    out, tgt, log = run('flashwrite', {'MODE': 'appverify', 'FW_FILE': custom_path},
                        flash=custom, fake_root=root)
    check('M: 一致时报 APP_VERIFY_OK', 'APP_VERIFY_OK' in out)
    check('M: 报出芯片里那段自洽的校验和', '逐字节和' in out)
    check('M: 认得出标记字符串', '标记字符串 ZK42V-EPD-CUSTOM-FW 在' in out)
    check('M: appverify 全程只读', not wrote(log))

    flip_custom = bytearray(custom)
    flip_custom[0xA000 + 0x800] ^= 0x01
    out, tgt, log = run('flashwrite', {'MODE': 'appverify', 'FW_FILE': custom_path},
                        flash=bytes(flip_custom), fake_root=root)
    check('M2: 芯片里 APP 被改过时报 APP_VERIFY_FAIL', 'APP_VERIFY_FAIL' in out)
    check('M2: 指出第一个不一样的偏移', '第一个不一样的偏移' in out)
    check('M2: appverify 失败也不写', not wrote(log))

    # M3: 芯片里还是原厂 APP（没刷过）时，本地自研镜像跟它对不上
    out, tgt, log = run('flashwrite', {'MODE': 'appverify', 'FW_FILE': custom_path},
                        fake_root=root)
    check('M3: 没刷过时报 APP_VERIFY_FAIL', 'APP_VERIFY_FAIL' in out)
    check('M3: 提示写进去的不是这份镜像', '写进去的不是这份镜像' in out)

    # ---------------------------------------------------------------
    # N: status —— 读 0x3001F000 的调试状态块
    # ---------------------------------------------------------------
    def dbg_block(stage=8, flags=0x000C, heart=77):
        return {
            0x3001F000: 0x5A4B3401,      # magic
            0x3001F004: heart,
            0x3001F008: stage,
            0x3001F00C: flags,
            0x3001F010: 0x3,             # busy_levels: 高低都见过
            0x3001F014: 1234,            # 轮询次数
            0x3001F018: 0,               # 超时次数
            0x3001F01C: 0,               # gpio 错误
            0x3001F020: 120,             # ms_init
            0x3001F024: 700,             # ms_write
            0x3001F028: 2100,            # ms_refresh
            0x3001F02C: 0x00000001,      # build_id
            0x3001F030: 1,               # boot_count
            0x3001F034: 0,               # uds_seen
            0x3001F038: 0xB007C0DE,      # boot_magic
        }

    env_fast = {'SAMPLES': '2', 'SAMPLE_GAP_MS': '0'}

    # N: 从来没跑过（状态块里没有我们的 magic）
    out, tgt, log = run('status', env_fast, fake_root=root)
    check('N: 没印记时明说', '我们的固件没写过这里' in out)
    check('N: 报出 PC 和它落在哪', 'PC        : 0x0100C6A9' in out and '我们的 APP' in out)
    check('N: status 不写芯片', not wrote(log))

    # N2: 我们的固件在跑
    out, tgt, log = run('status', env_fast, fake_root=root, dbg=dbg_block())
    check('N2: 认出自研固件在跑', '我们的固件在跑' in out)
    check('N2: 报出 stage 和中文解释', 'stage = 8' in out and '刷新完' in out)
    check('N2: 报出心跳和 BUSY 电平', 'heart = ' in out and '见过低=是 见过高=是' in out)
    check('N2: status 不写芯片', not wrote(log))

    # N3: AON SOFTWARE_1 带着「超深睡唤醒」标志 —— 必须指名点出来
    out, tgt, log = run('status', env_fast, fake_root=root, dbg=dbg_block(),
                        aon_sw1=0xF175)
    check('N3: 抓出超深睡唤醒标志', '抓到重点了' in out and '0xF175' in out)
    check('N3: 说明会无限重启', '无限重启循环' in out)
    check('N3: 报出 AON 寄存器', 'SOFTWARE_1 = 0x0000F175' in out)

    # N4: PC 在 ROM 和 APP 之间跳 —— 判成芯片在反复复位
    out, tgt, log = run('status', env_fast, fake_root=root, dbg=dbg_block(stage=64),
                        pc_seq=[0x00001234, 0x0100C6A9])
    check('N4: 判成芯片在反复复位', '芯片在反复复位' in out)
    check('N4: 报出 main_init 只走到 64', 'stage = 64' in out)

    # N4b: boot_count 在采样期间往上涨 —— 也要判成反复复位
    out, tgt, log = run('status', env_fast, fake_root=root, dbg=dbg_block(),
                        boot_grow=True)
    check('N4b: 靠 boot_count 也能判定反复复位', '芯片重启了' in out)
    check('N4b: 报出 boot_count', 'boot_count' in out)

    # N5: 不许再把「读不到」直接说成「跑的还是原厂固件」（v1 的误判）
    check('N5: 文案里不再有 v1 那句误判', '跑的还是原厂固件' not in out)

    bad = 0
    for label, ok in CHECKS:
        print('  [%s] %s' % ('PASS' if ok else '**FAIL**', label))
        bad += 0 if ok else 1
    print('全部通过 ✅' if bad == 0 else '有 %d 项失败 ❌' % bad)
    return bad


sys.exit(1 if main() else 0)
