#!/usr/bin/env python3
"""大数据量闸门：拦新增的「一次取全」——服务端无上限查询、前端整批取回再在浏览器里排 / 切页（2026-10-25）。

起因：客户页一次把 7.4 万客户全取回来渲染，页面卡死；同类写法散在各产品里。列表、选择、搜索先用 Frappe 原生的
服务端分页（`limit_start` / `limit_page_length`、`search_link`、`get_query`），见 docs/development/architecture.md
「列表、选择与搜索」。

算作「无上限」：
- Python：`frappe.get_all` / `frappe.get_list` / `frappe.db.get_all` / `frappe.db.get_list` 没给上限，
  或上限写成 0 / None（`limit` / `limit_page_length` / `page_length`）。
- 前端（apps/**/public/**/*.js）：请求参数里 `limit: 0` / `limit_page_length: 0`，或调用 `p.sort_rows(`
  （整批取回后在浏览器里排序切页的配套工具）。

确实有界的（按单号、按一张单的明细、按固定的小字典）在那一行（或调用的第一行）写 `# bounded: 原因` /
`// bounded: 原因`。存量登记在 scripts/unbounded-queries-baseline.json（按文件计数）：**只许减少不许增加**；
修掉一处后用 `--update` 把基线降下来。

    python3 scripts/check-unbounded-queries.py            # 检查；有文件超出基线就失败
    python3 scripts/check-unbounded-queries.py --list     # 逐处列出（文件:行）
    python3 scripts/check-unbounded-queries.py --update   # 按当前代码重写基线（只能在减少时用；增加时拒绝）

只读（--update 只写基线文件）；不连网络。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "unbounded-queries-baseline.json"
QUERY_CALLS = {("frappe", "get_all"), ("frappe", "get_list"), ("db", "get_all"), ("db", "get_list")}
LIMIT_KEYS = ("limit", "limit_page_length", "page_length")
JS_PATTERNS = (re.compile(r"\blimit(?:_page_length)?\s*:\s*0\b"), re.compile(r"\.sort_rows\("))
SKIP_PARTS = {"node_modules", "tests", "patches", "__pycache__"}


def _call_name(node: ast.Call) -> tuple[str, str] | None:
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id, func.attr
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
        return func.value.attr, func.attr
    return None


def _unbounded(node: ast.Call) -> bool:
    given = {kw.arg: kw.value for kw in node.keywords if kw.arg in LIMIT_KEYS}
    if any(kw.arg is None for kw in node.keywords):
        return False  # **kwargs 转发：上限由调用方给，算不出来，不算
    if not given:
        return True
    return all(isinstance(value, ast.Constant) and value.value in (0, None) for value in given.values())


def _marked(lines: list[str], lineno: int, marker: str) -> bool:
    return marker in lines[lineno - 1] if 0 < lineno <= len(lines) else False


def python_hits(path: Path) -> list[int]:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in QUERY_CALLS and _unbounded(node):
            if not _marked(lines, node.lineno, "# bounded:"):
                hits.append(node.lineno)
    return sorted(hits)


def js_hits(path: Path) -> list[int]:
    hits = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "// bounded:" in line or line.lstrip().startswith("//"):
            continue
        if any(pattern.search(line) for pattern in JS_PATTERNS):
            hits.append(lineno)
    return hits


def scan(root: Path = ROOT) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted((root / "apps").rglob("*.py")):
        rel = path.relative_to(root)
        if SKIP_PARTS & set(rel.parts) or path.name.startswith("test_"):
            continue
        if hits := python_hits(path):
            found[str(rel)] = hits
    for path in sorted((root / "apps").rglob("*.js")):
        rel = path.relative_to(root)
        if "public" not in rel.parts or SKIP_PARTS & set(rel.parts) or "vendor" in rel.parts or ".bundle." in path.name:
            continue
        if path.name.endswith(".min.js") or "assistant" in rel.parts or "pearnly-app" in rel.parts:
            continue  # 构建产物
        if hits := js_hits(path):
            found[str(rel)] = hits
    return found


def compare(found: dict[str, list[int]], baseline: dict[str, int]) -> list[str]:
    problems = []
    for file, hits in sorted(found.items()):
        allowed = int(baseline.get(file, 0))
        if len(hits) > allowed:
            problems.append(f"{file}: {len(hits)} 处无上限查询 / 整批取回（基线 {allowed}），行 {', '.join(map(str, hits))}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="逐处列出")
    parser.add_argument("--update", action="store_true", help="按当前代码降低基线（不许增加）")
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    found = scan(args.repo)
    baseline_path = args.repo / "scripts" / "unbounded-queries-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}
    problems = compare(found, baseline)
    if args.list:
        for file, hits in sorted(found.items()):
            print(f"{file}: {', '.join(map(str, hits))}")
    if args.update:
        if problems and baseline_path.exists():
            print("基线只许减少：下面这些超出了，先改成分页或写 bounded 原因", file=sys.stderr)
            print("\n".join(problems), file=sys.stderr)
            return 1
        baseline_path.write_text(json.dumps({file: len(hits) for file, hits in sorted(found.items())},
                                            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"基线已更新：{len(found)} 个文件、{sum(map(len, found.values()))} 处")
        return 0
    if problems:
        print("大数据量闸门：有新增的无上限查询 / 整批取回（列表、选择、搜索用服务端分页；确实有界的写 bounded 原因）")
        print("\n".join(problems))
        return 1
    total = sum(map(len, found.values()))
    print(f"大数据量闸门：通过（存量 {total} 处、{len(found)} 个文件，未超出基线）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
