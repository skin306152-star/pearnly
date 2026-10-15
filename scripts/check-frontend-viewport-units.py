# -*- coding: utf-8 -*-
"""CC 前端（frontend/assistant）的视口单位闸门：dvh / svh / lvh（以及 dvw 等同族单位）不许裸用，必须有 vh 兜底。

## 为什么必须有它

dvh、svh、lvh 要 Chrome / 安卓 WebView 108+、Safari 15.4+ 才认。旧浏览器遇到不认的单位，**整条声明作废**：
`max-height: 88dvh` 变成没有限高、`height: min(680px, 92dvh)` 变成随内容撑开。2026-10-15 三星 A14（安卓 13，CC 装成
应用，Chrome 106）上「编辑定时任务」弹窗高 937、视口只有 786，标题、关闭、保存都在屏外，滑不动也关不掉；「添加公司」
弹出键盘后弹窗高 3101。构建、类型检查都不会拦，桌面浏览器上也看不出来。

## 判据（frontend/assistant/src 下的 .ts/.tsx/.js/.jsx/.mjs/.css，含 globals.css；注释不算）

· 组件与 lib：出现 `88dvh`、`100svh` 一类数值，或 Tailwind 的 `h-dvh`、`min-h-svh`、`max-h-[88dvh]`、`h-[min(680px,92dvh)]`
  一类写法，都算违规；只放行带 `supports-[…]` 变体的类（旧浏览器整条不生效，前面另有 vh 的类兜底）。
  该怎么写：引用 globals.css 的 `--cc-vh`（100vh，认 dvh 的换成 100dvh）、`--cc-svh`、`--cc-viewport-h`（可见区域高）。
· CSS：用了这些单位的声明，要么在 `@supports (…dvh…)` 一类的块里，要么同一块里紧挨着前面有同一属性、不用这些单位的
  一条（`height: 100vh; height: 100dvh;`，旧浏览器丢掉后一条、用前一条）。值里带 `var(` 的不算有兜底：带变量的声明
  解析时总是有效，到计算值时才作废，前一条接不住。

没有忽略清单：只按路径排除 node_modules 与构建产物。

用法：

    python3 scripts/check-frontend-viewport-units.py            # 有违规退出码 1
    python3 scripts/check-frontend-viewport-units.py --root DIR # 检查别的目录（测试用）
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "frontend", "assistant", "src")
EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".css")
SKIP_DIRS = {"node_modules", "dist", "build", ".vite"}

UNITS = r"[dsl]v(?:h|w|i|b|min|max)"
# 数值：88dvh、100svh、.5lvh
NUMBER = re.compile(r"(?<![\w.#-])(?:\d+(?:\.\d+)?|\.\d+)" + UNITS + r"(?![\w-])")
# Tailwind 关键字：h-dvh、min-h-svh、max-w-dvw、size-lvh（--cc-svh 这类变量名不算）
KEYWORD = re.compile(r"(?<![\w-])(?:min-|max-)?(?:h|w|size)-" + UNITS + r"(?![\w-])")
# 类名的边界（同 check-frontend-tokens.py）
TOKEN_EDGE = re.compile(r"[\s\"'`]")


def _strip_comments():
    spec = importlib.util.spec_from_file_location("cc_frontend_tokens", os.path.join(HERE, "check-frontend-tokens.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.strip_comments


strip_comments = _strip_comments()


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def uses_new_units(text: str) -> bool:
    return bool(NUMBER.search(text) or KEYWORD.search(text))


def class_token(line: str, start: int, end: int) -> str:
    """命中位置所在的整个类名（到空白或引号为止）。"""
    left = start
    while left > 0 and not TOKEN_EDGE.match(line[left - 1]):
        left -= 1
    right = end
    while right < len(line) and not TOKEN_EDGE.match(line[right]):
        right += 1
    return line[left:right]


def check_code(text: str) -> list:
    """组件与 lib：返回 [(行号, 片段)]。"""
    found = []
    for lineno, line in enumerate(strip_comments(text).splitlines(), 1):
        for pattern in (NUMBER, KEYWORD):
            for m in pattern.finditer(line):
                token = class_token(line, m.start(), m.end())
                if re.match(r"^(?:[\w-]+:)*supports-\[", token):
                    continue
                found.append((lineno, token or m.group(0)))
    return found


def check_css(text: str) -> list:
    """CSS：返回 [(行号, 声明)]。"""
    code = strip_comments(text)
    found = []
    stack = []  # 每层：{"header": 块头, "last": {属性: 值}}
    top = {"header": "", "last": {}}
    buf_start = 0
    i, n = 0, len(code)
    quote = None
    while i < n:
        c = code[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
        elif c == "{":
            stack.append(top)
            top = {"header": code[buf_start:i].strip(), "last": {}}
            buf_start = i + 1
        elif c in ";}":
            decl = code[buf_start:i]
            if ":" in decl and not decl.strip().startswith("@"):
                prop, value = (part.strip() for part in decl.split(":", 1))
                prop = prop.lower()
                if uses_new_units(value):
                    guarded = any(
                        level["header"].startswith("@supports") and uses_new_units(level["header"])
                        for level in [*stack, top]
                    )
                    previous = top["last"].get(prop)
                    fallback = previous is not None and not uses_new_units(previous) and "var(" not in value
                    if not guarded and not fallback:
                        found.append((code.count("\n", 0, buf_start + len(decl) - len(decl.lstrip())) + 1, f"{prop}: {value}"))
                top["last"][prop] = value
            if c == "}":
                top = stack.pop() if stack else {"header": "", "last": {}}
            buf_start = i + 1
        i += 1
    return found


def scan(src: str) -> list:
    problems = []
    for base, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(EXTENSIONS):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, src)
            text = read(path)
            for lineno, snippet in (check_css(text) if name.endswith(".css") else check_code(text)):
                problems.append((rel, lineno, snippet))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", default=SRC, help="要检查的 src 目录（默认 frontend/assistant/src）")
    args = parser.parse_args(argv)
    problems = scan(args.root)
    for rel, lineno, snippet in problems:
        print(f"{rel}:{lineno}: [viewport-unit] {snippet}")
    if problems:
        print(f"FAIL: {len(problems)} 处裸用 dvh / svh / lvh；改用 globals.css 的 --cc-vh / --cc-svh / --cc-viewport-h，"
              "或先写一条 vh 兜底（Chrome / 安卓 WebView 108、Safari 15.4 以前不认这些单位，整条声明作废）")
        return 1
    print("PASS: 没有裸用 dvh / svh / lvh（都有 vh 兜底）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
