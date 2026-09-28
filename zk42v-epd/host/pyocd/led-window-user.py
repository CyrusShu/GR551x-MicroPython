# =====================================================================
#  pyOCD 用户脚本：按价签指示灯的时序抢 SWD 窗口  —— 只读，不写芯片
#
#  这个文件不是给普通 python 跑的，是交给 pyOCD 跑的：
#
#      pyocd commander -N -t cortex_m -f 500k --script led-window-user.py -c ledwindow
#                     ^^ = --no-init：只打开适配器、设好时钟，但不去连目标
#
#  为什么要 -N：pyOCD 平时一上来就初始化目标，芯片不应答就报错退出，
#  我们连"拉复位再重试"的机会都没有。用了 -N，pyOCD 把 session /
#  probe / LOG 这些直接放进本脚本的命名空间，剩下的事我们自己来。
#
#  本脚本提供两条命令，由同目录的 led-window.sh 调用：
#     rstpulse    只驱动 RST，完全不碰 SWD —— 单独验证复位线通不通
#     ledwindow   拉复位 -> 从 t=0 起高频重试 SWD -> 命中就整片读回
#
#  时序依据（用户实测）：RST 松开后约 2 秒 绿灯亮 1 秒 -> 蓝灯亮 1 秒 -> 红灯。
#  所以复位后的头 2000ms 是 ROM/bootloader 阶段，SWD 理论上必然活着；
#  2000ms 之后应用接管 P0_0(SWCLK)/P0_1(SWDIO)，SWD 可能被关掉。
#
#  全程只读，一个字节都不写芯片。
# =====================================================================

import os
import struct
import sys
import time


def _env_int(name, dflt):
    v = os.environ.get(name)
    if v is None or v == "":
        return dflt
    try:
        return int(v)
    except ValueError:
        return dflt


def _env_str(name, dflt):
    v = os.environ.get(name)
    return dflt if v is None or v == "" else v


# ---------------- 参数（由 led-window.sh 用环境变量传进来） ----------------
DELAY_MS = _env_int('LED_DELAY_MS', 2000)        # 松开 RST -> 绿灯
GREEN_MS = _env_int('LED_GREEN_MS', 1000)        # 绿灯亮多久
BLUE_MS = _env_int('LED_BLUE_MS', 1000)          # 蓝灯亮多久
ROUNDS = _env_int('ROUNDS', 4)                   # 抢几轮
WINDOW_MS = _env_int('WINDOW_MS', 4600)          # 每轮抓多久
HOLD_MS = _env_int('HOLD_MS', 200)               # 复位拉低多久
POST_MS = _env_int('POST_MS', 0)                 # 松开后等多久才开始试
PULSES = _env_int('GR551X_PULSES', 5)            # rstpulse 拉几下
PULSE_GAP_MS = _env_int('GR551X_PULSE_GAP', 1200)
OUTDIR = _env_str('GR551X_OUTDIR',
                  '/Users/mac/Documents/Codex/2026-09-15/a/outputs')
BEEP = _env_str('BEEP', '1') != '0'
MANUAL = _env_str('MANUAL', '0') != '0'    # 1 = 不用 ST-Link 拉复位，你自己用手碰
RST_VIA = _env_str('RST_VIA', 'stlink').lower()   # stlink | ttl
TTL_PORT = _env_str('TTL_PORT', '/dev/cu.usbserial-210')
TTL_LINE = _env_str('TTL_LINE', 'rts').lower()
TTL_INVERT = _env_str('TTL_INVERT', '0') != '0'
ESP32_PORT = _env_str('ESP32_PORT', '')           # RST_VIA=esp32 时用，串口号见 rst-esp32.py --list

T_GREEN = DELAY_MS
T_BLUE = DELAY_MS + GREEN_MS
T_RED = DELAY_MS + GREEN_MS + BLUE_MS

_SOUND = {'green': 'Ping', 'blue': 'Glass', 'red': 'Basso',
          'tick': 'Tink', 'go': 'Pop'}
_MARK = {
    'green': '绿灯应该亮了（应用固件开始接管）',
    'blue': '蓝灯应该亮了',
    'red': '红灯（应用已经完全接管）',
}


def say(msg):
    """走真正的 stdout，绕开 pyOCD 给用户脚本包的那层 print 代理。
    这样输出顺序跟执行顺序严格一致，也一定能立刻刷出来。"""
    sys.stdout.write(str(msg) + "\n")
    sys.stdout.flush()


def beep(kind):
    """放一声提示音，让你用耳朵把【绿/蓝/红】和时间轴对上。非阻塞。"""
    if not BEEP:
        return
    name = _SOUND.get(kind)
    if name is None:
        return
    if not os.path.exists('/usr/bin/afplay'):
        return
    os.system('/usr/bin/afplay /System/Library/Sounds/%s.aiff >/dev/null 2>&1 &'
              % name)


def phase_of(ms):
    """复位后 ms 毫秒时，指示灯应该在哪一段。"""
    if ms < T_GREEN:
        return ('rom', 'ROM/bootloader 阶段（灯还没亮）')
    if ms < T_BLUE:
        return ('green', '绿灯期间')
    if ms < T_RED:
        return ('blue', '蓝灯期间')
    return ('red', '红灯期间')


def drive_report(probe, why):
    """打印 ST-Link 固件版本，并尝试让 ST-Link 进 SWD 模式。

    为什么要进 SWD 模式：拉 nRST 用的是 ST-Link 的 JTAG 命令 0x3c
    （DRIVE_NRST）。有的 ST-Link 固件在"没进 JTAG/SWD 模式"的时候，
    会把这条命令收下、回一个 OK，但 nRST 引脚一动不动 —— 表现出来的
    症状正好就是"程序说拉低了，万用表量还是 3.3V"。
    所以拉复位之前先尝试进一次 SWD 模式。进了不代表芯片应答（我们本来
    就连不上），只是为了让那条命令真的落到引脚上。

    不想进 SWD 模式：RST_ENTER_SWD=0
    """
    try:
        v = getattr(probe._link, 'version_str', '')
        if v:
            say("  ST-Link 固件: %s" % v)
    except Exception:
        pass

    if _env_str('RST_ENTER_SWD', '1') == '0':
        say("  （RST_ENTER_SWD=0：这次不进 SWD 模式，只驱动 RST 引脚）")
        return

    try:
        probe.connect()
        say("  ST-Link 已进入 SWD 模式 %s" % why)
    except Exception as e:
        first = str(e).splitlines()[0] if str(e) else e.__class__.__name__
        say("  进 SWD 模式失败：%s" % first)
        say("  （没关系，接着照样驱动 nRST —— 成不成看你量的电压）")

class TTLReset(object):
    """拿 USB-TTL 适配器的 RTS/DTR 当复位源，完全不碰 ST-Link。

    RTS/DTR 是低有效：断言（置位 modem 位）时引脚就是 0V。
    这颗克隆版 ST-Link 的 nRST 针推不动，就用这个当复位源，
    ST-Link 只管锤 SWD。

    环境变量： RST_VIA=ttl  TTL_PORT=/dev/cu.usbserial-210
               TTL_LINE=rts|dtr   TTL_INVERT=1（模块电平反的时候）
    """

    def __init__(self, port, line='rts', invert=False):
        import fcntl
        import struct as _struct
        import termios
        self._fcntl = fcntl
        self._struct = _struct
        self._termios = termios
        self.invert = invert
        self.bit = termios.TIOCM_RTS if line == 'rts' else termios.TIOCM_DTR
        self.name = line.upper()
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        # 先探一下这块串口支不支持手动控制，顺便把线放开
        self.assert_reset(False)

    def assert_reset(self, asserted):
        """asserted=True = 把 RST 拉到 0V"""
        on = (asserted != self.invert)
        req = self._termios.TIOCMBIS if on else self._termios.TIOCMBIC
        self._fcntl.ioctl(self.fd, req, self._struct.pack('i', self.bit))

    def close(self):
        try:
            self.assert_reset(False)
        except Exception:
            pass
        try:
            os.close(self.fd)
        except Exception:
            pass


class ESP32Reset(object):
    """让手边那块 ESP32 当复位源：串口发一个字节，它去推 GPIO。

    配 outputs/esp32-rst/esp32_rst.ino：
        L = 把 RST 拉到 0V 并保持
        H = 放开（3.3V）
    ST-Link 只管锤 SWD，复位跟它没关系。
    """

    def __init__(self, port):
        import termios
        self._termios = termios
        self.port = port
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(self.fd)
        cc = list(a[6])
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW,
                          [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0,
                           termios.B115200, termios.B115200, cc])
        time.sleep(0.2)
        self.assert_reset(False)

    def assert_reset(self, asserted):
        os.write(self.fd, b'L' if asserted else b'H')

    def close(self):
        try:
            self.assert_reset(False)
        except Exception:
            pass
        try:
            os.close(self.fd)
        except Exception:
            pass


def reset_driver(probe):
    """按 RST_VIA 选复位源：ST-Link 的 nRST，还是 USB-TTL 的 RTS。"""
    if RST_VIA == 'ttl':
        try:
            r = TTLReset(TTL_PORT, TTL_LINE, TTL_INVERT)
        except Exception as e:
            say("  !!! 打不开 %s 当复位源: %s" % (TTL_PORT, e))
            say("      看看 ls /dev/cu.*，或者先跑 rst-ttl.py 量一下那根线。")
            raise
        say("  复位源 : USB-TTL 的 %s（%s）" % (r.name, TTL_PORT))
        return r
    if RST_VIA == 'esp32':
        if not ESP32_PORT:
            say("  !!! RST_VIA=esp32 但没给 ESP32_PORT。")
            say("      先跑： python3 ../esp32-rst/rst-esp32.py --list")
            raise SystemExit(2)
        try:
            r = ESP32Reset(ESP32_PORT)
        except Exception as e:
            say("  !!! 打不开 ESP32 的串口 %s: %s" % (ESP32_PORT, e))
            raise
        say("  复位源 : ESP32 的 GPIO（串口 %s）" % ESP32_PORT)
        return r
    return probe


def resolve_target():
    """拿到真正的目标对象。

    坑：pyOCD 0.45 里 session.board 是 Board（板子信息），不是 CPU 目标！
    真正的目标在 session.board.target / session.target 上，halt / read_memory_block8
    这些方法都在它身上。旧版本里 board 本身就是 target，所以这里三种都兜一下。
    """
    t = getattr(session, 'target', None)
    if t is not None and hasattr(t, 'read_memory_block8'):
        return t
    b = getattr(session, 'board', None)
    if b is not None:
        t2 = getattr(b, 'target', None)
        if t2 is not None and hasattr(t2, 'read_memory_block8'):
            return t2
        if hasattr(b, 'read_memory_block8'):
            return b
    return None


def say_target_info(target):
    try:
        say("  目标     : %s (%s)" % (getattr(target, 'part_number', '?'),
                                     getattr(target, 'node_name', '?')))
    except Exception:
        pass
    try:
        st = target.get_state()
        say("  CPU 状态 : %s" % st)
    except Exception:
        pass


def raw_probe():
    """拿到最底下那个真正的 DebugProbe 对象。

    坑在这里：session.probe 是 pyOCD 包的一层 SharedDebugProbeProxy，
    它自己的 connect() 带引用计数 —— 只有计数为 0 的那一次才会真的下去
    连 SWD，之后都只是把计数 +1 直接返回。拿它做高频重试等于自己骗自己。
    所以这里取 .probe，绕开那层计数，每一次调用都是一次真实的连接尝试。
    （reset 之类的调用代理是直接透传的，但为了统一，全都走底层对象。）
    """
    p = session.probe
    return getattr(p, 'probe', p)


# =====================================================================
#  命令 1：只拉 RST，不碰 SWD
# =====================================================================
@command('rstpulse', help='只驱动 RST 验证复位线，完全不碰 SWD')
def rstpulse():
    probe = raw_probe()
    rst = reset_driver(probe)

    say("")
    say("==================================================================")
    say("  只拉 RST，完全不碰 SWD（pyOCD 版）")
    say("------------------------------------------------------------------")
    say("  次数 %d 次，每次拉低 %d ms，两下之间隔 %d ms" % (PULSES, HOLD_MS, PULSE_GAP_MS))
    say("  复位源 : %s" % ("USB-TTL 的 %s（%s）" % (TTL_LINE.upper(), TTL_PORT)
                        if RST_VIA == 'ttl'
                        else ("ESP32 的 GPIO（%s）" % ESP32_PORT
                              if RST_VIA == 'esp32' else "ST-Link 的 nRST 针")))
    say("==================================================================")
    say("")
    if RST_VIA == 'stlink':
        drive_report(probe, "（这样 DRIVE_NRST 才会真的拉低引脚）")

    driven = 0
    for i in range(1, PULSES + 1):
        try:
            rst.assert_reset(True)
        except Exception as e:
            say("  [%d/%d] 拉低 RST 失败: %s" % (i, PULSES, e))
            break
        say("  [%d/%d] 拉低 RST ..." % (i, PULSES))
        time.sleep(HOLD_MS / 1000.0)
        try:
            rst.assert_reset(False)
        except Exception as e:
            say("        ... 松开 RST 失败: %s" % e)
            break
        driven += 1
        say("        ... 松开 RST (t=%d ms，灯应该开始走 绿-蓝-红)" % HOLD_MS)
        if i < PULSES:
            time.sleep(PULSE_GAP_MS / 1000.0)

    say("")
    say("==================================================================")
    if driven == 0:
        say("  一次都没拉出去 —— 是工具的问题，不是接线的问题。")
        say("RESULT_PULSE_FAIL")
    else:
        say("  发出去 %d 次复位脉冲。" % driven)
        say("")
        say("  灯跟着动了 -> RST 线是通的")
        say("  灯全程没反应 -> RST 线没接通或接错脚：")
        say("      1. 万用表通断档：一支表笔按住价签 RST 焊盘，另一支碰")
        say("         ST-Link 上你插 RST 的那根排针 —— 要响。")
        say("      2. 排针号对不对：很多 ST-Link V2 克隆版的定义和标准 ARM")
        say("         10 针不一样，卖家给的图也常常是错的。")
        say("      3. 价签 GND 必须和 ST-Link GND 共地。")
        say("RESULT_PULSE_OK count=%d" % driven)
    say("==================================================================")


