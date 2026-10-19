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

## 新判据 R4–R15（docs/work/2026-10-17-cc-frontend-standards.md 第 1.2、3 节）

尺寸、间距、字号与行高、层级、动效、透明度等数值类、任意值与 `!`、断点与输入设备变体、JSX 数值属性与内联 style、
globals.css 自身、`--cc-*` 引用必须已定义、字重——也只能走 globals.css 的标准定义。上面三条（R1–R3）的 scan() 不变；
新判据走 scan_strict()，**全仓失败**（B1–B7 分批迁移时用过的「收紧范围」清单在 B8 迁完后删掉了）。允许的名字同样运行时从
globals.css 读（@theme 的 --text-* / --ease-* / --animate-* / --font-weight-* 等与所有 --cc-* 定义），脚本里不维护数值清单。
不支持行内禁用注释，也不按目录或文件跳过。

用法：

    python3 scripts/check-frontend-tokens.py            # R1–R15 任何一处违例退出码 1
    python3 scripts/check-frontend-tokens.py --report   # 另列 R4–R15 各条命中数、示例、按文件 × 规则
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


# ---------------------------------------------------------------------------------------------------------------
# 新判据 R4–R15（docs/work/2026-10-17-cc-frontend-standards.md 第 3 节）：尺寸、间距、字号、层级、动效……只能走 globals.css。
# 现有 scan() 只判 R1–R3（颜色、圆角、阴影）；新判据走 scan_strict()。两者都全仓失败（B8 起，分批迁移用的收紧清单已删）。
# ---------------------------------------------------------------------------------------------------------------

RULES = {
    "R4": "尺寸 size/w/h/min-*/max-*",
    "R5": "间距 p/m/gap/space/inset/top…/translate/scroll-m",
    "R6": "字号与行高 text-*/leading-*",
    "R7": "层级 z-* / zIndex",
    "R8": "动效 duration/delay/ease/animate/transition",
    "R9": "其他数值类 opacity/stroke/blur/border-n/offset/scale/rotate/ring/outline/divide/line-clamp",
    "R10": "任意值 […] 与 ! 强制",
    "R11": "断点与输入设备变体",
    "R12": "JSX 数值属性与内联 style",
    "R13": "globals.css 自身",
    "R14": "--cc-* 必须已定义；组件不直接用 --code-* / --ccs-*",
    "R15": "字重",
}

# 尺寸、间距的关键字（布局用，不是设计值）
SIZE_KEYWORDS = {"0", "full", "auto", "fit", "min", "max", "none"}
SPACE_KEYWORDS = {"0", "auto", "full", "none", "reverse"}
TEXT_KEYWORDS = {"left", "center", "right", "justify", "start", "end", "wrap", "nowrap", "balance", "pretty",
                 "ellipsis", "clip", "transparent", "current", "inherit"}
TRANSITION_OK = {"", "colors", "opacity", "transform", "shadow", "none", "discrete", "normal"}
WEIGHT_WORDS = {"thin", "extralight", "light", "normal", "medium", "semibold", "bold", "extrabold", "black"}
BREAKPOINT_OK = {"md", "max-md"}
BREAKPOINT_BAD = re.compile(r"^(?:(?:max-|min-)?(?:sm|lg|xl|2xl|\d?xs)|(?:max|min)-\[.*\]|\[@media.*\])$")
POINTER_BAD = re.compile(r"^(?:any-pointer-[\w-]+|pointer-(?:fine|coarse|none)|hover-none|any-hover-[\w-]+)$")
TW_ANIMATE = re.compile(r"^(?:animate-(?:in|out)|(?:fade|zoom|spin|blur)-(?:in|out)(?:-.*)?|slide-(?:in-from|out-to)-.*)$")

SIZE_UTIL = re.compile(r"^(?:size|w|h|min-w|min-h|max-w|max-h|basis)-(?P<v>.+)$")
SPACE_UTIL = re.compile(
    r"^(?:p[xytrblse]?|m[xytrblse]?|gap(?:-[xy])?|space-[xy]|inset(?:-[xy])?|top|right|bottom|left|start|end|"
    r"translate(?:-[xyz])?|scroll-[mp][xytrblse]?)-(?P<v>.+)$")
VAR_FORM = re.compile(r"^\((?:[a-z-]+:)?--[\w-]+\)$")
FRACTION = re.compile(r"^\d+/\d+$")
NUMERIC = re.compile(r"^\d+(?:\.\d+)?$")

# 任意值里的数：不带单位的整数、fr、0、100% 放行；其余（带单位、小数）拦
NUMBER_IN_VALUE = re.compile(r"(?<![\w#.\-])(-?)(\d*\.\d+|\d+)([a-zA-Z%]*)")
CSS_NAME = re.compile(r"--[\w-]+")
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
TYPE_HINT = re.compile(r"^(?:length|color|number|percentage|integer|family-name|position|size|url|image|ratio|"
                       r"line-width|absolute-size|relative-size|bg-size|angle|vector|any):")

TIME_LITERAL = re.compile(r"(?<![\w.\-])(\d*\.?\d+)(ms|s)(?![\w-])")
EASE_WORD = re.compile(r"(?<![\w-])(ease-in-out|ease-in|ease-out|ease|linear|step-start|step-end)(?![\w(-])"
                       r"|(?<![\w-])(cubic-bezier|steps|linear)\(")
