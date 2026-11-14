#!/usr/bin/env python3
"""逐行查询闸门：拦新增的「循环里查库」（N+1）——一张表 N 行就发 N 条（或 N 的几倍）查询（2026-11-14）。

起因：ERP 盘点明细 251 行发 1,362 条查询（仓库名按行查、库存令牌按物料查），线上服务端 6–7 秒（nginx 实测，见
docs/work/2026-11-14-page-performance.md）。`check-unbounded-queries.py` 只拦「一次取全」，拦不住这一类。
修法：循环外按 `["in", 名单]` 一次取回建字典，循环里查字典（Frappe 原生 `frappe.get_all(..., filters={"name": ["in", …]})`）；
同一请求反复读的小主档用 `frappe.get_cached_value` / `frappe.get_cached_doc`。

算作「循环里查库」：for / while 循环体、推导式（列表 / 集合 / 字典 / 生成器）每轮都要执行的部分里，
- 直接查库：`frappe.db.get_value / get_values / get_all / get_list / exists / count / sql / get_single_value`、
  `frappe.get_all / get_list / get_doc / get_last_doc / get_value`、`frappe.qb ….run()`；
- 或调用了「会查库的函数」：本仓 apps/ 里函数体（含它再调用的本仓函数，按名字解析：同模块函数、`模块别名.函数`）有上面的直接查库。
不算：`frappe.get_cached_value` / `get_cached_doc`（同一请求只查一次）；`for x in frappe.get_all(...)` 的取数本身（只执行一次）。

确实有界的（按几家公司、几个仓、一张单的几行）在那一行写 `# loop-query: 原因`。存量登记在
scripts/loop-queries-baseline.json（按文件计数）：**只许减少不许增加**；修掉后用 `--update` 降基线。

    python3 scripts/check-loop-queries.py            # 检查；有文件超出基线就失败
    python3 scripts/check-loop-queries.py --list     # 逐处列出（文件:行  循环里调用的东西）
    python3 scripts/check-loop-queries.py --update   # 按当前代码重写基线（只能在减少时用；增加时拒绝）
    python3 scripts/check-loop-queries.py --repo <目录> --list   # 查另一份代码（别的分支解出来的目录）

只读（--update 只写基线文件）；不连网络。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "loop-queries-baseline.json"
MARKER = "loop-query:"
SKIP_PARTS = {"node_modules", "tests", "patches", "__pycache__", "test"}
DB_METHODS = {"get_value", "get_values", "get_all", "get_list", "exists", "count", "sql", "get_single_value", "sql_list"}
FRAPPE_METHODS = {"get_all", "get_list", "get_doc", "get_last_doc", "get_value"}


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    if isinstance(node, ast.Call):
        inner = _dotted(node.func)
        return (inner + "()." + ".".join(reversed(parts))) if inner else ""
    return ""


def direct_query(call: ast.Call) -> str | None:
    """这一个调用本身是不是查库，是就回显示用的名字。"""
    name = _dotted(call.func)
    if not name:
        return None
    head, _, last = name.rpartition(".")
    if head in ("frappe.db", "db") and last in DB_METHODS:
        return name
    if head == "frappe" and last in FRAPPE_METHODS:
        if last == "get_doc" and call.args and isinstance(call.args[0], ast.Dict):
            return None  # frappe.get_doc({...}) 只是建对象，不查库
        return name
    if last == "run" and "frappe.qb" in name:
        return "frappe.qb….run"
    return None


def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT / "apps")
    parts = list(rel.with_suffix("").parts[1:])  # apps/<app>/<app>/... → <app>/...
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def sources() -> list[Path]:
    return sorted(p for p in (ROOT / "apps").rglob("*.py")
                  if not SKIP_PARTS & set(p.relative_to(ROOT).parts) and not p.name.startswith("test_"))


class Module:
    def __init__(self, path: Path):
        self.path = path
        self.name = module_name(path)
        self.text = path.read_text(encoding="utf-8")
        self.lines = self.text.splitlines()
        self.tree = ast.parse(self.text, filename=str(path))
        self.aliases: dict[str, str] = {}   # 别名 → 模块点分名
        self.imported: dict[str, str] = {}  # 名字 → 模块.函数
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                    self.imported[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        self.functions: dict[str, ast.AST] = {}
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node

    def resolve(self, call: ast.Call) -> str | None:
        """被调用的本仓函数的全名（模块.函数），解析不了就 None。"""
        func = call.func
        if isinstance(func, ast.Name):
            if func.id in self.functions:
                return f"{self.name}.{func.id}"
            return self.imported.get(func.id)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in self.aliases:
            return f"{self.aliases[func.value.id]}.{func.attr}"
        return None


def calls_in(node) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def query_functions(modules: list[Module]) -> dict[str, str]:
    """会查库的本仓函数：全名 → 查库的那一处（按调用关系传递到不动点）。"""
    known: dict[str, str] = {}
    bodies = {f"{m.name}.{name}": (m, fn) for m in modules for name, fn in m.functions.items()}
    for full, (m, fn) in bodies.items():
        for call in calls_in(fn):
            hit = direct_query(call)
            if hit:
                known[full] = hit
                break
    changed = True
    while changed:
        changed = False
        for full, (m, fn) in bodies.items():
            if full in known:
                continue
            for call in calls_in(fn):
                target = m.resolve(call)
                if target in known:
                    known[full] = f"{target.rsplit('.', 1)[-1]}→{known[target]}"
                    changed = True
                    break
    return known


def loop_parts(tree):
    """每轮都要执行的节点：for/while 的循环体与 else、推导式的元素与条件（不含第一个 for 的取数）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            yield node, list(node.body) + list(node.orelse) + ([node.test] if isinstance(node, ast.While) else [])
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            parts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            for i, gen in enumerate(node.generators):
                parts.extend(gen.ifs)
                if i:
                    parts.append(gen.iter)
            yield node, parts


