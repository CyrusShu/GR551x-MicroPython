#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把基站里那几个**手写 JSON 查找函数**搬到电脑上跑一遍 —— 用真实的报文喂它们。

    python3 esp32/tests/test_json_fields.py

为什么要有它（2026-10-02 实机事故）：
    中继是 Python `json.dumps` 出来的，默认写法是 `"atype": "暴雨"`（**冒号后有空格**）。
    基站里找字符串的那个函数写的是 `"atype":"`（没有空格），于是：
        · 数字字段（那个函数本来就跳过空格）—— 全对：alevel=4 → 橙色
        · 字符串字段 —— 全空：atype=""、aend=""
    串口里就是 `⚠ 预警 橙色预警 到 `（名字和时间都是空的），屏上画不出预警；
    **天气照常，所以不看串口根本发现不了**。

    这类"手写解析 vs 真实报文"的坑，在电脑上跑 1 秒钟就能发现，没必要烧到板子上试。
    所以这个脚本**直接从 .ino 里抠出函数源码**（不复制、不会和固件脱节），
    配一个极小的 Arduino String 替身，把 JSON 喂进去看结果。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# 想拿**另一个版本**的 .ino 来跑（比如改之前那份，验证这个测试真能抓出问题）：
#     ZK_INO=/tmp/old.ino python3 esp32/tests/test_json_fields.py
INO = os.environ.get("ZK_INO") or os.path.join(HERE, "..", "zk_base_esp32", "zk_base_esp32.ino")

# 要从 .ino 里抠出来的函数（顺序 = 依赖顺序，前两个内部会用到字符串比较）
WANT = ["findJsonNumber", "findJsonStringInCurrent", "findJsonString"]


def extract_function(src: str, name: str) -> str:
    """按大括号配对把某个函数的定义整段抠出来（含前面的注释行不要，只要函数本体）。"""
    m = re.search(r"\n(static\s+bool\s+" + re.escape(name) + r"\s*\()", src)
    if not m:
        raise SystemExit("在 .ino 里找不到函数 %s" % name)
    start = m.start(1)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise SystemExit("函数 %s 的大括号不配对" % name)


# Arduino String 的极小替身：只实现 .ino 里用到的那几个方法。
STUB = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

class String {
public:
    char *b;
    String()                       { b = strdup(""); }
    String(const char *s)          { b = strdup(s ? s : ""); }
    String(const String &o)        { b = strdup(o.b); }
    ~String()                      { free(b); }
    String &operator=(const String &o) { if (this != &o) { free(b); b = strdup(o.b); } return *this; }

    unsigned int length() const    { return (unsigned int)strlen(b); }
    char operator[](int i) const   { return b[i]; }

    int indexOf(const String &o) const {
        const char *p = strstr(b, o.b);
        return p ? (int)(p - b) : -1;
    }
    int indexOf(const String &o, int from) const {
        if (from < 0 || from > (int)strlen(b)) return -1;
        const char *p = strstr(b + from, o.b);
        return p ? (int)(p - b) : -1;
    }
    int indexOf(char c, int from) const {
        if (from < 0 || from > (int)strlen(b)) return -1;
        const char *p = strchr(b + from, c);
        return p ? (int)(p - b) : -1;
    }
    String substring(int a) const {
        return String(b + a);
    }
    String substring(int a, int e) const {
        char *t = (char *)malloc((size_t)(e - a) + 1);
        memcpy(t, b + a, (size_t)(e - a));
        t[e - a] = 0;
        String r(t);
        free(t);
        return r;
    }
    void toCharArray(char *out, unsigned int n) const {
        if (n == 0) return;
        strncpy(out, b, n);
        out[n - 1] = 0;
    }
    float toFloat() const { return (float)atof(b); }
};

static String operator+(const String &a, const char *c) {
    char *t = (char *)malloc(strlen(a.b) + strlen(c) + 1);
    strcpy(t, a.b);
    strcat(t, c);
    String r(t);
    free(t);
    return r;
}
"""

MAIN = r"""
static int fails = 0;

static void check_str(const char *what, const char *got, const char *want)
{
    if (strcmp(got, want) == 0) {
        printf("  ✅ %-46s = \"%s\"\n", what, got);
    } else {
        printf("  ❌ %-46s = \"%s\"（应该是 \"%s\"）\n", what, got, want);
        fails++;
    }
}

static void check_num(const char *what, bool ok, float got, float want)
{
    if (ok && got == want) {
        printf("  ✅ %-46s = %g\n", what, got);
    } else {
        printf("  ❌ %-46s = %s%g（应该是 %g）\n", what, ok ? "" : "(没读到)", got, want);
        fails++;
    }
}

static void check_notfound(const char *what, bool found)
{
    if (!found) {
        printf("  ✅ %-46s 正确地没找到\n", what);
    } else {
        printf("  ❌ %-46s 不该找到\n", what);
        fails++;
    }
}