MEDIA_WIDTH = re.compile(r"\([^()]*\b(?:min-|max-)?(?:device-)?width\b[^()]*\)")
MEDIA_OK = {"(width < 768px)", "(width >= 768px)"}
PROVENANCE = re.compile(r"实测|系统值|现有值|CC 自定|运行时写入|照搬宣传页")

CSS_SIZE_PROPS = re.compile(r"^(?:(?:min-|max-)?(?:width|height|inline-size|block-size)|flex-basis)$")
CSS_SPACE_PROPS = re.compile(r"^(?:padding|margin|scroll-margin|scroll-padding|inset)(?:-[\w-]+)?$|"
                             r"^(?:gap|row-gap|column-gap|top|right|bottom|left|translate)$")
CSS_UNITLESS_PROPS = {"line-height", "font-weight", "z-index"}
CSS_MOTION_PROPS = re.compile(r"^(?:transition|animation)(?:-(?:duration|delay|timing-function))?$")
# 动画轮数（animation 简写里不带单位的数、animation-iteration-count）同时长一样只能写 var()：装饰动画播几轮登记在 globals.css
# （--cc-mascot-*-runs，docs/work/2026-10-18-cc-idle-animations.md）；infinite 是关键字不在这里判（「装饰动画不许无限循环」由
# tests/test_cc_idle_motion.py 按白名单查）
CSS_RUNS_PROPS = re.compile(r"^animation(?:-iteration-count)?$")

STYLE_LENGTH_PROPS = (
    "width|height|minWidth|minHeight|maxWidth|maxHeight|inlineSize|blockSize|flexBasis|"
    "padding\\w*|margin\\w*|gap|rowGap|columnGap|inset\\w*|top|right|bottom|left|translate|"
    "fontSize|lineHeight|fontWeight|letterSpacing|opacity|zIndex|transition\\w*|animation\\w*|transform")
STYLE_PAIR = re.compile(r"(?<![\w$.])[\"']?(?P<prop>" + STYLE_LENGTH_PROPS + r"|--[\w-]+)[\"']?\s*:\s*(?P<value>"
                        r"`(?:[^`\\]|\\.)*`|\"[^\"]*\"|'[^']*'|-?\d+(?:\.\d+)?(?![\w.]))")
