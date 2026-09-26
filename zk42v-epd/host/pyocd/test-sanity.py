# 离线验 sanity_file：真 dump 必须 SANITY_OK；假数据/坏数据必须 SANITY_FAIL
#
# 用法：  python3 test-sanity.py
#
# 不需要硬件：把 led-window-user.py 当普通 python 载入（stub 掉 pyOCD 注入的
# command 装饰器），拿 真 dump / 全 FF / 死值 / 改坏一字节 / 截断 五种文件喂它。
import os
import shutil
import struct
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'led-window-user.py')
REAL = os.path.join(HERE, 'zk42v-live-512k.bin')
WORK = tempfile.mkdtemp(prefix='sanity-work-')

def say(s=''):
    print(s)


def command(name, help=''):
    def deco(fn):
        return fn
    return deco


ns = {'__name__': 'ledwin', 'command': command, 'print': print}
src = open(SCRIPT, encoding='utf-8').read()
exec(compile(src, SCRIPT, 'exec'), ns)
sanity_file = ns['sanity_file']

FAIL = []


def check(label, ok):
    print(('  PASS  ' if ok else '  FAIL  ') + label)
    if not ok:
        FAIL.append(label)


def run(path, base):
    buf = StringIO()
    with redirect_stdout(buf):
        ok = sanity_file(path, base)
    return ok, buf.getvalue()


real = open(REAL, 'rb').read()

print('== 1. 真 dump（应当 SANITY_OK）==')
ok, out = run(REAL, 0x01000000)
check('真 dump -> SANITY_OK', ok)
check('真 dump: boot 头命中', '0x00003B00 0x00173927 0x01003000 0x01003000  ✓' in out)
check('真 dump: app 头命中', '0x0001F8F0 0x00CCE71B 0x0100A000 0x0100A000  ✓' in out)
check('真 dump: 校验和这条在', 'app 镜像校验和 0x00CCE71B == 头部 0x00CCE71B  ✓' in out)
check('真 dump: 字符串命中 5/5', '字符串命中 5/5' in out)
check('真 dump: 参照点 4/4', '参照点 4/4 命中' in out)
check('真 dump: 没有 SANITY_FAIL', 'SANITY_FAIL' not in out)

print()
print('== 2. 全 FF（读失败最典型的样子）==')
p = os.path.join(WORK, 'allff.bin')
open(p, 'wb').write(b'\xff' * len(real))
ok, out = run(p, 0x01000000)
check('全 FF -> SANITY_FAIL', not ok)
check('全 FF: 报出参照点对不上', '对不上' in out)

print()
print('== 3. 整片同一个死值（假数据，块读没自增）==')
p = os.path.join(WORK, 'dead.bin')
open(p, 'wb').write(struct.pack('<I', 0xF7C03404) * (len(real) // 4))
ok, out = run(p, 0x01000000)
check('死值 -> SANITY_FAIL', not ok)
check('死值: 报出整块没变化', '整块几乎没有变化' in out)

print()
print('== 4. 改坏 1 个字节（校验和这条必须抓住它）==')
p = os.path.join(WORK, 'bitflip.bin')
b = bytearray(real)
b[0xA000 + 0x1234] ^= 0x01
open(p, 'wb').write(bytes(b))
ok, out = run(p, 0x01000000)
check('改坏 1 字节 -> 校验和这条要 ✗', 'app 镜像校验和' in out and '!= 头部' in out)
check('改坏 1 字节 -> SANITY_FAIL', not ok)

print()
print('== 5. 只读了 128KB（app 镜像还没读进来）==')
p = os.path.join(WORK, 'trunc.bin')
open(p, 'wb').write(real[:0x20000])
ok, out = run(p, 0x01000000)
check('没读到 app 镜像 -> 跳过校验和（不当成失败）',
      'app 镜像不在这次读的范围内' in out)
check('没读到 app 镜像: 两个镜像头还是能验', '两个镜像头' not in out and 'boot 头 @0x0000' in out)
check('截断: 文件大小报的是 131072', '文件大小 131072 字节' in out)

print()
print('== 6. 别名窗口当 base（0x03000000）也要能验 ==')
ok, out = run(REAL, 0x03000000)
check('别名 base -> SANITY_OK（偏移换算一致）', ok)

print()
print('== 7. 空文件 / 不存在 ==')
p = os.path.join(WORK, 'empty.bin')
open(p, 'wb').write(b'')
ok, out = run(p, 0x01000000)
check('空文件 -> 不崩（SANITY_FAIL）', not ok)
ok, out = run(os.path.join(WORK, 'nope.bin'), 0x01000000)
check('文件不存在 -> 不崩（return False）', not ok)

print()
if FAIL:
    print('FAILED %d 项:' % len(FAIL))
    for f in FAIL:
        print('   -', f)
    sys.exit(1)
print('ALL PASS')
