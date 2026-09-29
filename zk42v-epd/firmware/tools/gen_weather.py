#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天气（图标 + 文字）生成器。

    /tmp/zkvenv/bin/python3 tools/gen_weather.py        # 需要 Pillow（见下）

产物（都在库里，能 review、能 diff）：
    tools/weather_img.py                点阵的 ASCII 画（一眼看出"像不像"）
    zk42v-epd-app/Src/img/weather.c/h   固件用的表

--------------------------------------------------------------------------
图标：**不是手画的**（build 41 第一版手画的那 9 个太糙，被否了），
      改成从开源图标字体渲染 ——

        Font Awesome 6 Free（solid 风格）
        https://github.com/FortAwesome/Font-Awesome
        字体 SIL OFL 1.1 / 图标 CC BY 4.0

      文件放 work/fonts/icons/fa-solid-900.ttf（不进库 —— 进库的是渲染出来
      的点阵），下载命令见 main() 里那段报错提示。

      渲染管线：8 倍超采样 -> BOX 缩小到 20x20 -> 阈值 -> 去孤立点。
      选 FA solid 的原因：它是**实心**图标，1 位（纯黑白）下形状最清楚；
      Weather Icons / MDI 都是空心线条，20px 下容易糊成灰边（三套的对比图
      见 docs/preview/preview-weather-icons.png）。

文字：晴 / 多云 / 阴 / 小雨 / 大雨 / 雷阵雨 / 雪 / 雾 / 风
      用**文泉驿点阵宋体 12pt**（16x16、笔画 1px）——跟月历格子里那行农历
      同一个字源，单独一张表（s_wx_glyph），不跟原厂那套 2px 的字混着用。
      为什么不用原厂 u8g2 子集：那个子集里**只有** 小/大/雨/雪 四个字，
      凑不出"雷阵雨"，而且两种笔画粗细摆在一行里会更难看。

--------------------------------------------------------------------------
天气码是**我们自己定的私有协议**（BLE 命令 0x71），跟网页那边对齐：

    0 不显示   1 晴    2 多云   3 阴    4 小雨
    5 大雨     6 雷阵雨 7 雪    8 雾/霾 9 风

