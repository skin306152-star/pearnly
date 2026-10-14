# -*- coding: utf-8 -*-
"""CC 前端（frontend/assistant）的样式规范闸门：颜色、圆角、阴影只能引用 globals.css 的变量。

## 为什么必须有它

CC 的样式规范「ccs」（docs/development/ccs.md）= Anthropic 官方公开的 Claude 样式变量 + 官方没覆盖的外壳实测值 +
CC 橘黄强调色，**取值只写在 frontend/assistant/src/globals.css 一处**。组件里一旦写死 `#c0392b`、`text-red-500`、
`rounded-[14px]`、`shadow-[0_2px_8px_rgba(...)]`，深色模式不跟着变、和官方取值慢慢漂开，构建和类型检查都不会拦。

## 判据（frontend/assistant/src 下，除 globals.css 外的 .ts/.tsx/.js/.jsx/.mjs/.css；注释不算）

· 颜色：不许 `#hex`、`rgb()/rgba()/hsl()/hsla()/hwb()/lab()/lch()/oklab()/oklch()`；
  不许 Tailwind 调色板类（`text-red-500`、`bg-emerald-50`、`border-white`、`bg-black/50` …）；
  style 里的颜色字面量（`color: "white"`）也不行，只能 `var(--…)`；
· 圆角：`rounded*` 只许 globals.css 接到变量的档位（xs / sm / md / lg / xl / full / none / bubble / dialog）
  或 `rounded-[var(--…)]` / `rounded-(--…)`；不许裸 `rounded`、`rounded-2xl` 一类 Tailwind 自带数值、`rounded-[14px]`；
  style 里的 `borderRadius` 只能 `var(--…)`；
· 阴影：`shadow*` 只许 globals.css 接到变量的档位（hairline / xs / sm / md / lg / pop / none）、主题色名
  或 `shadow-[var(--…)]` / `shadow-(--…)`；不许裸 `shadow`、`shadow-xl`、`shadow-2xl`、`drop-shadow-*`、`shadow-[0_…]`；
  style 里的 `boxShadow` 只能 `var(--…)`。

没有忽略清单：只按路径排除 node_modules 与构建产物。确需新档位，先在 globals.css 的 @theme 接到变量（本脚本从那里读允许的名字）。

用法：

    python3 scripts/check-frontend-tokens.py            # 有违规退出码 1
    python3 scripts/check-frontend-tokens.py --root DIR # 检查别的目录（测试用）
"""

from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "frontend", "assistant", "src")
SOURCE = "globals.css"
EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".css")
SKIP_DIRS = {"node_modules", "dist", "build", ".vite"}

HUES = ("slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|"
        "purple|fuchsia|pink|rose|mauve|olive|mist|taupe")
COLOR_UTIL = (r"(?:text|bg|border(?:-[xytrbles]|-[se])?|ring(?:-offset)?|inset-ring|outline|fill|stroke|from|via|to|divide|"
              r"decoration|caret|accent|placeholder|shadow|inset-shadow|drop-shadow)")
# 类名的左边界：行首、空白、引号、反引号、冒号（变体）、感叹号（!important）、方括号、花括号
LEFT = r"(?:^|(?<=[\s\"'`:!\[{(,]))"
# 档位：方括号 / 圆括号里的任意值，或普通名字；可带 /透明度
SIZE = r"(?:\[[^\]\s]*\]|\([^)\s]*\)|[^\s\"'`;,)}\[(/]+)(?:/\d+)?"
# 类名的右边界
RIGHT = r"(?=$|[\s\"'`;,)}\]!])"

