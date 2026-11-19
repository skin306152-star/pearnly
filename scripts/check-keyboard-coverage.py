#!/usr/bin/env python3
"""手机键盘闸门：凡是手机上会弹键盘的页面都要接上共用的 `desk/keyboard.js`（2026-11-01，用户：「又或者某些没覆盖到位」）。

接法（模块文件头同样写着）：
- Desk 页面与弹窗（ERP / Cowork / DMS / 控制面）：`pearnly_core/hooks.py` 的 `app_include_js`；
- 网站页（`{% extends "templates/web.html" %}`：改密码页等）：同一文件的 `web_include_js`——只到得了装了 pearnly_core 的产品站点，
  控制面站点只装 pearnly_control，它的网站页不算接上；
- 独立页（`apps/**/www/**/*.html` 等自己写整页 HTML 的，含 Python 里拼出的整页）：页面里用 `versioned_asset('pearnly_core', 'desk/keyboard.js')` 挂；
- 前端单页（`frontend/**/index.html`）：入口脚本 import 同一个文件打进包。

「会弹键盘」= 页面里有文字类 `<input>`（没写 type 或 type 不是勾选框 / 按钮 / 文件 / 隐藏 / 日期等）、`<textarea>`、`contenteditable`，
或脚本里建这些元素；挂单页应用的（`id="app"` / `id="root"` 挂载点）一律算——界面由组件画，页面文本里看不到输入框。
加载前端产物的 www 页（`/assets/pearnly_core/<产物>/index.html`）按对应前端入口算接没接上。按设计不接的写进 `scripts/keyboard-coverage-exempt.json`（路径 → 原因）：只许减少不许增加
（比对 merge-base(origin/main) 那一版），失效的（页面没了、没有输入框了、已经接上了）也报出来要删掉。

    python3 scripts/check-keyboard-coverage.py          # 检查
    python3 scripts/check-keyboard-coverage.py --list   # 列出每个页面怎么接上的

只读、不连网络。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXEMPT = ROOT / "scripts" / "keyboard-coverage-exempt.json"
HOOKS = ROOT / "apps" / "pearnly_core" / "pearnly_core" / "hooks.py"
MODULE = "desk/keyboard.js"
SKIP_PARTS = {"node_modules", "dist", "pearnly-app", "__pycache__", "tests"}
NO_KEYBOARD = {"button", "checkbox", "color", "date", "datetime-local", "file", "hidden", "image", "month", "radio", "range",
               "reset", "submit", "time", "week"}
INPUT = re.compile(r"<input\b([^>]*)>?", re.I)
TYPE = re.compile(r"""\btype\s*=\s*["']?([a-z-]+)""", re.I)
OTHER = re.compile(r"<textarea\b|contenteditable|createElement\(\s*['\"](?:input|textarea)['\"]", re.I)
MOUNT = re.compile(r"""<div\s+id=["'](?:app|root)["']""")
BUNDLE = re.compile(r"/assets/pearnly_core/([a-z-]+)/index\.html")
# 前端产物目录 → 源码入口（各自 vite.config.ts 的 outDir）
BUNDLES = {"pearnly-app": "frontend/index.html", "assistant": "frontend/assistant/index.html"}
CONTROL = "apps/pearnly_control/"  # 控制面站点不装 pearnly_core：hooks 里的 app_include_js / web_include_js 到不了
WEB_PAGE = re.compile(r"""\{%\s*extends\s+["']templates/web\.html["']""")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def typing(text: str) -> bool:
    if OTHER.search(text) or MOUNT.search(text):
        return True
    for match in INPUT.finditer(text):
        kind = TYPE.search(match.group(1) or "")
        if not kind or kind.group(1).lower() not in NO_KEYBOARD:
            return True
    return False