STYLE_ASSIGN = re.compile(r"\.style\.(?P<prop>\w+)\s*=\s*(?P<value>`(?:[^`\\]|\\.)*`|\"[^\"]*\"|'[^']*'|-?\d+(?:\.\d+)?)")
STYLE_SETPROP = re.compile(r"\.setProperty\(\s*[\"'`](?P<prop>[\w-]+)[\"'`]\s*,\s*(?P<value>`(?:[^`\\]|\\.)*`|\"[^\"]*\"|'[^']*'|-?\d+(?:\.\d+)?)")
# 内联 style 值里的字面长度（变量名先去掉，所以负号也算：marginTop: "-4px"）
STYLE_UNIT = re.compile(r"(?<![\w.$])-?(\d*\.?\d+)(px|rem|em|%|vh|vw|dvh|svh|lvh|ch|ms|s)(?![\w-])")
# 整串是一个值为 0 的长度（"0px"、"0ms"）不算写死，与 style 里 0 放行同一口径
ZERO_LENGTH = re.compile(r"^\s*-?(?:0*\.)?0+(?:px|rem|em|vh|vw|ms|s)\s*$")
# transform 里的平移：translate / translateX / translateY / translateZ / translate3d 的参数按间距判（scale、rotate 不判）
TRANSLATE_ARGS = re.compile(r"\btranslate(?:X|Y|Z|3d)?\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
LENGTH_STRING = re.compile(r"^\s*(?:-?\d*\.?\d+(?:px|rem|em|vh|vw|ms|s)|(?:calc|min|max|clamp)\(.*\d(?:px|rem|em|vh|vw|%).*\))\s*$")
JSX_OFFSET = re.compile(r"\b(?P<prop>sideOffset|alignOffset|collisionPadding|delayDuration)\b\s*(?:=\s*\{?\s*|:\s*)"
                        r"(?P<value>-?\d+(?:\.\d+)?)\}?")
JSX_DIMENSION = re.compile(r"\s(?P<prop>size|width|height|strokeWidth)=(?:\{\s*(?P<a>-?\d+(?:\.\d+)?)\s*\}|"
                           r"\"(?P<b>-?\d+(?:\.\d+)?)(?:px)?\")")
JSX_ZINDEX = re.compile(r"\bzIndex\b\s*(?:=\s*\{\s*|:\s*)(?P<value>-?\d+)")
CC_REF = re.compile(r"(?<![\w-])--(?P<ns>cc|code|ccs)-(?P<name>[\w-]*)(?P<tpl>\$\{)?")


# 任意属性里不带单位的那几种：写数就是写死的层级 / 行高 / 字重
ARBITRARY_UNITLESS = {"z-index": "R7", "line-height": "R6", "font-weight": "R15"}
# 模板里只拼一个数字字面量再接单位（`${4}px`、`${2 * 8}px`）：与直接写 "4px" 是一回事。拼的是常量名（`${GAP}px`）机械上判不出，
# 审查时按同一条规则看（2.6；登记为已知限制）
TEMPLATE_NUMBER = re.compile(r"\$\{(?P<expr>[\s\d.+\-*/()]+)\}(?P<unit>px|rem|em|vh|vw|ms|s|%)(?![\w-])")

# 这些族的任意值不按 R10 判：颜色 / 圆角 / 阴影归 R1–R3，content / url / 光标 / will-change 不是数值
UNCHECKED_ARBITRARY = ("content-", "bg-[url", "bg-[image", "cursor-", "will-change-", "rounded", "shadow", "drop-shadow",
                       "inset-shadow", "font-[")


def theme_block(css: str) -> str:
    return css.split("@theme inline {", 1)[1].split("\n}", 1)[0] if "@theme inline {" in css else ""


class Standard:
    """从 globals.css 读出的允许清单：@theme 接过的名字 + 所有 --cc-* 定义。脚本里不维护数值清单。"""

    def __init__(self, css: str):
        self.css = css
        self.text = {n for n in theme_names(css, "text") if "--" not in n}
        self.colors = theme_names(css, "color")
        self.ease = theme_names(css, "ease")
        self.animate = theme_names(css, "animate")
        self.weights = theme_names(css, "font-weight")
        self.fonts = {n for n in theme_names(css, "font") if not n.startswith("weight-")}
        self.defined = set(re.findall(r"(?<![\w-])(--cc-[\w-]+)\s*:", strip_css_comments(css)))

    def cc_defined(self, name: str, prefix: bool = False) -> bool:
        if prefix:
            return any(d.startswith(name) for d in self.defined)
        return name in self.defined


def strip_css_comments(text: str) -> str:
    """CSS 只有 /* … */ 注释；保留换行，字符串不动。"""
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
            if c == quote or c == "\n":
                quote = None
            i += 1
            continue
        if c in "\"'":
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
        out.append(c)
        i += 1
    return "".join(out)


REGEX_BEFORE = set("(,=:[!&|?{;+-*%~^")


def literals(code: str) -> list:
    """取出 JS/TS 源码里的字符串与模板字符串（已去注释）：[(起点偏移, 内容)]。
    模板里的 ${…} 换成 \\x00（内容另算一段）；单双引号字符串遇到换行就作废（JSX 文本里的撇号不会吞掉后面的代码）；
    正则字面量跳过。"""
    out = []

    def scan(i: int, stop_on_brace: bool) -> int:
        n = len(code)
        depth = 0
        prev = ""
        while i < n:
            c = code[i]
            if c in "\"'":
                j = i + 1
                while j < n and code[j] != c and code[j] != "\n":
                    j += 2 if code[j] == "\\" else 1
                if j < n and code[j] == c:
                    out.append((i + 1, code[i + 1:j]))
                    i = j + 1
                    prev = c
                    continue
                i += 1
                continue
            if c == "`":
                i = template(i + 1)
                prev = "`"
                continue
            if c == "/" and (prev in REGEX_BEFORE or prev == "") and code[i + 1:i + 2] not in ("/", "*", ">"):
                j = i + 1
                klass = False
                while j < n and code[j] != "\n":
                    if code[j] == "\\":
                        j += 2
                        continue
                    if code[j] == "[":
                        klass = True
                    elif code[j] == "]":
                        klass = False
                    elif code[j] == "/" and not klass:
                        break
                    j += 1
                if j < n and code[j] == "/":
                    i = j + 1
                    prev = "/"
                    continue
            if stop_on_brace:
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        return i + 1
                    depth -= 1
            if not c.isspace():
                prev = c if not (c.isalnum() or c in "_$") else "a"
                if prev == "a":
                    # return / typeof / case 后面可以接正则
                    k = i
                    while k < n and (code[k].isalnum() or code[k] in "_$"):
                        k += 1
                    word = code[i:k]
                    if word in ("return", "typeof", "case", "in", "of", "void", "throw", "new", "delete"):
                        prev = "("
                    i = k
                    continue
            i += 1
        return i

    def template(i: int) -> int:
        start = i
        buf = []
        n = len(code)
        while i < n:
            c = code[i]
            if c == "\\":
                buf.append(code[i:i + 2])
                i += 2
                continue
            if c == "`":
                out.append((start, "".join(buf)))
                return i + 1
            if code.startswith("${", i):
                j = scan(i + 2, True)
                # 同长度占位（换行保留），偏移与行号和原文对齐
                buf.append(re.sub(r"[^\n]", "\x00", code[i:j]))
                i = j
                continue
            buf.append(c)
            i += 1
        out.append((start, "".join(buf)))
        return i

    scan(0, False)
    return out


def split_top(token: str, sep: str = ":") -> list:
    """按顶层的 sep 切（方括号、圆括号里的不切）。"""
    parts, depth, cur = [], 0, []
    for ch in token:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return parts


def value_problem(content: str) -> str:
    """任意值方括号里的内容：只能由 var(--…)、calc/min/max/clamp/minmax、env(safe-area-inset-*)、0、100%、
    不带单位的整数、fr 组成。返回第一处不合规的片段，合规返回空串。"""
    text = TYPE_HINT.sub("", content).replace("_", " ")
    text = QUOTED.sub(" ", text)
    text = CSS_NAME.sub(" ", text)
    for m in NUMBER_IN_VALUE.finditer(text):
        number, unit = m.group(2), m.group(3)
        if unit == "" and "." not in number:
            continue
        if unit == "fr":
            continue
        if float(number) == 0:
            continue
        if unit == "%" and number == "100":
            continue
        return m.group(0)
    return ""


def has_var(content: str) -> bool:
    return "var(--" in content or content.startswith("--")


def foreign_var(value: str) -> str:
    """变量写法 / 任意值里引用的非 --cc-* 名字（1.2：变量写法只能引用 --cc-*）；没有返回空串。
    `(--x)` / `(length:--x)` 取括号里的名字，`[…var(--x)…]` 取所有 var() 里的名字。"""
    m = VAR_FORM.match(value)
    if m:
        name = TYPE_HINT.sub("", value[1:-1])
        return "" if name.startswith("--cc-") else name
    inner = arbitrary(value)
    if inner is None:
        return ""
    for name in re.findall(r"var\(\s*(--[\w-]+)", inner):
        if not name.startswith("--cc-"):
            return name
    return ""


def check_class(token: str, std: Standard, alone: bool = False) -> list:
    """一个 class 片段 → [(规则, 说明)]。不认识的词（普通文字、布局类）不管。"""
    found = []
    parts = split_top(token)
    variants, util = parts[:-1], parts[-1]
    for v in variants:
        # 组合变体先剥掉前缀再判：not-sm: 生成 @media not (width >= 40rem)、not-pointer-coarse: 生成 @media not (pointer: coarse)、
        # not-@md: 生成 @container not (…)，都是漏网的断点 / 输入设备 / 容器查询；group- / peer- / in- / has- 同样剥（group-@md: 之类）
        base = v
        while True:
            stripped = re.sub(r"^(?:not|group|peer|in|has)-(?=[@\w\[])", "", base)
            if stripped == base:
                break
            base = stripped
        if base in BREAKPOINT_OK:
            continue
        # 容器查询（@md: / @max-md: / @[480px]: / @lg/main:）一律拦：@md 是 28rem 的容器档，不是 768 那条断点
        if base.startswith("@") or BREAKPOINT_BAD.match(base) or POINTER_BAD.match(base):
            found.append(("R11", v + ":"))
    if not util:
        return found
    important = False
    if util.startswith("!"):
        important, util = True, util[1:]
    if util.endswith("!"):
        important, util = True, util[:-1]
    if util.startswith("-"):
        util = util[1:]
    if not util:
        return found
    # 任意属性 [--cc-x:var(--cc-y)] / [mask-image:…]
    if util.startswith("[") and util.endswith("]"):
        inner = util[1:-1]
        if ":" in inner and not inner.startswith("&") and not inner.startswith("@"):
            prop, value = inner.split(":", 1)
            # 引用的不是 --cc-*（[width:var(--sidebar-width)]、[transition-duration:var(--foo)]、[--cc-x:var(--foo)]）记 R10：
            # 变量写法只能引用 --cc-*（1.2 补充第一条；B0 补修复核建议 ①）
            for name in re.findall(r"var\(\s*(--[\w-]+)", value):
                if not name.startswith("--cc-"):
                    found.append(("R10", f"[{prop}:…var({name})…]"))
                    break
            if CSS_MOTION_PROPS.match(prop) or CSS_RUNS_PROPS.match(prop):
                # [transition:…] / [animation:…] / [*-timing-function:…] / [animation-iteration-count:…]：时间、缓动与轮数按 globals.css 的 R13 同一判法
                bad = css_value_problem(prop, value.replace("_", " "))
                if bad:
                    found.append(("R8", f"[{prop}:…{bad}…]"))
                return found
            bad = value_problem(value)
            if bad and re.match(r"^-?[\w-]+$", prop):
                found.append(("R10", f"[{prop}:…{bad}…]"))
            elif prop in ARBITRARY_UNITLESS and not has_var(value) and value not in ("0", "auto", "inherit", "normal", "none"):
                # 不带单位的数在任意值里本来放行（*2、*-1），但 [z-index:50] / [line-height:2] / [font-weight:600] 就是写死的层级、
                # 行高、字重：同 z-[…] / leading-[…] 方括号里必须有变量，按所在族记
                found.append((ARBITRARY_UNITLESS[prop], f"[{prop}:{value}]"))
        return found
    if alone and util == "blur":
        return found  # 单独一个 "blur" 是事件名（addEventListener("blur")），不是类
    managed = classify(util, std, found)
    if important and managed:
        found.append(("R10", "! 强制"))
    if not managed and not util.startswith(UNCHECKED_ARBITRARY):
        # 不受 R4–R9 管的工具类（grid-cols-[…]、aspect-[…] …）的任意值也按 R10 判
        m = re.match(r"^[\w-]+?-\[(?P<v>.+)\]$", util)
        if m and value_problem(m.group("v")):
            found.append(("R10", util))
    return found


def arbitrary(value: str):
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1]
    return None


