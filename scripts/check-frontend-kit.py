#!/usr/bin/env python3
"""CC 标准组件库闸门：通用控件只从 frontend/assistant/src/components/ui 取，不在业务组件里手写（2026-11-10）。

起因：用户 11-10 定 shadcn/ui 整套搬进 CC 作唯一标准组件库（docs/development/cc-ui-kit.md）——按钮、弹窗、菜单、表格、输入框、
勾选、单选、页签、提示、徽章、消息、附件、工具步骤、提问卡等统一引用 components/ui，不再每个窗口自己画。现有界面不动，
「改到哪换到哪」：存量登记在 scripts/frontend-kit-baseline.json（按文件 × 规则计数），**只许减少不许增加**。

判据（frontend/assistant/src 下 components/ui/ 以外的 .ts / .tsx；注释不算）：

  K1 手写原生控件：<button、<input（type="file" 的隐藏选文件框除外）、<select、<textarea、<table、<dialog
  K2 直接引底层件：from "radix-ui" / "@radix-ui/*" / "@shadcn/react*" / "@base-ui/*"
  K3 手写交互角色（JSX 属性；选择器里的 [role="menu"] 不算）：role="switch|checkbox|radio|tab|tablist|dialog|alertdialog|menu|menuitem|progressbar|tooltip"
  K4 直接写通用外观类：cc-btn、cc-btn-sm、cc-card-btn、cc-icon-btn、cc-row-btn、cc-link-btn、cc-input、cc-switch、cc-table、cc-tabs、
     cc-alert、cc-chip、cc-kbd、cc-spinner、cc-empty、cc-dialog（及 -head / -title / -body / -foot）——这些外观由 components/ui 的组件挂
     （选择器里的 .cc-btn 不算：那是找元素，不是画控件）

用组件库里的件：Button、Input、Textarea、Select、NativeSelect、Checkbox、RadioGroup、Switch、Table、Tabs、Dialog、AlertDialog、
Sheet、Popover、DropdownMenu、Tooltip、Alert、Badge、Kbd、Spinner、Empty、Card、Item、Field、Message、Bubble、Attachment、Trace、
Questionnaire、CodeBlock、Marker …（清单见 components/NOTICE.md）。

    python3 scripts/check-frontend-kit.py            # 检查；有文件 × 规则超出基线就失败
    python3 scripts/check-frontend-kit.py --list     # 逐处列出（文件:行 规则 原文）
    python3 scripts/check-frontend-kit.py --update   # 按当前代码重写基线（只能在减少时用；有增加时拒绝）
    python3 scripts/check-frontend-kit.py --root DIR # 检查别的 src 目录（测试用，基线同目录下的 frontend-kit-baseline.json）

只读（--update 只写基线文件）；不连网络。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "assistant" / "src"
BASELINE = ROOT / "scripts" / "frontend-kit-baseline.json"
LIBRARY = "components/ui/"
EXTENSIONS = (".ts", ".tsx")
SKIP_DIRS = {"node_modules", "dist", "build", ".vite"}

GENERIC_CLASSES = ("cc-btn", "cc-btn-sm", "cc-card-btn", "cc-icon-btn", "cc-row-btn", "cc-link-btn", "cc-input", "cc-switch",
                   "cc-table", "cc-tabs", "cc-alert", "cc-chip", "cc-kbd", "cc-spinner", "cc-empty",
                   "cc-dialog", "cc-dialog-head", "cc-dialog-title", "cc-dialog-body", "cc-dialog-foot")
RULES = {
    "K1": "手写原生控件（用 components/ui 的 Button / Input / Select / Textarea / Table / Dialog）",
    "K2": "直接引底层件（radix-ui / @shadcn/react / @base-ui 只在 components/ui 里用）",
    "K3": "手写交互角色（用 components/ui 的 Switch / Checkbox / RadioGroup / Tabs / Dialog / DropdownMenu / Progress / Tooltip）",
    "K4": "直接写通用外观类（外观由 components/ui 的组件挂）",
}
K1 = re.compile(r"<(button|input|select|textarea|table|dialog)(?=[\s>/])")
K2 = re.compile(r"""\bfrom\s+["'](radix-ui|@radix-ui/[\w-]+|@shadcn/react(?:/[\w-]+)?|@base-ui/[\w-]+)["']""")
K3 = re.compile(r"""(?<![\w\[-])role\s*=\s*\{?\s*["'`](switch|checkbox|radio|tab|tablist|dialog|alertdialog|menu|menuitem|progressbar|tooltip)["'`]""")
K4 = re.compile(r"(?<![\w.-])(" + "|".join(re.escape(c) for c in sorted(GENERIC_CLASSES, key=len, reverse=True)) + r")(?![\w-])")
FILE_INPUT = re.compile(r"""\btype\s*=\s*\{?\s*["']file["']""")
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def strip_comments(text: str) -> str:
    """注释换成等长空白（保留换行，行号不变）。字符串里的 // （网址）不当注释：只认行首或空白、括号后的 //。"""
    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    out = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    return re.sub(r"(?m)(^|[\s;{}(),])//[^\n]*", lambda m: m.group(1) + " " * (len(m.group(0)) - len(m.group(1))), out)


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """返回 [(行号, 规则, 原文)]。"""
    code = strip_comments(text)
    hits: list[tuple[int, str, str]] = []
    line_of = lambda pos: code.count("\n", 0, pos) + 1
    for m in K1.finditer(code):
        if m.group(1) == "input":
            tag_end = code.find(">", m.end())
            if FILE_INPUT.search(code[m.end(): tag_end if tag_end != -1 else m.end() + 400]):
                continue
        hits.append((line_of(m.start()), "K1", m.group(0)))
    for rule, pattern in (("K2", K2), ("K3", K3), ("K4", K4)):
        for m in pattern.finditer(code):
            hits.append((line_of(m.start()), rule, m.group(0)))
    return sorted(hits)


def scan(src: Path) -> dict[str, list[tuple[int, str, str]]]:
    out: dict[str, list[tuple[int, str, str]]] = {}
    for path in sorted(src.rglob("*")):
        if path.suffix not in EXTENSIONS or not path.is_file() or any(p in SKIP_DIRS for p in path.parts):
            continue
        rel = path.relative_to(src).as_posix()
        if rel.startswith(LIBRARY):
            continue
        hits = scan_text(path.read_text(encoding="utf-8"))
        if hits:
            out[rel] = hits
    return out


def counts(found: dict[str, list[tuple[int, str, str]]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for rel, hits in found.items():
        for _, rule, _ in hits:
            out.setdefault(rel, {}).setdefault(rule, 0)
            out[rel][rule] += 1
    return {rel: dict(sorted(rules.items())) for rel, rules in sorted(out.items())}


def over(now: dict[str, dict[str, int]], base: dict[str, dict[str, int]]) -> list[tuple[str, str, int, int]]:
    rows = []
    for rel, rules in now.items():
        for rule, n in rules.items():
            allowed = base.get(rel, {}).get(rule, 0)
            if n > allowed:
                rows.append((rel, rule, n, allowed))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args(argv)
    src = args.root.resolve() if args.root else SRC
    baseline = (src / "frontend-kit-baseline.json") if args.root else BASELINE
    found = scan(src)
    now = counts(found)
    base = json.loads(baseline.read_text(encoding="utf-8")) if baseline.exists() else None

    if args.list:
        for rel, hits in found.items():
            for line, rule, text in hits:
                print(f"{rel}:{line}: [{rule}] {text}")

    if args.update:
        if base is not None and over(now, base):
            for rel, rule, n, allowed in over(now, base):
                print(f"拒绝：{rel} {rule} {allowed} → {n}（基线只许减少；新代码用 components/ui 的组件）", file=sys.stderr)
            return 1
        baseline.write_text(json.dumps(now, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        total = sum(sum(r.values()) for r in now.values())
        print(f"基线已写：{len(now)} 个文件、{total} 处（{baseline.relative_to(ROOT) if not args.root else baseline}）")
        return 0

    if base is None:
        print(f"缺基线文件 {baseline}；首次登记用 --update", file=sys.stderr)
        return 1
    rows = over(now, base)
    if rows:
        for rel, rule, n, allowed in rows:
            print(f"frontend/assistant/src/{rel}: [{rule}] {n} 处，基线 {allowed} —— {RULES[rule]}", file=sys.stderr)
        print("FAIL: 新写的通用控件要用 components/ui 的标准组件（docs/development/cc-ui-kit.md）；"
              "确实减少了就用 --update 把基线降下来", file=sys.stderr)
        return 1
    shrunk = sum(base.get(rel, {}).get(rule, 0) - now.get(rel, {}).get(rule, 0) for rel in base for rule in base[rel])
    total = sum(sum(r.values()) for r in now.values())
    tail = f"；比基线少 {shrunk} 处，可用 --update 把基线降下来" if shrunk > 0 else ""
    print(f"PASS: 没有新增手写通用控件（存量 {total} 处 / {len(now)} 个文件，只许减少{tail}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