HEX = re.compile(r"(?<![\w&/\\-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
COLOR_FN = re.compile(r"(?<![A-Za-z0-9-])(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(")
PALETTE = re.compile(LEFT + r"-?" + COLOR_UTIL + r"-(?:(?:" + HUES + r")-\d{2,3}|white|black)(?:/\d+)?" + RIGHT)
ROUNDED = re.compile(LEFT + r"-?rounded(?:-(?:t|r|b|l|s|e|ss|se|es|ee|tl|tr|bl|br))?(?:-(?P<size>" + SIZE + r"))?" + RIGHT)
SHADOW = re.compile(LEFT + r"-?(?P<kind>drop-shadow|inset-shadow|shadow)(?:-(?P<size>" + SIZE + r"))?" + RIGHT)
STYLE_LITERAL = re.compile(
    r"\b(?P<prop>color|background|backgroundColor|borderColor|outlineColor|fill|stroke|borderRadius|boxShadow)"
    r"\s*:\s*(?P<value>[\"'`][^\"'`]*[\"'`]|\d[\w.]*)")
VARIABLE = re.compile(r"^(?:\[(?:length:|color:)?var\(--[\w-]+\)\]|\((?:length:|color:)?--[\w-]+\))(?:/\d+)?$")

RADIUS_OK_BASE = {"none", "full"}
SHADOW_OK_BASE = {"none"}
STYLE_KEYWORDS = {"transparent", "currentcolor", "currentColor", "inherit", "initial", "unset", "none", "0"}


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def theme_names(css: str, prefix: str) -> set:
    """globals.css 的 @theme 里接过的名字（--radius-md → md）。"""
    block = css.split("@theme inline {", 1)[1].split("\n}", 1)[0] if "@theme inline {" in css else ""
    return set(re.findall(r"--" + prefix + r"-([\w-]+?)\s*:", block))


def strip_comments(text: str) -> str:
    """去掉 /* … */ 与 // 行注释（保留换行，行号不变）；字符串里的 // （网址）不动。"""
    out = []
    i, n = 0, len(text)
    quote = None
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            out.append(c)
            i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:end]))
            i = end
            continue
        if text.startswith("//", i) and (i == 0 or text[i - 1] not in ":\\"):
            end = text.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def check_text(text: str, radius_ok: set, shadow_ok: set, colors: set) -> list:
    """返回 [(行号, 规则, 片段)]。"""
    found = []
    code = strip_comments(text)
    for lineno, line in enumerate(code.splitlines(), 1):
        for m in HEX.finditer(line):
            found.append((lineno, "color-hex", m.group(0)))
        for m in COLOR_FN.finditer(line):
            found.append((lineno, "color-function", m.group(0)))
        for m in PALETTE.finditer(line):
            found.append((lineno, "color-palette", m.group(0)))
        for m in ROUNDED.finditer(line):
            size = m.group("size")
            if size is None or not (size in radius_ok or VARIABLE.match(size)):
                found.append((lineno, "radius", m.group(0)))
        for m in SHADOW.finditer(line):
            size, kind = m.group("size"), m.group("kind")
            if kind != "shadow":
                if size is None or not VARIABLE.match(size):
                    found.append((lineno, "shadow", m.group(0)))
                continue
            name = (size or "").split("/", 1)[0]
            if size is None or not (size in shadow_ok or name in colors or VARIABLE.match(size)):
                found.append((lineno, "shadow", m.group(0)))
        for m in STYLE_LITERAL.finditer(line):
            value = m.group("value").strip("\"'`").strip()
            if value.startswith("var(--") or value in STYLE_KEYWORDS:
                continue
            found.append((lineno, "style-" + m.group("prop"), m.group(0)))
    return found


def scan(src: str) -> list:
    css_path = os.path.join(src, SOURCE)
    css = read(css_path) if os.path.exists(css_path) else ""
    radius_ok = RADIUS_OK_BASE | theme_names(css, "radius")
    shadow_ok = SHADOW_OK_BASE | theme_names(css, "shadow")
    colors = theme_names(css, "color")
    problems = []
    for base, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(EXTENSIONS):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, src)
            if rel == SOURCE:
                continue
            text = read(path)
            for lineno, rule, snippet in check_text(text, radius_ok, shadow_ok, colors):
                problems.append((rel, lineno, rule, snippet))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", default=SRC, help="要检查的 src 目录（默认 frontend/assistant/src）")
    args = parser.parse_args(argv)
    problems = scan(args.root)
    for rel, lineno, rule, snippet in problems:
        print(f"{rel}:{lineno}: [{rule}] {snippet}")
    if problems:
        print(f"FAIL: {len(problems)} 处写死的颜色 / 圆角 / 阴影；改成 globals.css 的变量（见 docs/development/ccs.md）")
        return 1
    print("PASS: 组件与 lib 只引用 globals.css 的颜色、圆角、阴影变量")
    return 0


if __name__ == "__main__":
    sys.exit(main())