def classify(util: str, std: Standard, found: list) -> bool:
    """按工具类族判；返回它是否属于受管的族（决定 ! 要不要拦）。"""
    def var_or_arbitrary(value: str, rule: str, need_var: bool = False) -> bool:
        """变量写法放行；方括号按 R10 判（need_var：方括号里必须有变量，否则记 rule）。
        变量写法与方括号里的 var() 只能引用 --cc-*，别的名字（--sidebar-width、--radix-*）记 rule。"""
        foreign = foreign_var(value)
        if foreign:
            found.append((rule, util))
        if VAR_FORM.match(value):
            return True
        inner = arbitrary(value)
        if inner is not None:
            bad = value_problem(inner)
            if bad:
                found.append(("R10", util))
            elif need_var and not has_var(inner) and not foreign:
                found.append((rule, util))
            return True
        return False

    m = SIZE_UTIL.match(util)
    if m:
        v = m.group("v")
        if v in SIZE_KEYWORDS or FRACTION.match(v) or var_or_arbitrary(v, "R4"):
            return True
        found.append(("R4", util))
        return True
    if util.startswith(("inset-ring", "inset-shadow")):
        return False
    m = SPACE_UTIL.match(util)
    if m:
        v = m.group("v")
        if v in SPACE_KEYWORDS or FRACTION.match(v) or var_or_arbitrary(v, "R5"):
            return True
        found.append(("R5", util))
        return True
    if util.startswith("text-"):
        v, _, modifier = util[5:].partition("/")
        if re.match(r"^(?:" + HUES + r")-\d{2,3}$|^(?:white|black)$", v):
            return False  # 调色板归 R1
        if v in std.text:
            if modifier:
                found.append(("R6", util))
            return True
        if v in std.colors or v in TEXT_KEYWORDS:
            return False
        if foreign_var(v):
            found.append(("R6", util))
        if VAR_FORM.match(v):
            return True
        inner = arbitrary(v)
        if inner is not None:
            if value_problem(inner):
                found.append(("R10", util))
            return True
        found.append(("R6", util))
        return True
    if util.startswith("leading-"):
        v = util[8:]
        if v == "none" or var_or_arbitrary(v, "R6", need_var=True):
            return True
        found.append(("R6", util))
        return True
    if util.startswith("z-"):
        v = util[2:]
        if v in ("0", "auto") or var_or_arbitrary(v, "R7", need_var=True):
            return True
        found.append(("R7", util))
        return True
    if TW_ANIMATE.match(util):
        found.append(("R8", util))
        return True
    if util == "transition" or util.startswith("transition-"):
        v = util[11:] if util != "transition" else ""
        if v in TRANSITION_OK:
            return True
        inner = arbitrary(v)
        if inner is not None and re.match(r"^[a-z-]+(?:,[a-z-]+)*$", inner):
            return True
        found.append(("R8", util))
        return True
    for prefix in ("duration-", "delay-"):
        if util.startswith(prefix):
            v = util[len(prefix):]
            if not var_or_arbitrary(v, "R8", need_var=True):
                found.append(("R8", util))
            return True
    if util.startswith("ease-"):
        v = util[5:]
        if v in std.ease or var_or_arbitrary(v, "R8", need_var=True):
            return True
        found.append(("R8", util))
        return True
    if util.startswith("animate-"):
        v = util[8:]
        inner = arbitrary(v)
        if inner is not None and EASE_WORD.search(QUOTED.sub(" ", TYPE_HINT.sub("", inner).replace("_", " "))):
            found.append(("R8", util))  # animate-[x_var(--cc-dur)_ease-out]：缓动只能写 var()
            return True
        if v == "none" or v in std.animate or var_or_arbitrary(v, "R8", need_var=True):
            return True
        found.append(("R8", util))
        return True
    if util.startswith("font-"):
        v = util[5:]
        if v in std.fonts:
            return False
        if foreign_var(v):
            found.append(("R15", util))
            return True
        if v in std.weights or VAR_FORM.match(v):
            return True
        inner = arbitrary(v)
        if inner is not None:
            if re.match(r"^\d", inner):
                found.append(("R15", util))
            elif value_problem(inner):
                found.append(("R10", util))
            return True
        if v in WEIGHT_WORDS:
            found.append(("R15", util))
            return True
        return False
    return r9(util, std, found, var_or_arbitrary)


