#!/usr/bin/env python3
"""拦「加锁之后又用普通读取单据」（MariaDB 关快照隔离后的写法规矩，docs/work/2026-11-26-lock-then-read.md）。

11-23 起所有 MariaDB 关了快照隔离（``--innodb-snapshot-isolation=OFF``）：同一事务里先做过普通读（建立了读视图），之后加锁读拿到的是最新
已提交的值，但**普通读仍然读读视图里的旧副本**。于是「先加锁（``select … for update`` / ``get_value(..., for_update=…)``），再
``frappe.get_doc(doctype, name)`` 普通读，再 ``save``」读到旧单，保存前比对报 ``TimestampMismatchError``（11-26 并发出采购凭证 417）。
规矩：加锁之后读要读的那张单，用 ``frappe.get_doc(doctype, name, for_update=…)``（Frappe 16 连子表一起加锁读，读到最新值）。

检查：同一个函数里，出现加锁调用之后，再出现不带 ``for_update`` 的 ``frappe.get_doc(<doctype>, <name>)``（不是用字典新建），或调用本仓
取单辅助函数（``_get`` / ``get_document`` / ``_owned``）却不传 ``lock``，就失败。
确认没问题的（读的是与锁无关、不会被并发改的单，或读完只读不写）在那一行行末标 ``# lock-read-ok: 原因``。
只看 apps/ 下的 Python，排除 tests、patches。启发式：跨函数（加锁在调用方、读在被调函数里）靠评审与 :mod:`transaction.conflicts`
的兜底重来（保存前比对撞上的那一种整笔重来）。
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MARK = "lock-read-ok:"


def _is_lock(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg == "for_update" and not (isinstance(keyword.value, ast.Constant) and not keyword.value.value):
            return True
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "for_update":   # frappe.qb … .for_update()
        return True
    if isinstance(func, ast.Attribute) and func.attr == "sql" and node.args:
        first = node.args[0]
        text = ""
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            text = first.value
        elif isinstance(first, ast.JoinedStr):
            text = "".join(part.value for part in first.values if isinstance(part, ast.Constant) and isinstance(part.value, str))
        return "for update" in text.lower()
    return False


# 本仓「取一张单」的辅助函数（带 ``lock`` 参数）：加锁之后调用它们却不传 ``lock``，读的就是普通读
_LOCKABLE_GETTERS = {"_get", "get_document", "_owned"}


def _is_unlocked_getter(node: ast.Call) -> bool:
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    return name in _LOCKABLE_GETTERS and not any(keyword.arg == "lock" for keyword in node.keywords)


def _is_plain_get_doc(node: ast.Call) -> bool:
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "get_doc"
            and isinstance(func.value, ast.Name) and func.value.id == "frappe"):
        return False
    if len(node.args) < 2 or isinstance(node.args[0], ast.Dict):
        return False
    return not any(keyword.arg == "for_update" for keyword in node.keywords)


def _calls_in_order(function):
    """函数体里的调用，按源码位置排（不进嵌套的函数 / 类）。"""
    found = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                found.append(child)
            walk(child)
    walk(function)
    return sorted(found, key=lambda call: (call.lineno, call.col_offset))


def violations(path: pathlib.Path, source: str):
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    out = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        locked = False
        for call in _calls_in_order(function):
            if _is_lock(call):
                locked = True
            elif locked and (_is_plain_get_doc(call) or _is_unlocked_getter(call)):
                line = lines[call.lineno - 1] if call.lineno - 1 < len(lines) else ""
                if MARK not in line:
                    out.append((call.lineno, function.name))
    return out


def files():
    for path in sorted((ROOT / "apps").rglob("*.py")):
        parts = path.relative_to(ROOT).parts
        if "tests" in parts or "patches" in parts or path.name.startswith("test_"):
            continue
        yield path


def main() -> int:
    failed = []
    for path in files():
        for line, name in violations(path, path.read_text(encoding="utf-8")):
            failed.append(f"{path.relative_to(ROOT)}:{line} {name}: 加锁之后用普通 frappe.get_doc 读（改成 for_update=… / lock=True，或行末标 # {MARK} 原因）")
    for item in failed:
        print(item)
    print(f"check-lock-then-read: {'FAIL' if failed else 'PASS'} ({len(failed)} violations)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