（"天气"这件事只可能从外部来 —— 原厂固件里连 I2C 都没有、没有温度传感器，
所以天气/天气温度都走 BLE 由手机下发，见 README。）
"""

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(FW))          # .../a
OUT_C = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img', 'weather.c')
OUT_H = os.path.join(FW, 'zk42v-epd-app', 'Src', 'img', 'weather.h')
OUT_PY = os.path.join(HERE, 'weather_img.py')

ICON_DIR = os.path.join(ROOT, 'work', 'fonts', 'icons')

# 三套候选图标字体（都是开源、都能商用）+ 一个"混搭"方案；用哪套看 PROFILE。
#   fa = Font Awesome 6 Free（solid 实心）：云/雨/雷/雪/风都实心、够醒目；
#        缺点是它的"太阳"在 20px 下中间会剩一个小白洞，像齿轮。
#   wi = Weather Icons（描边）：形状最像天气图标，但笔画只有 1px，偏轻。
#   ms = Material Symbols（Google 可变字体，FILL=1 填实）：太阳是实心圆 + 光芒，
#        最好看；但它的"大雨"只画几道斜线、没有云。
SETS = {
    'fa': {
        'ttf': 'fa-solid-900.ttf',
        'name': 'Font Awesome 6 Free (solid)',
        'license': 'SIL OFL 1.1（字体）/ CC BY 4.0（图标）',
        'url': 'https://raw.githubusercontent.com/FortAwesome/Font-Awesome/6.x/'
               'webfonts/fa-solid-900.ttf',
    },
    'wi': {
        'ttf': 'weathericons.ttf',
        'name': 'Weather Icons (erikflowers)',
        'license': 'SIL OFL 1.1',
        'url': 'https://raw.githubusercontent.com/erikflowers/weather-icons/master/'
               'font/weathericons-regular-webfont.ttf',
    },
    'ms': {
        'ttf': 'MaterialSymbolsOutlined.ttf',
        'name': 'Material Symbols (Google)',
        'license': 'Apache License 2.0',
        'url': 'https://raw.githubusercontent.com/google/material-design-icons/master/'
               'variablefont/MaterialSymbolsOutlined%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf',
        'axes': [1, 0, 20, 400],       # FILL=1（填实）, GRAD=0, opsz=20, wght=400
    },
}

# 每个天气码用哪套字体的哪个码点（码点来自各套字体官方的 codepoints 文件）
FA = {'晴': 0xF185, '多云': 0xF6C4, '阴': 0xF0C2, '小雨': 0xF73D, '大雨': 0xF740,
      '雷阵雨': 0xF76C, '雪': 0xF2DC, '雾': 0xF75F, '风': 0xF72E}
WI = {'晴': 0xF00D, '多云': 0xF002, '阴': 0xF013, '小雨': 0xF01C, '大雨': 0xF019,
      '雷阵雨': 0xF01E, '雪': 0xF01B, '雾': 0xF014, '风': 0xF021}
MS = {'晴': 0xE81A, '多云': 0xF172, '阴': 0xF15C, '小雨': 0xF176, '大雨': 0xF61F,
      '雷阵雨': 0xEBDB, '雪': 0xE2CD, '雾': 0xE818, '风': 0xEFD8}


def profile(one, two, three, four, five, six, seven, eight, nine):
    return {1: one, 2: two, 3: three, 4: four, 5: five,
            6: six, 7: seven, 8: eight, 9: nine}


PROFILES = {
    'fa': profile(('fa', FA['晴']), ('fa', FA['多云']), ('fa', FA['阴']),
                  ('fa', FA['小雨']), ('fa', FA['大雨']), ('fa', FA['雷阵雨']),
                  ('fa', FA['雪']), ('fa', FA['雾']), ('fa', FA['风'])),
    'wi': profile(('wi', WI['晴']), ('wi', WI['多云']), ('wi', WI['阴']),
                  ('wi', WI['小雨']), ('wi', WI['大雨']), ('wi', WI['雷阵雨']),
                  ('wi', WI['雪']), ('wi', WI['雾']), ('wi', WI['风'])),
    'ms': profile(('ms', MS['晴']), ('ms', MS['多云']), ('ms', MS['阴']),
                  ('ms', MS['小雨']), ('ms', MS['大雨']), ('ms', MS['雷阵雨']),
                  ('ms', MS['雪']), ('ms', MS['雾']), ('ms', MS['风'])),
    # 混搭（build 41 选这个）：太阳/多云用 Material Symbols（实心圆 + 光芒最好看），
    # 云/雨/雷/雪/雾/风用 Font Awesome（实心、形状最清楚）。
    'mix': profile(('ms', MS['晴']), ('ms', MS['多云']), ('fa', FA['阴']),
                   ('fa', FA['小雨']), ('fa', FA['大雨']), ('fa', FA['雷阵雨']),
                   ('fa', FA['雪']), ('fa', FA['雾']), ('fa', FA['风'])),
}

# 选中的方案 —— 三套 + 混搭的对比图都留在 docs/preview/preview-weather-icons*.png
PROFILE = 'mix'

# 天气码 -> 图标名/文字（顺序 = C 里的数组下标，**跟网页对齐别改**）
ICON_NAME = {1: '晴', 2: '多云', 3: '阴', 4: '小雨', 5: '大雨',
             6: '雷阵雨', 7: '雪', 8: '雾', 9: '风'}

ICON = 20       # 图标 20x20（表头 26px 高，上下各留 3px）
SS = 8          # 超采样倍数
TH = 128        # 二值化阈值

# 天气文字要不要"左右加粗 1px"（跟表头"农历"那两个字的字重一致）。
# False = 文泉驿点阵宋体原样（1px 细字）；True = 形态学膨胀，竖笔画 1px、横笔画 2px。
# 实测（build 41）：密集字（雷/阵/雾）加粗后会糊成一块，所以默认**不加粗**。
TEXT_BOLD = False

WQY_BDF12 = os.path.join(ROOT, 'work', 'fonts', 'wenquanyi_12pt.bdf')


def icon_ttf(setname):
    """某套图标字体的路径"""
    return os.path.join(ICON_DIR, SETS[setname]['ttf'])


# ---------------------------------------------------------------- 工具

def pack(rows, w):
    """ASCII 点阵 -> 每行若干字节（MSB first，行末补到整字节）"""
    stride = (w + 7) // 8
    out = []
    for r in rows:
        bits = [1 if c in '#1' else 0 for c in r]
        assert len(bits) == w, '一行 %d 位，实际 %d' % (w, len(bits))
        for b in range(stride):
            v = 0
            for i in range(8):
                idx = b * 8 + i
                v = (v << 1) | (bits[idx] if idx < w else 0)
            out.append(v)
    return out


def despeckle(rows, min_nb=1):
    """去掉孤立点：8 邻域里邻居少于 min_nb 个的像素扔掉（栅格化的毛刺）"""
    g = [[1 if c == '#' else 0 for c in r] for r in rows]
    h, w = len(g), len(g[0])
    out = []
    for y in range(h):
        line = []
        for x in range(w):
            if not g[y][x]:
                line.append('.')
                continue
            nb = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and g[yy][xx]:
                        nb += 1
            line.append('#' if nb >= min_nb else '.')
        out.append(''.join(line))
    return out


def render_icon(font_path, cp, size=ICON, ss=SS, th=TH, axes=None):
    """TTF 字形 -> size x size 的 '#'/'.' 点阵（居中，四周留 1px）"""
    from PIL import Image, ImageDraw, ImageFont

    box = size - 2                      # 留 1px 边，别贴死
    font = ImageFont.truetype(font_path, box * ss)
    if axes:                            # 可变字体（Material Symbols）：先把轴调好
        font.set_variation_by_axes(axes)
    big = int(box * ss * 2.5)
    img = Image.new('L', (big, big), 0)
    ImageDraw.Draw(img).text((big // 3, big // 3), chr(cp), font=font, fill=255)
    bbox = img.getbbox()
    if not bbox:
        raise SystemExit('码点 U+%04X 在 %s 里是空字形' % (cp, font_path))
    img = img.crop(bbox)
    w, h = img.size
    k = float(box) / max(w, h)          # 目标尺寸（最终像素）÷ 超采样后的尺寸
    img = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.BOX)
    cell = Image.new('L', (size, size), 0)
    cell.paste(img, ((size - img.size[0]) // 2, (size - img.size[1]) // 2))
    rows = []
    for y in range(size):
        rows.append(''.join('#' if cell.getpixel((x, y)) >= th else '.'
                            for x in range(size)))
    return despeckle(rows)


def wqy_glyph(ch):
    """文泉驿点阵宋体 12pt -> 16x16 的 '#'/'.'（1px 细字，不补笔画）"""
    import gen_wqy_bitmap as B

    f12 = B.load_bdf(WQY_BDF12)
    g = f12.get(ord(ch))
    if g is None:
        raise SystemExit('BDF 里没有 %s' % ch)
    rows = B.pad16(B.rows_of(g, g['bbx'][0]))
    if TEXT_BOLD:
        rows = B.dilate(rows, 1, horiz_only=True)
    return rows


# ---------------------------------------------------------------- 输出

def emit_module(icons, glyphs):
    """把点阵写成 tools/weather_img.py（进库，review 用）"""
    L = []
    a = L.append
    a('# 自动生成 —— 别手改。见 tools/gen_weather.py。')
    a('#')
    a('# 天气图标（%dx%d），方案 %s：' % (ICON, ICON, PROFILE))
    for code in range(1, 10):
        setname, cp = PROFILES[PROFILE][code]
        a('#   %d %-4s %s U+%04X' % (code, ICON_NAME[code], SETS[setname]['name'], cp))
    a('# 天气文字（16x16、1px）：文泉驿点阵宋体 12pt。')
    a('')
    a('ICON_W = %d' % ICON)
    a('ICON_H = %d' % ICON)
    a('')
    a('ICONS = {')
    for code in range(1, 10):
        a("    %d: [   # %s" % (code, ICON_NAME[code]))
        for r in icons[code]:
            a("        '%s'," % r)
        a('    ],')
    a('}')
    a('')
    a('GLYPHS = {')
    for ch in sorted(glyphs):
        a("    '%s': [" % ch)
        for r in glyphs[ch]:
            a("        '%s'," % r)
        a('    ],')
    a('}')
    a('')
    with open(OUT_PY, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))


def emit_c(icons, glyphs):
    chars = sorted(glyphs)
    stride = (ICON + 7) // 8
    macro = {1: 'SUNNY', 2: 'CLOUDY', 3: 'OVERCAST', 4: 'LIGHTRAIN', 5: 'HEAVYRAIN',
             6: 'THUNDER', 7: 'SNOW', 8: 'FOG', 9: 'WIND'}

    h = []
    a = h.append
    a('/*')
    a(' * 天气图标 + 天气文字 —— 自动生成，别手改（改 tools/gen_weather.py 再跑）。')
    a(' *')
    used = []
    for setname in ('fa', 'wi', 'ms'):
        if any(PROFILES[PROFILE][c][0] == setname for c in range(1, 10)):
            used.append('%s（%s）' % (SETS[setname]['name'], SETS[setname]['license']))
    a(' * 图标：%dx%d 的 1 位点阵，方案 "%s" —— 从开源图标字体渲染，不是手画的。' % (ICON, ICON, PROFILE))
    for u in used:
        a(' *       %s' % u)
    a(' *       来源见 tools/gen_weather.py 与 README 的「第三方素材」。')
    a(' * 文字：文泉驿点阵宋体 12pt（16x16、笔画 1px），单独一张表。')
    a(' *')
    a(' * 天气码（BLE 命令 0x71，跟网页对齐）：')
    a(' *   0 不显示  1 晴  2 多云  3 阴  4 小雨  5 大雨  6 雷阵雨  7 雪  8 雾  9 风')
    a(' *')
    a(' * 为什么天气要手机下发：这块板子上没有温度/天气传感器（原厂固件里连 I2C')
    a(' * 都没有），屏上要显示"天气"就只能从外部来。')
    a(' */')
    a('#ifndef __ZK_WEATHER_H__')
    a('#define __ZK_WEATHER_H__')
    a('')
    a('#include <stdint.h>')
    a('')
    a('#define ZK_WX_NONE      0u    /* 不显示 */')
    for code in range(1, 10):
        a('#define ZK_WX_%-11s %du' % (macro[code], code))
    a('#define ZK_WX_MAX       9u')
    a('')
    a('#define ZK_WX_ICON_W    %d' % ICON)
    a('#define ZK_WX_ICON_H    %d' % ICON)
    a('#define ZK_WX_TEXT_W    16')
    a('#define ZK_WX_TEXT_H    16')
    a('')
    a('/* 天气码 -> 中文名（UTF-8，给日志/通知用；未知码返回 "?"） */')
    a('const char *zk_weather_name(uint8_t code);')
    a('')
    a('/* 天气码 -> 文字（unicode 码点数组，0 结尾；码 0 = 空） */')
    a('const uint32_t *zk_weather_text(uint8_t code);')
    a('')
    a('/* 天气码 -> %dx%d 的 1 位图标（每行 %d 字节，MSB first）；码非法返回 0 */'
      % (ICON, ICON, stride))
    a('const uint8_t *zk_weather_icon(uint8_t code);')
    a('')
    a('/* 码点 -> 16x16 的文字点阵（每行 2 字节）；表里没有返回 0 */')
    a('const uint8_t *zk_weather_glyph(uint32_t cp);')
    a('')
    a('#endif /* __ZK_WEATHER_H__ */')

    c = []
    a = c.append
    a('/*')
    a(' * 天气图标/文字 —— 见 weather.h。点阵由 tools/gen_weather.py 生成。')
    a(' */')
    a('#include "weather.h"')
    a('')
    a('/* 图标：下标 = 天气码，0 空着（不显示）。每行 %d 字节，MSB first。' % stride)
    a('   方案 "%s"，每个图标的来源：' % PROFILE)
    for code in range(1, 10):
        setname, cp = PROFILES[PROFILE][code]
        a('     %d %s = %s U+%04X' % (code, ICON_NAME[code], SETS[setname]['name'], cp))
    a('   */')
    a('static const uint8_t s_wx_icon[10][%d] = {' % (stride * ICON))
    a('    { 0 },   /* 0 = 不显示 */')
    for code in range(1, 10):
        vals = pack(icons[code], ICON)
        a('    {')
        for i in range(0, len(vals), stride):
            a('        ' + ', '.join('0x%02X' % v for v in vals[i:i + stride]) + ',')
        a('    },   /* %d %s */' % (code, ICON_NAME[code]))
    a('};')
    a('')
    a('/* 文字单字表（文泉驿点阵宋体 12pt；每字 16 行 x 2 字节） */')
    a('static const uint32_t s_wx_glyph_cp[%d] = {' % len(chars))
    a('    ' + ', '.join('0x%04X' % ord(ch) for ch in chars) + ',')
    a('};')
    a('static const uint8_t s_wx_glyph[%d][32] = {' % len(chars))
    for ch in chars:
        vals = pack(glyphs[ch], 16)
        a('    {')
        for i in range(0, len(vals), 8):
            a('        ' + ', '.join('0x%02X' % v for v in vals[i:i + 8]) + ',')
        a('    },   /* %s */' % ch)
    a('};')
    a('')
    a('/* 天气码 -> 文字码点（0 结尾）。跟 zk_weather_name() 同一份数据的两种写法，')
    a('   固件画字用这个（省得在 MCU 上解 UTF-8）。 */')
    a('static const uint32_t s_wx_text[10][4] = {')
    a('    { 0 },   /* 0 = 不显示 */')
    for code in range(1, 10):
        cps = [ord(ch) for ch in ICON_NAME[code]] + [0]
        a('    { %s },   /* %s */'
          % (', '.join('0x%04X' % v for v in cps), ICON_NAME[code]))
    a('};')
    a('')
    a('static const char *const s_wx_name[10] = {')
    a('    "未知", ' + ', '.join('"%s"' % ICON_NAME[code] for code in range(1, 10)) + ',')
    a('};')
    a('')
    a('const char *zk_weather_name(uint8_t code)')
    a('{')
    a('    return (code <= ZK_WX_MAX) ? s_wx_name[code] : "?";')
    a('}')
    a('')
    a('const uint32_t *zk_weather_text(uint8_t code)')
    a('{')
    a('    return (code <= ZK_WX_MAX) ? s_wx_text[code] : s_wx_text[0];')
    a('}')
    a('')
    a('const uint8_t *zk_weather_icon(uint8_t code)')
    a('{')
    a('    return (code >= 1u && code <= ZK_WX_MAX) ? s_wx_icon[code] : 0;')
    a('}')
    a('')
    a('const uint8_t *zk_weather_glyph(uint32_t cp)')
    a('{')
    a('    int i;')
    a('')
    a('    for (i = 0; i < %d; i++)' % len(chars))
    a('    {')
    a('        if (s_wx_glyph_cp[i] == cp)')
    a('        {')
    a('            return s_wx_glyph[i];')
    a('        }')
    a('    }')
    a('    return 0;')
    a('}')

    with open(OUT_H, 'w', encoding='utf-8') as f:
        f.write('\n'.join(h) + '\n')
    with open(OUT_C, 'w', encoding='utf-8') as f:
        f.write('\n'.join(c) + '\n')


def main():
    print('图标方案：%s' % PROFILE)
    need = sorted({PROFILES[PROFILE][c][0] for c in range(1, 10)})
    for setname in need:
        st = SETS[setname]
        ttf = icon_ttf(setname)
        if not os.path.exists(ttf):
            raise SystemExit(
                '找不到 %s\n'
                '下载命令（%s）：\n'
                '  mkdir -p "%s" && curl -sSL -o "%s" "%s"'
                % (ttf, st['name'], ICON_DIR, ttf, st['url']))
        sha = hashlib.sha256(open(ttf, 'rb').read()).hexdigest()
        print('  %s -> %s' % (setname, os.path.basename(ttf)))
        print('     %s / %d 字节 / SHA-256 %s'
              % (st['license'], os.path.getsize(ttf), sha))

    icons = {}
    for code in range(1, 10):
        nm = ICON_NAME[code]
        setname, cp = PROFILES[PROFILE][code]
        rows = render_icon(icon_ttf(setname), cp, axes=SETS[setname].get('axes'))
        assert len(rows) == ICON and all(len(r) == ICON for r in rows), nm
        ink = sum(r.count('#') for r in rows)
        assert ink > 20, '%s 的图标几乎是空的（墨=%d）' % (nm, ink)
        icons[code] = rows
        print('  图标 %-4s 码 %d  %s U+%04X  墨=%3d'
              % (nm, code, setname, cp, ink))

    chars = sorted({ch for code in range(1, 10) for ch in ICON_NAME[code]})
    glyphs = {}
    for ch in chars:
        rows = wqy_glyph(ch)
        assert len(rows) == 16 and all(len(r) == 16 for r in rows), ch
        glyphs[ch] = rows
        print('  文字 %s 墨=%3d' % (ch, sum(r.count('#') for r in rows)))

    emit_module(icons, glyphs)
    emit_c(icons, glyphs)
    print('写出 %s\n     %s\n     %s' % (OUT_PY, OUT_C, OUT_H))


if __name__ == '__main__':
    sys.exit(main())