R9_FAMILIES = (
    (re.compile(r"^opacity-(?P<v>.+)$"), {"0", "100"}),
    (re.compile(r"^stroke-(?P<v>\d.*|\[.+\]|\(.+\))$"), {"0"}),
    (re.compile(r"^(?:backdrop-)?blur(?:-(?P<v>.+))?$"), {"none"}),
    (re.compile(r"^border(?:-[xytrblse]|-bs|-be)?-(?P<v>\d+(?:\.\d+)?|\(length:.+\))$"), {"0"}),
    (re.compile(r"^underline-offset-(?P<v>.+)$"), {"auto"}),
    (re.compile(r"^decoration-(?P<v>\d+|\[.+\])$"), set()),
    # 带轴的写在前面：先配上不带轴的 scale / rotate 会把 x-(--cc-mirror) 当成值、误判 scale-x-(--x)（B3 记过，B8 修）
    (re.compile(r"^(?:scale-[xyz]|scale|rotate-[xyz]|rotate|skew-[xy]|skew)-(?P<v>.+)$"), {"none"}),
    (re.compile(r"^ring(?:-offset)?-(?P<v>\d+|\[.+\])$"), {"0"}),
    (re.compile(r"^outline(?:-offset)?-(?P<v>\d+|\[.+\])$"), {"0"}),
    (re.compile(r"^divide-[xy]-(?P<v>\d+|\[.+\])$"), {"0"}),
    (re.compile(r"^line-clamp-(?P<v>.+)$"), {"none"}),
)


def r9(util: str, std: Standard, found: list, var_or_arbitrary) -> bool:
    for pattern, ok in R9_FAMILIES:
        m = pattern.match(util)
        if not m:
            continue
        v = m.group("v")
        if v is None:  # 裸 blur / backdrop-blur
            found.append(("R9", util))
            return True
        if v in ok:
            return True
        if var_or_arbitrary(v, "R9", need_var=True):
            return True
        found.append(("R9", util))
        return True
    return False