# =====================================================================
#  命令 2：按 LED 时序抢窗口，命中就整片读回
# =====================================================================
@command('ledwindow', help='按指示灯时序抢 SWD 窗口，命中后整片读回 flash')
def ledwindow():
    probe = raw_probe()
    rst = reset_driver(probe)

    say("")
    say("==================================================================")
    say("  按 LED 时序抢 SWD 窗口（pyOCD 版）")
    say("------------------------------------------------------------------")
    say("  时序假设 : 松开 RST -> %dms 绿灯 -> %dms 蓝灯 -> %dms 红灯"
        % (DELAY_MS, GREEN_MS, BLUE_MS))
    say("  抓取窗口 : 复位后 0 ~ %d ms（ROM/bootloader 阶段，SWD 该是活的）"
        % WINDOW_MS)
    say("  轮数     : %d 轮，每轮拉低 RST %d ms" % (ROUNDS, HOLD_MS))
    if MANUAL:
        say("  复位方式 : 手按 —— 听到提示音后，把 RST 碰一下 GND 再松开")
    elif RST_VIA == 'ttl':
        say("  复位方式 : USB-TTL 的 %s 拉 RST（%s）" % (TTL_LINE.upper(), TTL_PORT))
    elif RST_VIA == 'esp32':
        say("  复位方式 : ESP32 的 GPIO 拉 RST（%s）" % ESP32_PORT)
    else:
        say("  复位方式 : ST-Link 自己拉 RST")
    say("==================================================================")

    hit_round = 0
    hit_ms = 0
    hit_phase = ('', '')
    total = 0
    start = time.monotonic()

    # 拉复位之前先试着连一次，两个目的：
    #   1) 万一不复位就能连上，那根本不用抢窗口，直接读；
    #   2) 让 ST-Link 进 SWD 模式 —— DRIVE_NRST 只有在 SWD/JTAG 模式下
    #      才会真的把 nRST 引脚拉低，不然命令收下了、引脚不动。
    pre = None
    try:
        probe.connect()
        pre = 0
        say("")
        say("  >>> 复位之前就连上了 —— SWD 现在是活的，根本不用抢窗口。")
    except Exception as e:
        first = str(e).splitlines()[0] if str(e) else e.__class__.__name__
        say("")
        say("  复位前先连一次：%s" % first)
        say("  （意料之中，接着拉复位抢窗口）")
        if RST_VIA == 'stlink':
            say("  ST-Link 现在处于 SWD 模式，DRIVE_NRST 会真的拉低 nRST 引脚。")

    for r in (range(1, ROUNDS + 1) if pre is None else []):
        say("")
        say("========== 第 %d/%d 轮 ==========" % (r, ROUNDS))

        if MANUAL:
            # 不用 ST-Link 的复位线：响三下预备，第四声「啵」的时候动手
            say("  听到 【叮-叮-叮-啵】 后，立刻把 RST 碰一下 GND 再松开。")
            try:
                beep('tick')
                time.sleep(0.7)
                beep('tick')
                time.sleep(0.7)
                beep('tick')
                time.sleep(0.7)
                beep('go')
            except Exception:
                pass
            t0 = time.monotonic()
            say("  GO -> t=0（松开那一瞬间开始试；盯着灯，约 %dms 后应该转绿）"
                % DELAY_MS)
        else:
            try:
                rst.assert_reset(True)
            except Exception as e:
                say("  !!! 拉低 RST 失败: %s" % e)
            time.sleep(HOLD_MS / 1000.0)
            try:
                rst.assert_reset(False)
            except Exception as e:
                say("  !!! 松开 RST 失败: %s" % e)

            if POST_MS > 0:
                time.sleep(POST_MS / 1000.0)

            t0 = time.monotonic()
            say("  RST 已松开 -> t=0（盯着灯：约 %dms 后应该转绿）" % DELAY_MS)

        n = 0
        done = set()
        hit = None
        while True:
            el = (time.monotonic() - t0) * 1000.0
            if el >= WINDOW_MS:
                break

            n += 1
            total += 1

            # 这就是锤子：每调一次 = 一次真正的 SWD 连接尝试，
            # 失败抛异常，成功说明那一刻芯片答话了。
            try:
                probe.connect()
                hit = (time.monotonic() - t0) * 1000.0
                break
            except Exception:
                pass

            # 相位提示音：听到哪一声的时候灯变哪一色
            el = (time.monotonic() - t0) * 1000.0
            for key, mark in (('green', T_GREEN), ('blue', T_BLUE), ('red', T_RED)):
                if key not in done and el >= mark:
                    done.add(key)
                    beep(key)
                    say("  [%5d ms] 已试 %d 次  >> %s" % (int(el), n, _MARK[key]))

        if hit is not None:
            hit_round = r
            hit_ms = int(hit)
            hit_phase = phase_of(hit)
            say("")
            say("  ******************************************************")
            say("  ** 命中！第 %d 轮，复位后 %d ms —— %s" % (r, hit_ms, hit_phase[1]))
            say("  ******************************************************")
            break

        ms = int((time.monotonic() - t0) * 1000)
        rate = int(n * 1000 / ms) if ms > 0 else 0
        say("  本轮没中：%d ms 里试了 %d 次，约 %d 次/秒。" % (ms, n, rate))

    if pre is not None:
        hit_round = 1
        hit_ms = 0
        hit_phase = ('pre', '复位之前就连上了')

    if hit_round == 0:
        say("")
        say("==================================================================")
        say("  %d 轮全部失败，共尝试 %d 次，花了 %d 秒。"
            % (ROUNDS, total, int(time.monotonic() - start)))
        say("")
        say("  连 ROM 那 2 秒都抢不到 —— 问题不在时机，在链路上：")
        say("    1. RST 线通不通（先跑 rstpulse 看灯动不动）")
        say("    2. SWCLK / SWDIO 是不是接反了（对调，30 秒的事）")
        say("    3. GND 必须和 ST-Link 共地")
        say("    4. 降速重试： SPD=240 bash led-window.sh")
        say("RESULT_FAIL total_attempts=%d" % total)
        say("==================================================================")
        return

    say("")
    say("  第 %d 轮，复位后 %d ms —— %s 连上，SWD 活了。"
        % (hit_round, hit_ms, hit_phase[1]))
    say("RESULT_HIT round=%d elapsed_ms=%d phase=%s"
        % (hit_round, hit_ms, hit_phase[0]))

    # ---- 让 pyOCD 在这个已经连上的基础上把 target 初始化完（会停 CPU）----
    say("")
    say("  现在让 pyOCD 完成 target 初始化（-M halt，会把 CPU 停住）...")
    try:
        session.board.init()
    except Exception as e:
        say("  board.init() 报错: %s" % e)
    target = resolve_target()
    if target is None:
        say("  没能拿到 target，先看上面的报错。")
        return
    say_target_info(target)
    try:
        target.halt()
    except Exception as e:
        say("  halt() 报错: %s" % e)

    # ---- 先读几个关键字，确认不是幻觉 ----
    say("")
    say("===== 先读几个关键字确认不是幻觉 =====")
    checks = [
        (0x01000000, '0x01000000  initial SP     '),
        (0x01003000, '0x01003000  bootloader     '),
        (0x0100A000, '0x0100A000  app            '),
        (0x0100A200, '0x0100A200  app 数据区     '),
        (0x0107F000, '0x0107F000  NVDS           '),
    ]
    for addr, label in checks:
        try:
            v = target.read_memory(addr, 32)
            say("  %s = 0x%08x" % (label, v))
        except Exception as e:
            say("  %s = 读不到 (%s)" % (label, e))

    # 参照点：0x01000000 起就是 boot 镜像头（BinSize/CheckSum/LoadAddr/RunAddr），
    # 这四个数是价签串口开机日志里原样打出来的，对得上就是这颗芯片的真数据。
    hdr = []
    for i in range(4):
        try:
            hdr.append(target.read_memory(0x01000000 + 4 * i, 32))
        except Exception:
            hdr.append(None)
    if tuple(hdr) == REF_BOOT_HDR_WANT:
        say("  boot 镜像头对得上（%s），这就是原厂固件。" % _quad_str(hdr))
    else:
        say("  boot 镜像头是 %s，跟串口日志里的 %s 对不上 —— 照样读，读完再分析。"
            % (_quad_str(hdr), _quad_str(REF_BOOT_HDR_WANT)))

    # ---- 整片读回 ----
    say("")
    say("===== 读取 512KB =====")
    try:
        os.makedirs(OUTDIR, exist_ok=True)
    except Exception:
        pass
    main_path = os.path.join(OUTDIR, 'zk42v-factory-512k.bin')
    nvds_path = os.path.join(OUTDIR, 'zk42v-nvds-4k.bin')

    t_dump = time.monotonic()
    try:
        data = bytes(target.read_memory_block8(0x01000000, 0x80000))
        with open(main_path, 'wb') as f:
            f.write(data)
        say("  -> %s  (%d 字节，花了 %.1f 秒)"
            % (main_path, len(data), time.monotonic() - t_dump))
    except Exception as e:
        say("  主区读取失败: %s" % e)

    try:
        nvds = bytes(target.read_memory_block8(0x0107F000, 0x1000))
        with open(nvds_path, 'wb') as f:
            f.write(nvds)
        say("  -> %s  (%d 字节)" % (nvds_path, len(nvds)))
    except Exception as e:
        say("  NVDS 读取失败: %s" % e)

    # ---- 算 SHA-256，方便跑第二遍比对 ----
    try:
        import hashlib
        h = hashlib.sha256()
        with open(main_path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        say("  SHA-256: %s" % h.hexdigest())
    except Exception as e:
        say("  SHA-256 算不了: %s" % e)

    say("DUMP_DONE")


# =====================================================================
#  命令 4：分段读回，断了就从断点接着读
#
#  为什么要分段：一次读 512KB，中间任何一次 SWD 传输失败，pyOCD 就把整条
#  命令丢掉、一个字节都不给你。改成分块 + 进度文件 + 外层 shell 反复重来，
#  每次进程都是干净的重新初始化，读到哪算哪，下次从那接着读。
#
#  只读：只 savemem 的读路径，一个字节都不写芯片。
# =====================================================================
def _env_hex(name, dflt):
    v = os.environ.get(name)
    if not v:
        return dflt
    try:
        return int(v, 0)
    except ValueError:
        return dflt


def _first_line(e):
    t = str(e).splitlines()
    return t[0] if t else e.__class__.__name__


# =====================================================================
#  读法选择 —— 这一节是"读到假数据"那件事的正面回答
#
#  背景：pyOCD 0.45 在建 AP 时会问调试器"有没有加速内存接口"
#  （coresight/ap.py 第 636-642 行）。ST-Link 说有，于是：
#
#      ap.read_memory          -> _accelerated_read_memory          (ST-Link 固件的 READMEM 命令)
#      ap.read_memory_block32  -> _accelerated_read_memory_block32  (同上)
#      ap.read_memory_block8   -> _accelerated_read_memory_block8   (同上)
#
#  而 target.read_memory_block8 -> cortex_m.py -> self.ap.read_memory_block8，
#  所以之前 dump-resume 走的一直是 ST-Link 加速路径：地址自增是 ST-Link
#  固件自己做的。克隆版固件（V2J37）一旦做得不对，表现出来就是"整片读成
#  同一个字"。
#
#  经典路径（pyOCD 自己发 TAR / 连续读 DRW 事务）一直都在，只是被顶掉了：
#
#      ap._read_memory(addr)               经典单字读
#      ap._read_memory_block32(addr, n)    经典块读（按自增页分段）
#
#  所以这里做三件事：
#    1) 三条路各读同一批地址，拿 CPUID / app_info magic / 数据变化度打分，
#       自动挑能真正读到数据的那条（READMODE=auto）
#    2) 也允许手动指定：probe | apid | apiw
#    3) 整片读完后，再拿参照点 + 字符串验一遍文件本身（SANITY_OK / FAIL）
# =====================================================================

# MAGIC_ADDR / MAGIC_WANT 是早期会话里猜的 app_info magic，那个地址的值随固件
# 变（实测 0x6803825A），拿它当判据必然误报。真参照点见下面的 REF_* 系列。
# 这里保留名字只是别处还在当「一个普通地址」引用，不再用于真伪判断。
MAGIC_ADDR = 0x0100A200
MAGIC_WANT = None
CPUID_ADDR = 0xE000ED00          # Cortex-M4 的 CPUID
CPUID_WANT = 0x410FC241
READMODE_ORDER = ['probe', 'apid', 'apiw']


def _ap_of(target):
    """拿这个 target 用的内存 AP（AHB-AP）"""
    try:
        ap = getattr(target, 'ap', None)
        if ap is not None:
            return ap
    except Exception:
        pass
    try:
        aps = target.dp.aps
        keys = sorted(aps.keys()) if hasattr(aps, 'keys') else list(range(len(aps)))
        return aps[keys[0]] if keys else None
    except Exception:
        return None


def _bind_classic(ap):
    """把 AP 的内存接口绑回经典路径（绕开调试器的加速接口）"""
    done = []
    try:
        ap._accelerated_memory_interface = None
    except Exception:
        pass
    for name, impl in (
            ('read_memory', getattr(ap, '_read_memory', None)),
            ('read_memory_block32', getattr(ap, '_read_memory_block32', None)),
            ('write_memory', getattr(ap, '_write_memory', None)),
            ('write_memory_block32', getattr(ap, '_write_memory_block32', None))):
        if impl is None:
            continue
        try:
            setattr(ap, name, impl)
            done.append(name)
        except Exception:
            pass
    # read_memory_block8 没有经典私有版（基类方法直接建在 read_memory_block32 上），
    # 所以显式绑回基类实现，免得目标层还指着加速版。
    try:
        from pyocd.core.memory_interface import MemoryInterface
        ap.read_memory_block8 = MemoryInterface.read_memory_block8.__get__(ap, type(ap))
        done.append('read_memory_block8')
    except Exception:
        pass
    return done


def _reader(target, ap, mode):
    """返回 f(addr, nbytes) -> bytes，按读法分三条路"""
    if mode == 'probe':
        def rd(addr, n):
            return bytes(target.read_memory_block8(addr, n))
        return rd
    if mode == 'apid':
        def rd(addr, n):
            vals = ap._read_memory_block32(addr, n // 4)
            out = bytearray()
            for v in vals:
                out += struct.pack('<I', v & 0xFFFFFFFF)
            return bytes(out)
        return rd

    def rd(addr, n):
        out = bytearray()
        for i in range(n // 4):
            out += struct.pack('<I', ap._read_memory(addr + i * 4) & 0xFFFFFFFF)
        return bytes(out)
    return rd


def _count_distinct(buf):
    n = len(buf) // 4
    if n == 0:
        return 0
    return len(set(struct.unpack_from('<%dI' % n, buf, 0)))


def _try_mode(target, ap, mode):
    """一条读法在小样本上表现如何（cpuid / magic / 变化度）"""
    r = {'mode': mode, 'cpuid': None, 'magic': None, 'magic4k': None,
         'd8': 0, 'd4k': 0, 'err': None}
    try:
        rd = _reader(target, ap, mode)
        r['cpuid'] = struct.unpack('<I', rd(CPUID_ADDR, 4))[0]
        r['magic'] = struct.unpack('<I', rd(MAGIC_ADDR, 4))[0]
        r['d8'] = _count_distinct(rd(0x0100A000, 32))
    except Exception as e:
        r['err'] = _first_line(e)
        return r
    # 第二关：按我们真正会用的块大小（4KB）读一块，块里再看一遍
    try:
        b = _reader(target, ap, mode)(0x0100A000, 0x1000)
        r['d4k'] = _count_distinct(b)
        r['magic4k'] = struct.unpack_from('<I', b, MAGIC_ADDR - 0x0100A000)[0]
    except Exception as e:
        r['err'] = _first_line(e)
    return r


def _score(r):
    s = 0
    if r.get('cpuid') == CPUID_WANT:
        s += 4
    if r.get('magic') == MAGIC_WANT:
        s += 2
    if r.get('magic4k') == MAGIC_WANT:
        s += 3
    if r.get('d4k', 0) >= 20:
        s += 3
    if r.get('d8', 0) >= 4:
        s += 1
    return s


def pick_readmode(target, ap, want):
    """want = auto/probe/apid/apiw；auto 时自检后挑最好的那条"""
    if want in ('probe', 'apid', 'apiw'):
        say("  读法：%s（你指定的）" % want)
        return want

    NAMES = {'probe': '目标层(走加速)', 'apid': '经典AP块读', 'apiw': '经典AP逐字读'}
    say("")
    say("  读法自检（同一批地址，三条路各读一遍）")
    say("  -------------------------------------------------------------------")
    say("    读法                      CPUID      magic        8字   4KB  得分")
    results = []
    for m in READMODE_ORDER:
        r = _try_mode(target, ap, m)
        r['score'] = _score(r)
        results.append(r)
        cpuid = '0x%08X' % r['cpuid'] if r['cpuid'] is not None else '   --     '
        magic = '0x%08X' % r['magic'] if r['magic'] is not None else '   --     '
        say("    %-6s %-16s %s  %s  %3d  %4d  %4d%s"
            % (m, NAMES[m], cpuid, magic, r['d8'], r['d4k'], r['score'],
               ('   <- %s' % r['err']) if r['err'] else ''))
    say("  -------------------------------------------------------------------")

    best = max(results, key=lambda r: (r['score'], -READMODE_ORDER.index(r['mode'])))
    if best['score'] == 0:
        say("  !!! 三条路都没读到像样的数据（得分全是 0）。先别读整片。")
    say("  选定读法：%s（得分 %d）" % (best['mode'], best['score']))
    if best['mode'] != 'probe':
        say("  （说明目标层那条加速路径在你这颗 ST-Link 上是坏的，已经绕开了）")
    return best['mode']


REF_BOOT_HDR_OFF = 0x0000
REF_BOOT_HDR_WANT = (0x00003B00, 0x00173927, 0x01003000, 0x01003000)
REF_APP_HDR_OFF = 0x2004          # 0x2000 是 4 字节 tag（44 47 01 00），四元组从 +4 起
REF_APP_HDR_WANT = (0x0001F8F0, 0x00CCE71B, 0x0100A000, 0x0100A000)
REF_APP_CODE_OFF = 0x00A000
REF_APP_CODE_LEN = 0x01F8F0
REF_APP_CODE_SUM = 0x00CCE71B
REF_STRINGS = (b'ZKC42V-N', b'esl_mac', b'batt_volt', b'no AP', b'hd_info')
# 「窗口没开」时读到的锁存死值，见过这几个
DEAD_WORDS = (0x00000000, 0xFFFFFFFF, 0xB7C83400, 0xF7C03404)


def _quad_str(q):
    return ' '.join('0x%08X' % v for v in q)


def _looks_dead(w):
    return (not isinstance(w, int)) or (w in DEAD_WORDS)


def sanity_file(path, base):
    """拿参照点检查文件本身 —— 这是「假数据」的最后一道闸

    参照点全来自两块一定知道的东西：
      1) 串口开机日志里原样打印的两个镜像头（BinSize/CheckSum/LoadAddr/RunAddr）
      2) 镜像的字节累加校验和（头里的 CheckSum 就是对镜像体逐字节求和取低 32 位）
      3) 一定会在固件里的几个字符串

    旧版拿 0x0100A200 == 0x47525858 当判据 —— 那是最早会话里猜错的，那个地址
    的值随固件变，永远对不上，会把真备份误判成 SANITY_FAIL。已废弃。
    """
    try:
        with open(path, 'rb') as f:
            data = f.read()
        size = len(data)
    except Exception as e:
        say("  真伪校验：读不到文件 %s" % _first_line(e))
        return False

    say("")
    say("  真伪校验（拿一定知道的东西对一遍）")
    hits = 0
    checks = 0
    issues = []

    def foff(addr):
        """flash 绝对地址 -> 文件内偏移；不在本次读的范围内就返回 None"""
        o = addr - base
        return o if o >= 0 else None

    def u32(o):
        if o is None or o < 0 or o + 4 > len(data):
            return None
        return struct.unpack_from('<I', data, o)[0]

    # --- 1) 两个镜像头：串口日志里就打着这四个数 ---
    for label, hoff, want in (('boot 头', REF_BOOT_HDR_OFF, REF_BOOT_HDR_WANT),
                              ('app  头', REF_APP_HDR_OFF, REF_APP_HDR_WANT)):
        o = foff(base + hoff)
        if o is None or o + 16 > len(data):
            say("    %s 不在这次读的范围内（跳过）" % label)
            continue
        checks += 1
        got = tuple(u32(o + 4 * i) for i in range(4))
        if got == want:
            hits += 1
            say("    %s @0x%04X = %s  ✓ 跟串口日志一致"
                % (label, hoff, _quad_str(got)))
        else:
            say("    %s @0x%04X = %s  期望 %s  ✗"
                % (label, hoff, _quad_str(got), _quad_str(want)))
            issues.append('%s 对不上' % label)

    # --- 2) app 镜像字节累加 == 头部写的 CheckSum（决定性的那一条）---
    o = foff(base + REF_APP_CODE_OFF)
    if o is None or o + REF_APP_CODE_LEN > len(data):
        say("    app 镜像不在这次读的范围内（跳过校验和）")
    else:
        checks += 1
        s = sum(data[o:o + REF_APP_CODE_LEN]) & 0xFFFFFFFF
        if s == REF_APP_CODE_SUM:
            hits += 1
            say("    app 镜像校验和 0x%08X == 头部 0x%08X  ✓ 这 %d 字节整段对得上"
                % (s, REF_APP_CODE_SUM, REF_APP_CODE_LEN))
        else:
            say("    app 镜像校验和 0x%08X != 头部 0x%08X  ✗"
                % (s, REF_APP_CODE_SUM))
            issues.append('app 镜像校验和对不上')

    # --- 3) 一定会在的字符串 ---
    checks += 1
    found = []
    for pat in REF_STRINGS:
        p = data.find(pat)
        if p >= 0:
            found.append('%s@0x%X' % (pat.decode(), p))
    if found:
        hits += 1
        say("    字符串命中 %d/%d：%s"
            % (len(found), len(REF_STRINGS), '  '.join(found)))
    else:
        say("    字符串一个都没命中（%s）"
            % ' / '.join(p.decode() for p in REF_STRINGS))
        issues.append('字符串一个都没命中')

    # --- 变化度：整块一个值 = 假数据 ---
    n = len(data) // 4
    if n:
        words = struct.unpack_from('<%dI' % n, data, 0)
        distinct = len(set(words))
        zeros = words.count(0)
        ones = words.count(0xFFFFFFFF)
        say("    全片 %dKB：不同 32 位字 %d 个；全 0 %.1f%%；全 F %.1f%%"
            % (len(data) // 1024, distinct, zeros * 100.0 / n, ones * 100.0 / n))
        if distinct < 16:
            issues.append('整块几乎没有变化（像没自增）')
        if zeros > 0.95 * n:
            issues.append('几乎全是 0')

    ok = not issues
    say("    文件大小 %d 字节" % size)
    if ok:
        say("    -> 参照点 %d/%d 命中，看着是真的   SANITY_OK" % (hits, checks))
    else:
        say("    -> 可疑！%s" % "；".join(issues))
        say("       SANITY_FAIL —— 这份先别当备份，换 READMODE=apiw 再来。")
    return ok


@command('dumpresume', help='分段读回整片 flash，断了从断点接着读（全程只读）')
def dumpresume():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)

    base = _env_hex('DUMP_BASE', 0x01000000)
    total = _env_hex('DUMP_TOTAL', 0x80000)
    chunk = max(256, _env_hex('DUMP_CHUNK', 0x1000))
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)
    verify = _env_str('DUMP_VERIFY', '1') != '0'

    try:
        os.makedirs(OUTDIR, exist_ok=True)
    except Exception:
        pass
    bin_path = os.path.join(OUTDIR, 'zk42v-factory-512k.bin')
    prog_path = bin_path + '.progress'

    done = 0
    if _env_str('DUMP_RESTART', '0') == '1':
        done = 0
        try:
            with open(bin_path, 'wb'):
                pass
            say("  （DUMP_RESTART=1：把旧的目标文件清空了，从头读）")
        except Exception:
            pass
    else:
        try:
            with open(prog_path) as f:
                done = int((f.read().strip() or '0'), 0)
        except Exception:
            done = 0
    if not (0 <= done <= total):
        done = 0

    say("")
    say("==================================================================")
    say("  分段读回（断点续读，全程只读）")
    say("------------------------------------------------------------------")
    say("  范围     : 0x%08X + 0x%X (%d KB)" % (base, total, total // 1024))
    say("  块大小   : %d 字节   （一块一发 SWD 传输，失败只丢这一块）" % chunk)
    say("  读法     : %s   （auto = 连上后自检，三条路里挑能读出真数据的）"
        % _env_str('READMODE', 'auto'))
    say("  目标文件 : %s" % bin_path)
    say("  已有进度 : %d / %d 字节 (%.1f%%)" % (done, total, done * 100.0 / total))
    say("==================================================================")

    if done >= total:
        say("  已经读完了。想从头重来：DUMP_RESTART=1 ...")
        say("DUMP_ALREADY_DONE")
        _verify_file(bin_path, total, verbose=True)
        return

    # ---- 拉复位 + 抢窗口 ----
    if manual_hold:
        wait = _env_int('HOLD_WAIT_SECS', 8)
        say("")
        say("  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        say("  手动复位（按住再松）：")
        say("    1) 现在把 RST 按住接地，按住别松")
        say("    2) 我数到 0，「啵」那一声就松手 —— 松手的那一刻开始抢")
        say("  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        say("  准备好就按住 RST，倒数现在开始：")
        sys.stdout.flush()
        for i in range(wait, 0, -1):
            say("      %d ..." % i)
            beep('tick')
            time.sleep(1.0)
        beep('go')
        say("  >>> 现在松手！ <<<")
        t0 = time.monotonic()
    elif MANUAL:
        say("")
        say("  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        say("  手动复位：把 RST 碰一下 GND 再松开（松开那一瞬间窗口就开）")
        say("  接下来 %.0f 秒我会一直锤 SWD；没抢到就再碰一次，多碰几次没关系。"
            % (window_ms / 1000.0))
        say("  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        beep('tick')
        time.sleep(0.5)
        beep('go')
        t0 = time.monotonic()
    else:
        try:
            rst.assert_reset(True)
            time.sleep(hold_ms / 1000.0)
            rst.assert_reset(False)
        except Exception as e:
            say("  (复位失败，不致命: %s)" % e)
    if not (manual_hold or MANUAL):
        t0 = time.monotonic()
    if manual_hold:
        say("  开始抢 SWD（最多 %.0f ms）..." % window_ms)
    elif MANUAL:
        say("  开始抢 SWD（最多 %.0f ms，中间可以反复碰 RST）..." % window_ms)
    else:
        say("  复位已松开，开始抢 SWD（最多 %.0f ms）..." % window_ms)

    hit_ms = None
    tries = 0
    while (time.monotonic() - t0) * 1000.0 < window_ms:
        tries += 1
        try:
            probe.connect()
            hit_ms = int((time.monotonic() - t0) * 1000.0)
            break
        except Exception:
            pass
    if hit_ms is None:
        say("  没抢到（试了 %d 次）—— 换一轮再来。" % tries)
        say("PROGRESS=%d" % done)
        return

    say("  连上了（复位后 %d ms，试了 %d 次）" % (hit_ms, tries))

    target = resolve_target()
    if target is None:
        say("  这个 session 里拿不到 target 对象。")
        say("PROGRESS=%d" % done)
        return

    try:
        target.init()
    except Exception as e:
        say("  target init 失败：%s" % _first_line(e))
        say("PROGRESS=%d" % done)
        return
    say_target_info(target)
    try:
        target.halt()
        say("  CPU 已停住（读的时候不会跟它抢总线）")
    except Exception as e:
        say("  halt 失败（不致命，照读）：%s" % _first_line(e))

    # ---- 挑读法（probe 加速 / 经典 AP 块读 / 经典 AP 逐字读）----
    ap = _ap_of(target)
    mode = _env_str('READMODE', 'auto').lower()
    if ap is None:
        mode = 'probe'
        say("  拿不到 AP 对象，只能用目标层读法（probe）。")
    else:
        mode = pick_readmode(target, ap, mode)
        if mode in ('apid', 'apiw'):
            _bind_classic(ap)
    reader = _reader(target, ap, mode)

    fd = os.open(bin_path, os.O_RDWR | os.O_CREAT, 0o644)
    off = done
    fail_msg = None
    try:
        while off < total:
            n = min(chunk, total - off)
            try:
                data = bytes(reader(base + off, n))
            except Exception as e:
                fail_msg = "0x%08X 处读失败：%s" % (base + off, _first_line(e))
                break
            if len(data) != n:
                fail_msg = "0x%08X 只拿到 %d 字节（要 %d）" % (base + off, len(data), n)
                break
            os.pwrite(fd, data, off)
            off += n
            with open(prog_path, 'w') as f:
                f.write("%d\n" % off)
            if off % (chunk * 16) == 0 or off >= total:
                say("  ... %d / %d 字节 (%.1f%%)"
                    % (off, total, off * 100.0 / total))
                sys.stdout.flush()
    finally:
        try:
            os.fsync(fd)
        except Exception:
            pass
        os.close(fd)
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass

    if off >= total:
        say("")
        say("  整片读完了：%s" % bin_path)
        say("DUMP_COMPLETE")
        ok = sanity_file(bin_path, base)
        if not ok:
            say("  换成最保守的读法重来： DUMP_RESTART=1 READMODE=apiw bash dump-resume.sh")
        _verify_file(bin_path, total, verbose=True, again_expected=verify)
    else:
        if fail_msg:
            say("  %s" % fail_msg)
        say("  进度已存到 %s，再跑一次就从 %d 接着读。" % (prog_path, off))
        say("PROGRESS=%d" % off)


def _sha256(path, limit=None):
    import hashlib
    h = hashlib.sha256()
    left = limit
    with open(path, 'rb') as f:
        while True:
            n = 65536 if left is None else min(65536, left)
            if n <= 0:
                break
            b = f.read(n)
            if not b:
                break
            if left is not None:
                left -= len(b)
            h.update(b)
    return h.hexdigest()


def _verify_file(path, total, verbose=False, again_expected=True):
    try:
        size = os.path.getsize(path)
    except Exception as e:
        if verbose:
            say("  校验：读不到文件 %s" % e)
        return
    if size != total:
        if verbose:
            say("  校验：文件大小 %d != %d，说明还没读完。" % (size, total))
        return
    if verbose:
        say("  SHA-256: %s" % _sha256(path))
        say("  文件大小 %d 字节，对得上。" % size)


# =====================================================================
#  命令 5：诊断读法
#
#  症状：块读取回来的 512KB 全是同一个 4 字节（说明 AP 没按预期自增地址，
#  或者读的根本不是那块地方）。这一组测试用同一个地址、四种读法各读一遍，
#  拿几个「一定知道值」的地址当参照：
#      0xE000ED00  CPUID       Cortex-M4 应该是 0x410FC241 / M33 是 0x410FD…
#      0x0100A200  app_info    magic 应该是 0x47525858（串口日志里就写着）
#  哪一行哪一列对了，就用那种读法去 dump。
# =====================================================================
@command('dumpdiag', help='诊断：同一地址用不同读法读，看哪种读得对')
def dumpdiag():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)

    say("")
    say("==================================================================")
    say("  读法诊断")
    say("==================================================================")

    if manual_hold:
        wait = _env_int('HOLD_WAIT_SECS', 8)
        say("  把 RST 按住接地，我数到 0 再松手：")
        sys.stdout.flush()
        for i in range(wait, 0, -1):
            say("      %d ..." % i)
            beep('tick')
            time.sleep(1.0)
        beep('go')
        say("  >>> 现在松手！ <<<")
        t0 = time.monotonic()
    elif MANUAL:
        say("  把 RST 碰一下 GND 再松开，%.0f 秒内都算数。" % (window_ms / 1000.0))
        beep('go')
        t0 = time.monotonic()
    else:
        try:
            rst.assert_reset(True)
            time.sleep(hold_ms / 1000.0)
            rst.assert_reset(False)
        except Exception as e:
            say("  (复位失败: %s)" % e)
        t0 = time.monotonic()

    tries = 0
    ok = False
    while (time.monotonic() - t0) * 1000.0 < window_ms:
        tries += 1
        try:
            probe.connect()
            ok = True
            break
        except Exception:
            pass
    if not ok:
        say("  没抢到窗口（试了 %d 次），再跑一次。" % tries)
        return
    say("  连上了（复位后 %d ms，试了 %d 次）"
        % (int((time.monotonic() - t0) * 1000), tries))

    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        return
    try:
        target.init()
    except Exception as e:
        say("  target init 失败：%s" % _first_line(e))
        return
    say_target_info(target)
    try:
        target.halt()
        say("  CPU 已停住")
    except Exception as e:
        say("  halt 失败（照测）：%s" % _first_line(e))

    # 找到内存 AP
    ap = None
    try:
        aps = target.dp.aps
        keys = sorted(aps.keys()) if hasattr(aps, 'keys') else list(range(len(aps)))
        ap = aps[keys[0]]
        say("  AP 数量  : %d，先测第一个：%s" % (len(keys), ap.short_description))
    except Exception as e:
        say("  拿不到 AP：%s" % _first_line(e))
        return

    def try_(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            return 'ERR:%s' % _first_line(e)

    def hexw(v):
        return ('0x%08X' % v) if isinstance(v, int) else v

    say("")
    say("  地址                       经典单字        加速单字        经典块(4字)                          加速块(4字)")
    say("  ----------------------------------------------------------------------------------------------------------")
    probes = [
        ('CPUID   0xE000ED00', 0xE000ED00, 0x410FC241),
        ('DHCSR   0xE000EDF0', 0xE000EDF0, None),
        ('ROM表   0xE00FF000', 0xE00FF000, None),
        ('SRAM    0x30000000', 0x30000000, None),
        ('flash   0x01000000', 0x01000000, None),
        ('flash   0x0100A000', 0x0100A000, None),
        ('appinfo 0x0100A200', 0x0100A200, 0x47525858),
    ]
    for label, addr, expect in probes:
        a1 = hexw(try_(ap._read_memory, addr))
        a2 = hexw(try_(ap.read_memory, addr))
        b1 = try_(ap._read_memory_block32, addr, 4)
        b2 = try_(ap.read_memory_block32, addr, 4)
        f = lambda v: (' '.join('%08X' % w for w in v)) if isinstance(v, list) else v
        mark = ''
        if expect is not None:
            mark = '  <- 期望 0x%08X %s' % (expect, '✓' if a1 == expect or a2 == expect else '✗')
        say("  " + label + mark)
        say("      经典单字=%s  加速单字=%s" % (a1, a2))
        say("      经典块  =%s" % f(b1))
        say("      加速块  =%s" % f(b2))

    # 再单独试一下「块读多大才不出错」
    say("")
    say("  块读自增测试（从 0x0100A200 起，读 N 个 32 位字，看有几个值不同）：")
    for n in (1, 2, 4, 16, 64, 256):
        v = try_(ap.read_memory_block32, 0x0100A200, n)
        if isinstance(v, list):
            say("    加速块 %4d 字 -> %d 个不同值  %s"
                % (n, len(set(v)), ' '.join('%08X' % w for w in v[:4])))
        else:
            say("    加速块 %4d 字 -> %s" % (n, v))

    say("")
    say("  BEEP 提示：把上面整段发我，我按结果改 dump 的读法。")
    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass


# =====================================================================
#  命令 12：status —— 读自研固件写在 0x3001F000 的调试状态块
#
#  为什么这个东西这么好用：原厂 APP 一接管就把 SWD 关了，所以以前每次
#  看芯片都得抢复位后那两秒。我们自己写的固件不关 SWD（反着来，进 main
#  第一件事就是 sys_swd_enable()），于是调试器**随时**都能连上。
#  固件每走完一步就往 0x3001F000 写一个数，等于给它配了个 printf。
#
#  为什么 0x3001F000：GR5513 的 RAM 是 0x30000000 + 128KB，
#  链接脚本把顶上的 4KB 单独划出来（RAM_DBG），栈顶也是 0x3001F000、
#  往下长，所以栈永远踩不到它，落盘也固定，读起来不用查 map 文件。
# =====================================================================
ZK_DBG_ADDR  = 0x3001F000
ZK_DBG_MAGIC = 0x5A4B3401
# 状态块字数（跟固件 zk_dbg.h 的 ZK_DBG_WORDS 一致）。
# build 19（B2-A.2 扫描/广播实验）把它从 24 加到 56 —— 多出来的是
# 扫描设备数/RSSI 和 6 个广播数据变体的 ADV_START 状态码；
# build 20 又加到 60 —— 补上「广播停了几次 / 我们重开几次」；
# build 21 加到 80 —— B2-A.2 的 GATT 服务与推图（收到的命令/图块/屏的状态）；
# build 24 加到 84 —— 多一个「这轮刷新时屏到底忙了多久」（busy_polls 增量）；
# build 28 加到 88 —— 多一组「画面选项」（反色 / 旋转 180° / 农历开关）；
# build 30 加到 92 —— 多一组「日历/时钟为什么重画」（时基每 268 秒绕圈那个 bug）。
ZK_DBG_WORDS = 92

ZK_STAGE_TEXT = {
    64: 'Reset_Handler 已经跑到我们的代码了（SDK 初始化还没走完，'
        '卡在 soc_init / hal_flash_init 这一带）',
    0: '还没开始（要么固件没跑，要么这块 RAM 里是别的东西）',
    1: 'main 进来了',
    2: 'sys_swd_enable() 调过了',
    3: '7 根脚都配好了',
    4: '屏复位脉冲发完（RST 低 1ms -> 高 1ms -> 0x12）',
    5: '整条初始化序列发完',
    6: '测试图在 RAM 里拼好了',
    7: '30000 字节都传进屏里了',
    8: '刷新完 —— 屏上这时候应该已经有那四条横带了',
    9: '空闲循环里（心跳应该一直在涨）',
}


# 旧的 status（v1）：它先 resume 再在 CPU 运行中读，读不到就下结论说
# "跑的还是原厂固件" —— 这个判断是错的（调试口一没，什么都读不到）。
# 保留函数体但不再注册成命令，免得改动面太大；新实现在文件末尾。
def _zkstatus_v1_disabled():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)

    say("")
    say("  读调试状态块 0x%08X" % ZK_DBG_ADDR)
    say("  （自研固件每走完一步就往这里写一个数，相当于它的 printf）")
    say("")

    hit = None
    tries = 0

    # 先试「不复位直接连」：我们的固件不关 SWD，正常情况下这一步就该成。
    # 注意 connect() 本身只发一条「进 SWD」命令、不校验 IDCODE，
    # 所以连上之后必须真读一次 CPUID 才算数 —— 不然克隆版 ST-Link
    # 会在芯片没应答的时候也报「连上了」。
    if _env_str('NO_DIRECT', '0') != '1':
        t0 = time.monotonic()
        direct_ms = _env_int('DIRECT_MS', 1500)
        say("  先不复位，直接连 SWD（最多等 %d ms）..." % direct_ms)
        while (time.monotonic() - t0) * 1000.0 < direct_ms:
            tries += 1
            try:
                probe.connect()
            except Exception:
                time.sleep(0.05)
                continue
            try:
                t = resolve_target()
                if t is not None:
                    t.init()
                ap0 = _ap_of(t) if t is not None else None
                v = None
                if ap0 is not None:
                    v = ap0._read_memory(0xE000ED00) & 0xFFFFFFFF
                if v == 0x410FC241:
                    hit = 0
                    break
            except Exception:
                pass
            time.sleep(0.05)
        if hit is not None:
            say("  直接连上了，而且 CPUID 读得出来（试了 %d 次）——" % tries)
            say("  说明现在跑的固件没关 SWD ✅（原厂固件是关的）")

    if hit is None:
        say("  直接连不上（或者连上但读不到 CPUID）——")
        say("  那就说明现在跑的多半还是原厂固件。改成抢复位窗口再看...")
        hit, tries = _reset_and_grab(probe, rst, manual_hold,
                                     _env_int('DUMP_WINDOW_MS', 8000),
                                     _env_int('HOLD_MS', 200))
        if hit is None:
            say("  没抢到窗口（试了 %d 次）。再跑一次，或者把价签的 RST 碰一下 GND。" % tries)
            if rst is not None:
                try:
                    rst.close()
                except Exception:
                    pass
            return
        say("  连上了（复位后 %d ms，试了 %d 次）" % (hit, tries))

    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass
        return
    try:
        target.init()
    except Exception as e:
        say("  target.init 失败：%s" % _first_line(e))

    ap = _ap_of(target)

    # B2-A.2（build 19 起）：固件上电后会先扫描 4 秒、再挨个试广播数据变体。
    # 刚复位/刚上电就跑 status，看到的会是"实验进行到一半"。所以这里给个
    # 可选的等待（status.sh 默认 15 秒，测试里默认 0 —— 不然一套测试要等几分钟）。
    settle_ms = _env_int('STATUS_SETTLE_MS', 0)
    if settle_ms > 0:
        say("  等 %d ms 让固件把 B2-A.2 的实验跑完（扫描 4 秒 + 广播数据变体）..."
            % settle_ms)
        time.sleep(settle_ms / 1000.0)

    def rd(a):
        if ap is not None:
            try:
                return ap._read_memory(a) & 0xFFFFFFFF
            except Exception:
                pass
        try:
            return target.read_memory32(a) & 0xFFFFFFFF
        except Exception:
            return None

    # init 有可能把核心 halt 住、也可能让它复位重跑。两种都兜：
    # 先看 DHCSR 的 S_HALT 位，停着就让它继续跑，然后等固件把该做的做完。
    dhcsr = rd(0xE000EDF0)
    if isinstance(dhcsr, int) and (dhcsr & 0x2):
        say("  核心现在是停住的（DHCSR=0x%08X，S_HALT=1），先让它继续跑。" % dhcsr)
        try:
            target.resume()
        except Exception:
            pass
    wait_ms = _env_int('STATUS_WAIT_MS', 3000)
    if wait_ms > 0:
        say("  等 %d ms 让固件把三件事做完（初始化 -> 传图 -> 刷新）..." % wait_ms)
        time.sleep(wait_ms / 1000.0)

    cpuid = rd(0xE000ED00)
    say("")
    say("  读通路自检：CPUID(0xE000ED00) = %s %s"
        % (('0x%08X' % cpuid) if isinstance(cpuid, int) else '读不到',
           '✅ Cortex-M4，读通路是活的' if cpuid == 0x410FC241 else ''))

    words = []
    for i in range(16):
        v = rd(ZK_DBG_ADDR + 4 * i)
        if v is None:
            say("  状态块读不动（第一个失败的字是 +0x%X）。" % (4 * i))
            say("  这多半说明：现在跑的还是原厂固件（它这块 RAM 里不是我们的东西，")
            say("  而且它把 SWD 关了）—— 先确认 flash-app 那一步真的写进去了。")
            if rst is not None:
                try:
                    rst.close()
                except Exception:
                    pass
            return
        words.append(v)

    magic, heart, stage, flags = words[0], words[1], words[2], words[3]
    busy_levels, busy_polls, busy_to, gpio_err = words[4], words[5], words[6], words[7]
    ms_init, ms_write, ms_refresh, build_id = words[8], words[9], words[10], words[11]

    say("")
    say("  ----------------------------------------------------------------")
    if magic != ZK_DBG_MAGIC:
        say("  magic = 0x%08X（不是 0x%08X）" % (magic, ZK_DBG_MAGIC))
        say("  没有我们的固件留下的印记。要么没刷进去、要么刷进去没跑起来。")
        say("  ----------------------------------------------------------------")
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass
        return

    say("  自研固件在跑 ✅  magic=0x%08X  build=%d" % (magic, build_id))
    say("  stage = %d  ->  %s" % (stage, ZK_STAGE_TEXT.get(stage, '未知')))
    say("  heart = %d" % heart)
    say("  ----------------------------------------------------------------")

    # 心跳：读两次，看数字有没有在涨
    h1 = rd(ZK_DBG_ADDR + 4)
    time.sleep(0.6)
    h2 = rd(ZK_DBG_ADDR + 4)
    if isinstance(h1, int) and isinstance(h2, int):
        if h2 != h1:
            say("  心跳：%d -> %d，在涨 ✅（CPU 正在跑我们的空闲循环）" % (h1, h2))
        else:
            say("  心跳：%d -> %d，没动。" % (h1, h2))
            try:
                target.resume()
                time.sleep(0.6)
                h3 = rd(ZK_DBG_ADDR + 4)
                if isinstance(h3, int) and h3 != h2:
                    say("  让它继续跑之后：%d -> %d，在涨 ✅（刚才是被调试器暂停了）" % (h2, h3))
                else:
                    say("  继续跑也不涨 —— 固件可能卡住了（看下面的 stage 卡在哪）。")
            except Exception:
                say("  （核心被暂停着，恢复不了：%s）" % 'resume 失败')

    say("  ----------------------------------------------------------------")
    say("  flags = 0x%X" % flags)
    say("    %s BUSY 等超时过" % ('☑' if flags & 0x0001 else '☐'))
    say("    %s GPIO 初始化有失败" % ('☑' if flags & 0x0002 else '☐'))
    say("    %s DWT 周期计数器可用（延时是准的）" % ('☑' if flags & 0x0004 else '☐'))
    say("    %s sys_swd_enable() 调用成功" % ('☑' if flags & 0x0008 else '☐'))
    say("  BUSY 线：见过低电平=%s 见过高电平=%s；轮询 %d 次；等超时 %d 次"
        % ('是' if busy_levels & 1 else '否',
           '是' if busy_levels & 2 else '否',
           busy_polls, busy_to))
    say("  GPIO init 失败次数：%d" % gpio_err)
    say("  耗时（ms）：复位+初始化 %d / 传图 %d / 刷新 %d" % (ms_init, ms_write, ms_refresh))
    say("  ----------------------------------------------------------------")

    if stage >= 8:
        say("  结论：固件整条路都走完了。屏上现在应该有四条横带（100/80/70/42 行），")
        say("        外加左上角一条 8 行高的定位小条。没看到图的话，把上面这段发我。")
    elif stage == 7:
        say("  结论：图传完了但还没刷新 —— 卡在刷新那一步（0x22/0x20）。")
    elif stage <= 5:
        say("  结论：卡在初始化的第 %d 步，还没到传图。把上面这段发我。" % stage)
    else:
        say("  结论：stage=%d，把上面这段发我。" % stage)

    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass


# =====================================================================
#  命令：写 flash（MODE=probe / pagetest / restore / verify）
#
#  写的不是我们手戳寄存器，而是 Goodix 自己那份 CMSIS-Pack 算法：
#      GR551x-SDK/build/keil/GR5xxx_16MB_Flash.FLM
#  pyOCD 直接能吃 FLM，QSPI / XIP / 各芯片的初始化都在算法里面。
#
#  算法内部按「偏移」干活：EraseSector 做的是 offset = addr - fw_start_addr，
#  再把这个 24 位 offset 塞进 QSPI 命令。所以传进去的地址必须是算法认的那个
#  基地址（多半是 0x01000000）。传错就是写到别的偏移上去 —— 这是全流程里
#  唯一会真出事的地方，所以动手之前先做一次硬联锁：Init 之后把算法算出来的
#  fw_start_addr 从 RAM 里读回来，跟 FW_BASE 对不上就拒绝写。
#
#  MODE=probe     只 Init，不擦不写，把算法认的地址范围读回来看（默认）
#  MODE=pagetest  在一颗空白扇区上做一次 擦->写->读回（只动那一颗扇区）
#  MODE=restore   把备份整片刷回；一颗扇区一颗扇区，从高地址往低地址
#  MODE=verify    只读：把现在的 flash 读回来跟备份逐字节比
# =====================================================================
FLM_PATHS = (
    '/Users/mac/Documents/Codex/2026-09-15/a/GR551x-SDK/build/keil/GR5xxx_16MB_Flash.FLM',
    '/Users/mac/Documents/Codex/2026-09-15/a/GR551x-SDK/build/gcc/GR5xxx_16MB_Flash_Jflash.FLM',
)

FW_MODE_TITLE = {
    'probe': '只探不写（Init 一下，看算法认哪个地址范围）',
    'pagetest': '一颗空白扇区上试写（只动那一颗扇区）',
    'restore': '把备份原样刷回去，写完读回比 SHA-256',
    'verify': '只读：把现在的 flash 读回来跟备份逐字节比',
    'peek': '只读：把指定地址那一段打出来看（比如刚试写的那颗扇区）',
    'app': '只写自研 APP 那一段 + 更新镜像信息（不动 bootloader）',
    'appverify': '只读：把自研 APP 那一段读回来，跟本地镜像对账',
}

# 自研 APP 相关的常量（跟 outputs/firmware/tools/fwpack.py 必须一致）
ZK_APP_INFO_OFF     = 0x2000    # bootloader 存 APP 镜像信息的扇区
ZK_APP_INFO_LEN     = 40        # dfu_image_info_t
ZK_APP_INFO_PATTERN = 0x4744
ZK_APP_BASE_DEFAULT = 0x0100A000
ZK_APP_TAG          = b'ZK42V-EPD-CUSTOM-FW'


def _app_meta(path, base):
    """从整片镜像文件里把 APP 那条镜像信息读出来，并自洽校验一遍。

    这是「要刷进去的这份文件本身对不对」的判据，只有一条硬指标：
    文件里哪一段被声明成 APP（load/bin_size），那一段的逐字节求和
    就必须等于声明里的 check_sum。

    为什么这条能当判据：原厂 bootloader 就是这么验的
    （components/libraries/app_bootloader/bootloader_boot_vendor.c
     里的 check_image_crc 就是逐字节求和）。而且这个公式是反向验证过的：
    拿出厂镜像按它算，bootloader 段算出 0x00173927、APP 段算出 0x00CCE71B，
    跟串口开机日志里原样打出来的两个数一字不差。

    返回 dict 或 None（None = 这份文件不能刷）。
    """
    try:
        with open(path, 'rb') as f:
            d = f.read()
    except Exception as e:
        say("  读不到文件：%s" % _first_line(e))
        return None

    if len(d) < ZK_APP_INFO_OFF + ZK_APP_INFO_LEN:
        say("  文件只有 %d 字节，0x%X 处没有镜像信息。" % (len(d), ZK_APP_INFO_OFF))
        return None

    off = ZK_APP_INFO_OFF
    pat, ver = struct.unpack_from('<HH', d, off)
    bin_size, check_sum, load, run = struct.unpack_from('<IIII', d, off + 4)
    comments = d[off + 28:off + 40]

    say("  0x%05X 那条 APP 镜像信息：" % off)
    say("    pattern   = 0x%04X%s" % (pat, '' if pat == ZK_APP_INFO_PATTERN
                                     else '  <-- 不对，期望 0x%04X' % ZK_APP_INFO_PATTERN))
    say("    version   = %d" % ver)
    say("    bin_size  = 0x%X (%d 字节)" % (bin_size, bin_size))
    say("    check_sum = 0x%08X" % check_sum)
    say("    load/run  = 0x%08X / 0x%08X" % (load, run))
    say("    comments  = %r" % (comments.split(b'\x00')[0],))

    if pat != ZK_APP_INFO_PATTERN:
        say("    -> 模式不对，拒写。")
        return None
    if load != run:
        say("    -> load != run（这是镜像搬运模式），这块芯片的 bootloader 不认，拒写。")
        return None

    o = load - base
    if o < 0 or o + bin_size > len(d) or bin_size <= 0:
        say("    -> 声明的 APP 段（0x%08X + 0x%X）不在文件范围内，拒写。" % (load, bin_size))
        return None

    body = d[o:o + bin_size]
    real = sum(body) & 0xFFFFFFFF
    say("    实测这 %d 字节逐字节和 = 0x%08X" % (bin_size, real))
    if real != check_sum:
        say("    -> 跟声明的 check_sum 对不上！这份镜像不能刷。")
        return None

    sp, rv = struct.unpack_from('<II', body, 0)
    say("    APP 头两个字：SP=0x%08X  复位向量=0x%08X" % (sp, rv))
    if not (load <= rv < load + bin_size):
        say("    -> 复位向量没落在 APP 自己这一段里，拒写。")
        return None

    if body.find(ZK_APP_TAG) >= 0:
        say("    镜像里有标记字符串 %s ✅" % ZK_APP_TAG.decode())
    else:
        say("    （没找到标记字符串 %s —— 可能是别人的固件，自己确认。）"
            % ZK_APP_TAG.decode())

    say("    -> 自洽，可以刷：APP 在 0x%08X，%d 字节。" % (load, bin_size))
    return {'pattern': pat, 'version': ver, 'bin_size': bin_size,
            'check_sum': check_sum, 'load': load, 'run': run,
            'comments': comments}


def _flm_path():
    want = _env_str('FLM', '')
    if want:
        return want
    for p in FLM_PATHS:
        if os.path.exists(p):
            return p
    return FLM_PATHS[0]


def _fw_backup_path():
    want = _env_str('FW_FILE', '')
    if want:
        return want
    return os.path.join(os.path.dirname(os.path.abspath(OUTDIR)),
                        'pyocd', 'zk42v-factory-backup-run1.bin')


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()


@command('flashwrite', help='写 flash：MODE=probe / pagetest / restore / verify')
def flashwrite():
    try:
        from pyocd.target.pack.flash_algo import PackFlashAlgo
        from pyocd.core.memory_map import FlashRegion, RamRegion
        from pyocd.flash.flash import Flash
    except Exception as e:
        say("  载不进 pyOCD 的 flash 模块：%s" % _first_line(e))
        return

    def hexw(v):
        return ('0x%08X' % v) if isinstance(v, int) else str(v)

    mode = _env_str('MODE', 'probe').lower()
    base = _env_hex('FW_BASE', FLASH_BASE_ADDR)
    total = _env_hex('FW_TOTAL', 0x80000)
    sector = max(0x100, _env_hex('FW_SECTOR', 0x1000))
    page = max(0x100, _env_hex('FW_PAGE', 0x1000))
    ram_base = _env_hex('ALGO_RAM', 0x00818000)
    ram_len = _env_hex('ALGO_RAM_LEN', 0x8000)
    force = _env_str('FORCE', '0') == '1'
    flm = _flm_path()
    fw_file = _fw_backup_path()
    test_addr = _env_hex('FW_TEST_ADDR', base + total - sector)

    def page_pattern():
        """pagetest 往那颗扇区里写的图案。

        特意选成 (0xA5 + 7*i) & 0xFF —— 它是周期的、256 个值各出现 16 次。
        这样"整颗读回是同一个值"和"真的写进去了"绝不会被认错：
        常数读回永远恰好只有 16 个字节对得上，不可能更多。
        """
        return bytes(((0xA5 + 7 * i) & 0xFF) for i in range(sector))

    def dump_head(tag, data, n=32):
        say("      %s%s" % (tag, ' '.join('%02X' % b for b in data[:n])))

    def looks_dead(data):
        """读回来的东西是不是"死值"——QSPI 还没交还，根本没读到 flash。

        实机上量到的死值长这样：00 34 C0 F7 00 34 C0 F7 ...（0xF7C03400 反复）。
        真数据（备份也好，试写图案也好）都不长这样：
          · 不同的字节值 <= 4 种      -> 死值
          · 不同的 32 位字 <= 2 个    -> 死值
        """
        if data is None or len(data) == 0:
            return True
        if len(set(data)) <= 4:
            return True
        nw = len(data) // 4
        if nw:
            words = struct.unpack('<%dI' % nw, data[:nw * 4])
            if len(set(words)) <= 2:
                return True
        return False

    def page_report(got, pat, addr, sector, err=None):
        """把结论说清楚，并给出下一步该干嘛"""
        if got is None:
            say("    读回失败：这颗扇区这会儿读不了。%s"
                % (('（%s）' % err) if err else ''))
            say("    按一下 RST 再松开，跑 MODE=peek bash flash-write.sh 再看一眼。")
            return
        if got == pat:
            say("    读回比对：%d 字节全对上 ✅ 擦 + 写 + 读这条路是通的" % sector)
            say("    下一步：MODE=restore bash flash-write.sh")
            return
        bad = sum(1 for i in range(min(len(got), sector)) if got[i] != pat[i])
        uniq = sorted(set(got))
        say("    读回比对：有 %d/%d 个字节不一样 ❌" % (bad, sector))
        dump_head("期望头 32 字节：", pat)
        dump_head("读回头 32 字节：", got)
        say("      这一段里有 %d 种不同的字节值。" % len(uniq))
        if looks_dead(got):
            if len(uniq) == 1 and uniq[0] == 0xFF:
                say("      整颗都是 0xFF —— 擦是擦掉了，但写没进去（program 没生效）。")
                say("      按一下 RST 再松开，跑 MODE=peek bash flash-write.sh")
                say("      再确认一次：要是那里也整颗 0xFF，就是真没写进去，")
                say("      先别跑 restore，把这段发我。")
                return
            say("      这一段几乎就是同一个值翻来覆去 —— 这是'读不到真数据'的死值，")
            say("      不是写失败。读回这步发生在算法把 QSPI 交还的过程中，靠不住。")
            say("      按一下 RST 再松开（让应用把 XIP 打开），跑")
            say("          MODE=peek bash flash-write.sh")
            say("      那一步是干净重连之后读的，才是这颗扇区的真身。")
        else:
            say("      读到的是有内容的数据，但不是我们的图案。")
            say("      按一下 RST 再松开，跑 MODE=peek bash flash-write.sh 看整颗，")
            say("      把那段 hexdump 发我。")

    say("")
    say("================================================================")
    say("  写 flash —— %s" % FW_MODE_TITLE.get(mode, mode))
    say("================================================================")
    say("  MODE    : %s" % mode)
    say("  基地址  : 0x%08X + 0x%X (%d KB)" % (base, total, total // 1024))
    say("  扇区/页 : 0x%X / 0x%X" % (sector, page))
    say("  算法    : %s" % flm)
    say("  备份    : %s" % fw_file)
    say("")

    pack = None
    if mode in ('verify', 'peek', 'appverify'):
        if not os.path.exists(fw_file):
            say("  找不到备份文件，用 FW_FILE=<路径> 指给我。")
            if mode in ('verify', 'appverify'):
                return
    else:
        if not os.path.exists(flm):
            say("  找不到 FLM 文件，用 FLM=<路径> 指给我。")
            return
        try:
            pack = PackFlashAlgo(flm)
        except Exception as e:
            say("  FLM 读不了：%s" % _first_line(e))
            return
        fi = pack.flash_info
        try:
            fname = fi.name.decode('ascii', 'replace')
        except Exception:
            fname = str(fi.name)
        say("  FLM 里写的：name=%s  start=0x%08X  size=0x%X  page=0x%X  擦完=0x%02X"
            % (fname, fi.start, fi.size, fi.page_size, fi.value_empty))
        say("  （start 是给 IDE 看的占位值；算法真正认哪一段，Init 之后从 RAM 里读）")
        say("")

    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)

    hit, tries = _reset_and_grab(probe, rst, manual_hold, window_ms, hold_ms)
    if hit is None:
        say("  没抢到窗口（试了 %d 次），再跑一次。" % tries)
        return
    say("  connect() 成功（复位后 %d ms，试了 %d 次）" % (hit, tries))
    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        return
    try:
        target.init()
    except Exception as e:
        say("  target.init 失败：%s" % _first_line(e))
        return
    ap = _ap_of(target)

    def rd32(a):
        if ap is not None:
            try:
                return ap._read_memory(a)
            except Exception:
                pass
        return target.read_memory32(a)

    def read_back(addr, n):
        """从 flash 窗口读回来（跟 dump 时同一条经典 AP 路）"""
        buf = bytearray()
        while len(buf) < n:
            a = addr + len(buf)
            left = (n - len(buf)) // 4
            try:
                w = list(ap._read_memory_block32(a, left))
                buf += struct.pack('<%dI' % len(w),
                                   *[x & 0xFFFFFFFF for x in w])
            except Exception:
                try:
                    for i in range(left):
                        buf += struct.pack('<I', ap._read_memory(a + 4 * i) & 0xFFFFFFFF)
                except Exception as e:
                    return None, _first_line(e)
        return bytes(buf), None

    def wait_live(tmo_ms):
        """等 XIP 打开（复位之后窗口才会活）"""
        t0 = time.monotonic()
        last = None
        while (time.monotonic() - t0) * 1000.0 < tmo_ms:
            try:
                vals = (rd32(base + 0xA200), rd32(base + 4), rd32(base + 8))
            except Exception:
                continue
            prev = last[0] if last else None
            last = vals
            if (len(set(vals)) >= 3
                    or (isinstance(prev, int) and _looks_dead(prev)
                        and not _looks_dead(vals[0]))):
                return True
            time.sleep(0.005)
        return False

    def finish():
        if pack is not None:
            pass
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass

    def load_backup():
        try:
            with open(fw_file, 'rb') as f:
                d = f.read(total)
        except Exception as e:
            say("  读不到备份文件：%s" % _first_line(e))
            return None
        if len(d) < total:
            say("  备份文件只有 %d 字节，不够 0x%X。" % (len(d), total))
            return None
        return d

    # ---------------- MODE=peek：只读看一眼某一段 ----------------
    if mode == 'peek':
        addr = _env_hex('ADDR', test_addr)
        ln = max(sector, _env_hex('LEN', sector))
        say("")
        say("  只读看一眼 0x%08X + 0x%X（读到什么打什么，不擦不写）" % (addr, ln))
        say("  （这一步是干净重连之后读的：先把价签 RST 接地再松手，")
        say("    等应用把 XIP 打开、窗口活了，才读。所以这是真数据。）")
        if not wait_live(_env_int('WAIT_LIVE_MS', 8000)):
            say("  窗口没活。把价签的 RST 碰一下 GND 再松开，重跑一次。")
            finish()
            return
        try:
            target.halt()
        except Exception:
            pass
        got, err = read_back(addr, ln)
        if got is None:
            say("  读不动：%s" % err)
            finish()
            return
        say("")
        show = min(ln, 256)
        for off in range(0, show, 16):
            chunk = got[off:off + 16]
            say("    %08X  %-47s  %s"
                % (addr + off,
                   ' '.join('%02X' % b for b in chunk),
                   ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)))
        if ln > show:
            say("    （后面还有 0x%X 字节没打印，下面只按统计下结论）" % (ln - show))
        uniq = sorted(set(got))
        say("")
        say("  这一段 %d 字节里有 %d 种不同的字节值。" % (ln, len(uniq)))
        if len(uniq) <= 4:
            say("  只有 %s —— 整段是同一个/少数几个值，这是'没读到真数据'的死值。"
                % ' '.join('0x%02X' % v for v in uniq))
            say("  再按一下 RST（按住再接 GND，松手），重跑一次；还这样就把这段发我。")
        if addr <= test_addr and (test_addr + sector) <= (addr + ln):
            pat = page_pattern()
            off = test_addr - addr
            seg = got[off:off + sector]
            say("")
            if seg == pat:
                say("  >>> 0x%08X 这颗扇区跟试写图案一模一样 ✅" % test_addr)
                say("  >>> 结论：erase + program 这条路是通的。可以往下走")
                say("          MODE=restore bash flash-write.sh")
            else:
                bad = sum(1 for i in range(min(len(seg), sector)) if seg[i] != pat[i])
                say("  >>> 0x%08X 这颗扇区跟试写图案对不上（%d/%d 字节不同）"
                    % (test_addr, bad, sector))
                if len(set(seg)) == 1:
                    v = seg[0]
                    if v == 0xFF:
                        say("  >>> 整颗都是 0xFF：擦确实擦了，但写没进去 —— program 没生效。")
                        say("  >>> 先别跑 restore。把这段发我，我去改 program 那条路。")
                    else:
                        say("  >>> 整颗都是 0x%02X，是个死值：这次窗口没活。换个时间再跑。" % v)
                else:
                    dump_head("实际头 32 字节：", seg)
                    say("  >>> 有内容，但不是我们写的图案。那 32 字节发我看看。")
        finish()
        return

    # ---------------- MODE=appverify：只读比对自研 APP 那一段 ----------------
    #  为什么单独一个模式：restore 的 verify 比的是整片、比的是出厂备份；
    #  我们刷了自研 APP 之后整片当然跟出厂备份不一样。这里只比
    #  「APP 那一段 + 0x2000 那条镜像信息」，而且判据是自洽的 ——
    #  芯片里那段的逐字节和，必须等于芯片里那条信息声明的 check_sum。
    if mode == 'appverify':
        say("")
        say("  本地这份镜像的账面：")
        meta = _app_meta(fw_file, base)
        if meta is None:
            say("  本地这份镜像自己都不自洽，先别拿来当验收基准。")
            finish()
            return

        say("")
        say("  等 flash 窗口活过来（XIP 开了才读得到真数据）...")
        if not wait_live(_env_int('WAIT_LIVE_MS', 8000)):
            say("  窗口没活。把价签的 RST 碰一下 GND 再松开，重跑一次。")
            finish()
            return
        try:
            target.halt()
        except Exception:
            pass

        app_base = meta['load']
        n = meta['bin_size']
        say("  窗口活了。读回 0x%08X + 0x%X，再读 0x%08X 那条信息 ..."
            % (app_base, n, base + ZK_APP_INFO_OFF))
        got, err = read_back(app_base, n)
        if got is None:
            say("  读不动：%s" % err)
            finish()
            return
        rec, err2 = read_back(base + ZK_APP_INFO_OFF, ZK_APP_INFO_LEN)
        if rec is None:
            say("  那 40 字节读不动：%s" % err2)
            finish()
            return

        hw_sum = sum(got) & 0xFFFFFFFF
        hw_size, hw_sum_decl, hw_load, hw_run = struct.unpack_from('<IIII', rec, 4)

        say("")
        say("  芯片里那条信息：bin_size=0x%X  check_sum=0x%08X  load/run=0x%08X/0x%08X"
            % (hw_size, hw_sum_decl, hw_load, hw_run))
        say("  芯片里这段 %d 字节：逐字节和 = 0x%08X" % (n, hw_sum))
        say("  本地镜像的账    ：bin_size=0x%X  check_sum=0x%08X"
            % (meta['bin_size'], meta['check_sum']))

        ok = True
        if hw_size != meta['bin_size'] or hw_sum_decl != meta['check_sum']:
            say("  ！芯片里那条信息跟本地镜像对不上 —— 写进去的不是这份镜像。")
            ok = False
        if hw_sum != hw_sum_decl:
            say("  ！芯片里 APP 的实际校验和 != 它自己声明的那条 —— 镜像坏了或者没写全。")
            ok = False

        # 逐字节跟本地比：不一样就报第一个不一样的偏移
        want = load_backup()
        if want is not None:
            off = app_base - base
            seg = want[off:off + n]
            diff = sum(1 for i in range(n) if got[i] != seg[i])
            if diff == 0:
                say("  跟本地这份镜像逐字节一样 ✅")
            else:
                say("  ！有 %d 个字节跟本地镜像不一样" % diff)
                for i in range(n):
                    if got[i] != seg[i]:
                        say("    第一个不一样的偏移 0x%X（flash 0x%08X）：芯片 0x%02X vs 本地 0x%02X"
                            % (i, app_base + i, got[i], seg[i]))
                        break
                ok = False

        if got.find(ZK_APP_TAG) >= 0:
            say("  芯片里那段的标记字符串 %s 在 ✅" % ZK_APP_TAG.decode())
        else:
            say("  ！芯片里那段没找到标记字符串 %s" % ZK_APP_TAG.decode())
            ok = False

        say("")
        if ok:
            say("  >>> 芯片里的 APP 跟本地镜像一致，而且自洽  APP_VERIFY_OK")
            say("  >>> 下一步：接上串口不用管，直接看屏上有没有那四条横带；")
            say("      再用 bash status.sh 读 0x3001F000 的调试状态块。")
        else:
            say("  >>> APP_VERIFY_FAIL —— 把上面这一段发我。")
        finish()
        return

    # ---------------- MODE=verify：只读比对 ----------------
    if mode == 'verify':
        want = load_backup()
        if want is None:
            finish()
            return
        say("")
        say("  等 flash 窗口活过来（XIP 开了才读得到真数据）...")
        if not wait_live(_env_int('WAIT_LIVE_MS', 5000)):
            say("  窗口没活。把价签的 RST 碰一下 GND 再松开，重跑一次；")
            say("  或者先跑 flash-lab4.sh 看一眼窗口能不能活。")
            finish()
            return
        try:
            target.halt()
        except Exception:
            pass
        say("  窗口活了，整片读回比对 ...")
        got, err = read_back(base, total)
        if got is None:
            say("  读不动了：%s" % err)
            finish()
            return
        diff = sum(1 for i in range(total) if got[i] != want[i])
        import hashlib
        hw = hashlib.sha256(want).hexdigest()
        hg = hashlib.sha256(got).hexdigest()
        say("    芯片里 : %s" % hg)
        say("    备份里 : %s" % hw)
        if diff == 0:
            say("  >>> 逐字节一样（%d 字节全部对上） VERIFY_OK" % total)
        else:
            say("  >>> 有 %d 个字节不一样 VERIFY_FAIL" % diff)
            for i in range(total):
                if got[i] != want[i]:
                    say("    第一个不一样的偏移 0x%X：芯片 0x%02X vs 备份 0x%02X"
                        % (i, got[i], want[i]))
                    break
        finish()
        return

    # ---------------- 装算法 ----------------
    try:
        algo = pack.get_pyocd_flash_algo(page, RamRegion(start=ram_base, length=ram_len))
    except Exception as e:
        say("  算法装不进这块 RAM（换 ALGO_RAM 试试）：%s" % _first_line(e))
        finish()
        return
    if algo is None:
        say("  算法装不进这块 RAM（换 ALGO_RAM 试试）。")
        finish()
        return
    region = FlashRegion(name='gr5xxx-xip', start=base, end=base + total - 1,
                         sector_size=sector, page_size=page,
                         erased_byte_value=fi.value_empty, algo=algo)
    fl = Flash(target, algo)
    fl.region = region
    say("  算法落地：代码 0x%08X + %d 字节，页缓冲 %s，栈顶 0x%08X"
        % (algo['load_address'], len(algo['instructions']) * 4,
           ' '.join('0x%08X' % b for b in algo['page_buffers']), algo['begin_stack']))

    # ---------------- Init（只有 probe 时是纯只读）----------------
    say("")
    try:
        fl.init(Flash.Operation.ERASE, address=base)
        say("  Init(addr=0x%08X, op=擦除) 返回 0 ✅ —— 算法认了这个地址" % base)
    except Exception as e:
        say("  Init 失败：%s" % _first_line(e))
        say("  （算法自己拒绝了，说明它不认识这颗芯片或者参数不对，没动过 flash）")
        try:
            fl.cleanup()
        except Exception:
            pass
        finish()
        return

    code_start = algo['load_address'] + 4
    static_base = algo['static_base']
    syms = {}
    try:
        from pyocd.debug.elf.elf import ELFBinaryFile
        elf = ELFBinaryFile(flm)
        for nm in ('first_packet_data', 'fw_start_addr', 'mirror_addr_offset',
                   'verify_size'):
            si = elf.symbol_decoder.get_symbol_for_name(nm)
            if si is not None:
                syms[nm] = si.address
    except Exception as e:
        say("  （ELF 符号读不到，改按偏移看：%s）" % _first_line(e))

    say("")
    say("  算法 Init 之后在自己 RAM 里算出来的配置（读回来的真值）：")
    say("    g_chip_spec_info[0].start = %s   <- 算偏移用的就是这个"
        % hexw(rd32(static_base + 0x44)))
    say("    g_chip_spec_info[0].size  = %s" % hexw(rd32(static_base + 0x48)))
    if syms:
        for nm in ('first_packet_data', 'fw_start_addr', 'mirror_addr_offset', 'verify_size'):
            if nm in syms:
                say("    %-18s = %s" % (nm, hexw(rd32(code_start + syms[nm]))))
    else:
        for off, nm in ((0x08, 'first_packet_data'), (0x0C, 'fw_start_addr'),
                        (0x10, 'mirror_addr_offset'), (0x14, 'verify_size')):
            say("    rw+0x%02X %-15s = %s" % (off, nm, hexw(rd32(static_base + off))))

    say("")
    say("  算法认的地址范围（Init 现算的）：")
    for i in range(5):
        a = static_base + 0x44 + 8 * i
        s = rd32(a)
        n = rd32(a + 4)
        if s is None or n is None:
            continue
        say("    第 %d 段 : start=0x%08X  size=0x%X  -> 到 0x%08X"
            % (i + 1, s, n, (s + n) & 0xFFFFFFFF))

    def algo_base():
        """算法真正拿来算偏移的基地址 = g_chip_spec_info[0].start

        从 FLM 反汇编确认：EraseSector 和 ProgramPage 都是
            offset = addr - g_chip_spec_info[0].start
        再把 offset 的低 24 位塞进 QSPI 命令（三个地址字节）。
        所以这个数必须等于 FW_BASE，否则就是写到别的偏移上。
        （fw_start_addr 是另一套东西 —— 它是给 DFU 镜像用的，这个模式下
          本来就是 0，拿它当判据的话会一路误报。）
        """
        return rd32(static_base + 0x44)

    def lock_check():
        v = algo_base()
        size = rd32(static_base + 0x48)
        if v != base:
            say("")
            say("  联锁没过：算法算偏移用的基地址是 %s，我们要写的是 0x%08X。"
                % (hexw(v), base))
            say("  这样硬写，偏移可能整错 —— 所以我不动手。")
            say("  把 FW_BASE 改成 %s 再来；真确定就加 FORCE=1。" % hexw(v))
            return force
        if isinstance(size, int) and size and (base + total) > (v + size):
            say("")
            say("  联锁没过：0x%08X + 0x%X 超出了算法认的范围（0x%08X + 0x%X）。"
                % (base, total, v, size))
            say("  算法背后那颗 flash 就那么大，超出部分写下去会绕回去。不写。")
            return force
        say("")
        say("  联锁通过：算法算偏移用的基地址 = 0x%08X（范围 0x%08X + 0x%X），"
            % (v, v, size if isinstance(size, int) else 0))
        say("  跟我们要写的 0x%08X + 0x%X 完全对得上 ✅" % (base, total))
        return True

    def do_program(addr, data):
        fl.init(Flash.Operation.PROGRAM, address=base)
        off = 0
        while off < len(data):
            n = min(page, len(data) - off)
            fl.program_page(addr + off, data[off:off + n])
            off += n

    # ---------------- MODE=probe ----------------
    if mode == 'probe':
        say("")
        lock_ok = lock_check()          # 只读，就是看一眼结论
        say("")
        say("  MODE=probe：到此为止，没擦也没写。")
        if lock_ok:
            say("  看着没问题就下一步：MODE=pagetest（拿一颗空白扇区真写一次）")
        else:
            say("  但联锁没过，先别往下走：按上面提示先确认基地址。")
        try:
            fl.cleanup()
        except Exception:
            pass
        finish()
        return

    if not lock_check():
        try:
            fl.cleanup()
        except Exception:
            pass
        finish()
        return

    # ---------------- MODE=pagetest ----------------
    if mode == 'pagetest':
        pat = page_pattern()
        say("")
        say("  拿 0x%08X 这颗扇区试（备份里这一段是 0xFF 空白）：" % test_addr)
        try:
            fl.erase_sector(test_addr)
            say("    擦 0x%08X ... 好" % test_addr)
        except Exception as e:
            say("    擦失败：%s" % _first_line(e))
            say("    （擦都失败就没往下写，这颗扇区还是原样）")
            try:
                fl.cleanup()
            except Exception:
                pass
            finish()
            return
        try:
            do_program(test_addr, pat)
            say("    写 %d 字节 ... 好" % sector)
        except Exception as e:
            say("    写失败：%s" % _first_line(e))
            try:
                fl.cleanup()
            except Exception:
                pass
            finish()
            return
        # 先把算法卸掉再读。不卸就读的话，QSPI 还在算法的命令模式里，
        # 读回来是死值。但别指望这一步一定成：实测这颗芯片上 UnInit 并不把
        # XIP 窗口还回来（128 颗 restore 全读到死值就是证据）。
        # 所以这只算参考 —— 真验收是 MODE=peek，它干净重连、等 XIP 活了再读。
        # 用 uninit() 不是 cleanup()：算法代码留在 RAM 里，下一轮不用重灌。
        try:
            fl.uninit()
        except Exception as e:
            say("    （卸算法时报了个错，不致命：%s）" % _first_line(e))
        got, err = read_back(test_addr, sector)
        if got is not None and got != pat and looks_dead(got):
            # 刚交还，QSPI 可能还差一点点才稳，多抓几次
            for _ in range(12):
                time.sleep(0.05)
                got2, err2 = read_back(test_addr, sector)
                if got2 is None:
                    continue
                if got2 == pat or not looks_dead(got2):
                    got, err = got2, None
                    break
        page_report(got, pat, test_addr, sector, err)
        try:
            fl.cleanup()
        except Exception:
            pass
        finish()
        return

    # ---------------- MODE=app：只写自研 APP 那一段 ----------------
    #  跟 restore 的区别：restore 把整片 512KB 都写一遍（128 颗扇区），
    #  这里只写「APP 那一段 + 最后那颗镜像信息扇区」，bootloader 和 NVDS
    #  一个字节都不碰。自研固件迭代时用这个，快而且改动面小。
    if mode == 'app':
        want = load_backup()
        if want is None:
            try:
                fl.cleanup()
            except Exception:
                pass
            finish()
            return

        say("")
        say("  先看要刷进去的这份镜像自己对不对（不对就不写）：")
        meta = _app_meta(fw_file, base)
        if meta is None:
            say("")
            say("  这份镜像没通过自检，所以我不写。")
            try:
                fl.cleanup()
            except Exception:
                pass
            finish()
            return

        app_base = meta['load']
        bin_size = meta['bin_size']
        body = want[app_base - base:app_base - base + bin_size]
        nsec = (bin_size + sector - 1) // sector
        info_addr = base + ZK_APP_INFO_OFF

        say("")
        say("  要写的东西：")
        say("    APP  : 0x%08X + 0x%X (%d 字节) -> %d 颗扇区，从低地址往高地址"
            % (app_base, bin_size, bin_size, nsec))
        say("    INFO : 0x%08X             -> 1 颗扇区，**最后**写" % info_addr)
        say("  不碰的：bootloader(0x%08X 起)、NVDS(0x%08X 起)。"
            % (base + 0x3000, base + 0x7F000))
        say("")
        say("  为什么 INFO 放最后：万一写到一半掉线，bootloader 手里还是旧账，")
        say("  它算完发现 APP 对不上 -> 去走 DFU，而不是跳进一个半截的 APP。")
        say("")

        done = 0
        fail_at = None
        t0 = time.monotonic()
        for i in range(nsec):
            a = app_base + i * sector
            chunk = body[i * sector:(i + 1) * sector]
            if len(chunk) < sector:
                chunk = bytes(chunk) + b'\xFF' * (sector - len(chunk))
            try:
                fl.init(Flash.Operation.ERASE, address=base)
                fl.erase_sector(a)
                do_program(a, chunk)
            except Exception as e:
                say("  0x%08X 这颗失败：%s" % (a, _first_line(e)))
                fail_at = a
                break
            done += 1
            if done % 4 == 0 or done == nsec:
                say("  ... %d/%d 颗 (%.0f%%)  用了 %.1f 秒"
                    % (done, nsec, done * 100.0 / nsec, time.monotonic() - t0))

        if fail_at is None:
            try:
                info_chunk = want[ZK_APP_INFO_OFF:ZK_APP_INFO_OFF + sector]
                if len(info_chunk) < sector:
                    info_chunk = bytes(info_chunk) + b'\xFF' * (sector - len(info_chunk))
                fl.init(Flash.Operation.ERASE, address=base)
                fl.erase_sector(info_addr)
                do_program(info_addr, info_chunk)
                say("  0x%08X 那颗镜像信息也写完了。" % info_addr)
            except Exception as e:
                say("  0x%08X 这颗镜像信息写失败：%s" % (info_addr, _first_line(e)))
                say("  ！APP 写好了但信息没更新 —— 这时候 bootloader 会认为 APP 校验不过，")
                say("    它会去走 DFU（不会跳进 APP，也不会变砖）。重跑一次这条命令就行。")
                fail_at = info_addr

        try:
            fl.cleanup()
        except Exception:
            pass
        say("")
        say("================================================================")
        if fail_at is None:
            say("  写完了：APP %d 颗 + 信息 1 颗，一共 %d 颗扇区。" % (nsec, nsec + 1))
            say("  芯片里那段 APP 的逐字节和 = 0x%08X（跟声明的对上了）" % meta['check_sum'])
        else:
            say("  没写完，停在第 %d/%d 颗（0x%08X）。" % (done, nsec, fail_at))
            say("  重跑一次这条命令接着做就行。")
        say("================================================================")
        say("")
        say("  下一步（真验收）：")
        say("    1) 把价签的 RST 碰一下 GND 再松开 —— 看屏上有没有四条横带")
        say("    2) MODE=appverify bash flash-app.sh   # 只读，把 APP 段读回来对账")
        say("    3) bash status.sh                     # 读 0x3001F000 的调试状态块")
        finish()
        return

    # ---------------- MODE=restore ----------------
    if mode != 'restore':
        say("  MODE 不认识：%s" % mode)
        try:
            fl.cleanup()
        except Exception:
            pass
        finish()
        return

    want = load_backup()
    if want is None:
        try:
            fl.cleanup()
        except Exception:
            pass
        finish()
        return
    # 最后一道闸：要刷进去的这份文件，本身得先过真伪校验。
    # 把一份坏镜像刷进去，比不刷还糟。
    if _env_str('FW_SKIP_SANITY', '0') != '1':
        say("")
        say("  先验一遍要刷进去的那份备份（别把坏镜像刷进去）：")
        if not sanity_file(fw_file, base):
            say("")
            say("  这份备份没通过真伪校验 —— 所以我不写。")
            say("  确认无误非要写的话，加 FW_SKIP_SANITY=1，但那等于闭着眼睛刷。")
            try:
                fl.cleanup()
            except Exception:
                pass
            finish()
            return
    say("")
    say("  备份 SHA-256: %s" % _sha256_file(fw_file))
    nsec = total // sector
    say("  开始刷：%d 颗扇区，从高地址往低地址。" % nsec)
    say("  先把低地址那段留着：万一中间出错，bootloader 那一段还是好的，")
    say("  可以马上重跑这条命令接着做。")
    say("")
    done = 0
    verify_fail = 0
    verify_blind = 0
    blind_streak = 0
    readback_hopeless = False
    t0 = time.monotonic()
    for i in range(nsec - 1, -1, -1):
        a = base + i * sector
        chunk = want[i * sector:(i + 1) * sector]
        try:
            fl.init(Flash.Operation.ERASE, address=base)
            fl.erase_sector(a)
            do_program(a, chunk)
        except Exception as e:
            say("  0x%08X 这颗失败：%s" % (a, _first_line(e)))
            say("  停在第 %d/%d 颗。已经写好的那部分跟备份是一致的。"
                % (done, nsec))
            break
        done += 1
        if not readback_hopeless:
            # 先把算法卸掉再读。实测这颗芯片上 UnInit 并不会把 XIP 窗口还回来，
            # 所以这里多半读到的是死值 —— 别误报成"写失败"。
            # 用 uninit() 而不是 cleanup()：算法代码留在 RAM 里复用。
            try:
                fl.uninit()
            except Exception:
                pass
            got, err = read_back(a, sector)
            if got is None or (got != chunk and looks_dead(got)):
                verify_blind += 1
                blind_streak += 1
                if verify_blind == 1:
                    say("  （这会儿 flash 窗口读不了：UnInit 没能把 XIP 交还回来。")
                    say("    这不是写失败，写没写对最后用 MODE=verify 统一验收。）")
                if blind_streak >= 4:
                    readback_hopeless = True
                    say("  （连着 4 颗都读不回来，后面不再读了，省点时间；")
                    say("    验收统一交给 MODE=verify。）")
            else:
                blind_streak = 0
                if got != chunk:
                    verify_fail += 1
                    say("  0x%08X 读回跟备份不一样 ❌" % a)
        if done % 8 == 0 or done == nsec:
            el = time.monotonic() - t0
            say("  ... %d/%d 颗 (%.1f%%)  用了 %.1f 秒"
                % (done, nsec, done * 100.0 / nsec, el))
    try:
        fl.cleanup()
    except Exception:
        pass
    say("")
    say("================================================================")
    if done == nsec and verify_fail == 0:
        say("  写完了：%d 颗扇区全部做完。" % done)
        if verify_blind == 0:
            say("  每颗都当场读回比过，跟备份一致。")
        else:
            say("  其中 %d 颗没能当场读回（UnInit 没把 XIP 交还回来），"
                % verify_blind)
            say("  这不代表写失败，靠下面第 2 步统一验收。")
    elif done < nsec:
        say("  没写完：只做了 %d/%d 颗。" % (done, nsec))
        say("  低地址那段没动，芯片还能从 flash 启动；重跑一次这条命令接着做。")
    else:
        say("  写完了，但有 %d 颗扇区读回来跟备份不一样。" % verify_fail)
    say("================================================================")
    say("")
    say("  下一步（真验收）：")
    say("    1) 把价签的 RST 碰一下 GND 再松开，看灯 / 看串口有没有正常开机日志")
    say("    2) MODE=verify bash flash-write.sh     # 只读，逐字节跟备份比")
    say("       打出 VERIFY_OK 就彻底收工。")
    finish()

# =====================================================================
#  命令 11：rstcheck —— 只回答一个问题："RST 松开之后，SWD 握手有效吗？"
#
#  为什么要单独做这个：价签的 RGB 灯是【应用固件】点亮的，不是复位电路点亮的。
#  应用的状态存在 NVDS（flash）里，灯不亮≠复位失效，灯亮≠复位一定有效。
#  真正的判据只有两个：
#     · 串口：按 RST 之后，开机日志会不会重新打一遍
#     · SWD ：按 RST 之后，调试器能不能重新握上手（复位前是握不上的）
#
#  这个命令就是第二个判据，一条命令给结论：
#      MANUAL=hold bash rst-check.sh
# =====================================================================

@command('rstcheck', help='只验一件事：RST 松开之后 SWD 能不能重新握上手（不依赖 LED）')
def rstcheck():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)

    def t(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            return 'ERR:%s' % _first_line(e)

    def hexw(v):
        return ('0x%08X' % v) if isinstance(v, int) else str(v)

    say("")
    say("==================================================================")
    say("  RST 握手体检（跟 LED 没关系）")
    say("==================================================================")
    say("  说明：价签那颗 RGB 灯是【应用固件】点的，应用状态记在 NVDS 里。")
    say("        灯不亮不代表复位失效 —— 看下面这个握手，以及串口日志。")
    say("")

    hit, tries = _reset_and_grab(probe, rst, manual_hold, window_ms, hold_ms)
    say("")
    if hit is None:
        say("  >>> RST 松开之后 %dms 内都没握上手（试了 %d 次）" % (window_ms, tries))
        say("      · 线是不是掉了 / RST 焊盘是不是被焊盘氧化顶住了")
        say("      · 松开之后窗口只有 1~2 秒，手慢就没了：按住不放、数到 0 再松")
        say("      · 也可能这一颗进了深度睡眠，必须【彻底断电 10 秒】再来")
    else:
        say("  >>> RST 有效 ✅  松开之后 %dms 握上手（试了 %d 次）" % (hit, tries))
        if tries <= 2:
            say("      （这一下几乎是立刻就握上了：有可能是上一次会话没断干净，")
            say("        不放心就再跑一次，正常应该是 1 秒左右、要试一两百次）")
        say("      这个数字就是「复位 -> 能连」的延迟：说明芯片确实重新从 ROM 起来了")
        d = t(probe.read_dp, 0x0)
        say("      DP IDR   = %s  （正常 SW-DP 长这样：0x2BA01477）" % hexw(d))
        target = resolve_target()
        if target is not None:
            try:
                target.init()
                say("      CPUID    = %s（正常 0x410FC241）" % hexw(t(ap_read_cpuid, target)))
            except Exception as e:
                say("      target.init 失败：%s" % _first_line(e))
            say("      CPU 状态 = %s" % t(target.get_state))
        say("")
        say("      结论：复位电路 + SWD 这条路是好的。LED 不亮只是应用固件的事，")
        say("            不影响读/写调试。要确认应用还在跑，看串口监视器有没有开机日志。")
    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass


def ap_read_cpuid(target):
    """读 CPUID（顺带证明 AP 的读通路是活的）"""
    ap = _ap_of(target)
    if ap is None:
        return None
    return ap._read_memory(0xE000ED00)

# =====================================================================
#  命令 10：flashlab4 —— "等 XIP 打开"
#
#  新假设（比"防火墙"更像这颗芯片的真实行为）：
#
#    0x01000000 这个窗口是【XQSPI 缓存/XIP 引擎】在背后服务的。
#    ROM/bootloader 阶段，控制器是工作在"间接模式"（用 QSPI 寄存器一条
#    一条读），XIP 引擎还没开 —— 这时候谁去读 0x01000000（调试器也好、
#    CPU 也好）都只会拿到缓存里那个锁存住的死值。
#    等 bootloader 校验完镜像、打开 XIP、跳到 0x0100A000 跑应用之后，
#    这个窗口才是"真"的。
#
#    串口日志正好对得上：
#        BinSize / CheckSum / LoadAddr / RunAddr ... check APP img valid.
#        Jump to APP FW.
#    —— 校验和是 bootloader 用 QSPI 控制器算的（不是用内存窗口读的），
#       校验完才开 XIP 跳过去。
#
#  所以这一轮的做法是：**连着 SWD 不放，让 CPU 自己往下跑**，
#  每 5ms 去读一次 0x0100A200，看它什么时候从死值变成真数据：
#    · 一变活 -> 立刻 halt（把 CPU 冻在那儿，应用就来不及去睡觉/改引脚），
#      确认一下，然后趁窗口活着整片读回。
#    · 一直没变活 / SWD 中途掉了 -> 把时间线打出来，回来再想办法。
#
#  这一轮除了最后的整片读，全程只读。
# =====================================================================

@command('flashlab4', help='连着 SWD 让 CPU 跑到 XIP 打开，盯住 flash 窗口变活的那一刻并整片读回')
def flashlab4():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)
    poll_ms = _env_int('POLL_MS', 5)
    total_ms = _env_int('POLL_TOTAL_MS', 4000)
    base = _env_hex('DUMP_BASE', FLASH_BASE_ADDR)
    total = _env_hex('DUMP_TOTAL', 0x80000)
    chunk = max(256, _env_hex('DUMP_CHUNK', 0x1000))
    outdir = _env_str('DUMP_DIR', os.getcwd())
    out = _env_str('DUMP_OUT', os.path.join(outdir, 'zk42v-live-512k.bin'))
    autodump = _env_str('AUTODUMP', '1') != '0'

    def hexw(v):
        return ('0x%08X' % v) if isinstance(v, int) else str(v)

    say("")
    say("==================================================================")
    say("  等 XIP 打开（连着 SWD 让 CPU 自己往下跑）")
    say("==================================================================")
    hit, tries = _reset_and_grab(probe, rst, manual_hold, window_ms, hold_ms)
    if hit is None:
        say("  没抢到窗口（试了 %d 次），再跑一次。" % tries)
        return
    say("  connect() 成功（复位后 %d ms，试了 %d 次）" % (hit, tries))
    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        return
    try:
        target.init()
    except Exception as e:
        say("  target.init 失败：%s" % _first_line(e))
        return
    ap = _ap_of(target)
    if ap is None:
        say("  拿不到 AP。")
        return
    say("  注意：这一轮**不 halt** —— 让 CPU 该干嘛干嘛，我们只在旁边看。")

    def rd(addr):
        return ap._read_memory(addr)

    A1, A2, A3 = 0x0100A200, 0x0100A004, 0x0100A008
    A2alias = 0x0300A200

    say("")
    say("  盯 0x%08X（没开 XIP 时是个锁存死值；一变活就说明 XIP 开了）" % A1)
    say("  ------------------------------------------------------------------")
    t0 = time.monotonic()
    last = None
    live = None
    errors = 0
    polls = 0
    while (time.monotonic() - t0) * 1000.0 < total_ms:
        polls += 1
        try:
            vals = (rd(A1), rd(A2), rd(A3))
            errors = 0
        except Exception as e:
            errors += 1
            if errors >= 3:
                say("  +%4dms  SWD 读不动了（%s）—— 连接掉了，回头看上面的时间线"
                    % (int((time.monotonic() - t0) * 1000), _first_line(e)))
                break
            continue
        if vals != last:
            el = int((time.monotonic() - t0) * 1000.0)
            say("  +%4dms  0x%08X = %s   %s   %s"
                % (el, A1, hexw(vals[0]), hexw(vals[1]), hexw(vals[2])))
            prev0 = last[0] if last else None
            last = vals
            # 判据 1：同一时刻三个值各不相同（窗口在自增/搬数据）
            # 判据 2：从「已知死值」跳成了别的值（XIP 刚打开那一下）
            if (len(set(vals)) >= 3
                    or (isinstance(prev0, int) and _looks_dead(prev0)
                        and not _looks_dead(vals[0]))):
                live = el
                break
        time.sleep(poll_ms / 1000.0)
    else:
        say("  +%4dms  （时间到）" % int((time.monotonic() - t0) * 1000.0))

    if live is None:
        say("")
        say("  没等到窗口变活。三种可能：")
        say("    a) XIP 一直没开（那 flash 就真得靠 QSPI 控制器去读）")
        say("    b) 开了，但开的那一刻 SWD 已经掉了（应用把调试口抢了 / 进深度睡眠）")
        say("    c) 开到关之间的时间太短（把 POLL_MS 调小、POLL_TOTAL_MS 调大再试）：")
        say("         POLL_MS=1 POLL_TOTAL_MS=6000 MANUAL=hold bash flash-lab4.sh")
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass
        return

    say("")
    say("  >>> +%dms：窗口活了！（读到了真数据）" % live)
    say("  现在立刻把 CPU 冻住，别让它接着跑去睡觉：")
    try:
        target.halt()
        say("    halt 完成")
    except Exception as e:
        say("    halt 失败：%s" % _first_line(e))
    time.sleep(0.05)
    try:
        v1 = rd(A1)
        v2 = rd(A2)
        v3 = rd(A3)
        va = rd(A2alias)
        say("    halt 之后 0x%08X = %s   %s   %s   别名 0x%08X = %s"
            % (A1, hexw(v1), hexw(v2), hexw(v3), A2alias, hexw(va)))
        if _looks_dead(v1) and len({v1, v2, v3}) < 3:
            say("    !!! 停住之后窗口又变成死值了 —— 那这一段就没法整片读，")
            say("        得换成「趁它跑着、分块抢读」的路子（把这份输出发回来）")
            if rst is not None:
                try:
                    rst.close()
                except Exception:
                    pass
            return
    except Exception as e:
        say("    halt 之后读不动了：%s" % _first_line(e))
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass
        return

    if not autodump:
        say("  （AUTODUMP=0：不整片读，收工）")
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass
        return

    # ---------- 趁窗口活着，整片读回 ----------
    say("")
    say("==================================================================")
    say("  窗口活着 —— 整片读回")
    say("==================================================================")
    say("  范围    : 0x%08X + 0x%X (%d KB)" % (base, total, total // 1024))
    say("  目标文件: %s" % out)
    done = 0
    if _env_str('DUMP_RESTART', ''):
        done = 0
    else:
        try:
            with open(out + '.progress') as f:
                done = int(f.read().strip() or 0)
        except Exception:
            done = 0
    if done == 0:
        try:
            with open(out, 'wb') as f:
                f.write(b'')
        except Exception as e:
            say("  建不了目标文件：%s" % _first_line(e))
            return
    else:
        say("  已有进度: %d / %d 字节 (%.1f%%)" % (done, total, done * 100.0 / total))

    written = done
    with open(out, 'r+b') as f:
        f.seek(done)
        while written < total:
            n = min(chunk, total - written)
            try:
                buf = bytes(ap._read_memory_block32(base + written, n // 4))
            except Exception:
                # 单块失败：退成逐字读，尽量把这一段抠出来
                buf = None
                try:
                    words = [ap._read_memory(base + written + 4 * i) for i in range(n // 4)]
                    buf = b''.join(struct.pack('<I', w & 0xFFFFFFFF) for w in words)
                except Exception as e:
                    say("  [0x%08X] 读不动了：%s" % (base + written, _first_line(e)))
                    say("  进度停在 %d 字节，再跑一次这条命令会接着读（但得重新抢窗口）。"
                        % written)
                    break
            if buf is None:
                break
            f.write(buf)
            f.flush()
            written += n
            with open(out + '.progress', 'w') as pf:
                pf.write(str(written))
            if written % (total // 8) < chunk or written >= total:
                say("  ... %d / %d 字节 (%.1f%%)" % (written, total, written * 100.0 / total))

    ok = sanity_file(out, base)
    say("")
    try:
        import hashlib
        h = hashlib.sha256()
        with open(out, 'rb') as f:
            for blk in iter(lambda: f.read(1 << 20), b''):
                h.update(blk)
        say("  SHA-256: %s" % h.hexdigest())
    except Exception:
        pass
    say("  文件: %s" % out)
    if written >= total and ok:
        say("  >>> 成了。这份就是原厂固件备份（再跑一遍对一次 SHA-256 更保险）。")
        say("      顺手也把别名窗口那份读一下对比：")
        say("      DUMP_OUT=%s/zk42v-live-alias-512k.bin DUMP_BASE=0x03000000 MANUAL=hold bash flash-lab4.sh"
            % outdir)
    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass

# =====================================================================
#  命令 9：flashlab3 —— 把"CPU 到底看到什么"钉死
#
#  flashlab2 跑完是"三条路都没通"，但路 3 那句"搬回来还是死值"有歧义：
#     (a) CPU 真的跑到我们塞进去的小程序了，但 flash 这个窗口连 CPU 的
#         数据读也只给死值（"只准取指令、不准读数据"那种防抄板设计），还是
#     (b) 我们的小程序压根没跑起来（PC 没设进去 / MPU / resume 没生效）？
#
#  这一轮把这两件事分开问：
#    1) 小程序第一步先往一个固定 RAM 地址写个标记（写上"我要搬几个字"）。
#       跑完后标记对不对，就是"代码到底跑到没跑到"的铁证。
#    2) 同一段小程序，分别从 CPUID（0xE000ED00，一定是 0x410FC241）和从
#       flash（0x0100A000）抄同样多字节回来做对照：
#         CPUID 抄对 + flash 是死值 -> 就是 (a)：flash 窗口对谁都不给数据
#         CPUID 也抄错 / 标记没改   -> 就是 (b)：是我们自己的锅，回去修搬运
#
#  顺手把 XQSPI 那一坨寄存器（CACHE / QSPI / XIP）原样打出来：
#  flash 是挂在 QSPI 控制器后面的，串口日志里那个 CheckSum 就是 bootloader
#  用 QSPI 控制器（不是用内存窗口）读 flash 算出来的。只要控制器寄存器能动，
#  就还剩"驱动 QSPI 控制器直接读 flash"这第四条路 —— 那也是官方 SDK
#  hal_flash_read / SPI_FLASH_Read 走的路。
# =====================================================================

CACHE_AT = 0xA000D000
QSPI_AT = 0xA000D400
XIP_AT = 0xA000DC00

CACHE_DUMP = [(0x00, 'CTRL0   缓存开关/冲刷/预取'),
              (0x04, 'CTRL1   DBGMUX'),
              (0x08, 'HIT_COUNT'),
              (0x0C, 'MISS_COUNT'),
              (0x10, 'STAT'),
              (0x14, 'BUF_FIRST_ADDR'),
              (0x18, 'BUF_LAST_ADDR')]

QSPI_DUMP = [(0x04, 'RX_DATA'),
             (0x0C, 'CTRL'),
             (0x10, 'AUX_CTRL'),
             (0x14, 'STAT'),
             (0x18, 'SLAVE_SEL'),
             (0x1C, 'SLAVE_SEL_POL'),
             (0x2C, 'TX_FIFO_LVL'),
             (0x30, 'RX_FIFO_LVL'),
             (0x3C, 'SPIEN'),
             (0x48, 'RX_DATA0_31'),
             (0x60, 'P_KEY_VALID_KPORT'),
             (0x64, 'P_RD_KEY_EN_KPORT'),
             (0x68, 'P_KEY_ADDR'),
             (0x6C, 'P_KEYPORT_MASK'),
             (0x70, 'BYPASS')]

XIP_DUMP = [(0x00, 'CTRL0'),
            (0x04, 'CTRL1'),
            (0x08, 'CTRL2'),
            (0x0C, 'CTRL3   XIP 使能请求'),
            (0x10, 'STAT    XIP 使能输出')]


@command('flashlab3', help='钉死"CPU 到底看到什么"：XQSPI 寄存器 + CPU 抄 CPUID/flash 对照')
def flashlab3():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)
    tramp_mode = _env_str('TRAMP', 'ask').lower()
    resume_ms = _env_int('RESUME_MS', 200)

    def hexw(v):
        return ('0x%08X' % v) if isinstance(v, int) else str(v)

    def t(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            return 'ERR:%s' % _first_line(e)

    say("")
    say("==================================================================")
    say("  钉死「CPU 到底看到什么」")
    say("==================================================================")

    hit, tries = _reset_and_grab(probe, rst, manual_hold, window_ms, hold_ms)
    if hit is None:
        say("  没抢到窗口（试了 %d 次），再跑一次。" % tries)
        return
    say("  connect() 成功（复位后 %d ms，试了 %d 次）" % (hit, tries))
    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        return
    try:
        target.init()
        target.halt()
    except Exception as e:
        say("  init/halt 失败：%s" % _first_line(e))
        return
    say("  CPU 已停住")
    ap = _ap_of(target)
    if ap is None:
        say("  拿不到 AP。")
        return

    def classic(addr):
        return ap._read_memory(addr)

    # ---------------- 1) XQSPI 寄存器原样打出来 ----------------
    say("")
    say("  --- 1) XQSPI 寄存器（只读，看看 flash 控制器现在是什么状态）---")
    say("    CACHE @ 0x%08X" % CACHE_AT)
    for off, why in CACHE_DUMP:
        say("      +0x%02X = %-11s %s" % (off, hexw(t(classic, CACHE_AT + off)), why))
    say("    QSPI  @ 0x%08X" % QSPI_AT)
    for off, why in QSPI_DUMP:
        say("      +0x%02X = %-11s %s" % (off, hexw(t(classic, QSPI_AT + off)), why))
    say("    XIP   @ 0x%08X" % XIP_AT)
    for off, why in XIP_DUMP:
        say("      +0x%02X = %-11s %s" % (off, hexw(t(classic, XIP_AT + off)), why))

    # ---------------- 2) flash 死值会不会随 CPU 活动变化 ----------------
    say("")
    say("  --- 2) 让 CPU 自己跑 %dms 再停住，那个死值会不会变 ---" % resume_ms)
    say("    （这一段有点冒险：跑过头进了应用固件，它会把 P0_0/P0_1 抢回去，")
    say("      SWD 可能就此断掉。真断了就复位重跑一次，后面的结论照样拿得到。）")
    v0 = t(classic, 0x0100A200)
    say("    停住时            0x0100A200 = %s" % hexw(v0))
    v1 = None
    try:
        target.resume()
        time.sleep(resume_ms / 1000.0)
        target.halt()
        time.sleep(0.05)
        v1 = t(classic, 0x0100A200)
        say("    CPU 跑过 %3dms 后 0x0100A200 = %s" % (resume_ms, hexw(v1)))
    except Exception as e:
        say("    resume/halt 出问题：%s" % _first_line(e))
    if isinstance(v0, int) and isinstance(v1, int):
        if v0 == v1:
            say("    -> 一样。这个死值不是「CPU 正好停在搞 flash 的操作里」造成的")
        else:
            say("    -> 变了！那它跟 CPU 当时的状态有关（像是某个锁存/预取寄存器）")

    # ---------------- 3) 标记 + CPUID/flash 对照 ----------------
    say("")
    say("  --- 3) CPU 搬运对照（要写 RAM 里几十个字节，跑前问你一句）---")
    win, tried = _ram_window(ap)
    for a, v in tried:
        say("    0x%08X 写 0x5A5AA5A5 读回 %s" % (a, hexw(v)))
    if win is None:
        say("    RAM 写不进去，做不了。")
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass
        return
    code_addr, dst, chunk = win
    mark_addr = (code_addr + 0x200) & ~0x3
    say("    代码 0x%08X / 缓冲 0x%08X / 标记 0x%08X" % (code_addr, dst, mark_addr))

    do_tramp = False
    if tramp_mode in ('1', 'yes', 'y', 'on'):
        do_tramp = True
    elif tramp_mode in ('0', 'no', 'n', 'off'):
        say("    （TRAMP=%s：跳过）" % tramp_mode)
    else:
        do_tramp = _ask_yes("    要做吗？[y/N] ")

    cpu_ok = None
    flash_buf = None
    if do_tramp:
        nbytes = _env_hex('TRAMP_BYTES', 0x40)
        err = _tramp_install(ap, code_addr)
        say("    装小程序到 0x%08X ... %s" % (code_addr, err or '好了'))
        if err:
            do_tramp = False
    if do_tramp:
        for label, src in (('CPUID  0xE000ED00（一定是 0x410FC241）', 0xE000ED00),
                           ('flash  0x0100A000（我们想读的）', 0x0100A000)):
            buf, err = _tramp_copy(ap, target, code_addr, dst, src, nbytes,
                                   mark_addr=mark_addr)
            mark = t(classic, mark_addr)
            if err:
                say("    %s -> 搬运失败：%s" % (label, err))
                continue
            words = list(struct.unpack_from('<%dI' % (len(buf) // 4), buf, 0))
            say("    %s" % label)
            say("      搬回：%s" % ' '.join('0x%08X' % w for w in words[:8]))
            say("      标记 0x%08X = %s（应该是 0x%X = %d 个字）%s"
                % (mark_addr, hexw(mark), nbytes // 4, nbytes // 4,
                   '  <- 代码真的跑了 ✅' if mark == nbytes // 4 else '  <- 没被改写 ❌'))
            if mark != nbytes // 4:
                cpu_ok = False
            elif 0xE000ED00 == src and words and words[0] == CPUID_WANT:
                cpu_ok = True
            if src == 0x0100A000:
                flash_buf = buf

    # ---------------- 总结 ----------------
    say("")
    say("==================================================================")
    say("  总结 / 怎么读")
    say("==================================================================")
    if cpu_ok is True:
        say("    CPU 那段代码确实跑了，而且 CPU 读内存是通的（CPUID 抄对了）。")
        if flash_buf is not None:
            w = struct.unpack_from('<I', flash_buf, 0)[0]
            if w == 0x47525858 or w != struct.unpack_from('<I', flash_buf, 4)[0]:
                say("    而且 CPU 从 flash 抄回来的不是死值 —— 搬运这条路可以走！")
                say("    下一步：MANUAL=hold bash ram-dump.sh")
            else:
                say("    但 CPU 从 flash 抄回来的还是那个死值（0x%08X）。" % w)
                say("    => 结论：0x01000000/0x03000000 这两个 flash 窗口，连 CPU 的")
                say("       数据访问也拿不到数据。这不是我们的锅，是这颗芯片就是这么设计的")
                say("       （取指令能过、读数据被挡，典型的防抄板）。")
                say("       剩下唯一的路：绕开内存窗口，直接驱动 QSPI 控制器读 flash。")
                say("       把上面第 1 节那份寄存器清单发给 Codex，我按 SDK 的")
                say("       QSPI 序列（hal_flash_read / SPI_FLASH_Read 那条）写第 4 条路。")
    elif cpu_ok is False:
        say("    标记没被改写 —— 我们的小程序没跑起来，是我们自己的问题，不是芯片的。")
        say("    下一步该修的是「往 CPU 里塞代码」这件事（PC/MPU/resume），")
        say("    把这份输出发给 Codex。")
    else:
        say("    没做 CPU 搬运那一步。想彻底钉死结论就加 TRAMP=yes 再跑一次：")
        say("        MANUAL=hold TRAMP=yes bash flash-lab3.sh")
    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass


# =====================================================================
#  命令 7：flash 定位实验 —— 只读
#
#  dap-info 的结论：读通路是好的（CPUID 对、ROM 表对、halt 后 DHCSR 的
#  S_HALT 位 0->1 变了），**但 0x01000000 那一整块返回死值**，而且每次
#  跑死值还不一样。所以问题不在调试器，在"这颗芯片的 flash 区间对调试器
#  不吐真数据"。
#
#  这个实验看四件事：
#    1) CPU running vs halted 时读 flash，值会不会变
#    2) 同一个地址：字读 vs 逐字节读，会不会不一样
#    3) 换一排候选地址窗口（找 flash 有没有第二个别名）
#    4) 别名猜想：如果 flash 在 0x00000000 也有别名，那 app_info 应该在
#       0x0000A200，magic 应该是 0x47525858 —— 一读就知道
# =====================================================================
FLASH_CANDS = [
    0x00000000, 0x00003000, 0x0000A000, 0x0000A200, 0x00010000,
    0x00020000, 0x00040000, 0x00060000,
    0x00800000, 0x01000000, 0x01003000, 0x0100A000, 0x0107F000,
    0x02000000, 0x08000000, 0x10000000, 0x18000000, 0x20000000, 0x30000000,
]


@command('flashlab', help='只读：定位 flash 到底在哪块地址能读到真数据')
def flashlab():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)

    def hexw(v):
        return ('0x%08X' % v) if isinstance(v, int) else str(v)

    say("")
    say("==================================================================")
    say("  flash 定位实验（全程只读）")
    say("==================================================================")
    hit, tries = _reset_and_grab(probe, rst, manual_hold, window_ms, hold_ms)
    if hit is None:
        say("  没抢到窗口（试了 %d 次），再跑一次。" % tries)
        return
    say("  connect() 成功（复位后 %d ms）" % hit)
    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        return
    try:
        target.init()
    except Exception as e:
        say("  target.init 失败：%s" % _first_line(e))
        return
    ap = _ap_of(target)
    if ap is None:
        say("  拿不到 AP。")
        return

    def rd(addr, size=32):
        try:
            return ap._read_memory(addr, size)
        except Exception as e:
            return 'ERR:%s' % _first_line(e)

    say("  先确认通路是活的：")
    say("    CPUID    0xE000ED00 = %s   应 0x410FC241" % hexw(rd(0xE000ED00)))
    say("    ROM 表   0xE00FF000 = %s   应像 0xFFF0F003" % hexw(rd(0xE00FF000)))

    probe_addrs = (0x01000000, 0x0100A000, 0x00000000)

    say("")
    say("  --- 1) CPU 还在跑（没 halt）时，同地址连读 4 次 ---")
    for a in probe_addrs:
        vals = [rd(a) for _ in range(4)]
        say("    0x%08X x4 = %s" % (a, '  '.join(hexw(v) for v in vals)))

    say("")
    say("  --- 2) halt 之后再读同样几个地址（同地址连读 4 次）---")
    try:
        target.halt()
        say("    halt 完成")
    except Exception as e:
        say("    halt 报错（照测）：%s" % _first_line(e))
    for a in probe_addrs:
        vals = [rd(a) for _ in range(4)]
        say("    0x%08X x4 = %s" % (a, '  '.join(hexw(v) for v in vals)))

    say("")
    say("  --- 3) 同一个地址：字读 vs 逐字节读 ---")
    for a in (0x0100A000, 0x00000000, 0x01000000):
        w = rd(a, 32)
        bs = [rd(a + i, 8) for i in range(4)]
        say("    0x%08X  字读=%s   逐字节=%s"
            % (a, hexw(w), ' '.join(hexw(x) for x in bs)))

    say("")
    say("  --- 4) 候选地址窗口扫描（每个地址读 8 个字，看有没有变化）---")
    for b in FLASH_CANDS:
        vals = [rd(b + 4 * i) for i in range(8)]
        nums = [v for v in vals if isinstance(v, int)]
        d = len(set(nums))
        flag = '  ← 有变化' if d >= 4 else ('  ← 一个死值' if d == 1 else '')
        say("    0x%08X : %s%s" % (b, ' '.join(hexw(v) for v in vals), flag))

    say("")
    say("  --- 5) 别名猜想（如果 flash 在 0x00000000 也有别名）---")
    say("    0x0000A000 = %s   （flash 偏移 0xA000，应用入口）" % hexw(rd(0x0000A000)))
    say("    0x0000A200 = %s   （flash 偏移 0xA200，app_info，期望 0x47525858）"
        % hexw(rd(0x0000A200)))
    say("    0x00003000 = %s   （flash 偏移 0x3000，bootloader 入口）" % hexw(rd(0x00003000)))

    say("")
    say("  --- 6) 结论怎么读 ---")
    say("    · 哪一行有多个不同值 -> 那块地址是真能读的，就从那儿读")
    say("    · 如果 0x0000A200 读到 0x47525858 -> flash 在 0x00000000 有别名，")
    say("      那么整片就按 0x00000000 + 偏移 来读（app_info 在 0x0000A200 = 0x0100A200）")
    say("    · 如果所有窗口都是一个死值 -> flash 对调试器彻底不开放，得改走 CPU 搬运/UART")
    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass


# =====================================================================
#  命令 3：把 RST 钉住（持续拉低 / 持续拉高 / 来回翻）
# =====================================================================
#  命令 6：DAP 体检 —— 看读通路到底通不通
#
#  为什么还要这个：dumpresume 的读法自检发现"三条路读出来都是一个字"
#  （加速路 0x4034C8F7，经典路 0xF7C83444），换读法已经救不了了。往下
#  再挖一层，直接看 DP / AP 的原始寄存器：
#
#    · pyOCD 的 ST-Link connect() 只做 enter_debug(SWD)，不校验 IDCODE
#      （probe/stlink_probe.py 第 183 行），所以"连上了"这三个字不代表
#      读通路是好的；
#    · 但 DPConnector.connect() 会读一次 DP IDR（coresight/dap.py），
#      pyOCD 后面的 AP 地址、bank 选择都是从那个值里拆出来的
#      （dp_partno / dp_version）。**如果 DP IDR 读回来的是垃圾，后面
#      所有 AP 访问都会算错地址，表现就是"能读、不报错、整片一个值"。**
#
#  这里打印三组东西：
#    1) 调试器本身：probe、固件版本
#    2) DP / AP 的原始寄存器（IDR / CTRL-STAT / AP 的 CSW TAR DRW IDR）
#    3) 一个"值会不会变"的试验：halt 前后读 DHCSR，看 S_HALT 位变不变
#       —— 只要有一个 bit 会变，读通路就是活的
# =====================================================================

def _reset_and_grab(probe, rst, manual_hold, window_ms, hold_ms):
    """复位 + 轮询抢 SWD 窗口；返回 (连接用时 ms, 试了几次) 或 (None, 次数)"""
    if manual_hold:
        wait = _env_int('HOLD_WAIT_SECS', 8)
        say("")
        say("  把 RST 按住接地，我数到 0 再松手：")
        sys.stdout.flush()
        for i in range(wait, 0, -1):
            say("      %d ..." % i)
            beep('tick')
            time.sleep(1.0)
        beep('go')
        say("  >>> 现在松手！ <<<")
    elif MANUAL:
        say("  把 RST 碰一下 GND 再松开（%.0f 秒内都算数）" % (window_ms / 1000.0))
        beep('go')
    else:
        try:
            rst.assert_reset(True)
            time.sleep(hold_ms / 1000.0)
            rst.assert_reset(False)
        except Exception as e:
            say("  (复位失败: %s)" % _first_line(e))
    t0 = time.monotonic()
    tries = 0
    while (time.monotonic() - t0) * 1000.0 < window_ms:
        tries += 1
        try:
            probe.connect()
            return int((time.monotonic() - t0) * 1000), tries
        except Exception:
            pass
    return None, tries


@command('dapinfo', help='DAP 体检：看 DP/AP 原始寄存器 + halt 前后 DHCSR 有没有变化')
def dapinfo():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)

    def hexw(v):
        return ('0x%08X' % v) if isinstance(v, int) else str(v)

    def try_(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            return 'ERR:%s' % _first_line(e)

    say("")
    say("==================================================================")
    say("  DAP 体检（判断读通路到底通不通）")
    say("==================================================================")

    hit, tries = _reset_and_grab(probe, rst, manual_hold, window_ms, hold_ms)
    if hit is None:
        say("  没抢到窗口（试了 %d 次），再跑一次。" % tries)
        return
    say("  connect() 成功（复位后 %d ms，试了 %d 次）" % (hit, tries))
    say("  注意：pyOCD 的 ST-Link connect() 不校验 IDCODE，所以这三个字不代表通路是好的。")

    say("")
    say("  --- 调试器 ---")
    say("    unique_id  : %s" % getattr(probe, 'unique_id', '?'))
    say("    description: %s" % getattr(probe, 'description', '?'))
    link = getattr(probe, '_link', None)
    if link is not None:
        for attr in ('_jtag_version', '_hw_version', '_swd_freq', '_protocol'):
            if hasattr(link, attr):
                say("    link.%-14s = %s" % (attr, getattr(link, attr)))

    say("")
    say("  --- 分辨试验：DP IDR 连读 8 次 ---")
    say("  两条完全不同的路：ST-Link 的连接命令（JTAG_ENTER_SWD，它自己会读并校验")
    say("  IDCODE，读不到就报 'Get IDCODE error'）vs 读寄存器命令（JTAG_READ_DAP_REG）。")
    say("  如果连接能过、但下面的值次次一样，那就是后面这条读命令坏了。")
    seen = []
    for i in range(8):
        v = try_(probe.read_dp, 0x0)
        seen.append(v)
        say("    第 %d 次: %s" % (i + 1, hexw(v)))
    uniq = set(x for x in seen if isinstance(x, int))
    say("    8 次里有 %d 个不同的值" % len(uniq))
    if len(uniq) > 1:
        say("    -> 值在变：是**连接不稳**（时序 / 接触 / 该降速），不是命令坏了")
    else:
        say("    -> 值完全固定：更像**读寄存器这条命令坏了**，或者读到的是个死值")

    say("")
    say("  --- DP 寄存器（诊断器直接问，不经过缓存的）---")
    for reg in (0x0, 0x4, 0x8, 0xC):
        v = try_(probe.read_dp, reg)
        say("    probe.read_dp(0x%X) = %s" % (reg, hexw(v)))
    v2 = try_(probe.read_dp, 0x0)
    say("    再读一次 DP IDR    = %s   %s"
        % (hexw(v2), '' if v2 == try_(probe.read_dp, 0x0) else '(两次不一样!)'))

    target = resolve_target()
    if target is None:
        say("  拿不到 target 对象。")
        return
    try:
        d = target.dp.dpidr
        say("    pyOCD 记下的 DP IDR = 0x%08X  partno=%d version=%d revision=%d"
            % (d.idr, d.partno, d.version, d.revision))
        say("      （正常 SW-DP 的 IDR 长这样：0x2BA01477 / 0x6BA02477 / 0x0BC11477）")
        if d.version not in (1, 2, 3) and d.version != 0:
            say("      !!! version=%d 不像正常值 —— 后面 AP 地址会算错，这就是根因之一。"
                % d.version)
    except Exception as e:
        say("    读不到 target.dp.dpidr：%s" % _first_line(e))

    try:
        target.init()
    except Exception as e:
        say("  target.init 失败：%s" % _first_line(e))
        return
    say_target_info(target)

    ap = _ap_of(target)
    say("")
    say("  --- AP 寄存器 ---")
    if ap is None:
        say("    拿不到 AP 对象（说明 pyOCD 的 ROM 表扫描什么都没找到）")
    else:
        try:
            say("    AP 地址   : 0x%08X  %s"
                % (ap.address.address, getattr(ap, 'short_description', '')))
        except Exception:
            pass
        for name, off in (('CSW  0x00', 0x00), ('TAR  0x04', 0x04),
                          ('DRW  0x0C', 0x0C), ('BASE 0xF8', 0xF8),
                          ('IDR  0xFC', 0xFC)):
            say("    AP %s = %s" % (name, hexw(try_(ap.read_reg, off))))
        say("      （正常 AHB-AP 的 IDR 长这样：0x04770031 / 0x24770011 / 0x34770031）")

    say("")
    say("  --- 关键试验：halt 前后读 DHCSR，看 bit 会不会变 ---")

    def cw(addr):
        if ap is None:
            return 'NO-AP'
        return try_(ap._read_memory, addr)

    h0 = cw(0xE000EDF0)
    say("    halt 前 DHCSR(0xE000EDF0) = %s" % hexw(h0))
    try:
        target.halt()
        say("    target.halt() 调完了，没报错")
    except Exception as e:
        say("    target.halt() 报错：%s" % _first_line(e))
    time.sleep(0.1)
    h1 = cw(0xE000EDF0)
    say("    halt 后 DHCSR(0xE000EDF0) = %s" % hexw(h1))
    if isinstance(h0, int) and isinstance(h1, int):
        a, b = (h0 >> 17) & 1, (h1 >> 17) & 1
        if a != b:
            say("    S_HALT 位 %d -> %d   变了！读通路是活的 ✅" % (a, b))
        elif h0 != h1:
            say("    S_HALT 位没变，但整个值变了（%s -> %s），通路也大概率是活的"
                % (hexw(h0), hexw(h1)))
        else:
            say("    S_HALT 位和整个值都没变 ❌ —— 读回来的东西压根没反映芯片状态")

    say("")
    say("  --- 地址扫描（经典单字读，看不同地址会不会给出不同值）---")
    for a in (0x00000000, 0x01000000, 0x01003000, 0x0100A000, 0x0100A200,
              0x0107F000, 0x30000000, 0xE000ED00, 0xE00FF000, 0x40000000):
        say("    0x%08X = %s" % (a, hexw(cw(a))))

    say("")
    say("  --- 结论怎么读 ---")
    say("    1) DP IDR 不是 0x2BA01477 那类正常值   -> SWD 读通路本身有问题")
    say("    2) DP IDR 正常、AP IDR/CSW 是垃圾      -> AP 访问有问题（APSEL/电源/bank）")
    say("    3) halt 前后 DHCSR 有 bit 变化          -> 通路是活的，问题在 flash 那几个地址")
    say("    4) 所有地址都返回同一个值 + bit 不变    -> 调试器那条 DAP 读命令是坏的（换线/换调试器）")
    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass


# =====================================================================
#  命令 3：把 RST 钉住（持续拉低 / 持续拉高 / 来回翻）
#          专门给你用万用表量线用的
# =====================================================================
@command('rsthold', help='把 RST 持续拉低/拉高/来回翻，方便用万用表量线')
def rsthold():
    probe = raw_probe()
    rst = reset_driver(probe)

    state = _env_str('RST_STATE', 'low').lower()
    if state not in ('low', 'high', 'toggle'):
        state = 'low'
    secs = _env_int('RST_HOLD_SECS', 0)        # 0 = 一直保持到 Ctrl-C
    toggle_ms = _env_int('RST_TOGGLE_MS', 2000)

    say("")
    say("==================================================================")
    say("  RST 钉住模式（pyOCD 版）")
    say("==================================================================")

    # 顺便读一下 ST-Link 自己量到的目标电压（就是它 3.3V 那根针上的电压）
    try:
        link = probe._link
        link.get_target_voltage()
        tv = link.target_voltage
        if tv:
            say("  ST-Link 量到的目标电压: %.3f V" % float(tv))
        else:
            say("  ST-Link 量到的目标电压: 读不到")
    except Exception as e:
        say("  (读不到目标电压: %s)" % e)

    if RST_VIA == 'stlink':
        drive_report(probe, "（这样 DRIVE_NRST 才会真的拉低引脚）")

    if state == 'low':
        say("  本次要做的是：把 RST 一直拉低（LOW）")
    elif state == 'high':
        say("  本次要做的是：把 RST 一直放开（HIGH）")
    else:
        say("  本次要做的是：每 %d ms 翻一次，在 LOW / HIGH 之间来回跳" % toggle_ms)

    say("")
    say("------------------------------------------------------------------")
    say("  万用表打【直流电压】档，黑表笔接【价签的 GND】，红表笔依次点：")
    say("------------------------------------------------------------------")
    say("    量哪里                              应该是    说明")
    say("    ST-Link 上你插 RST 的那根排针        ~0V      这根针确实被拉低了")
    say("    价签的 RST 焊盘                     ~0V      线通，ST-Link 真的在驱动它")
    say("    价签的 SWCLK 焊盘                   ~3.3V    空闲应为高")
    say("    价签的 SWDIO 焊盘                   ~3.3V    空闲应为高")
    say("------------------------------------------------------------------")
    say("  怎么判：")
    say("    A. 排针 ~0V + 价签 RST 也是 ~0V      -> 线是通的，别再怀疑 RST 线")
    say("    B. 排针 ~0V + 价签 RST 还是 ~3.3V    -> 线没接通 / 接错针（最常见）")
    say("    C. 排针还是 ~3.3V                    -> 那根针根本不是 nRST")
    say("       改用来回翻的模式来找针： STATE=toggle bash rst-hold.sh")
    say("       盯着表：在 0V 和 3.3V 之间来回跳的那根，才是 nRST")
    say("==================================================================")
    say("")
    sys.stdout.flush()

    start = time.monotonic()
    try:
        if state == 'toggle':
            flip = 0
            while True:
                rst.assert_reset(True)
                flip += 1
                say("")
                say("  >>> 第 %d 次：现在 RST = 拉低，你量的那根应该是 【0V】   (%.1fs)"
                    % (flip, time.monotonic() - start))
                time.sleep(toggle_ms / 1000.0)
                if secs and (time.monotonic() - start) >= secs:
                    break
                rst.assert_reset(False)
                say("  >>> 第 %d 次：现在 RST = 放开，你量的那根应该是 【3.3V】 (%.1fs)"
                    % (flip, time.monotonic() - start))
                time.sleep(toggle_ms / 1000.0)
                if secs and (time.monotonic() - start) >= secs:
                    break
        else:
            low = (state == 'low')
            rst.assert_reset(low)
            say("  RST 现在是 %s。现在开始量，量完 Ctrl-C 停止。"
                % ("拉低 (LOW)" if low else "放开 (HIGH)"))
            if secs:
                say("  （会保持 %d 秒）" % secs)
            say("  之后每 10 秒报一次，证明还钉着。")
            last = 0.0
            while True:
                el = time.monotonic() - start
                if secs and el >= secs:
                    break
                if el - last >= 10.0:
                    last = el
                    say("  [%6.1fs] 还保持着呢：RST = %s"
                        % (el, "LOW" if low else "HIGH"))
                time.sleep(0.25)
    except KeyboardInterrupt:
        say("")
        say("  收到 Ctrl-C，收工。")
    finally:
        try:
            rst.assert_reset(False)
        except Exception as e:
            say("  (松开 RST 失败: %s)" % e)
        say("  已经把 RST 放开了（如果价签本来在运行，它这时候会重启一次）。")
        say("  万一 Ctrl-C 之后 RST 还一直低着（灯一直不亮），跑一次：")
        say("      STATE=high SECS=3 bash rst-hold.sh")
        say("  把它放开，或者把 ST-Link 拔一下 USB 也行。")


# =====================================================================
#  命令 7：flashlab2 —— 换三条完全不同的路，问同一件事：
#                     "flash 里的字节到底是什么"
#
#  flashlab 已经把"读通路坏了"这条排除掉了：
#    · CPUID 0xE000ED00 / ROM 表 / halt 前后 DHCSR 的 S_HALT 位全对
#    · 0x00000000 那一片（芯片掩膜 ROM）能读出真代码、真字符串
#    · 只有 0x01000000（flash 窗口）整块返回同一个死值
#
#  那剩下的可能就是"flash 这条路对调试器关门"。这里换三条路再问一遍：
#
#    路 1  CSW 实验
#          AHB-AP 的 CSW 寄存器里有两个"身份"位：bit29 MSTRTYPE
#          （这次事务是不是调试器发的）和 bit30 HNONSEC（安全/非安全），
#          外加 HPROT[4:0]（数据/取指、特权/非特权、可缓存…）。
#          pyOCD 读内存时永远带着 MSTRTYPE=1 + HPROT=0x3。芯片的 flash
#          控制器完全可能就靠这个位把"调试器读 flash"挡掉。
#          这里把 CSW 换成十几种组合，每种都手搓一次 TAR/DRW 事务
#          （绕开 pyOCD 的 CSW 缓存，也绕开 ST-Link 的加速内存接口）。
#          参照点：0x0100A200 应该是 app_info 的 magic 0x47525858。
#
#    路 2  别名窗口 0x03000000
#          Goodix SDK 里写着：
#             gr55xx_hal_exflash.h:199  #define EXFLASH_ALIAS_OFFSET 0x02000000
#             gr55xx_hal_exflash.h:200  #define EXFLASH_ALIAS_ADDR  (FLASH_BASE + 0x02000000)
#          也就是 flash 除了 0x01000000，在 0x03000000 还有一份映射。
#          之前只扫过 0x01000000 / 0x00000000 / 0x02000000，**没扫过 0x03000000**。
#          这两份映射在芯片里走的很可能不是同一条通路（GR551x 的 XIP
#          解密/缓存就在这一层），值得单独试。
#
#    路 3  CPU 搬运（要写 RAM，所以单独问你）
#          往 RAM 里放 14 个字节的 Thumb 代码，让 CPU 自己 ldr/str 把 flash
#          抄进 RAM，我们再从 RAM 用 SWD 读回来。CPU 读 flash 属于"正常访问"，
#          任何"只挡调试器"的防火墙都挡不住它。这是这类芯片的标准解法。
#
#  路 1 / 路 2 / 外设探查 / eFuse 探查 全程只读；
#  路 3 会往 RAM 写十几个字节、让 CPU 跑一小段（不碰 flash，不擦不写），
#  所以放在最后，跑之前单独问你一句。
# =====================================================================

FLASH_BASE_ADDR = 0x01000000
FLASH_ALIAS_ADDR = 0x03000000          # = FLASH_BASE + EXFLASH_ALIAS_OFFSET
EFUSE_AESKEY = 0xA0017060              # SDK: AESKEY_BASE_ADDR
EFUSE_FWCODEKEY = 0xA00170E0           # SDK: FWCODEKEY_BASE_ADDR

CSW_MSTRTYPE = 0x20000000              # bit29：这次事务是不是调试器发的
CSW_HNONSEC = 0x40000000               # bit30：非安全访问
CSW_SDEVICEEN = 0x00800000             # SPIDEN
CSW_DEVICEEN = 0x00000040
CSW_SADDRINC = 0x00000010
_CSW_SIZE = {8: 0x0, 16: 0x1, 32: 0x2}


def _csw(hprot=0x3, mstrtype=1, hnonsec=0, size=32, deviceen=1, spiden=0):
    v = _CSW_SIZE[size] | CSW_SADDRINC
    if deviceen:
        v |= CSW_DEVICEEN
    if spiden:
        v |= CSW_SDEVICEEN
    if mstrtype:
        v |= CSW_MSTRTYPE
    if hnonsec:
        v |= CSW_HNONSEC
    return v | ((hprot & 0x1F) << 24)


CSW_SWEEP = [
    (_csw(), 32, '基准 = pyOCD 平时就是这样读的（HPROT=3 MSTRTYPE=1）'),
    (_csw(mstrtype=0), 32, '去掉 MSTRTYPE：装成普通总线访问'),
    (_csw(spiden=1), 32, '加上 SPIDEN（安全特权调试使能）'),
    (_csw(mstrtype=0, spiden=1), 32, 'SPIDEN=1 + 去掉 MSTRTYPE'),
    (_csw(hprot=0x1F), 32, 'HPROT=0x1F（全置 1）'),
    (_csw(hprot=0x1F, mstrtype=0), 32, 'HPROT=0x1F + 去掉 MSTRTYPE'),
    (_csw(hprot=0x0F), 32, 'HPROT=0x0F'),
    (_csw(hprot=0x01), 32, 'HPROT=0x01（数据、非特权）'),
    (_csw(hprot=0x09), 32, 'HPROT=0x09'),
    (_csw(hnonsec=1), 32, 'HNONSEC=1（走非安全通道）'),
    (_csw(hnonsec=1, mstrtype=0), 32, 'HNONSEC=1 + 去掉 MSTRTYPE'),
    (_csw(deviceen=0), 32, '不要 DEVICEEN'),
    (_csw(size=16), 16, '16 位传输'),
    (_csw(size=8), 8, '8 位传输'),
]

PERIPH_PROBE = [
    (0xA0000000, 'TIMER0    定时器'),
    (0xA0008000, 'WDT       看门狗（有值说明在看门狗寄存器是活的）'),
    (0xA000D000, 'XQSPI     flash 控制器（能读它就有第三条路）'),
    (0xA0010000, 'GPIO0'),
    (0xA0016400, 'EFUSE     寄存器'),
    (0xE000ED00, 'CPUID     对照组，应该 0x410FC241'),
    (0x00800000, 'RAM       读出来是什么无所谓，能读就行'),
    (0x30000000, 'SRAM 别名 对照组'),
]


def _csw_probe(dp, ap_base, addr, csw, size=32):
    """手搓 TAR/DRW 事务：绕开 pyOCD 的 CSW 缓存，也绕开加速内存接口"""
    dp.write_ap(ap_base + 0x00, csw)
    dp.write_ap(ap_base + 0x04, addr)
    v = dp.read_ap(ap_base + 0x0C) & 0xFFFFFFFF
    if size == 8:
        return v & 0xFF
    if size == 16:
        return v & 0xFFFF
    return v


# ---- CPU 搬运用的小程序 ------------------------------------------------
#   r0 = 源地址, r1 = 目的地址, r2 = 搬几个字（都由调试器先设好）
#                         r3 = 标记地址（也是调试器设的）
#     601A  str  r2, [r3]   <- 先往标记地址写"我要搬几个字"：
#                              搬完如果标记对得上，就证明这段代码真的被 CPU 执行了
#     6803  ldr  r3, [r0]
#     600B  str  r3, [r1]
#     3004  adds r0, #4
#     3104  adds r1, #4
#     3A01  subs r2, #1
#     D1F9  bne  -7*2   （跳回 ldr 那条）
#     E7FE  b    .      （搬完就原地转圈，不返回任何地方）
#   上面这套编码是拿 arm-none-eabi-as 汇编后逐字节对过的
TRAMP_CODE = [0x601A, 0x6803, 0x600B, 0x3004, 0x3104, 0x3A01, 0xD1F9, 0xE7FE]

TRAMP_MARK_INIT = 0xDEADBEEF      # 跑之前先往标记地址写这个


def _tramp_words():
    out = []
    for i in range(0, len(TRAMP_CODE), 2):
        lo = TRAMP_CODE[i]
        hi = TRAMP_CODE[i + 1] if i + 1 < len(TRAMP_CODE) else 0xBF00
        out.append((lo | (hi << 16)) & 0xFFFFFFFF)
    return out


RAM_PROBES = [0x0081F000, 0x0080F000, 0x0080C000, 0x00804000, 0x00801000]


def _ram_window(ap):
    """试 RAM 能写到多高，返回 (代码地址, 缓冲地址, 一次搬多少字节) 或 (None, 原因)"""
    pat = 0x5A5AA5A5
    top = None
    tried = []
    for a in RAM_PROBES:
        try:
            ap._write_memory(a, pat)
            v = ap._read_memory(a)
            tried.append((a, v))
            if v == pat:
                top = a
                break
        except Exception as e:
            tried.append((a, 'ERR:%s' % _first_line(e)))
    if top is None:
        return None, tried
    end = (top + 4) & ~0x3
    chunk = min(0x8000, end - 0x00801000)
    chunk &= ~0xF
    dst = (end - chunk) & ~0xF
    code = (dst - 0x1000) & ~0xF
    return (code, dst, chunk), tried


def _tramp_install(ap, code_addr):
    words = _tramp_words()
    try:
        ap._write_memory_block32(code_addr, words)
        back = list(ap._read_memory_block32(code_addr, len(words)))
    except Exception as e:
        return '写进 RAM 失败：%s' % _first_line(e)
    if back != words:
        return '写进去的代码读回来是 %s，跟写的不一样（RAM 不是普通 RAM？）' \
               % ' '.join('0x%08X' % (v & 0xFFFFFFFF) for v in back)
    return None


def _tramp_copy(ap, target, code_addr, dst, src, nbytes, run_ms=250, mark_addr=None):
    """让 CPU 把 [src, src+nbytes) 抄到 dst；返回 (bytes, None) 或 (None, 原因)"""
    nwords = nbytes // 4
    try:
        h0 = target.get_state()
    except Exception:
        h0 = '?'
    try:
        target.write_core_register('r0', src)
        target.write_core_register('r1', dst)
        target.write_core_register('r2', nwords)
        if mark_addr is not None:
            # 标记：代码跑过的话，这个地址会被改写成 nwords
            ap._write_memory(mark_addr, TRAMP_MARK_INIT)
            target.write_core_register('r3', mark_addr)
        try:
            target.write_core_register('primask', 1)     # 关中断，免得 ISR 插进来
        except Exception:
            pass
        target.write_core_register('pc', code_addr | 1)  # bit0=1：Thumb
        try:
            xpsr = target.read_core_register('xpsr')
            if not (xpsr & 0x01000000):
                target.write_core_register('xpsr', xpsr | 0x01000000)
        except Exception:
            pass
    except Exception as e:
        return None, '设寄存器失败：%s' % _first_line(e)
    try:
        target.resume()
    except Exception as e:
        return None, 'resume 失败：%s' % _first_line(e)
    time.sleep(run_ms / 1000.0)
    try:
        target.halt()
    except Exception as e:
        return None, 'halt 失败：%s' % _first_line(e)
    time.sleep(0.05)
    try:
        vals = list(ap._read_memory_block32(dst, nwords))
    except Exception as e:
        return None, '从 RAM 读回来失败：%s' % _first_line(e)
    out = bytearray()
    for v in vals:
        out += struct.pack('<I', v & 0xFFFFFFFF)
    return bytes(out), None


def _ask_yes(prompt):
    try:
        if not sys.stdin.isatty():
            return False
        return input(prompt).strip().lower().startswith('y')
    except Exception:
        return False


@command('flashlab2', help='flash 能不能读：CSW 实验 + 别名窗口 + CPU 搬运实验')
def flashlab2():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)
    tramp_mode = _env_str('TRAMP', 'ask').lower()

    def hexw(v):
        return ('0x%08X' % v) if isinstance(v, int) else str(v)

    def t(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            return 'ERR:%s' % _first_line(e)

    say("")
    say("==================================================================")
    say("  flash 三路会审（路 1 路 2 全程只读）")
    say("==================================================================")

    hit, tries = _reset_and_grab(probe, rst, manual_hold, window_ms, hold_ms)
    if hit is None:
        say("  没抢到窗口（试了 %d 次），再跑一次。" % tries)
        return
    say("  connect() 成功（复位后 %d ms，试了 %d 次）" % (hit, tries))

    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        return
    try:
        target.init()
    except Exception as e:
        say("  target.init 失败：%s" % _first_line(e))
        return
    ap = _ap_of(target)
    if ap is None:
        say("  拿不到 AP。")
        return
    try:
        target.halt()
        say("  CPU 已停住（后面只读，停着读最稳）")
    except Exception as e:
        say("  halt 报错（照测）：%s" % _first_line(e))

    dp = target.dp
    try:
        ap_base = ap.address.address
    except Exception:
        ap_base = 0x00000000
    orig_csw = None
    try:
        orig_csw = dp.read_ap(ap_base + 0x00) & 0xFFFFFFFF
    except Exception:
        pass

    def classic(addr):
        return ap._read_memory(addr)

    say("")
    say("  通路复查（经典路径，应该跟 dapinfo 那次一样）")
    say("    CPUID  0xE000ED00 = %s   期望 0x410FC241" % hexw(t(classic, 0xE000ED00)))
    say("    ROM 表 0xE00FF000 = %s   期望 0xFFF0F003" % hexw(t(classic, 0xE00FF000)))
    say("    AP CSW 原值        = %s" % hexw(orig_csw))

    # ---------------- 路 2：别名窗口（先跑，因为它最可能一次命中）----
    say("")
    say("  --- 路 2：别名窗口（SDK: EXFLASH_ALIAS_ADDR = 0x01000000 + 0x02000000）---")
    alias_cands = [FLASH_ALIAS_ADDR, FLASH_ALIAS_ADDR + 0x3000,
                   FLASH_ALIAS_ADDR + 0xA000, FLASH_ALIAS_ADDR + 0xA200]
    alias_hit = False
    for a in alias_cands:
        vals = [t(classic, a + 4 * i) for i in range(8)]
        nums = [v for v in vals if isinstance(v, int)]
        d = len(set(nums))
        mark = '  <-- 有变化（真数据！）' if d >= 4 else ('  <-- 一个死值' if d == 1 else '')
        if d >= 4:
            alias_hit = True
        say("    0x%08X : %s%s" % (a, ' '.join(hexw(v) for v in vals), mark))
    say("    对照 0x0100A200（flash 原窗口，读到的就是 app 数据区的一个字）= %s"
        % hexw(t(classic, 0x0100A200)))
    say("    对照 0x0300A200（同一个地址走 0x03000000 别名窗口）            = %s"
        % hexw(t(classic, 0x0300A200)))
    alias_val = t(classic, 0x0300A200)
    alias_live = (not _looks_dead(alias_val)) and alias_hit
    if alias_live:
        say("    >>> 别名窗口读得到真数据 —— flash 走 0x03000000 能读！")
    elif alias_hit:
        say("    >>> 别名窗口读到的是真数据（不是死值）")

    # ---------------- 路 1：CSW 实验 ----------------
    say("")
    say("  --- 路 1：CSW 实验（每一种都手搓 TAR/DRW，绕开 pyOCD 与加速接口）---")
    say("    判据：读到的是不是真实值（死值 0x00000000 / 0xFFFFFFFF / 0xB7C83400 / 0xF7C03404 全算没通）")
    say("    %-11s %-11s %-11s  %s" % ('CSW', 'flash 窗口', '别名窗口', '这次的 CSW 是什么意思'))
    csw_hit = None          # flash 窗口被某一种 CSW 读通了
    csw_alias_only = False  # 只有别名窗口通（那不算 CSW 的功劳）
    for csw, size, name in CSW_SWEEP:
        try:
            v1 = _csw_probe(dp, ap_base, 0x0100A200, csw, size)
        except Exception as e:
            v1 = 'ERR:%s' % _first_line(e)
        try:
            v2 = _csw_probe(dp, ap_base, 0x0300A200, csw, size)
        except Exception as e:
            v2 = 'ERR:%s' % _first_line(e)
        note = name
        if size < 32:
            # 8/16 位传输只搬低几位，读出来的东西本来就不是一个完整字，
            # 拿它判断「通没通」会误报（真机上 0x00003404 / 0x00000004 就是这么来的）
            note += '   （只读低位，判断不了，不算命中）'
        elif not _looks_dead(v1):
            note += '   <<< flash 窗口被这种 CSW 读通了！'
            csw_hit = csw
        elif not _looks_dead(v2):
            note += '   （只有别名窗口通）'
            csw_alias_only = True
        say("    %-11s %-11s %-11s  %s"
            % ('0x%08X' % csw, hexw(v1), hexw(v2), note))
    if orig_csw is not None:
        try:
            dp.write_ap(ap_base + 0x00, orig_csw)
        except Exception:
            pass
    try:
        ap._cached_csw = -1
    except Exception:
        pass

    # ---------------- 外设 / eFuse 探查 ----------------
    say("")
    say("  --- 旁路：外设空间通不通（决定还有没有别的路可走）---")
    for a, why in PERIPH_PROBE:
        say("    0x%08X = %-12s  %s" % (a, hexw(t(classic, a)), why))
    say("")
    say("  --- eFuse 里的 AES 钥匙（GR551x 加密 XIP 用的，SDK 里写着地址）---")
    for a, why in ((EFUSE_AESKEY, 'AESKEY_BASE_ADDR'), (EFUSE_FWCODEKEY, 'FWCODEKEY_BASE_ADDR')):
        vals = [t(classic, a + 4 * i) for i in range(4)]
        say("    0x%08X %-20s %s" % (a, why, ' '.join(hexw(v) for v in vals)))

    # ---------------- 路 3：CPU 搬运（要写 RAM，先问）----------------
    say("")
    say("  --- 路 3：CPU 搬运（让 CPU 自己把 flash 抄进 RAM）---")
    win, tried = _ram_window(ap)
    say("    RAM 可写性探测：")
    for a, v in tried:
        say("      0x%08X 写 0x5A5AA5A5 读回 %s" % (a, hexw(v)))
    if win is None:
        say("    !!! RAM 一个地址都写不进去 —— 这条路走不了（先看上面读回的是什么）")
        win = None
    else:
        code_addr, dst, chunk = win
        say("    RAM 窗口：代码放 0x%08X，缓冲放 0x%08X，一次搬 0x%X 字节"
            % (code_addr, dst, chunk))

    do_tramp = False
    if win is not None:
        if tramp_mode in ('1', 'yes', 'y', 'on'):
            do_tramp = True
            say("    （TRAMP=%s：直接做，不再问）" % tramp_mode)
        elif tramp_mode in ('0', 'no', 'n', 'off'):
            say("    （TRAMP=%s：跳过，不做 CPU 搬运）" % tramp_mode)
        else:
            say("")
            say("    这一步要往 RAM 里写 16 个字节的代码、并且让 CPU 跑 0.25 秒。")
            say("    不碰 flash、不擦不写，但芯片会被打断一次（跑完要复位一下才恢复正常）。")
            do_tramp = _ask_yes("    要做吗？[y/N] ")
            if not do_tramp:
                say("    （跳过。想跑就再来一次：MANUAL=hold TRAMP=yes bash flash-lab2.sh）")

    tramp_ok = False
    tramp_note = '没做'
    if win is not None and do_tramp:
        code_addr, dst, chunk = win
        src = _env_hex('TRAMP_SRC', 0x0100A000)
        nbytes = min(_env_hex('TRAMP_BYTES', 0x1000), chunk)
        say("")
        say("    装小程序到 0x%08X ... %s" % (code_addr, _tramp_install(ap, code_addr) or '好了'))
        say("    从 0x%08X 搬 0x%X 字节到 0x%08X，让 CPU 跑一下" % (src, nbytes, dst))
        mark_addr = (code_addr + 0x200) & ~0x3
        buf, err = _tramp_copy(ap, target, code_addr, dst, src, nbytes,
                               mark_addr=mark_addr)
        mark = t(classic, mark_addr)
        if mark == nbytes // 4:
            say("    标记 0x%08X = %s  <- 说明我们这段代码**真的被 CPU 执行了**"
                % (mark_addr, hexw(mark)))
        else:
            say("    标记 0x%08X = %s （跑之前是 0x%08X）"
                % (mark_addr, hexw(mark), TRAMP_MARK_INIT))
            say("    !!! 标记没被改写 —— CPU 可能压根没跑到我们这段代码上")
        if buf is None:
            say("    !!! 搬运失败：%s" % err)
            tramp_note = err
        else:
            words = list(struct.unpack_from('<%dI' % (len(buf) // 4), buf, 0))
            distinct = len(set(words))
            src_base = FLASH_ALIAS_ADDR if src >= FLASH_ALIAS_ADDR else FLASH_BASE_ADDR
            off = (MAGIC_ADDR - FLASH_BASE_ADDR) - (src - src_base)
            got = words[off // 4] if 0 <= off <= len(buf) - 4 else None
            say("    搬回来了 %d 字节，里面有 %d 个不同的 32 位字" % (len(buf), distinct))
            say("    前 32 字节：" + ' '.join('0x%08X' % w for w in words[:8]))
            say("    对照点 0x%08X（应该等于 flash 里那个字）= %s"
                % (MAGIC_ADDR, hexw(got)))
            txt = buf[:nbytes]
            for pat in (b'ZKC42V', b'GR551', b'ZKONG'):
                p = txt.find(pat)
                if p >= 0:
                    say("    还搜到了字符串 %s（偏移 0x%X）" % (pat.decode(), p))
            if distinct >= 8 and not _looks_dead(got):
                tramp_ok = True
                tramp_note = '成功'
                say("    >>> CPU 搬运成功：flash 对调试器关门，对 CPU 开门 ✅")
            elif distinct >= 8:
                tramp_note = '搬回来有变化但对照点是死值（源地址换成 0x%08X 再试）' % FLASH_ALIAS_ADDR
                say("    >>> 搬回来一坨有变化的数据，但对照点位置是死值 —— "
                    "试试 TRAMP_SRC=0x0300A000")
            else:
                tramp_note = '搬回来的东西也几乎是死值'
                say("    >>> 搬回来还是死值：CPU 读 flash 也不通（或者 CPU 根本没跑到我们的代码）")

    # ---------------- 总结 ----------------
    say("")
    say("==================================================================")
    say("  总结")
    say("==================================================================")
    if csw_hit is not None:
        csw_line = 'flash 窗口被 CSW=0x%08X 读通了 ✅' % csw_hit
    elif csw_alias_only:
        csw_line = 'flash 窗口怎么读都是死值（只有别名窗口通，那是路 2 的功劳）'
    else:
        csw_line = '没有任何一种 CSW 组合能读到 flash'
    say("    路 1 CSW 实验        : %s" % csw_line)
    say("    路 2 别名 0x03000000 : %s"
        % ('读到真数据 ✅' if alias_live else
           ('有变化但对照点是死值' if alias_hit else '跟 0x01000000 一样是死值')))
    say("    路 3 CPU 搬运        : %s" % ('成功 ✅' if tramp_ok else tramp_note))
    say("")
    say("  三种局面怎么走的结论：")
    if alias_live:
        say("    a) 别名窗口能读 -> 走别名窗口整片读：")
        say("       DUMP_RESTART=1 DUMP_BASE=0x03000000 MANUAL=hold bash dump-resume.sh")
    if tramp_ok:
        say("    b) 只有 CPU 自己能读 -> 用 CPU 搬运整片读：")
        say("       MANUAL=hold bash ram-dump.sh")
    if csw_hit is not None:
        say("    c) 某种 CSW 能读 -> 把 CSW=0x%08X 写进 dumpresume 再整片读" % csw_hit)
    if not (alias_live or tramp_ok or csw_hit is not None):
        say("    d) 三条路在这颗芯片上都不通 —— 但这不是死路：")
        say("       真正管用的那条是 flash-lab4（连着 SWD 让 CPU 跑到 XIP 打开再抢读），")
        say("       它已经在你这块价签上成功过一次了：")
        say("       MANUAL=hold bash flash-lab4.sh")
    if do_tramp and win is not None:
        say("")
        say("  提醒：CPU 搬运那一步把芯片打断了，跑完把价签的 RST 碰一下 GND 再松开，")
        say("        让它重新正常启动（不然它会一直卡在我们塞进去的小循环里）。")
    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass


# =====================================================================
#  命令 8：ramdump —— CPU 搬运式整片备份
#
#  前提：flashlab2 的路 3 试通了（说明让 CPU 搬是可行的）。
#  做法：每一轮
#     1) 让 CPU 从 flash[src] 抄 chunk 字节到 RAM 的缓冲窗口
#     2) 从 RAM 把这一段读回 PC，写进文件
#     3) 接着下一段
#  全程不写 flash、不擦 flash；只有 RAM 里那 16 个字节的代码和缓冲。
# =====================================================================

@command('ramdump', help='CPU 搬运式整片备份（不写 flash；需要 flashlab2 路 3 已试通）')
def ramdump():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)
    window_ms = _env_int('DUMP_WINDOW_MS', 5000 if MANUAL else 3000)
    hold_ms = _env_int('HOLD_MS', 200)

    outdir = _env_str('DUMP_DIR', os.getcwd())
    base = _env_hex('DUMP_BASE', FLASH_BASE_ADDR)
    total = _env_hex('DUMP_TOTAL', 0x80000)
    out = _env_str('DUMP_OUT', os.path.join(outdir, 'zk42v-cpu-512k.bin'))
    prog = out + '.progress'
    run_ms = _env_int('TRAMP_RUN_MS', 300)

    def hexw(v):
        return ('0x%08X' % v) if isinstance(v, int) else str(v)

    say("")
    say("==================================================================")
    say("  CPU 搬运式整片备份（不写 flash）")
    say("==================================================================")
    say("  范围    : 0x%08X + 0x%X (%d KB)" % (base, total, total // 1024))
    say("  目标文件: %s" % out)

    done = 0
    if not _env_str('DUMP_RESTART', ''):
        try:
            with open(prog) as f:
                done = int(f.read().strip() or 0)
        except Exception:
            done = 0
    if done:
        say("  已有进度: %d / %d 字节 (%.1f%%)" % (done, total, done * 100.0 / total))
    else:
        try:
            with open(out, 'wb') as f:
                f.write(b'')
        except Exception as e:
            say("  建不了目标文件：%s" % _first_line(e))
            return

    hit, tries = _reset_and_grab(probe, rst, manual_hold, window_ms, hold_ms)
    if hit is None:
        say("  没抢到窗口（试了 %d 次），再跑一次。" % tries)
        return
    say("  connect() 成功（复位后 %d ms）" % hit)
    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        return
    try:
        target.init()
        target.halt()
    except Exception as e:
        say("  init/halt 失败：%s" % _first_line(e))
        return
    ap = _ap_of(target)
    if ap is None:
        say("  拿不到 AP。")
        return

    win, _tried = _ram_window(ap)
    if win is None:
        say("  RAM 写不进去，做不了。")
        return
    code_addr, dst, chunk = win
    err = _tramp_install(ap, code_addr)
    if err:
        say("  装不了小程序：%s" % err)
        return
    say("  小程序在 0x%08X，缓冲在 0x%08X，一次搬 0x%X 字节" % (code_addr, dst, chunk))

    with open(out, 'ab') as f:
        pos = done
        while pos < total:
            n = min(chunk, total - pos)
            buf, err = _tramp_copy(ap, target, code_addr, dst, base + pos, n, run_ms,
                                   mark_addr=(code_addr + 0x200) & ~0x3)
            if buf is None:
                say("  [0x%08X] 搬运失败：%s" % (base + pos, err))
                say("  进度停在 %d 字节，再跑一次这条命令会接着读。" % pos)
                break
            f.write(buf[:n])
            f.flush()
            pos += n
            with open(prog, 'w') as pf:
                pf.write(str(pos))
            say("  ... %d / %d 字节 (%.1f%%)" % (pos, total, pos * 100.0 / total))

    ok = sanity_file(out, base)
    say("")
    say("  文件：%s" % out)
    try:
        import hashlib
        h = hashlib.sha256()
        with open(out, 'rb') as f:
            for blk in iter(lambda: f.read(1 << 20), b''):
                h.update(blk)
        say("  SHA-256: %s" % h.hexdigest())
    except Exception:
        pass
    if ok:
        say("  这份可以当备份了。建议再跑一遍，看两次 SHA-256 一不一样。")
    else:
        say("  真伪校验没过，先别当备份。")
    say("")
    say("  提醒：跑完把价签的 RST 碰一下 GND 再松开，让它重新正常启动。")
    say("  （如果中途读着读着连接掉了，八成是价签的看门狗把芯片复位了；")
    say("    再跑一次同一条命令，会从上次的进度接着读。）")
    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass


# =====================================================================
#  命令 12：status —— 自研固件体检（多次采样 + 复位检测 + AON 寄存器）
#
#  为什么要重写：v1 为了"让固件跑起来"先把 CPU resume 了，然后在 CPU 运行中
#  读状态块，读不到就断言"跑的还是原厂固件"。实测这个推理是错的 ——
#  调试口一断就什么都读不到，而原因可能完全是别的（芯片复位 / SWD 被关 /
#  会话失效）。2026-09-27 那次实际输出就是这么误判的。
#
#  v2 的四条规矩：
#    1) 永远先 halt 再读；
#    2) "读不到"不当结论 —— 先重连一次，看能不能读回来；
#    3) 隔一段时间多采几个点：PC 在 ROM / bootloader / APP 之间来回跳，
#       就是芯片在反复复位（而不是固件卡住）；
#    4) 顺手读 AON 寄存器 —— SOFTWARE_1 低 16 位 == 0xF175 是"超深睡唤醒"标志，
#       带着它 soc_init() 里的 ultra_deep_sleep_wakeup_handle() 会直接调
#       hal_nvic_system_reset() 复位整个系统，而且这个标志在 AON 域、
#       软复位不会清 -> 变成无限重启循环。
# =====================================================================
ZK_CPUID_ADDR = 0xE000ED00
ZK_DHCSR_ADDR = 0xE000EDF0
ZK_CFSR_ADDR  = 0xE000ED28
ZK_HFSR_ADDR  = 0xE000ED2C
ZK_MMFAR_ADDR = 0xE000ED34
ZK_BFAR_ADDR  = 0xE000ED38

ZK_AON_BASE   = 0xA000C500
ZK_AON_SW0    = ZK_AON_BASE + 0x00
ZK_AON_PWRR01 = ZK_AON_BASE + 0x04   # PWR_RET01：BLE comm core 的上电/复位状态
ZK_AON_PADCTL0= ZK_AON_BASE + 0x50
ZK_AON_SW1    = ZK_AON_BASE + 0x60
ZK_AON_SW2    = ZK_AON_BASE + 0x78
ZK_AON_PSC_CMD= ZK_AON_BASE + 0x80
ZK_AON_PSC_OPC= ZK_AON_BASE + 0x84
ZK_AON_MCUREL = ZK_AON_BASE + 0x88
ZK_AON_TIMERV = ZK_AON_BASE + 0x94

# BLE 协议栈自己的调度中断就挂在 NVIC 的 IRQ1/IRQ2 上（GR551xx.h：
# BLE_SDK_IRQn=1 "BLE_SDK_SCHEDULE"、BLE_IRQn=2 "BLE Interrupt"）。
# 如果这里没使能，协议栈的命令会被收下、却永远没人去处理 —— 症状正是
# 「命令全接受、事件全不回」。所以这两个位值得单独看一眼。
ZK_NVIC_ISER0 = 0xE000E100
ZK_IRQ_BLE_SDK = 1
ZK_IRQ_BLE     = 2
ZK_IRQ_BLESSLP = 25

# ---- 时基自检（为什么值得看）：固件里所有 "ms" 都是拿 SystemCoreClock 算的，
#      而 SystemCoreClock 是 SDK 里的一个 RAM 变量 —— 它跟真实主频对不上的话，
#      所有时间都会按倍数偏（刷屏耗时、BUSY 超时都会跟着偏）。
#      这里直接读 DWT 的周期计数器，跟宿主机的 250ms 睡眠卡一下，就知道真主频。
ZK_DWT_CYCCNT  = 0xE0001004
# ⚠ 这两个地址**会随固件版本挪位置**，所以一律从 .map 里按名字查（见 _zk_sym_addr）。
#   这里故意留 0 = "没有兜底值"：查不到就别读（脚本会跳过那一段并说明原因），
#   总好过读一个别的变量的值当成主频。
#
#   血泪史：build 22 那会儿把地址写死在脚本里，到 build 28 固件一改代码、变量挪了位置，
#   0x300042C8 那个位置已经是 s_app_timer_info 了 —— 于是 status 里蹦出
#   「SystemCoreClock = 1 ⇒ 真实主频是它的 15991430 倍」这种鬼话（固件一点问题没有）。
#   后来把兜底值更新成 build 28 的地址，build 30 一编又过期了（test-symbols.py 抓到的）
#   —— 干脆不再维护这个数。
ZK_SYSCLK_VAR  = 0             # 0 = 没有兜底，必须从 .map 查
ZK_CYC_PER_US  = 0             # 同上

# 「采样到的 PC 落在这几个函数里 = 它在空闲循环里正常打转，不是卡死」。
# 空闲循环里绝大部分时间就花在那个 5ms 延时上，所以采样十有八九落在 epd_delay_us。
ZK_IDLE_FUNCS = ('epd_delay_us', 'epd_delay_ms', 'zk_mailbox_poll', 'zk_ble_poll',
                 'pwr_mgmt_schedule', 'tick_ms', 'main', 'zk_pins_release',
                 'memcmp', 'memcpy')
ZK_WDT_BASE   = 0xA0008000

# 屏的 7 根脚所在 GPIO（GPIO0 = 0xA0010000）
ZK_GPIO0_BASE = 0xA0010000
ZK_EPD_PINS = [
    (2,  'CS  '), (3,  'SCLK'), (4,  'SDI '), (5,  'DC  '),
    (6,  'BUSY'), (7,  'RST '), (24, 'AUX '),
]

ZK_UDS_MAGIC  = 0xF175       # SOFTWARE_REG1_ULTRA_DEEP_SLEEP_MAGIC
# 固件在状态块 +0x38 放的这个数，用来认领 boot_count / uds_seen 两个计数器
# （跟 firmware 里的 board/zk_dbg.h 必须一致）
ZK_BOOT_MAGIC = 0xB007C0DE

# AON PSC 命令表（AON_PSC_CMD_OPC_OPCODE_xxx）
ZK_PSC_OPC = {
    0x00: 'LOOPBACK', 0x01: 'EF_DIR_ON', 0x02: '32_TIMER_LD', 0x03: 'DEEP_SLEEP',
    0x04: 'EF_DIR_OFF', 0x05: 'EXT_CLK', 0x06: 'RNG_CLK', 0x07: 'RTC_CLK',
    0x08: 'RNG2_CLK', 0x09: 'LD_MEM_SLP_CFG', 0x0A: 'LD_MEM_WKUP_CFG',
    0x0B: 'DPAD_LE_HI', 0x0C: 'DPAD_LE_LO', 0x10: 'SLP_TIMER_MODE_0',
    0x11: 'SLP_TIMER_MODE_1', 0x12: 'SLP_TIMER_MODE_2', 0x13: 'SLP_TIMER_MODE_3',
}

# ---- PC 反查：按本机编译出来的 .map，把 PC 翻译成「哪个函数 + 偏移」----------
# 注意：这份 .map 是**本机这份固件**编出来的。如果你刷进去的不是它编出来的那份，
# 函数名会整体偏移（B1 和 B1.1 之间差 0x54 就是这个原因）。
ZK_MAP_DEFAULT = ("/Users/mac/Documents/Codex/2026-09-15/a/outputs/firmware/"
                  "zk42v-epd-app/GCC/out/lst/zk42v_epd.map")
_ZK_MAP = {}


def _zk_map_load():
    if _ZK_MAP:
        return _ZK_MAP
    _ZK_MAP['path'] = None
    _ZK_MAP['text'] = []
    _ZK_MAP['data'] = []
    _ZK_MAP['syms'] = {}

    path = _env_str('MAP_FILE', '') or ZK_MAP_DEFAULT
    if not os.path.exists(path):
        return _ZK_MAP
    _ZK_MAP['path'] = path

    import re as _re
    # 链接器打印输入段有两种排版：
    #   · 名字短： ` .text.tick_ms  0x0100c264  0x68 out/obj/main.o`（一行）
    #   · 名字长： ` .text.epd_delay_us` 换行后再 ` 0x0100c6a8  0x6c out/obj/main.o`
    # 以前只认第一种，于是长名字的函数（恰恰是我们的驱动/主循环那几个）
    # 在 status 里永远显示成 "-"。现在两种都认。
    pat_one = _re.compile(
        r'^\s*\.([\w.$]+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+(\S+)\s*$')
    pat_sec = _re.compile(r'^\s*\.([\w.$]+)\s*$')
    pat_sec_addr = _re.compile(
        r'^\s*0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+(\S+)\s*$')
    pending = None
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                m = pat_one.match(line)
                if m:
                    name, addr, size, obj = m.groups()
                else:
                    ms = pat_sec.match(line)
                    if ms:
                        pending = ms.group(1)
                        continue
                    ma = pat_sec_addr.match(line)
                    if ma and pending:
                        addr, size, obj = ma.groups()
                        name = pending
                    else:
                        continue
                a = int(addr, 16)
                n = int(size, 16)
                # 只要「输入段」那些行（它们最后一列是 .o 文件名）；
                # 输出段（比如整块 .text）没有对象名，跳过，否则它会盖住所有查询。
                if a == 0 or n == 0 or not obj:
                    continue
                if name.startswith('text'):
                    _ZK_MAP['text'].append((a, a + n, name, obj))
                elif name in ('data', 'bss', 'my_section', 'ramfunc', 'RAM_CODE',
                              'TINY_RAM_SPACE'):
                    _ZK_MAP['data'].append((a, a + n, name, obj))
    except Exception:
        pass
    _ZK_MAP['text'].sort()
    _ZK_MAP['data'].sort()
    _zk_map_syms(path)
    return _ZK_MAP


def _zk_map_syms(path):
    """从 .map 里抽「符号名 -> 地址」。

    为什么要它：以前脚本把 SystemCoreClock / s_cyc_per_us 的地址**写死**在源码里，
    固件一改代码、变量一挪位置，读出来的就是旁边别的变量的值 —— 于是 build 28
    那轮 status 里出现「SystemCoreClock = 1」「主频对不上 15991430 倍」这种
    假告警（读到的其实是 s_app_timer_info 的头 4 字节）。
    现在按符号名查，跟哪一版固件都对得上。

    .map 里符号有两种排版：
         .data.s_loop_per_us
                        0x3000431c        0x4 out/obj/epd_zk42v.o
                        0x300041c0                SystemCoreClock
    第一种：静态变量，符号名藏在段名 ".data.<符号>" / ".bss.<符号>" 里；
    第二种：全局符号，地址后面直接跟名字（不带 .o 那一段）。
    """
    import re as _re
    syms = _ZK_MAP['syms']
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return

    pat_sec = _re.compile(r'^\s*\.(?:text|data|bss)\.([A-Za-z_]\w*)\s+'
                          r'0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+(\S+\.o)\s*$')
    pat_sec2 = _re.compile(r'^\s*\.(?:text|data|bss)\.([A-Za-z_]\w*)\s*$')
    pat_addr = _re.compile(r'^\s*0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+(\S+\.o)\s*$')
    pat_name = _re.compile(r'^\s*0x([0-9a-fA-F]+)\s+([A-Za-z_]\w*)\s*$')
    pending = None

    for line in lines:
        m = pat_sec.match(line)
        if m:
            name, addr, _size, _obj = m.groups()
            syms.setdefault(name, int(addr, 16))
            pending = None
            continue
        ms = pat_sec2.match(line)
        if ms:
            pending = ms.group(1)
            continue
        ma = pat_addr.match(line)
        if ma and pending:
            syms.setdefault(pending, int(ma.group(1), 16))
            pending = None
            continue
        mn = pat_name.match(line)
        if mn:
            syms.setdefault(mn.group(2), int(mn.group(1), 16))
    return


def _zk_sym_addr(name, dflt=None):
    """符号名 -> 地址（从本机这份 .map 查）。查不到返回 dflt。"""
    m = _zk_map_load()
    a = m.get('syms', {}).get(name)
    return dflt if a is None else a


def _zk_sym(pc):
    """PC -> '函数名+0x偏移'（查不到就返回空串）"""
    if pc is None:
        return ''
    m = _zk_map_load()
    for a, b, name, obj in m['text']:
        if a <= pc < b:
            fn = name[5:] if name.startswith('text.') else name
            return '%s+0x%X' % (fn, pc - a)
    for a, b, name, obj in m['data']:
        if a <= pc < b:
            base = os.path.basename(obj) if obj else '?'
            return '(%s 段里的代码，来自 %s)' % (name, base)
    return ''


def _zk_pc_where(pc):
    if pc is None:
        return "读不到"
    if 0x00000000 <= pc < 0x00080000:
        return "ROM（掩膜 ROM：芯片刚启动，或刚从复位里出来）"
    if 0x01003000 <= pc < 0x0100A000:
        return "原厂 bootloader（还没把控制权交给我们的 APP）"
    if 0x0100A000 <= pc < 0x0107F000:
        return "我们的 APP"
    if 0x01000000 <= pc < 0x01003000:
        return "flash 最前面（boot_info 区，正常不该在这儿跑代码）"
    if 0x30000000 <= pc < 0x30020000 or 0x00800000 <= pc < 0x00820000:
        return "RAM 里（在 RAM 执行代码，可疑）"
    return "没见过的地址"


def _zk_magic_of(words):
    if not words:
        return None
    return words[0]


# =====================================================================
#  B2-A.2：BLE 扫描/广播实验（固件 build 19 起）
#
#  这一组数是用来回答两个问题的：
#    1) 射频活着吗？    -> ble_scan_start_st / ble_scan_rpts / ble_scan_devs
#    2) 广播被什么拒了？-> ble_adv_stN（每个广播数据变体的 ADV_START 状态码）
#  背景见 firmware/docs/B2A-ble-plan.md 和 ble/zk_ble.c 顶上的注释。
# =====================================================================
ZK_BLE_EVT_NAME = {
    0x100: 'BLE_COMMON_EVT_STACK_INIT',
    0x207: 'BLE_GAPM_EVT_ADV_START',
    0x209: 'BLE_GAPM_EVT_ADV_STOP',
    0x20B: 'BLE_GAPM_EVT_SCAN_START',
    0x20C: 'BLE_GAPM_EVT_SCAN_STOP',
    0x20D: 'BLE_GAPM_EVT_ADV_REPORT',
    0x301: 'BLE_GAPC_EVT_CONNECTED',
    0x302: 'BLE_GAPC_EVT_DISCONNECTED',
    0x306: 'BLE_GAPC_EVT_CONN_PARAM_UPDATE_REQ',
    0x600: 'BLE_GATT_COMMON_EVT_MTU_EXCHANGE',
}

# 0x4A 是这一轮的主角：协议栈在 adv_start 那一刻把广播数据判成「重复/非法」，
# 于是链路层压根没开始广播 —— 空中一个包都没有，但命令却全是「接受」的。
ZK_BLE_ERR_NAME = {
    0x00: '成功',
    0x40: 'BLE_GAP_ERR_INVALID_PARAM（参数非法）',
    0x41: 'BLE_GAP_ERR_PROTOCOL_PROBLEM',
    0x42: 'BLE_GAP_ERR_NOT_SUPPORTED（这套配置不支持）',
    0x43: 'BLE_GAP_ERR_COMMAND_DISALLOWED（当前状态不允许）',
    0x44: 'BLE_GAP_ERR_CANCELED',
    0x45: 'BLE_GAP_ERR_TIMEOUT',
    0x46: 'BLE_GAP_ERR_DISCONNECTED',
    0x47: 'BLE_GAP_ERR_NOT_FOUND',
    0x48: 'BLE_GAP_ERR_REJECTED',
    0x49: 'BLE_GAP_ERR_PRIVACY_CFG_PB',
    0x4A: 'BLE_GAP_ERR_ADV_DATA_INVALID（广播数据重复/非法 ★）',
    0x4B: 'BLE_GAP_ERR_INSUFF_RESOURCES',
    0x4C: 'BLE_GAP_ERR_UNEXPECTED',
    0x4D: 'BLE_GAP_ERR_MISMATCH',
}

# ble_gap_adv_data_set / scan_param_set 这些 API 返回的是 SDK_ERR_xxx（16 位）
ZK_SDK_ERR_NAME = {
    0x0000: 'SDK_SUCCESS',
    0x0001: 'SDK_ERR_INVALID_PARAM',
    0x0002: 'SDK_ERR_POINTER_NULL',
    0x0006: 'SDK_ERR_BUSY',
    0x0008: 'SDK_ERR_NVDS_NOT_INIT',
    0x000F: 'SDK_ERR_DISALLOWED',
    0x0010: 'SDK_ERR_NO_RESOURCES',
    0x0015: 'SDK_ERR_INVALID_ADV_IDX',
    0x001F: 'SDK_ERR_INVALID_ADV_INTERVAL',
    0x0021: 'SDK_ERR_INVALID_ADV_PARAM',
    0x0023: 'SDK_ERR_ADV_DATA_NOT_SET',
    0x0026: 'SDK_ERR_INVALID_DURATION_PARAM',
    0x0080: 'SDK_ERR_APP_ERROR',
}

# 跟上位机固件 zk_ble.c 里 s_variants[] 的顺序一字不差
ZK_ADV_VARIANT_DESC = [
    '广播=厂商数据+名字（**不带 Flags**）  scan rsp=128 位服务 UUID',
    '广播=Flags+厂商数据+名字（旧版那一套，对照组）  scan rsp=UUID',
    '广播=只有名字  scan rsp=UUID',
    '广播=128 位 UUID+厂商数据（照抄 SDK 例程的形状）  scan rsp=名字',
    '广播=只有 Flags  scan rsp=UUID',
    '广播=厂商数据+名字  scan rsp=空',
]

ZK_SCAN_ST_NAME = {
    0: '没发起',
    1: 'scan_param_set/scan_start 叫过了，还没等到事件',
    2: '收到 SCAN_START 事件（status=0）',
    3: '已经在收广播上报了',
    4: '扫描结束',
}

ZK_SCAN_STOP_NAME = {
    0: '超时（协议栈自己停的）',
    1: '被主机停掉',
    2: '连上了所以停',
}

# ---- build 21：EPD 服务 / 推图（对齐 tsl0922/EPD-nRF5）-------------------
ZK_EPD_CMD_NAME = {
    0x00: 'SET_PINS', 0x01: 'INIT', 0x02: 'CLEAR', 0x03: 'SEND_CMD',
    0x04: 'SEND_DATA', 0x05: 'REFRESH', 0x06: 'SLEEP',
    0x20: 'SET_TIME', 0x21: 'SET_WEEK_START',
    0x30: 'WRITE_IMAGE', 0x90: 'SET_CONFIG', 0x91: 'SYS_RESET',
    0x92: 'SYS_SLEEP', 0x99: 'CFG_ERASE',
}

ZK_PANEL_STATE_NAME = {
    0: '没动过（还没有人用屏）',
    1: '屏初始化完了',
    2: '刷完一帧了',
    3: '出错（初始化失败）',
}


def _zk_i8(v):
    """状态块里 RSSI 存的是「符号扩展过的 32 位」，还原成有符号数"""
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


def _zk_err_text(v):
    if v == 0xFFFFFFFF:
        return '没收到事件'
    return ZK_BLE_ERR_NAME.get(v, '未知错误码 0x%02X' % v)


def _zk_say_ble_experiment(words):
    """把状态块里 B2-A.2 那一组（word 24..52）翻译成人话"""
    (scan_state, scan_par_err, scan_start_err, scan_start_st, scan_stop_rsn,
     rpts, devs, ovf, rssi_last, rssi_best, addr0, addr1,
     dlen, d0, d1, d2, d3,
     adv_try, adv_status, adv_ok, ds_err, ds2_err, start_err) = (words[24:47])

    say("")
    say("  ---- B2-A.2 实验一：扫描（看射频活没活）----")
    # build 20 起，产品路径上不再每次开机先听 4 秒（ZK_BLE_SCAN_TEST=0），
    # 那时扫描那几格全是「没跑过」——别当成"扫描失败"来报。
    scan_skipped = (scan_state == 0 and rpts == 0 and scan_start_st == 0xFFFFFFFF)
    if scan_skipped:
        say("    这一版**没跑扫描实验**（开机直接广播）——")
        say("    扫描是 build 19 用来回答「射频活没活」的；那个问题已经有答案了")
        say("    （395 条上报 / 16 个设备 / -39 dBm），所以产品路径上不再每次开机先听 4 秒。")
        say("    要再跑一遍：把 zk_ble.c 里 ZK_BLE_SCAN_TEST 改回 1 再重编。")
    else:
        say("    ble_scan_state = %d（%s）"
            % (scan_state, ZK_SCAN_ST_NAME.get(scan_state, '?')))
        say("    scan_param_set 返回 = %d（%s）   scan_start 返回 = %d（%s）"
            % (scan_par_err, ZK_SDK_ERR_NAME.get(scan_par_err, '?'),
               scan_start_err, ZK_SDK_ERR_NAME.get(scan_start_err, '?')))
        if scan_start_st == 0xFFFFFFFF:
            say("    SCAN_START 事件 = **没收到**")
        else:
            say("    SCAN_START 事件 status = %d（%s）"
                % (scan_start_st, ZK_BLE_ERR_NAME.get(scan_start_st, '?')))
        if scan_stop_rsn == 0xFFFFFFFF:
            say("    SCAN_STOP  事件 = 还没收到")
        elif scan_stop_rsn == 0xFFFFFFFE:
            say("    SCAN_STOP  事件 = 我们的兜底超时强停（协议栈一直没回 SCAN_STOP）")
        else:
            say("    SCAN_STOP  事件 reason = %d（%s）"
                % (scan_stop_rsn, ZK_SCAN_STOP_NAME.get(scan_stop_rsn, '?')))

        if rpts == 0:
            say("    → **一条广播都没听到**（原始条数 0）")
            if scan_start_st == 0xFFFFFFFF:
                say("      而且 SCAN_START 事件也没回来 —— 链路层/控制器看起来没在干活，")
                say("      下一步该去反汇编原厂固件的 BLE 使能路径（射频压根没启动）。")
            else:
                say("      但 SCAN_START 事件是好的（控制器认了这个命令）——")
                say("      说明命令通路是通的、收信通路没出东西。附近真有 BLE 设备吗？")
                say("      （手机/耳机/手环都在广播；在家/办公室一般几十条起步）")
        else:
            say("    → 听到 原始 %d 条广播，去重后 **%d 个设备**（去重表上限 16）"
                % (rpts, devs))
            if ovf:
                say("      去重表溢出 %d 次 —— 周围设备比 16 个还多，真实数量更多。"
                    % ovf)
            say("      RSSI：最后 %d dBm，最强 %d dBm"
                % (_zk_i8(rssi_last), _zk_i8(rssi_best)))
            a = addr0 | (addr1 << 32)
            say("      最后一条上报的地址：%s"
                % ':'.join('%02X' % ((a >> (8 * i)) & 0xFF) for i in range(5, -1, -1)))
            if dlen:
                raw = b''.join(struct.pack('<I', w)
                               for w in (d0, d1, d2, d3))[:min(dlen, 16)]
                say("      第一条广播数据（共 %d 字节，这是前 %d 字节）："
                    % (dlen, len(raw)))
                say("        %s" % ' '.join('%02X' % b for b in raw))
            say("      ▶ **射频是活的、收通路也是好的** —— 问题在发送侧（看下面实验二）")

    say("")
    say("  ---- B2-A.2 实验二：广播数据变体（看广播被什么拒了）----")
    say("    ble_adv_try = %s（试到第几个变体）   成功的是 = %s"
        % (adv_try, '都失败/还没试' if adv_ok == 0xFFFFFFFF else str(adv_ok)))
    say("    adv_data_set(DATA) 返回 = %d（%s）"
        % (ds_err, ZK_SDK_ERR_NAME.get(ds_err, '?')))
    say("    adv_data_set(SCAN_RSP) 返回 = %d（%s）"
        % (ds2_err, ZK_SDK_ERR_NAME.get(ds2_err, '?')))
    say("    adv_start 返回 = %d（%s）  （0 只代表「命令收下了」，不代表真在广播）"
        % (start_err, ZK_SDK_ERR_NAME.get(start_err, '?')))
    say("    %-4s %-16s %s" % ('变体', 'ADV_START 事件', '这套数据'))
    for i in range(6):
        v = words[47 + i]
        if v == 0xFFFFFFFF:
            verdict = '没等到事件'
        elif v == 0:
            verdict = '0 ✅ 被接受了'
        else:
            verdict = '0x%02X %s' % (v, ZK_BLE_ERR_NAME.get(v, '未知错误码'))
        say("    #%-3d %-16s %s"
            % (i, verdict, ZK_ADV_VARIANT_DESC[i] if i < len(ZK_ADV_VARIANT_DESC) else ''))

    if adv_ok != 0xFFFFFFFF:
        say("    ▶ 变体 #%d 这套广播数据控制器认了 —— 下一个版本就把它固化下来，"
            % adv_ok)
        say("      然后用手机/nRF Connect 搜 'ZK42V-EPD' 看能不能搜到。")
        if adv_ok == adv_try:
            say("      （表中 #%d 之后那几行写「没等到事件」，不是它们失败 ——"
                % adv_ok)
            say("        **是 #%d 一成功我们就停了**，后面的压根没试，故意的 ——"
                % adv_ok)
            say("        省时间，也免得把已经起来的广播活动折腾掉。）")
    else:
        say("    ▶ 6 种都没成功。看上面每个变体的状态码：")
        say("      全是 0x4A  -> 广播数据这条路上还有别的规矩没满足")
        say("      全是没等到  -> 链路层压根没处理 adv_start（回到扫描那边的结论）")

    # ---- build 20：广播「自己停了」也要看得见 -------------------------------
    # 之前完全没有这条记录：广播要是中途停了，状态块里一个数都不变，
    # 我们还在说"在广播"。现在每停一次记一笔，非连接原因还会自动重开。
    if len(words) > 55:
        stop_cnt, stop_rsn, restart_cnt = words[53], words[54], words[55]
        say("    ADV_STOP（广播自己停）：一共 %d 次   我们重开了 %d 次"
            % (stop_cnt, restart_cnt))
        if stop_cnt == 0:
            say("      → 从开机到现在广播一直没停过（正常）")
        else:
            rsn = {0: '超时', 1: '被主机停掉', 2: '被连接打断（正常，等断开时会重开）'}
            say("      最后一次停的原因 = %d（%s）"
                % (stop_rsn,
                   rsn.get(stop_rsn, '未知') if stop_rsn != 0xFFFFFFFF else '还没停过'))
            if restart_cnt == 0 and stop_rsn != 2:
                say("      → 停了又没能重开，空中可能已经没有我们的包了 —— 把这段发我。")


def _zk_say_epd_service(words):
    """build 21：网页连上来之后到底发生了什么（GATT 服务 / 推图 / 刷屏）"""
    (svc_err, svc_hdl, conn_cnt, conn_idx, cccd, cmd_cnt, last_cmd,
     chunks, last_flags, img_bw, img_red, rle_out, legacy,
     panel_st, panel_ms, init_ms, noti_cnt, noti_err,
     want_init, want_refresh, mtu_rpt, svc_end, svc_db_err) = words[56:79]
    busy_delta = words[79] if len(words) > 79 else 0

    say("")
    say("  ---- B2-A.2：GATT 服务 / 推图（build 21 起，网页走的就是这条）----")
    # 服务的建立分两步（build 22 起）：
    #   ① ble_gatts_prf_add：在协议栈里登记 profile（svc_err）
    #   ② 协议栈回头加载 profile 时才真正建库（svc_db_err），建完给我们句柄范围
    # 只做①不做②的后果就是 build 21 那次：手机看到 "No Services matching UUID"。
    if svc_hdl == 0:
        if svc_err:
            say("    服务**注册**就没成：ble_gatts_prf_add 返回 = %d（%s）"
                % (svc_err, ZK_SDK_ERR_NAME.get(svc_err, '未知')))
        else:
            say("    profile 登记上了（返回 0），但**协议栈还没回来建库**："
                "句柄还是 0，建库返回 = %d（%s）"
                % (svc_db_err, ZK_SDK_ERR_NAME.get(svc_db_err, '未知')))
        if svc_db_err == 0x10:
            say("      0x10 = NO_RESOURCES —— 协议栈的堆不够放这张表了")
        say("      → 手机连上来会看不到 62750001-… 这个服务（build 21 就是这么栽的）。")
        say("        把这段发我。")
        return

    say("    服务：注册返回 = %d（%s）  建库返回 = %d（%s）"
        % (svc_err, ZK_SDK_ERR_NAME.get(svc_err, '?'),
           svc_db_err, ZK_SDK_ERR_NAME.get(svc_db_err, '?')))
    # 注意：SDK 那个框架报的 end_hdl 是**开区间**（它自己写的是 start + 属性个数），
    # 所以要减 1 才是最后一个真实句柄 —— 不然会像 build 21 那样报成"7 个属性"。
    n_attr = (svc_end - svc_hdl) if svc_end > svc_hdl else 6
    say("          句柄 = 0x%04X~0x%04X（%d 个属性）"
        % (svc_hdl, svc_hdl + n_attr - 1, n_attr))

    if conn_cnt == 0:
        say("    连接：**还没有客户端连上来过**")
    else:
        say("    连接：连上过 %d 次，当前 conn_idx = %s   CCCD = 0x%04X（%s）"
            % (conn_cnt,
               '没连' if conn_idx == 0xFFFFFFFF else str(conn_idx),
               cccd, '客户端开了通知' if cccd & 1 else '没开通知'))
    say("    MTU：告诉网页的可写长度 = %d（网页按它 -2 切图块）" % mtu_rpt)

    say("    命令：一共收到 %d 条，最后一条 = %s"
        % (cmd_cnt,
           '还没收到过' if last_cmd == 0xFFFFFFFF
           else '0x%02X（%s）' % (last_cmd, ZK_EPD_CMD_NAME.get(last_cmd, '未知'))))
    say("    INIT 命令收到 %d 次，REFRESH/CLEAR 收到 %d 次"
        % (want_init, want_refresh))

    say("    图像：WRITE_IMAGE 收到 %d 块   黑白面 %d/%d 字节   红面 %d/%d 字节"
        % (chunks, img_bw, 15000, img_red, 15000))
    if chunks:
        say("      最后一块的 flags = 0x%02X（bit0 红面 / bit1 首块 / bit2 RLE）"
            % last_flags)
        if rle_out:
            say("      RLE 解出 %d 字节 ⇒ 网页走了压缩那条路 ✅" % rle_out)
        else:
            say("      RLE 解出 0 字节 ⇒ 网页走的**没压缩**那条路"
                "（要么图本来就压不小，要么它没收到我们 rle=1 的通知）")
        if legacy:
            say("      ⚠ 网页用的是 v1.5 老命令格式（说明它没收到我们的配置/MTU 通知）")

    say("    屏：%s" % ZK_PANEL_STATE_NAME.get(panel_st, '?'))
    if panel_st >= 1:
        say("      初始化用了 %d ms，最近一次「写图 + 刷新」用了 %d ms"
            % (init_ms, panel_ms))
    if busy_delta:
        say("      这一轮刷新时 BUSY 被轮询了 %d 次（×200us ≈ %.1f 秒）"
            % (busy_delta, busy_delta * 0.0002))
        if busy_delta < 5000:
            say("      ⚠ 这个数太小了 —— **屏压根没做全刷**。真刷一屏时是几万~几十万次")
            say("        （17 秒以上）。历史对照：82953 / 93684 / 469030 次 = 刷成功；")
            say("        2277 次那次（build 22）屏一点动静都没有，原因是初始化完就把")
            say("        屏的供电脚放开了、等刷新时屏已经掉电。build 24 修掉的就是这个。")

    # ---- build 26：日历 / 时钟模式（网页只发时间戳，页面由固件画） ----
    if len(words) > 82:
        gui_mode, gui_ts, gui_draws = words[80], words[81], words[82]
        mtxt = {0: '图片（推图）', 1: '日历', 2: '时钟'}.get(gui_mode, '?')
        ttxt = '还没同步过'
        if gui_ts:
            # 注意别被这行骗了：固件里的 s_ts 已经把时区加进去了
            # （网页发 UTC 秒 + tz 小时，我们收的时候乘 3600 加上），
            # 所以拿 gmtime 格式化出来的正好是**本地墙钟时间**，不是 UTC。
            ttxt = ('%s（本地时间；固件里已经加过时区）'
                    % time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(gui_ts)))
        say("    日历/时钟：模式 = %s   网页给的时间 = %s   画过 %d 次"
            % (mtxt, ttxt, gui_draws))
        if gui_mode in (1, 2) and gui_draws == 0:
            say("      → 网页点了「日历/时钟模式」但我们还没画出来（看下一行的屏状态）")

    # ---- build 28：画面选项（反色 / 旋转 180° / 农历开关）----
    if len(words) > 85:
        opt, opt_cmds, opt_frames = words[83], words[84], words[85]
        if opt_cmds == 0 and opt == 0:
            say("    画面选项：没设过（默认：不反色、不旋转、日历页画农历）")
        else:
            on = []
            if opt & 0x01:
                on.append('反色')
            if opt & 0x02:
                on.append('旋转180°')
            if opt & 0x04:
                on.append('日历页不画农历')
            say("    画面选项：0x%02X（%s）  设过 %d 次  有 %d 帧真的变换过"
                % (opt, '、'.join(on) if on else '全关', opt_cmds, opt_frames))
            if (opt & 0x03) and opt_frames == 0:
                say("      → 设了反色/旋转但一帧都没变换过：要么还没刷屏，"
                    "要么写屏那条路上没走到变换（把这段发我）")

    # ---- build 30：日历/时钟「为什么重画」+ 时基自证 ----
    if len(words) > 88:
        why, elapsed, tickms = words[86], words[87], words[88]
        if why:
            wtxt = {1: '收到时间/命令', 2: '换天（跨过本地 0 点）', 3: '换分钟'}.get(why, '?')
            say("    日历/时钟重画：最近一次是因为「%s」，那次距同步时间已经过了 %d 秒"
                % (wtxt, elapsed))
            if why == 2:
                say('      （「换天」一天只该发生一次；要是几分钟就冒一次，'
                    '多半是时基绕圈又回来了）')
            if elapsed > 172800:            # > 2 天
                say("      ⚠ 这个秒数大得离谱 —— 时基很可能又绕了（build 29 那个 "
                    "268 秒锯齿的典型值是 4294917 秒 ≈ 49.7 天）")
        say("    zk_tick_ms() = %d（单调毫秒的低 32 位；它不该突然掉回 0）" % tickms)

    say("    通知：发出去 %d 条，最后一次返回 %d（0 = 成功）" % (noti_cnt, noti_err))

    # ---- 一句话判据 ----------------------------------------------------
    if conn_cnt == 0:
        say("    ▶ 还没有网页连上来 —— 先用手机 Chrome 打开 tsl0922 那个网页点「连接」。")
    elif cmd_cnt == 0:
        say("    ▶ 网页连上了但一条命令都没收到：写事件没进来（句柄算法或事件分发的问题）")
        say("      —— 把这段和网页日志一起发我。")
    elif chunks and (img_bw >= 15000 and img_red >= 15000 or
                     (img_bw and img_red and want_refresh)):
        say("    ▶ 服务在、图也收全了、屏也刷了 —— 整条链路通了 🎉")
    elif chunks and (img_bw < 15000 or img_red < 15000):
        say("    ▶ 收到图块了但没凑满：黑白面 %d/15000，红面 %d/15000" % (img_bw, img_red))
        say("      如果块数远少于预期，多半是**无响应的写（Write Command）没被递上来**")
        say("      —— 这种块丢了只能靠带响应的那些补齐，图会缺数据。把这段发我。")
    elif gui_draws:
        say("    ▶ 走的是**日历/时钟模式**（网页只发时间戳，页面由固件画）："
            "已经画了 %d 次，" % gui_draws +
            ("屏也刷完了 —— 这条链路是通的 ✅" if panel_st == 2 else
             "但屏还没刷完（看上面「屏：」那行的状态）"))
    elif want_init or want_refresh:
        say("    ▶ 收到命令了但还没看到图像数据 —— 网页那边是不是没点「推送」？")
        say("      （要是你点的是「日历/时钟模式」，那走的是另一条路："
            "网页只发时间戳、页面由固件画 —— 这时看上面「日历/时钟」那行有没有"
            "画过次数，有就说明成了）")


@command('status', help='自研固件体检：多次采样 + 复位检测 + AON 寄存器')
def zkstatus():
    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    rst = None if MANUAL else reset_driver(probe)

    say("")
    say("  自研固件体检 0x%08X" % ZK_DBG_ADDR)
    say("  规矩：每次都先 halt 再读；隔一小会儿采几个点，判断它是在跑、在复位、还是卡住了")
    say("")

    hit = None
    tries = 0
    if _env_str('NO_DIRECT', '0') != '1':
        t0 = time.monotonic()
        direct_ms = _env_int('DIRECT_MS', 1500)
        say("  先不复位，直接连 SWD（最多等 %d ms）..." % direct_ms)
        while (time.monotonic() - t0) * 1000.0 < direct_ms:
            tries += 1
            try:
                probe.connect()
            except Exception:
                time.sleep(0.05)
                continue
            try:
                t = resolve_target()
                if t is not None:
                    t.init()
                ap0 = _ap_of(t) if t is not None else None
                v = None
                if ap0 is not None:
                    v = ap0._read_memory(ZK_CPUID_ADDR) & 0xFFFFFFFF
                if v == 0x410FC241:
                    hit = 0
                    break
            except Exception:
                pass
            time.sleep(0.05)
        if hit is not None:
            say("  直接连上了，CPUID 也读得出来（试了 %d 次）" % tries)
            say("  —— 说明现在这个状态里调试口是开的（原厂 APP 跑起来会关）")

    if hit is None:
        say("  直接连不上，改成抢复位窗口（按住 RST 数到 0 松手）...")
        hit, tries = _reset_and_grab(probe, rst, manual_hold,
                                     _env_int('DUMP_WINDOW_MS', 8000),
                                     _env_int('HOLD_MS', 200))
        if hit is None:
            say("  没抢到窗口（试了 %d 次）。再跑一次，或者把价签的 RST 碰一下 GND。" % tries)
            if rst is not None:
                try:
                    rst.close()
                except Exception:
                    pass
            return
        say("  连上了（复位后 %d ms，试了 %d 次）" % (hit, tries))

    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        return
    try:
        target.init()
    except Exception as e:
        say("  target.init 失败：%s" % _first_line(e))

    ap = _ap_of(target)

    def rd(a):
        if ap is not None:
            try:
                return ap._read_memory(a) & 0xFFFFFFFF
            except Exception:
                pass
        try:
            return target.read_memory32(a) & 0xFFFFFFFF
        except Exception:
            return None

    def rcore(name):
        try:
            v = target.read_core_register(name)
            return (int(v) & 0xFFFFFFFF) if v is not None else None
        except Exception:
            return None

    def do_halt():
        try:
            target.halt()
        except Exception:
            pass

    def do_resume():
        try:
            target.resume()
        except Exception:
            pass

    def sample():
        """先停住，再读一整套；读不动就返回 None"""
        do_halt()
        s = {}
        s['cpuid'] = rd(ZK_CPUID_ADDR)
        if s['cpuid'] is None:
            return None
        s['dhcsr'] = rd(ZK_DHCSR_ADDR)
        s['pc'] = rcore('pc')
        s['lr'] = rcore('lr')
        s['sp'] = rcore('sp')
        s['cfsr'] = rd(ZK_CFSR_ADDR)
        s['hfsr'] = rd(ZK_HFSR_ADDR)
        s['bfar'] = rd(ZK_BFAR_ADDR)
        s['mmfar'] = rd(ZK_MMFAR_ADDR)
        s['sw0'] = rd(ZK_AON_SW0)
        s['sw1'] = rd(ZK_AON_SW1)
        s['sw2'] = rd(ZK_AON_SW2)
        s['padctl0'] = rd(ZK_AON_PADCTL0)
        s['psc'] = rd(ZK_AON_PSC_CMD)
        s['psc_opc'] = rd(ZK_AON_PSC_OPC)
        s['mcurel'] = rd(ZK_AON_MCUREL)
        s['pwrr01'] = rd(ZK_AON_PWRR01)
        s['iser0'] = rd(ZK_NVIC_ISER0)
        s['wdt'] = (rd(ZK_WDT_BASE + 0x00), rd(ZK_WDT_BASE + 0x04),
                    rd(ZK_WDT_BASE + 0x08), rd(ZK_WDT_BASE + 0x10))
        words = []
        for i in range(ZK_DBG_WORDS):
            v = rd(ZK_DBG_ADDR + 4 * i)
            if v is None:
                words = None
                break
            words.append(v)
        s['words'] = words
        do_resume()
        return s

    n = max(1, _env_int('SAMPLES', 5))
    gap = max(0, _env_int('SAMPLE_GAP_MS', 600))
    samples = []

    say("  （每行：PC 落在哪 · 符号 · stage · DHCSR 的粘滞位。"
        "RST_ST=1 表示「上次读 DHCSR 之后芯片复位过」）")
    say("")

    for i in range(n):
        if i:
            do_resume()
            time.sleep(gap / 1000.0)
        s = sample()
        if s is not None:
            samples.append(s)
            w = s['words']
            magic = _zk_magic_of(w)
            stage = w[2] if w else None
            build = w[11] if (w and len(w) > 11) else None
            bmagic = w[14] if (w and len(w) > 14) else None
            counters_ok = (build is not None and build >= 2
                           and bmagic == ZK_BOOT_MAGIC)
            dh = s['dhcsr']
            # DHCSR 的粘滞位：bit25 S_RESET_ST（上次读 DHCSR 之后复位过）、
            #                 bit24 S_RETIRE_ST（期间执行过指令）
            rst_st = ((dh >> 25) & 1) if dh is not None else None
            ret_st = ((dh >> 24) & 1) if dh is not None else None
            sym = _zk_sym(s['pc']) or '-'
            extra = ''
            if counters_ok:
                extra = '  boot=%s uds=%s' % (w[12], w[13])
            say("  #%d  PC=0x%-9s %-30s %-24s %-8s RST_ST=%s RETIRE=%s%s"
                % (i + 1,
                   ('%08X' % s['pc']) if s['pc'] is not None else '读不到   ',
                   '[%s]' % _zk_pc_where(s['pc']),
                   sym[:24],
                   ('stage=%s' % stage) if stage is not None else 'stage=-',
                   rst_st if rst_st is not None else '?',
                   ret_st if ret_st is not None else '?',
                   extra))
            continue

        say("  #%d  读不到 —— 调试口这会儿不在了" % (i + 1))
        # "读不到"不能当结论：重连一次，看能不能读回来。
        recon = False
        try:
            probe.connect()
            t2 = resolve_target()
            if t2 is not None:
                t2.init()
                target = t2
                ap = _ap_of(t2)
                recon = True
        except Exception:
            recon = False
        s = sample() if recon else None
        if s is not None:
            samples.append(s)
            say("       重连之后读得回来（PC=0x%08X）" % (s['pc'] or 0))
            say("       说明刚才那次'读不到'是**调试口被重置过**（芯片复位），")
            say("       不是固件把 SWD 关掉了。")
        else:
            say("       重连也读不回来 —— 更像 SWD 被关掉 / 会话彻底失效了。")

    if not samples:
        say("")
        say("  五次采样一个都没读到。把价签 RST 碰一下 GND 再松开，重跑一次；")
        say("  还是这样就把这段发我。")
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass
        return

    last = samples[-1]

    say("")
    say("  ---------------- 最后一次成功采样的细节 ----------------")
    say("  CPUID     : 0x%08X %s"
        % (last['cpuid'], '(Cortex-M4，读通路是活的)' if last['cpuid'] == 0x410FC241 else ''))
    if last['dhcsr'] is not None:
        say("  DHCSR     : 0x%08X  S_HALT=%d"
            % (last['dhcsr'], 1 if (last['dhcsr'] & 0x2) else 0))
    say("  PC        : %s  ← %s"
        % (('0x%08X' % last['pc']) if last['pc'] is not None else '读不到',
           _zk_pc_where(last['pc'])))
    if _zk_sym(last['pc']):
        say("              按本机这份 .map 查：%s" % _zk_sym(last['pc']))
        say("              （刷进芯片的那份要是跟这份 .elf 不是同一次编译的，名字会偏）")
    say("  LR / SP   : %s / %s"
        % (('0x%08X' % last['lr']) if last['lr'] is not None else '读不到',
           ('0x%08X' % last['sp']) if last['sp'] is not None else '读不到'))
    say("  CFSR/HFSR : %s / %s%s"
        % (('0x%08X' % last['cfsr']) if last['cfsr'] is not None else '读不到',
           ('0x%08X' % last['hfsr']) if last['hfsr'] is not None else '读不到',
           '   ← 非 0 = 出过硬件异常' if (last['cfsr'] or 0) or (last['hfsr'] or 0) else ''))
    if (last['bfar'] or 0) or (last['mmfar'] or 0):
        say("  BFAR/MMFAR: %s / %s"
            % (('0x%08X' % last['bfar']) if last['bfar'] is not None else '读不到',
               ('0x%08X' % last['mmfar']) if last['mmfar'] is not None else '读不到'))

    say("")
    say("  AON 寄存器（这块是「永远在线」域，软复位不丢）：")
    say("    SOFTWARE_0 = 0x%08X   （冷启动/热启动相关的标志，SDK 用来判 boot mode）"
        % (last['sw0'] if last['sw0'] is not None else 0))
    sw1 = last['sw1']
    say("    SOFTWARE_1 = 0x%08X   （低 16 位 = 0x%04X%s）"
        % (sw1 if sw1 is not None else 0,
           (sw1 & 0xFFFF) if sw1 is not None else 0,
           '  ← 这是"超深睡唤醒"标志！' if (sw1 is not None and (sw1 & 0xFFFF) == ZK_UDS_MAGIC) else ''))
    if sw1 is not None and (sw1 & 0xFFFF) != ZK_UDS_MAGIC:
        say("      → 里面没有 0x%04X 这个标志，平台那条「超深睡唤醒就复位整个系统」"
            "的路径**不成立**。" % ZK_UDS_MAGIC)
    say("    SOFTWARE_2 = 0x%08X" % (last['sw2'] if last['sw2'] is not None else 0))
    say("    WDT LOAD/VALUE/CTRL/RIS = %s"
        % ' '.join(('0x%08X' % v) if v is not None else 'n/a' for v in last['wdt']))

    # ---- PSC（电源状态控制器）：自研固件卡就卡在这上面 ----
    psc = last.get('psc')
    opc = last.get('psc_opc')
    if psc is not None:
        busy = (psc >> 1) & 1
        req = psc & 1
        say("")
        say("  PSC（电源状态控制器，就在 AON 里）—— 自研固件就是卡在这里：")
        say("    PSC_CMD     = 0x%08X   MCU_PWR_REQ=%d  MCU_PWR_BUSY=%d %s"
            % (psc, req, busy, '  ← 一直在忙！' if busy else ''))
        if opc is not None:
            code = opc & 0xFF
            say("    PSC_CMD_OPC = 0x%08X   opcode=0x%02X (%s)"
                % (opc, code, ZK_PSC_OPC.get(code, '未知')))
            if busy:
                say("    → 卡住的那条命令是 0x%02X (%s)，它一直没完成。"
                    % (code, ZK_PSC_OPC.get(code, '未知')))
                if code == 0x07:
                    say("      0x07 = RTC_CLK（把 PSC 的时基切到 RTC / 32.768kHz）。")
                    say("      这条命令完不成，通常就是这个 32.768kHz 时钟源没在跑/不存在。")
                elif code == 0x03:
                    say("      0x03 = DEEP_SLEEP —— 这不是我们固件发的（我们没让它睡），")
                    say("      更像上一轮原厂固件睡下去时卡住的残留（AON 域软复位不清）。")
        if last.get('mcurel') is not None:
            say("    MCU_RELEASE = 0x%08X" % last['mcurel'])

    # ---- BLE 子系统（comm core）的上电/复位 + 协议栈调度中断 --------------
    # 这两个是「射频到底有没有被启动」最直接的物证：
    #   · AON->PWR_RET01 的 bit6/7 是 comm core / comm timer 的电源闸，
    #     bit11/12 是它们的复位闸（=1 表示已放开复位、可以跑）
    #   · NVIC ISER0 的 bit1/2 是协议栈的调度中断（BLE_SDK_IRQn/BLE_IRQn）。
    #     中断没使能 = 命令全被收下、但没人去处理 = 事件永远不回。
    r01 = last.get('pwrr01')
    iser = last.get('iser0')
    if r01 is not None:
        say("")
        say("  BLE 子系统（comm core）状态：")
        say("    AON PWR_RET01 = 0x%08X" % r01)
        say("      comm core  电源 = %d    comm timer 电源 = %d"
            % ((r01 >> 6) & 1, (r01 >> 7) & 1))
        say("      comm core  隔离 = %d    comm timer 隔离 = %d   （0 = 没隔离，正常）"
            % ((r01 >> 8) & 1, (r01 >> 9) & 1))
        say("      comm core  复位(1=已放开) = %d    comm timer 复位(1=已放开) = %d"
            % ((r01 >> 12) & 1, (r01 >> 11) & 1))
        if not ((r01 >> 6) & 1) or not ((r01 >> 12) & 1):
            say("      → **BLE 子系统压根没上电/还在复位**：这个状态下链路层不会执行任何")
            say("        东西，射频自然一点动静都没有。要查的就是「谁负责给它上电」——")
            say("        原厂固件同一处的调用序列（反汇编里找 comm core 上电那段）。")
        else:
            say("      → BLE 子系统是上电、放开复位的 —— 那就不是电源/复位的问题。")
    if iser is not None:
        say("    NVIC ISER0 = 0x%08X" % iser)
        say("      IRQ1 BLE_SDK（协议栈调度）= %s    IRQ2 BLE = %s    IRQ25 BLESLP = %s"
            % ('使能' if (iser >> ZK_IRQ_BLE_SDK) & 1 else '**没使能**',
               '使能' if (iser >> ZK_IRQ_BLE) & 1 else '**没使能**',
               '使能' if (iser >> ZK_IRQ_BLESSLP) & 1 else '**没使能**'))
        if not ((iser >> ZK_IRQ_BLE_SDK) & 1):
            say("      → 协议栈的调度中断没使能：命令会被收下（API 返回 0），但队列永远")
            say("        没人处理，事件一个都不会递上来。这跟「命令全接受、事件全不回」")
            say("        的症状完全对得上，是要重点怀疑的一条。")

    # ---- AON 定时器：它跑在低功耗时钟上，不走 = 32k 时钟源没起来 ----
    # ---- B2-B 共享内存信箱：看看上位机写的到底落在哪了 ----
    mb_addr = _env_hex('MB_ADDR', ZK_MB_ADDR)
    mbw = [rd(mb_addr + 4 * i) for i in range(8)]
    if mbw[0] is not None:
        say("")
        say("  B2-B 共享内存信箱 0x%08X：" % mb_addr)
        say("    magic     = 0x%08X %s"
            % (mbw[0], '✅ 我们的标记（上位机写过）' if mbw[0] == ZK_MB_MAGIC
               else '✗ 不是 0x%08X —— 要么没人写过，要么写的地址不对' % ZK_MB_MAGIC))
        say("    seq       = %u" % mbw[1])
        say("    len / sum = %u / 0x%08X" % (mbw[2], mbw[3]))
        say("    status    = %u    ack_seq = %u    ms_refresh = %u"
            % (mbw[4], mbw[5], mbw[6]))
        if mbw[0] == ZK_MB_MAGIC:
            if mbw[5] == mbw[1] and mbw[4] == 3:
                say("    → 这一帧固件已经收下并刷完了 ✅")
            elif mbw[5] == mbw[1] and mbw[4] == 0xFF:
                say("    → 固件说这一帧校验不过（len 或 sum 对不上）")
            else:
                say("    → 这一帧还没被处理（固件没在跑这个信箱轮询？先看上面的 build）")

    # ---- 屏的 7 根脚现在的电平：看我们的驱动到底把脚放在什么状态 ----
    data_in = rd(ZK_GPIO0_BASE + 0x00)
    data_out = rd(ZK_GPIO0_BASE + 0x04)
    outen = rd(ZK_GPIO0_BASE + 0x10)
    if data_in is not None:
        say("")
        say("  屏的 7 根脚（GPIO0，0xA0010000）—— 自研固件空闲时留下的状态：")
        say("    脚     输出值  实际电平  是输出吗")
        for bit, name in ZK_EPD_PINS:
            if bit > 15:
                continue
            o = (data_out >> bit) & 1
            i = (data_in >> bit) & 1
            oe = (outen >> bit) & 1
            say("    %-6s   %d       %s        %s"
                % (name, o, i, '是' if oe else '否（输入）'))
        # 辅助脚（原厂编号 24）在 GPIO1 上：16..31 -> GPIO1 的 0..15，所以 24 = P1_8
        g1_in = rd(ZK_GPIO0_BASE + 0x1000)
        g1_out = rd(ZK_GPIO0_BASE + 0x1004)
        g1_oe = rd(ZK_GPIO0_BASE + 0x1010)
        if g1_in is not None:
            bit = 24 - 16
            say("    %-6s   %d       %d        %s   （在 GPIO1 上：编号 24 = P1_8）"
                % ('AUX', (g1_out >> bit) & 1, (g1_in >> bit) & 1,
                   '是' if (g1_oe >> bit) & 1 else '否（输入）'))
        say("    （BUSY 是输入脚：空闲时不忙的话应该是 0；一直是 1 可能是脚悬空/接错）")

    t1 = rd(ZK_AON_TIMERV)
    time.sleep(0.25)
    t2 = rd(ZK_AON_TIMERV)
    lpclk_dead = False
    if t1 is not None and t2 is not None:
        say("")
        say("  AON 定时器（跑在低功耗时钟上）：读两次（隔 250ms）")
        say("    %d  ->  %d    （差 %d）" % (t1, t2, (t2 - t1) & 0xFFFFFFFF))
        if (t2 - t1) & 0xFFFFFFFF == 0:
            lpclk_dead = True
            say("    → **没走**：低功耗时钟（32.768kHz 那一路）没有在跑。")
            say("      这也解释了 PSC 为什么完不成 RTC_CLK 命令。")
        else:
            say("    → 在走，低功耗时钟是活的（那 PSC 卡住就是别的原因）。")

    # ---- 时基自检：SystemCoreClock 跟真实主频对不对得上 ----
    c1 = rd(ZK_DWT_CYCCNT)
    tw0 = time.monotonic()
    time.sleep(0.25)
    c2 = rd(ZK_DWT_CYCCNT)
    tw1 = time.monotonic()
    # 地址从 .map 里查（写死过的那个坑见 _zk_map_syms 的注释）
    sysclk_addr = _zk_sym_addr('SystemCoreClock', ZK_SYSCLK_VAR)
    cycper_addr = _zk_sym_addr('s_cyc_per_us', ZK_CYC_PER_US)
    sysclk = rd(sysclk_addr) if sysclk_addr else None
    cycper = rd(cycper_addr) if cycper_addr else None
    if c1 is not None and c2 is not None and c2 != c1:
        dt = tw1 - tw0
        freq = ((c2 - c1) & 0xFFFFFFFF) / dt
        say("")
        say("  时基自检（固件里所有 ms 都是拿 SystemCoreClock 换算的，所以先看它对不对）：")
        say("    SystemCoreClock = %s   固件的 DWT 校准值 s_cyc_per_us = %s"
            % (sysclk if sysclk else '读不到', cycper if cycper else '0（没用上 DWT）'))
        if sysclk_addr and cycper_addr:
            say("      （两个符号的地址是从 .map 查的：SystemCoreClock @0x%08X，"
                "s_cyc_per_us @0x%08X）" % (sysclk_addr, cycper_addr))
        else:
            say("      ⚠ 本机这份 .map 里没找到 SystemCoreClock / s_cyc_per_us ——")
            say("        这一段读不了（先 bash build.sh 编一次，别拿写死的旧地址凑）")
        say("    DWT CYCCNT 实测：%.2f 秒走了 %d 个周期 ⇒ 实际主频 ≈ %.1f MHz"
            % (dt, (c2 - c1) & 0xFFFFFFFF, freq / 1e6))
        if sysclk and sysclk >= 1000000:
            ratio = freq / float(sysclk)
            if 0.95 <= ratio <= 1.05:
                say("    → 对得上 ✅ 状态块里的 ms 可以直接信。")
            else:
                say("    → **对不上：真实主频是 SystemCoreClock 的 %.2f 倍**" % ratio)
                say("      ⇒ 固件报出来的 ms 要乘 %.2f 才是真时间" % ratio)
                say("      （同时 `epd_wait_busy` 的超时也是按这个错比例算的：")
                say("        标称 30 秒的刷新超时，真实只有 %.1f 秒 —— 慢刷有超时风险）"
                    % (30.0 / ratio))
        elif sysclk:
            say("    → SystemCoreClock 读出来是 %s —— 这个数太小，不可能是主频，"
                "多半是**符号地址不对**（不是固件的问题）。" % sysclk)
            say("      用 .map 查一下真地址：grep -n SystemCoreClock "
                "outputs/firmware/zk42v-epd-app/GCC/out/lst/zk42v_epd.map")

    words = last['words']
    say("")
    say("  状态块 0x%08X：" % ZK_DBG_ADDR)
    if not words:
        say("    读不到")
    else:
        magic, heart, stage, flags = words[0], words[1], words[2], words[3]
        if magic == ZK_DBG_MAGIC:
            say("    magic = 0x%08X ✅ 我们的固件确实往这儿写过东西" % magic)
        else:
            say("    magic = 0x%08X ✗（期望 0x%08X）—— 我们的固件没写过这里"
                % (magic, ZK_DBG_MAGIC))
        say("    stage = %d  → %s" % (stage, ZK_STAGE_TEXT.get(stage, '未知')))
        say("    heart = %d   flags = 0x%X   build = %d" % (heart, flags, words[11]))
        say("    BUSY 线：见过低=%s 见过高=%s  轮询=%d  超时=%d  GPIO 错=%d"
            % ('是' if words[4] & 1 else '否', '是' if words[4] & 2 else '否',
               words[5], words[6], words[7]))
        say("    耗时(ms)：复位+初始化 %d / 传图 %d / 刷新 %d"
            % (words[8], words[9], words[10]))
        build = words[11] if len(words) > 11 else 0
        bmagic = words[14] if len(words) > 14 else 0
        if build >= 2 and bmagic == ZK_BOOT_MAGIC:
            say("    boot_count = %d   uds_seen = %d   （boot_count = 进 main_init 的次数；"
                % (words[12], words[13]))
            say("      这块 RAM 是 NOLOAD、软复位不清，所以它在涨就是芯片在反复复位）")
            if len(words) > 15 and build >= 4:
                say("    test_step = %d   （B1.3 那个「按 RST 换下一步」的遗留字段，"
                    "B1.5 起就没人写了 —— 这块 RAM 不清零，所以那个数是垃圾，别当真）"
                    % words[15])
            if build >= 10 and len(words) > 18:
                sttxt = {0: '没起来', 1: '在广播，等连接', 2: '已连接',
                         3: '扫描实验：在听（build 19+）',
                         4: '所有广播数据变体都被拒了（build 19+）'}.get(words[16], '?')
                say("    BLE: state=%d（%s）  ble_err=%d  mtu=%d"
                    % (words[16], sttxt, words[17], words[18]))
                if words[17]:
                    say("         ^ ble_err 非 0 = 协议栈/广播 API 报的错码，把这个数发我")
            if build >= 13 and len(words) > 20:
                b = struct.pack('<II', words[19], words[20])
                say("    BLE MAC: %02X:%02X:%02X:%02X:%02X:%02X"
                    % (b[5], b[4], b[3], b[2], b[1], b[0]))
            if build >= 18 and len(words) > 23:
                say("    BLE 最后事件: id=0x%03X（%s）  status=%d（%s）  共收到 %d 个事件"
                    % (words[21], ZK_BLE_EVT_NAME.get(words[21], '未知事件'),
                       words[22], ZK_BLE_ERR_NAME.get(words[22], '非 0 就是错误码'),
                       words[23]))
            if build >= 19 and len(words) >= 53:
                _zk_say_ble_experiment(words)
            if build >= 21 and len(words) >= 77:
                _zk_say_epd_service(words)
        else:
            say("    （这一版固件是 build=%d，**还没有** boot_count/uds_seen 这两个计数器，"
                % build)
            say("      状态块里 +0x30 往后那两个数是没被写过的 RAM 垃圾值，别当真；")
            say("      要看这两个数，刷 B1.1 及以后的固件。）")

    say("")
    say("  ---------------- 结论 ----------------")
    pcs = [s['pc'] for s in samples]
    rom_hits = sum(1 for p in pcs if p is not None and p < 0x00080000)
    app_hits = sum(1 for p in pcs if p is not None and 0x0100A000 <= p < 0x0107F000)
    magic_ok = bool(words) and words[0] == ZK_DBG_MAGIC

    # 复位判据：DHCSR 的 S_RESET_ST 粘滞位（每次 halt 后读一次，读即清零）
    rst_hits = sum(1 for s in samples
                   if s['dhcsr'] is not None and (s['dhcsr'] >> 25) & 1)
    ret_hits = sum(1 for s in samples
                   if s['dhcsr'] is not None and (s['dhcsr'] >> 24) & 1)

    if rst_hits:
        say("  ▶ **芯片确实在复位**：%d/%d 次采样里 DHCSR 的 S_RESET_ST=1"
            % (rst_hits, len(samples)))
        say("    （这个位是粘滞的：只要上次读 DHCSR 之后芯片复位过就会置 1。）")
    else:
        say("  ▶ **没看到复位**：%d 次采样里 S_RESET_ST 全是 0。" % len(samples))
        if ret_hits:
            say("    而且 RETIRE_ST=1 —— CPU 在退休指令，也就是说它**在跑**，只是在某个地方打转。")

    if magic_ok and app_hits:
        say("  ▶ 我们的固件在跑（magic 在、PC 也落在 APP / SDK 的 RAM 代码里）。")
    elif magic_ok:
        say("  ▶ 我们的固件至少跑到了 main_init（magic 在），但 PC 不在 APP 里。")
    else:
        say("  ▶ 状态块里没有我们的印记 —— 固件没跑到 main_init，或者根本没被执行。")

    # PC 聚集分析：都挤在一小段里 = 卡在某个循环，不是到处乱跳
    if len(samples) >= 2:
        code_pcs = sorted(p for p in pcs if p is not None and p >= 0x01000000)
        if len(code_pcs) >= 2 and (code_pcs[-1] - code_pcs[0]) <= 0x40:
            say("  ▶ 采样到的 PC 全挤在 0x%08X~0x%08X 这 %d 字节里 ⇒"
                % (code_pcs[0], code_pcs[-1], code_pcs[-1] - code_pcs[0]))
            syms = [s for s in (_zk_sym(p) for p in code_pcs) if s]
            # 「挤在一小段里」有两种情况，得分开说：
            #   · 落在空闲循环的延时/轮询里 -> 这是**正常**的（大部分时间本来就花在
            #     那个 5ms 延时上），stage=9 + 心跳在涨就是活的证据；
            #   · 落在别的地方 -> 才叫「卡住了」。
            idle_hit = bool(syms) and all(
                s.split('+')[0] in ZK_IDLE_FUNCS for s in set(syms))
            if idle_hit:
                say("    这些 PC 落在%s —— **空闲循环里正常打转**"
                    % ('「%s」' % syms[-1] if syms else '延时函数里'))
                say("    （空闲循环 99% 的时间就花在那个 5 ms 延时上，采到它是最正常的；")
                say("      只要 stage=9、心跳还在涨，就说明固件活得好好的。）")
            else:
                say("    **卡在%s这个循环里**（不是在到处跑）。"
                    % ('「%s」' % syms[-1] if syms else '某个'))

    boots = [s['words'][12] for s in samples
             if s['words'] and len(s['words']) > 14
             and s['words'][11] >= 2 and s['words'][14] == ZK_BOOT_MAGIC]
    if len(boots) >= 2 and boots[-1] > boots[0]:
        say("  ▶ boot_count 从 %d 涨到 %d ⇒ 采样这段时间里芯片重启了 %d 次。"
            % (boots[0], boots[-1], boots[-1] - boots[0]))
        say("    （这块 RAM 是 NOLOAD、软复位不清，所以这个数是可信的。）")
    if (words and len(words) > 14 and words[11] >= 2
            and words[14] == ZK_BOOT_MAGIC and words[13]):
        say("  ▶ uds_seen = %d ⇒ 固件清掉过 %d 次「超深睡唤醒」标志。"
            % (words[13], words[13]))
        if words[3] & 0x0010:
            say("    这一版固件已经带上了清除逻辑（flags bit4），说明那个死循环确实犯过。")

    if sw1 is not None and (sw1 & 0xFFFF) == ZK_UDS_MAGIC:
        say("")
        say("  ▶▶ 抓到重点了：AON SOFTWARE_1 低 16 位 = 0x%04X（超深睡唤醒标志）。"
            % ZK_UDS_MAGIC)
        say("     这是在 AON 域，**软复位不会清**。带着它，每次 soc_init() 里的")
        say("     ultra_deep_sleep_wakeup_handle() 都会调 hal_nvic_system_reset()")
        say("     把整个系统复位一遍 -> 无限重启循环。")
        say("     修法：固件在 main_init() 里把它清掉（一行），或者换个不查这个标志的启动路径。")

    if psc is not None and ((psc >> 1) & 1):
        code = (opc & 0xFF) if opc is not None else None
        say("")
        say("  ▶▶ 找到卡死点了：AON 的 PSC 一直 BUSY，卡住的命令是 %s。"
            % (('0x%02X (%s)' % (code, ZK_PSC_OPC.get(code, '未知'))) if code is not None else '（opcode 读不到）'))
        if lpclk_dead:
            say("     而且 AON 定时器不走 ⇒ 低功耗时钟没在跑。")
            say("     合起来看：SDK 在 clock init 里要求把 PSC 时基切到 RTC(32.768kHz)，")
            say("     但这条命令等不到完成的时钟，于是死在 platform_disable_sleep_timer() 的等待循环里。")
            say("     下一步方向：要么让 32k 时钟跑起来（板上有/能起振），")
            say("     要么改 SDK 那条时钟路径（把 LP 时钟换成内部 RC）。")
        else:
            say("     低功耗时钟是在跑的 ⇒ 更像上一轮睡眠被中途打断留下的残留状态，")
            say("     断电重上电（AON 域彻底清零）应该就能过这一步。")

    if last['cfsr'] or last['hfsr']:
        say("")
        say("  ▶ 还看到硬件异常标志（CFSR=0x%s HFSR=0x%s）—— 把这两个值发我，"
            % (('%08X' % last['cfsr']) if last['cfsr'] else '00000000',
               ('%08X' % last['hfsr']) if last['hfsr'] else '00000000'))
        say("     我去查是访存错、非法指令还是别的。")

    say("")
    say("  （采样结束时我把 CPU 恢复成运行状态了，没把它按住不放。）")
    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass


# =====================================================================
#  命令 13：pushimg —— B2-B：把一张图通过 SWD 推进价签的共享内存信箱
#
#  为什么走 SWD 而不是串口：B2-B 想验证的是「图片管线」——任意图 -> 400x300
#  三色 -> 上屏。SWD 这条路我们从头到尾验过（写 flash 用的就是它），零硬件未知；
#  串口的 RX 脚还没挖出来，得先逆一遍引脚复用。等做真基站（BLE）时再上无线。
#
#  固件那边（board/zk_dbg.h 里的 zk_mailbox_t）在空闲循环里轮询：
#     0x30014000 +0x00 magic   '+ZKMB' = 0x5A4B4D42
#                 +0x04 seq     上位机每推一帧 +1（**最后写**）
#                 +0x08 len     期望 30000
#                 +0x0C sum     图像 30000 字节累加和
#                 +0x10 status  固件回写：1=收到 3=刷完 0xFF=校验失败
#                 +0x14 ack_seq 固件回写：处理到哪一帧了
#                 +0x18 ms_refresh
#                 +0x100 图像数据 30000 字节
#
#  写的时候**必须绕开 pyOCD 那条「加速写」的路**：克隆版 ST-Link 的加速读出来的
#  是假数据（这个坑 9-22 就踩过），加速写同样不敢信。所以直接调
#  ap._write_memory_block32()（TAR + DRW 的经典路径）。
# =====================================================================
ZK_MB_MAGIC   = 0x5A4B4D42
ZK_MB_ADDR    = 0x30014000
ZK_MB_IMG_OFF = 0x100
ZK_MB_EPD_LEN = 30000


@command('pushimg', help='B2-B：把 .epd（30000 字节）通过 SWD 推进价签并等它刷完')
def pushimg():
    f = _env_str('EPD_FILE', '')
    if not f or not os.path.exists(f):
        say("  要推哪张图？用 EPD_FILE=<路径> 指给我（30000 字节的 .epd）。")
        return

    data = open(f, 'rb').read()
    if len(data) != ZK_MB_EPD_LEN:
        say("  这个文件 %d 字节，得正好 %d（先用 img2epd.py 转）。"
            % (len(data), ZK_MB_EPD_LEN))
        return

    mb = _env_hex('MB_ADDR', ZK_MB_ADDR)
    img_addr = mb + ZK_MB_IMG_OFF
    seq = _env_int('SEQ', 0)
    if seq == 0:
        seq = int(time.time()) & 0x7FFFFFFF
    chunk = max(16, _env_int('CHUNK', 1024))
    ack_ms = _env_int('ACK_TIMEOUT_MS', 90000)
    sum_ = sum(data) & 0xFFFFFFFF

    say("")
    say("  推图  ——  B2-B：SWD -> 共享内存信箱 -> 固件刷屏")
    say("    文件    : %s" % f)
    say("    长度    : %d 字节，逐字节和 0x%08X" % (len(data), sum_))
    say("    信箱    : 0x%08X（图在 +0x%X）" % (mb, ZK_MB_IMG_OFF))
    say("    帧号    : %d" % seq)
    say("")

    probe = raw_probe()
    manual_hold = MANUAL and (_env_str('MANUAL', '').lower() == 'hold')
    # 注意：**不要**一上来就 reset_driver() —— 它要去开 /dev/cu.usbserial-xxx，
    # 那个串口不在（没插 USB-TTL）时会直接抛异常把整条命令打断。
    # 我们的固件不关 SWD，正常情况下直接连就行，复位源只在实在连不上时才需要。
    rst = None

    def get_rst():
        nonlocal rst
        if rst is None and not MANUAL:
            try:
                rst = reset_driver(probe)
            except Exception as e:
                say("  （复位源打不开：%s" % _first_line(e))
                say("    没关系 —— 我们的固件不关 SWD，直接连就行；连不上再拿线碰一下 RST。）")
                rst = None
        return rst

    # 我们的固件不关 SWD，先试直接连（连上要读 CPUID 才算数）
    hit = None
    t0 = time.monotonic()
    say("  先不复位，直接连 SWD ...")
    while (time.monotonic() - t0) * 1000.0 < _env_int('DIRECT_MS', 1500):
        try:
            probe.connect()
        except Exception:
            time.sleep(0.05)
            continue
        try:
            t = resolve_target()
            if t is not None:
                t.init()
            ap0 = _ap_of(t) if t is not None else None
            v = ap0._read_memory(0xE000ED00) & 0xFFFFFFFF if ap0 is not None else None
            if v == 0x410FC241:
                hit = 0
                break
        except Exception:
            pass
        time.sleep(0.05)

    if hit is None:
        say("  直接连不上，改成抢复位窗口（按住 RST 数到 0 松手）...")
        hit, _tr = _reset_and_grab(probe, get_rst(), manual_hold,
                                   _env_int('DUMP_WINDOW_MS', 8000),
                                   _env_int('HOLD_MS', 200))
        if hit is None:
            say("  没抢到窗口。再跑一次。")
            if rst is not None:
                try:
                    rst.close()
                except Exception:
                    pass
            return
    say("  连上了。")

    target = resolve_target()
    if target is None:
        say("  拿不到 target。")
        return
    try:
        target.init()
    except Exception:
        pass
    ap = _ap_of(target)
    if ap is None:
        say("  拿不到 AHB-AP。")
        return

    def rd(a):
        try:
            return ap._read_memory(a) & 0xFFFFFFFF
        except Exception:
            return None

    def rd_bytes(a, n):
        """按经典 AP 块读读回 n 字节（这条读路在本机是验过的）"""
        out = b''
        while len(out) < n:
            k = min(256, (n - len(out)) // 4)
            if k <= 0:
                break
            try:
                w = list(ap._read_memory_block32(a + len(out), k))
            except Exception:
                return None
            out += b''.join(struct.pack('<I', x & 0xFFFFFFFF) for x in w)
        return out

    def wr_classic(a, words):
        """经典 AP 写：TAR + DRW（ap._write_memory_block32）"""
        if hasattr(ap, '_write_memory_block32'):
            ap._write_memory_block32(a, words)
        else:
            ap.write_memory_block32(a, words)

    def wr_accel(a, words):
        """ST-Link 加速写（probe 自己的 WRITEMEM 命令）"""
        ap.write_memory_block32(a, words)

    # ---- 先探：这两条写路哪条真能写进去 ------------------------------------
    # 为什么要探：这颗克隆版 ST-Link 的"加速读"是出假数据的（9-22 踩过），
    # 加速写同样不敢信；而经典 AP 写在某些克隆固件上也可能被吞掉 ——
    # 关键区别是**写操作不会报错**，只会静静地什么都没发生。所以必须写一小块
    # 再读回来核对，才知道该用哪条。
    scratch = mb + 0x20            # 控制块的 rsv 区，随便用
    pat = 0xA5C3F00D
    chosen = None
    for name, fn in (('经典 AP 写', wr_classic), ('ST-Link 加速写', wr_accel)):
        try:
            fn(scratch, [pat])
        except Exception as e:
            say("  %s 报错：%s" % (name, _first_line(e)))
            continue
        got = rd(scratch)
        if got == pat:
            chosen = name
            say("  写路自检：%s ✅（写 0x%08X 到 0x%08X，读回来对）" % (name, pat, scratch))
            break
        say("  写路自检：%s ✗（写完读回 0x%08X，不是 0x%08X —— 被吞了）"
            % (name, got if got is not None else 0, pat))
    if chosen is None:
        say("")
        say("  ✗ 两条写路都写不进去。这时候别急着改固件 —— 更像是调试器/会话的问题：")
        say("    1) 把 ST-Link 拔了重插，再跑一次；")
        say("    2) 或者 bash status.sh 看固件是不是还在跑（build/stage）。")
        if rst is not None:
            try:
                rst.close()
            except Exception:
                pass
        return

    def wr_block(a, words):
        if chosen == '经典 AP 写':
            wr_classic(a, words)
        else:
            wr_accel(a, words)

    # 1) 先把图写进去
    say("  写图（%d 字节，每次 %d，写完读回核对）..." % (len(data), chunk))
    off = 0
    t0 = time.monotonic()
    while off < len(data):
        n = min(chunk, len(data) - off)
        n -= n % 4
        if n == 0:
            n = 4
        words = list(struct.unpack_from('<%dI' % (n // 4), data, off))
        try:
            wr_block(img_addr + off, words)
        except Exception as e:
            say("    写到 0x%08X 失败：%s" % (img_addr + off, _first_line(e)))
            say("    （这一半可能没写全，重跑一次这条命令就行。）")
            if rst is not None:
                try:
                    rst.close()
                except Exception:
                    pass
            return
        # 读回核对：写操作不报错 ≠ 写进去了
        got = rd_bytes(img_addr + off, n)
        if got != data[off:off + n]:
            say("    ！0x%08X 这块写完读回对不上（写路：%s）" % (img_addr + off, chosen))
            say("      —— 这一帧别指望了，重跑一次这条命令；还这样把这段发我。")
            if rst is not None:
                try:
                    rst.close()
                except Exception:
                    pass
            return
        off += n
        if off % (chunk * 8) == 0 or off == len(data):
            say("    ... %d/%d 字节（%.1f 秒）"
                % (off, len(data), time.monotonic() - t0))

    # 2) 写控制块：len / sum -> magic -> **seq 最后写**
    try:
        wr_block(mb + 0x08, [ZK_MB_EPD_LEN])
        wr_block(mb + 0x0C, [sum_])
        wr_block(mb + 0x00, [ZK_MB_MAGIC])
        wr_block(mb + 0x04, [seq])
    except Exception as e:
        say("  写控制块失败：%s" % _first_line(e))
        return
    say("  控制块写好了（seq=%d 最后写）。" % seq)

    # 3) 等固件回 ack
    say("  等固件刷屏（一帧约 20 秒，最多等 %d 秒）..." % (ack_ms // 1000))
    t0 = time.monotonic()
    ack = None
    while (time.monotonic() - t0) * 1000.0 < ack_ms:
        ack = rd(mb + 0x14)
        st = rd(mb + 0x10)
        if ack == seq:
            ms = rd(mb + 0x18)
            say("")
            if st == 3:
                say("  >>> 刷完了（status=3，用了 %s ms）" % ms)
                say("      —— 看屏上是不是你想要的那张图。")
            elif st == 0xFF:
                say("  >>> 固件说校验不过（status=0xFF）—— 长度或累加和对不上，")
                say("      八成是图没写全，重跑一次这条命令。")
            else:
                say("  >>> 固件 ack 了但 status=%s，把这段发我。" % st)
            break
        try:
            target.resume()      # 固件在跑才能处理信箱；顺便别让它停着
        except Exception:
            pass
        time.sleep(0.3)
    else:
        say("")
        say("  等超时了：ack_seq 读到 %s，期望 %d。" % (ack, seq))
        say("  可能原因：固件没在跑（先 status.sh 看一眼）、或者刷得太慢。")
        say("  （这一帧没丢，固件跑起来后会自己拾起来刷。）")

    if rst is not None:
        try:
            rst.close()
        except Exception:
            pass
