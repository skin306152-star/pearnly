# -*- coding: utf-8 -*-
"""GET 能调到的接口不许写库。

为什么（2026-10-29）：Frappe 不提交 GET 请求的事务——GET 接口里写的东西请求结束就被回滚。
手机盘点 `api_line_stocktake_lookup`（GET）在快照里没有这件商品时当场登记一行，回滚后保存带着这一行去找，
空仓里每一笔实盘都 404（53a725f6 引入，到 10-29 才发现）。会写库的接口一律 `methods=["POST"]`，不靠在 GET 里手动 commit。

判据：`@frappe.whitelist` 没限定方法、或允许 GET 的接口，沿着它调到的**本仓函数**往下找（同模块、`from x import y`、
`import x as m` 的 `m.f`），找到写库动作就算违规：
  insert / save / submit / cancel / delete / db_set / frappe.db.set_value / set_single_value / frappe.db.delete /
  delete_doc / rename_doc / bulk_insert / frappe.db.commit / frappe.enqueue / enqueue_doc /
  frappe.defaults.set_* / 写语句的 frappe.db.sql（字面量以 insert/update/delete/replace 开头）。
只看本仓代码；Frappe 自己的函数不往里追（框架的 GET 接口不归这里管）。

确属预期的逐条登记在 `scripts/get-writes-exempt.tsv`：`<模块>.<函数>\t<接口|经过>\t<原因>`。
  · 接口：这个 GET 接口写库是预期的（例如 OAuth 回调按协议只能是 GET），或已列入整改、暂时留着（原因里写「待改」）；
  · 经过：这个函数在 GET 路径上不会真写（例如只在显式 `create=True` 时新建），扫描到它就不往下追。
登记了却已不再命中的接口条目也报错（基线只许缩小）。

用法：
    python3 scripts/check-get-writes.py            # 有违规退出码 1
    python3 scripts/check-get-writes.py --list     # 逐条列出违规及写库路径
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "apps"
EXEMPT = ROOT / "scripts" / "get-writes-exempt.tsv"
MAX_DEPTH = 6

# 写到内存里的工作簿 / 图片（openpyxl、PIL）不是写库
NOT_DB = {"book", "wb", "workbook", "image", "img", "picture", "sheet", "ws", "archive", "zf", "pdf", "canvas", "buffer", "out", "rows",
          "items", "lines", "parts", "result", "values", "names", "list", "stack", "queue"}
WRITE_ATTRS = {"insert", "save", "submit", "cancel", "db_set", "db_insert", "db_update", "bulk_insert",
               "set_single_value", "delete_doc", "rename_doc", "enqueue", "enqueue_doc", "commit", "delete_key_never"}
WRITE_SQL = ("insert", "update", "delete", "replace")


def module_name(path: Path, apps: Path = APPS) -> str:
    rel = path.relative_to(apps).with_suffix("")
    parts = list(rel.parts)[1:]  # apps/<app>/<pkg>/... → <pkg>/...
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def sources(apps: Path = APPS):
    for path in apps.glob("*/*/**/*.py"):
        if any(part in {"tests", "test", "__pycache__", "patches"} for part in path.parts) or path.name.startswith("test_"):
            continue
        yield path


class Module:
    def __init__(self, path: Path, apps: Path = APPS):
        self.path = path
        self.name = module_name(path, apps)
        self.tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        self.functions = {node.name: node for node in self.tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.aliases = {}   # 本地名 → 模块全名（import x as m / from pkg import mod）
        self.imported = {}  # 本地名 → (模块全名, 函数名)（from mod import f）
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    local = alias.asname or alias.name
                    self.imported[local] = (node.module, alias.name)
                    self.aliases.setdefault(local, f"{node.module}.{alias.name}")


def whitelisted_get(node) -> bool:
    for deco in node.decorator_list:
        call = deco if isinstance(deco, ast.Call) else None
        target = call.func if call else deco
        dotted = ast.unparse(target)
        if dotted not in {"frappe.whitelist", "whitelist"}:
            continue
        methods = None
        for keyword in (call.keywords if call else []):
            if keyword.arg == "methods":
                try:
                    methods = [str(m).upper() for m in ast.literal_eval(keyword.value)]
                except ValueError:
                    methods = None
        return methods is None or "GET" in methods
    return False


def direct_writes(node) -> list[str]:
    found = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        text = ast.unparse(func)
        if isinstance(func, ast.Attribute):
            if func.attr in WRITE_ATTRS:
                # `list.insert(...)`、`book.save(buffer)`、`image.save(...)` 之类不是写库
                if isinstance(func.value, ast.Name) and func.value.id in NOT_DB:
                    continue
                if func.attr == "commit" and text not in {"frappe.db.commit", "frappe.local.db.commit"}:
                    continue
                if func.attr in {"save", "insert"} and isinstance(func.value, ast.Attribute) and ast.unparse(func.value).startswith(("frappe.cache", "frappe.local.cache")):
                    continue
                found.append(f"{text}()")
            elif text in {"frappe.db.set_value", "frappe.db.delete", "frappe.delete_doc", "frappe.rename_doc", "frappe.db.truncate",
                          "frappe.defaults.set_user_default", "frappe.defaults.set_global_default", "frappe.defaults.set_default",
                          "frappe.defaults.add_user_default", "frappe.defaults.clear_user_default"}:
                found.append(f"{text}()")
            elif text == "frappe.db.sql" and sub.args:
                first = sub.args[0]
                literal = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else (
                    "".join(v.value for v in first.values if isinstance(v, ast.Constant)) if isinstance(first, ast.JoinedStr) else "")
                if literal.strip().lower().startswith(WRITE_SQL):
                    found.append("frappe.db.sql(写语句)")
        elif isinstance(func, ast.Name) and func.id in {"enqueue"}:
            found.append("enqueue()")
    return found


def callees(module: Module, node, modules: dict) -> list[tuple[Module, ast.AST, str]]:
    out = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name):
            if func.id in module.functions:
                out.append((module, module.functions[func.id], f"{module.name}.{func.id}"))
            elif func.id in module.imported:
                mod, name = module.imported[func.id]
                target = modules.get(mod)
                if target and name in target.functions:
                    out.append((target, target.functions[name], f"{mod}.{name}"))
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            mod = module.aliases.get(func.value.id)
            target = modules.get(mod) if mod else None
            if target and func.attr in target.functions:
                out.append((target, target.functions[func.attr], f"{mod}.{func.attr}"))
    return out


def scan(passes=frozenset(), apps: Path = APPS):
    modules = {}
    for path in sources(apps):
        try:
            module = Module(path, apps)
        except SyntaxError:
            continue
        modules[module.name] = module
    memo: dict[str, list[str] | None] = {}

    def writes_of(module: Module, node, label: str, depth: int, seen: frozenset) -> list[str] | None:
        if depth and label in passes:
            return None
        if label in memo:
            return memo[label]
        direct = direct_writes(node)
        if direct:
            memo[label] = [f"{label}: {direct[0]}"]
            return memo[label]
        if depth >= MAX_DEPTH:
            return None
        for target, sub, sub_label in callees(module, node, modules):
            if sub_label in seen:
                continue
            path = writes_of(target, sub, sub_label, depth + 1, seen | {sub_label})
            if path:
                memo[label] = [label] + path
                return memo[label]
        memo[label] = None
        return None

    findings = {}
    for module in modules.values():
        for name, node in module.functions.items():
            if whitelisted_get(node):
                label = f"{module.name}.{name}"
                path = writes_of(module, node, label, 0, frozenset({label}))
                if path:
                    findings[label] = (module.path, node.lineno, path)
    return findings


def load_exempt() -> tuple[dict[str, str], dict[str, str], list[str]]:
    """（接口豁免, 经过豁免, 格式错的行）"""
    endpoints, passes, broken = {}, {}, []
    if not EXEMPT.exists():
        return endpoints, passes, broken
    for number, line in enumerate(EXEMPT.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        cells = [cell.strip() for cell in line.split("\t")]
        if len(cells) != 3 or cells[1] not in {"接口", "经过"} or not cells[2]:
            broken.append(f"第 {number} 行：要三列 <函数全名>\\t<接口|经过>\\t<原因>")
            continue
        (endpoints if cells[1] == "接口" else passes)[cells[0]] = cells[2]
    return endpoints, passes, broken


def main(argv) -> int:
    exempt, passes, broken = load_exempt()
    findings = scan(frozenset(passes))
    bad = {key: value for key, value in findings.items() if key not in exempt}
    stale = sorted(key for key in exempt if key not in findings)
    missing_reason = broken
    if "--list" in argv or bad:
        for key, (path, line, chain) in sorted(bad.items()):
            print(f"{path}:{line} {key}\n    写库路径：{' → '.join(chain)}")
    for key in stale:
        print(f"豁免已不再需要（删掉这一条）：{key}")
    for line in missing_reason:
        print(f"豁免登记格式不对：{line}")
    if bad or stale or missing_reason:
        print(f"\nGET 能调到却写库的接口：{len(bad)} 个。改成 @frappe.whitelist(methods=[\"POST\"])（调用方一起改）；"
              f"确属预期的登记到 {EXEMPT.relative_to(ROOT)}（写原因）。")
        return 1
    print(f"GET 可达接口写库检查：PASS（接口豁免 {len(exempt)} 条，经过豁免 {len(passes)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