def findings() -> list[tuple[str, int, str]]:
    modules = []
    for path in sources():
        try:
            modules.append(Module(path))
        except SyntaxError:
            continue
    known = query_functions(modules)
    out = set()
    for m in modules:
        rel = str(m.path.relative_to(ROOT))
        for _loop, parts in loop_parts(m.tree):
            for part in parts:
                for call in calls_in(part):
                    what = direct_query(call)
                    if not what:
                        target = m.resolve(call)
                        what = f"{target.rsplit('.', 1)[-1]}→{known[target]}" if target in known else None
                    if not what:
                        continue
                    line = m.lines[call.lineno - 1] if call.lineno <= len(m.lines) else ""
                    if MARKER in line:
                        continue
                    out.add((rel, call.lineno, what))
    return sorted(out)


def counts(rows) -> dict[str, int]:
    result: dict[str, int] = {}
    for rel, _line, _what in rows:
        result[rel] = result.get(rel, 0) + 1
    return dict(sorted(result.items()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="另一份仓库目录（测试与查别的分支用），默认本仓")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args(argv)
    global ROOT, BASELINE
    if a.repo:
        ROOT = Path(a.repo).resolve()
        BASELINE = ROOT / "scripts" / "loop-queries-baseline.json"
    rows = findings()
    current = counts(rows)
    if a.list:
        for rel, line, what in rows:
            print(f"{rel}:{line}  {what}")
        print(f"共 {len(rows)} 处，{len(current)} 个文件")
        return 0
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    grown = {f: (baseline.get(f, 0), n) for f, n in current.items() if n > baseline.get(f, 0)}
    if a.update:
        if grown and BASELINE.exists():
            for f, (old, new) in grown.items():
                print(f"拒绝：{f} 从 {old} 增加到 {new}", file=sys.stderr)
            return 1
        BASELINE.write_text(json.dumps(current, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"基线已写：{sum(current.values())} 处，{len(current)} 个文件")
        return 0
    if grown:
        print("循环里查库（逐行查询）超出基线——循环外按名单一次取回建字典，或确实有界的那一行写 `# loop-query: 原因`：", file=sys.stderr)
        for f, (old, new) in grown.items():
            print(f"  {f}: 基线 {old} → 现在 {new}", file=sys.stderr)
            for rel, line, what in rows:
                if rel == f:
                    print(f"    {rel}:{line}  {what}", file=sys.stderr)
        return 1
    shrunk = sum(baseline.values()) - sum(current.values())
    print(f"循环里查库：{sum(current.values())} 处（基线 {sum(baseline.values())}{'，可用 --update 降基线' if shrunk > 0 else ''}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