def class_tokens(text: str):
    """字符串内容按空白切；带模板占位的片段跳过（运行时拼出来的）。"""
    for m in re.finditer(r"\S+", text):
        token = m.group(0)
        if "\x00" in token:
            continue
        yield m.start(), token


def line_of(starts: list, offset: int) -> int:
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def balanced(code: str, i: int) -> int:
    """code[i] 是 {：返回匹配的 } 之后的位置（字符串里的括号已在注释剥离后保留，粗略按引号跳过）。"""
    depth, n = 0, len(code)
    quote = None
    while i < n:
        c = code[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def style_value_problem(prop: str, value: str) -> str:
    """内联 style 的一个值：字面长度、透明度、层级、时长拦；var(--cc-…) 与运行时插值放行。
    var() 只能引用 --cc-*（1.2），var(--sidebar-width) 这类别的名字拦；transform 只判 translate*() 里的长度（50% 放行，同 R13）。"""
    if value[:1] in "\"'`":
        body = value[1:-1]
        for name in re.findall(r"var\(\s*(--[\w-]+)", body):
            if not name.startswith("--cc-"):
                return name
        if prop == "transform":
            for args in TRANSLATE_ARGS.findall(re.sub(r"\$\{[^}]*\}", " ", body)):
                bad = number_problem(CSS_NAME.sub(" ", args), unitless=False)
                if bad:
                    return bad
            return ""
        if body.startswith("var(--") and value_problem(body.replace(" ", "_")) == "":
            return ""
        if value[0] == "`":
            # ${…} 里写了数再接单位（如 ${level * 1.5}em）算写死
            for m in re.finditer(r"\$\{([^}]*)\}(px|rem|em|%|vh|vw|ms|s)\b", body):
                if re.search(r"(?<![\w.$])\d*\.?\d+(?![\w])", m.group(1)) and not re.fullmatch(r"\s*0\s*", m.group(1)):
                    return m.group(0)
            body = re.sub(r"\$\{[^}]*\}", " ", body)
        body = CSS_NAME.sub(" ", body)
        m = STYLE_UNIT.search(body)
        if m and not (float(m.group(1)) == 0 or m.group(0) == "100%"):
            return m.group(0)
        if prop in ("opacity", "zIndex", "fontWeight", "lineHeight") and re.fullmatch(r"\s*-?\d*\.?\d+\s*", body) \
                and float(body) not in (0, 1):
            return body
        if prop in ("animation", "animationIterationCount"):
            # 轮数同 globals.css：animation 简写里不带单位的数、animationIterationCount 只能走 var(--cc-…)
            for m in NUMBER_IN_VALUE.finditer(body):
                if m.group(3) == "" and float(m.group(2)) != 0:
                    return m.group(0)
        return ""
    number = float(value)
    if number == 0 or (prop == "opacity" and number == 1):
        return ""
    return value


def check_source_strict(text: str, std: Standard) -> list:
    """组件与 lib 源码 → [(行号, 规则, 片段)]。

    同一处只记一次：style={{…}} 里的值归 STYLE_PAIR，.style.x = / setProperty / svg 宽高的值归各自的模式，
    这些值的字面字符串不再另按「整串是长度」记 R12；style 块里的 zIndex 只按 STYLE_PAIR 记一条 R7。"""
    found = []
    code = strip_comments(text)
    starts = [0] + [m.end() for m in re.finditer(r"\n", code)]
    covered = []  # 已由具体模式记过的值的绝对区间
    blocks = []
    for m in re.finditer(r"\bstyle=\{", code):
        end = balanced(code, m.end() - 1)
        blocks.append((m.end() - 1, end))
        block = code[m.end() - 1:end]
        for pair in STYLE_PAIR.finditer(block):
            prop, value = pair.group("prop"), pair.group("value")
            covered.append((m.end() - 1 + pair.start(), m.end() - 1 + pair.end()))
            bad = style_value_problem(prop, value)
            if bad:
                rule = "R7" if prop == "zIndex" else "R12"
                found.append((line_of(starts, m.end() - 1 + pair.start()), rule, pair.group(0).strip()))

    def in_block(pos: int) -> bool:
        return any(a <= pos < b for a, b in blocks)

    for lineno, line in enumerate(code.splitlines(), 1):
        base = starts[lineno - 1]
        for m in JSX_OFFSET.finditer(line):
            if float(m.group("value")) != 0:
                found.append((lineno, "R12", m.group(0)))
        for m in JSX_DIMENSION.finditer(line):
            covered.append((base + m.start(), base + m.end()))
            found.append((lineno, "R12", m.group(0).strip()))
        for m in JSX_ZINDEX.finditer(line):
            if in_block(base + m.start()):
                continue  # style 块里的 zIndex 由 STYLE_PAIR 记
            if int(m.group("value")) != 0:
                found.append((lineno, "R7", m.group(0)))
        for pattern in (STYLE_ASSIGN, STYLE_SETPROP):
            for m in pattern.finditer(line):
                covered.append((base + m.start(), base + m.end()))
                bad = style_value_problem(m.group("prop"), m.group("value"))
                if bad:
                    found.append((lineno, "R12", m.group(0)))
    # 模板里拼数字字面量成长度（`${4}px`）：style 块、.style.x =、setProperty 里的已由各自模式记过，不重复
    for m in TEMPLATE_NUMBER.finditer(code):
        if any(a <= m.start() < b for a, b in covered):
            continue
        if re.search(r"[1-9]", m.group("expr")):
            found.append((line_of(starts, m.start()), "R12", m.group(0)))
    for start, content in literals(code):
        tokens = list(class_tokens(content))
        for offset, token in tokens:
            for rule, snippet in check_class(token, std, alone=len(tokens) == 1):
                found.append((line_of(starts, start + offset), rule, token if snippet == "! 强制" else snippet))
        if LENGTH_STRING.match(content) and "\x00" not in content and not ZERO_LENGTH.match(content) \
                and not any(a <= start < b for a, b in covered):
            found.append((line_of(starts, start), "R12", f'"{content}"'))
    found.extend(check_refs(code, std, starts))
    found.sort(key=lambda h: h[0])
    return found


def check_refs(code: str, std: Standard, starts: list, components: bool = True) -> list:
    found = []
    for m in CC_REF.finditer(code):
        ns, name, tpl = m.group("ns"), m.group("name"), m.group("tpl")
        if ns != "cc":
            if components:
                found.append((line_of(starts, m.start()), "R14", f"--{ns}-{name}（组件里不直接用）"))
            continue
        full = f"--cc-{name}"
        if tpl or not name or name.endswith("-"):
            if not std.cc_defined(full, prefix=True):
                found.append((line_of(starts, m.start()), "R14", full + "${…}"))
            continue
        if not std.cc_defined(full):
            found.append((line_of(starts, m.start()), "R14", full))
    return found


def css_statements(code: str):
    """把（去注释的）CSS 切成语句：[(起点偏移, 种类, 文本, 外层块种类栈)]。种类：decl / apply / block。"""
    out = []
    stack = []
    i, n = 0, len(code)
    start = 0
    quote = None
    paren = 0
    while i < n:
        c = code[i]
        if quote:
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
        elif c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif paren == 0 and c in "{;}":
            text = code[start:i]
            lead = len(text) - len(text.lstrip())
            stmt = text.strip()
            pos = start + lead
            if c == "{":
                kind = "keyframes" if re.match(r"@(?:-webkit-)?keyframes\b", stmt) else \
                       "font-face" if stmt.startswith("@font-face") else \
                       "media" if stmt.startswith("@media") else "block"
                out.append((pos, "block", stmt, list(stack)))
                stack.append(kind)
            elif stmt:
                kind = "apply" if stmt.startswith("@apply") else "decl"
                out.append((pos, kind, stmt, list(stack)))
            if c == "}" and stack:
                stack.pop()
            start = i + 1
        i += 1
    return out


def css_value_problem(prop: str, value: str) -> str:
    text = QUOTED.sub(" ", value)
    text = CSS_NAME.sub(" ", text)
    text = re.sub(r"!important", " ", text)
    if CSS_MOTION_PROPS.match(prop) or CSS_RUNS_PROPS.match(prop):
        for m in TIME_LITERAL.finditer(text):
            if float(m.group(1)) != 0:
                return m.group(0)
        m = EASE_WORD.search(text)
        if m:
            return m.group(0)
        if CSS_RUNS_PROPS.match(prop):
            for m in NUMBER_IN_VALUE.finditer(text):
                if m.group(3) == "" and float(m.group(2)) != 0:
                    return m.group(0)
        return ""
    if prop == "transform":
        # 只判平移的长度（按间距）；scale()、rotate() 等不在间距范畴
        for args in TRANSLATE_ARGS.findall(text):
            bad = number_problem(args, unitless=False)
            if bad:
                return bad
        return ""
    if prop == "flex":
        # flex 简写：伸缩系数不带单位（放行）；带单位的是 flex-basis，按尺寸判
        for m in NUMBER_IN_VALUE.finditer(text):
            number, unit = m.group(2), m.group(3)
            if unit and float(number) != 0 and not (unit == "%" and number in ("100", "50")):
                return m.group(0)
        return ""
    if prop == "font":
        # font 简写：字重（纯数）、字号、/行高 里的数都只能走 var()
        return number_problem(text, unitless=True)
    unitless = prop in CSS_UNITLESS_PROPS
    if not (unitless or CSS_SIZE_PROPS.match(prop) or CSS_SPACE_PROPS.match(prop) or prop == "font-size"):
        return ""
    return number_problem(text, unitless)


def number_problem(text: str, unitless: bool) -> str:
    """受管属性值里的数：0、100%、50% 放行；calc 里不带单位的整数倍数放行（unitless 属性除外）；其余拦。"""
    for m in NUMBER_IN_VALUE.finditer(text):
        number, unit = m.group(2), m.group(3)
        if float(number) == 0:
            continue
        if unit == "%" and number in ("100", "50"):
            continue
        if unit == "" and "." not in number and not unitless:
            continue  # calc 里的倍数
        return m.group(0)
    return ""


def check_globals_strict(css: str, std: Standard) -> list:
    """globals.css 自身（R13 + @apply 按组件规则 + R14 引用存在）→ [(行号, 规则, 片段)]。"""
    found = []
    raw_lines = css.splitlines()
    code = strip_css_comments(css)
    starts = [0] + [m.end() for m in re.finditer(r"\n", code)]
    for pos, kind, stmt, stack in css_statements(code):
        if "keyframes" in stack or "font-face" in stack:
            continue
        lineno = line_of(starts, pos)
        if kind == "block":
            if stmt.startswith("@media"):
                for m in MEDIA_WIDTH.finditer(stmt):
                    if re.sub(r"\s+", " ", m.group(0)) not in MEDIA_OK:
                        found.append((lineno, "R13", "@media " + m.group(0)))
            continue
        if kind == "apply":
            for token in stmt.split()[1:]:
                for rule, snippet in check_class(token, std):
                    found.append((lineno, rule, "@apply " + (token if snippet == "! 强制" else snippet)))
            continue
        if ":" not in stmt or not stack:
            continue
        prop, value = (part.strip() for part in stmt.split(":", 1))
        if prop.startswith("--"):
            if prop.startswith("--cc-"):
                text = CSS_NAME.sub(" ", QUOTED.sub(" ", value))
                writes_number = any(
                    float(m.group(2)) != 0 and not (m.group(3) == "" and "." not in m.group(2) and "calc(" in text)
                    for m in NUMBER_IN_VALUE.finditer(text))
                end_line = lineno + stmt.count("\n")
                comment = " ".join(raw_lines[lineno - 1:end_line])
                if writes_number and not PROVENANCE.search(comment):
                    found.append((lineno, "R13", f"{prop}: {value}（写数要注明出处）"))
            continue
        bad = css_value_problem(prop.lower(), value)
        if bad:
            found.append((lineno, "R13", f"{prop}: {value}"[:120]))
    found.extend(check_refs(code, std, starts, components=False))
    return found


def checked_files(src: str):
    """受检文件：[(相对 src 的 / 分隔路径, 绝对路径)]，与 scan_strict 遍历的完全一致。"""
    out = []
    for base, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(EXTENSIONS):
                path = os.path.join(base, name)
                out.append((os.path.relpath(path, src).replace(os.sep, "/"), path))
    return out


def scan_strict(src: str) -> list:
    """R4–R15：返回 [(相对路径, 行号, 规则, 片段)]，全仓（受检文件同 checked_files）。"""
    css_path = os.path.join(src, SOURCE)
    css = read(css_path) if os.path.exists(css_path) else ""
    std = Standard(css)
    out = []
    for rel, path in checked_files(src):
        text = read(path)
        hits = check_globals_strict(text, std) if rel.endswith(".css") else check_source_strict(text, std)
        for lineno, rule, snippet in hits:
            out.append((rel, lineno, rule, snippet))
    return out


def report(strict_hits: list, limit: int = 3) -> None:
    by_rule = {r: 0 for r in RULES}
    examples = {r: [] for r in RULES}
    by_file = {}
    for rel, lineno, rule, snippet in strict_hits:
        by_rule[rule] += 1
        if len(examples[rule]) < limit:
            examples[rule].append(f"{rel}:{lineno} {snippet}")
        by_file.setdefault(rel, {}).setdefault(rule, 0)
        by_file[rel][rule] += 1
    print("== 新判据报数（R4–R15，全仓）==")
    print(f"{'规则':<5} {'命中':>6}  说明")
    for rule, count in by_rule.items():
        print(f"{rule:<5} {count:>6}  {RULES[rule]}")
    print(f"{'合计':<5} {sum(by_rule.values()):>6}")
    print("== 示例 ==")
    for rule, items in examples.items():
        for item in items:
            print(f"{rule:<4} {item}")
    print("== 按文件 × 规则 ==")
    rules = list(RULES)
    print("文件".ljust(44) + " ".join(r.rjust(4) for r in rules) + "  合计")
    for rel in sorted(by_file, key=lambda f: (-sum(by_file[f].values()), f)):
        row = by_file[rel]
        print(rel.ljust(44) + " ".join(str(row.get(r, 0) or "·").rjust(4) for r in rules) + f"  {sum(row.values())}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", default=SRC, help="要检查的 src 目录（默认 frontend/assistant/src）")
    parser.add_argument("--report", action="store_true", help="列出 R4–R15 各条命中数、示例和按文件 × 规则的报数")
    args = parser.parse_args(argv)
    problems = scan(args.root)
    for rel, lineno, rule, snippet in problems:
        print(f"{rel}:{lineno}: [{rule}] {snippet}")
    strict_hits = scan_strict(args.root)
    for rel, lineno, rule, snippet in strict_hits:
        print(f"{rel}:{lineno}: [{rule}] {snippet}")
    if args.report:
        report(strict_hits)
    failed = False
    if problems:
        print(f"FAIL: {len(problems)} 处写死的颜色 / 圆角 / 阴影；改成 globals.css 的变量（见 docs/development/ccs.md）")
        failed = True
    if strict_hits:
        files = len({h[0] for h in strict_hits})
        print(f"FAIL: {len(strict_hits)} 处 / {files} 个文件 R4–R15 违例；改成 globals.css 的标准（见 docs/development/ccs.md、"
              "docs/work/2026-10-17-cc-frontend-standards.md 1.2）")
        failed = True
    if failed:
        return 1
    print("PASS: 组件与 lib 只引用 globals.css 的颜色、圆角、阴影变量；尺寸、间距、字号、层级、动效等 R4–R15 全仓没有违例")
    return 0


if __name__ == "__main__":
    sys.exit(main())