def pages() -> list[Path]:
    """自己出整页的文件（只看进版本库的，构建产物不算）：apps 下的 html 页面、Python 里拼的整页、frontend 各单页入口。"""
    listed = subprocess.run(["git", "-c", "core.quotepath=off", "ls-files", "--cached", "--others", "--exclude-standard", "apps", "frontend"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.splitlines()
    found = []
    for name in sorted(listed):
        path = ROOT / name
        if not path.is_file() or SKIP_PARTS & set(Path(name).parts):
            continue
        if name.startswith("frontend/"):
            if path.name == "index.html":
                found.append(path)
        elif path.suffix == ".html":
            found.append(path)
        elif path.suffix == ".py" and re.search(r"<!doctype html", path.read_text(encoding="utf-8", errors="ignore"), re.I):
            found.append(path)
    return found


def frontend_source(index: Path) -> str:
    """前端单页：入口 html 加上它引用的入口脚本（打进包的就算接上）。"""
    text = index.read_text(encoding="utf-8")
    for src in re.findall(r"""<script[^>]+src=["']/?([^"']+)["']""", text):
        entry = index.parent / src
        if entry.is_file():
            text += entry.read_text(encoding="utf-8")
    return text


def hooks_ok() -> list[str]:
    text = HOOKS.read_text(encoding="utf-8")
    problems = []
    for hook in ("app_include_js", "web_include_js"):
        line = next((row for row in text.splitlines() if row.startswith(f"{hook} =")), "")
        if MODULE not in line:
            problems.append(f"{rel(HOOKS)}：{hook} 没挂 {MODULE}（Desk 页面 / 网站页就漏了）")
    return problems


def base_exempt() -> dict | None:
    base = subprocess.run(["git", "-c", "core.quotepath=off", "merge-base", "origin/main", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not base:
        return None
    shown = subprocess.run(["git", "-c", "core.quotepath=off", "show", f"{base}:{rel(EXEMPT)}"], cwd=ROOT, capture_output=True, text=True)
    return json.loads(shown.stdout) if shown.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    exempt = json.loads(EXEMPT.read_text(encoding="utf-8"))
    problems = hooks_ok()
    rows = []
    for path in pages():
        name = rel(path)
        text = frontend_source(path) if name.startswith("frontend/") else path.read_text(encoding="utf-8", errors="ignore")
        for bundle in set(BUNDLE.findall(text)):
            if bundle in BUNDLES:
                text += frontend_source(ROOT / BUNDLES[bundle])
            else:
                problems.append(f"{name}：加载前端产物 {bundle}，不知道它的源码入口；补进 BUNDLES")
        if not typing(text):
            how = "没有文字输入格"
        elif MODULE in text:
            how = "页面自己挂 / 打进包"
        elif WEB_PAGE.search(text) and not name.startswith(CONTROL):
            how = "网站页（web_include_js）"
        else:
            how = ""
        if name in exempt:
            if how:
                problems.append(f"{name}：登记为不接，但它{how}，从 {rel(EXEMPT)} 删掉这一条")
            how = "不接：" + exempt[name]
        elif not how:
            problems.append(f"{name}：页面有文字输入格，没接 {MODULE}（独立页用 versioned_asset 挂；按设计不接写进 {rel(EXEMPT)} 并写原因）")
        rows.append((name, how))
    names = {name for name, _ in rows}
    for name in exempt:
        if name not in names:
            problems.append(f"{rel(EXEMPT)}：{name} 已不是页面，删掉这一条")
    base = base_exempt()
    if base is not None:
        for name in sorted(set(exempt) - set(base)):
            problems.append(f"{rel(EXEMPT)}：新增不接的页面 {name}（只许减少）；接上 {MODULE}")
    if args.list:
        for name, how in rows:
            print(f"{name}\t{how}")
    for problem in problems:
        print("✗ " + problem)
    if problems:
        return 1
    print(f"手机键盘覆盖：通过（{len(rows)} 个页面，Desk / 网站页由 hooks 统一挂）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