int main(void)
{
    char buf[64];
    float v = 0;
    bool  ok;

    /* ---- ① 中继的**带空格**报文（就是 2026-10-02 出事那种；json.dumps 默认写法）---- */
    const char *spaced =
        "{\"code\": 4, \"temp\": 26, \"text\": \"小雨\", \"src\": \"qweather\", "
        "\"obs\": \"2026-10-02T19:48+08:00\", \"alert\": 3, \"alevel\": 4, "
        "\"atype\": \"暴雨\", \"aend\": \"2026-10-03T19:25+08:00\"}";
    printf("① 中继带空格的报文（回归用例）\n");
    {
        String b(spaced);
        ok = findJsonNumber(b, "alert", &v);
        check_num("alert", ok, v, 3);
        ok = findJsonNumber(b, "alevel", &v);
        check_num("alevel", ok, v, 4);
        buf[0] = 0;
        ok = findJsonString(b, "atype", buf, sizeof(buf));
        check_str("atype", ok ? buf : "(没读到)", "暴雨");
        buf[0] = 0;
        ok = findJsonString(b, "aend", buf, sizeof(buf));
        check_str("aend", ok ? buf : "(没读到)", "2026-10-03T19:25+08:00");
    }

    /* ---- ② 紧凑报文（中继改成 separators=(",",":") 之后就是这个）---- */
    const char *compact =
        "{\"code\":4,\"temp\":26,\"text\":\"小雨\",\"src\":\"qweather\","
        "\"obs\":\"2026-10-02T19:48+08:00\",\"alert\":3,\"alevel\":4,"
        "\"atype\":\"暴雨\",\"aend\":\"2026-10-03T19:25+08:00\"}";
    printf("② 紧凑报文（改完之后）\n");
    {
        String b(compact);
        ok = findJsonNumber(b, "alevel", &v);
        check_num("alevel", ok, v, 4);
        buf[0] = 0;
        findJsonString(b, "atype", buf, sizeof(buf));
        check_str("atype", buf, "暴雨");
    }

    /* ---- ③ 没有预警的老版中继 / 中继关机时的兜底报文 ---- */
    printf("③ 没有预警字段（老中继 / 兜底）\n");
    {
        String b("{\"code\": 2, \"temp\": 29.5, \"text\": \"多云\"}");
        check_notfound("alert（不该找到）", findJsonNumber(b, "alert", &v));
        buf[0] = 0;
        check_notfound("atype（不该找到）", findJsonString(b, "atype", buf, sizeof(buf)));
        ok = findJsonNumber(b, "temp", &v);
        check_num("temp（带小数）", ok, v, 29.5f);
    }

    /* ---- ④ 预警类型名里的**冒号**不能把解析带偏（正文里常有 "预计：…"）---- */
    printf("④ 值里带冒号 / 转义引号\n");
    {
        const char *tricky =
            "{\"alert\":1,\"alevel\":3,\"atype\":\"暴雨\","
            "\"aend\":\"2026-10-03T19:25+08:00\",\"text\":\"预计：未来 2 小时\"}";
        String b(tricky);
        buf[0] = 0;
        findJsonString(b, "atype", buf, sizeof(buf));
        check_str("atype（后面别的字段里有冒号）", buf, "暴雨");
    }

    /* ---- ⑤ 找错 key 时不能"顺手套"到别的字段上 ---- */
    printf("⑤ 别把别的字段当目标\n");
    {
        String b("{\"alevelx\": 9, \"alevel\": 4}");
        ok = findJsonNumber(b, "alevel", &v);
        check_num("alevel（前面有个 alevelx 干扰）", ok, v, 4);
    }

    printf("\n%s\n", fails == 0 ? "全部通过 ✅" : "有失败 ❌");
    return fails == 0 ? 0 : 1;
}
"""


def main() -> int:
    src = open(INO, encoding="utf-8").read()
    parts = [STUB]
    for name in WANT:
        parts.append("\n/* ---- 从 .ino 抠出来的 %s ---- */\n" % name)
        parts.append(extract_function(src, name))
    parts.append(MAIN)

    with tempfile.TemporaryDirectory(prefix="zkjson-") as td:
        c = os.path.join(td, "t.c")
        exe = os.path.join(td, "t")
        with open(c, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        # 用 c++ 而不是 cc：.ino 里那几个函数是 C++（Arduino String），
        # 用 cc 链接会缺 libc++ 的符号（std::terminate 之类）。
        r = subprocess.run(["c++", "-std=gnu++11", "-O0", "-Wall", "-Wno-unused-function",
                            c, "-o", exe], capture_output=True, text=True)
        if r.returncode != 0:
            print("编译失败：\n" + r.stderr)
            return 2
        return subprocess.run([exe]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
