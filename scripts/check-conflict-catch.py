#!/usr/bin/env python3
"""数据库冲突只经共用层接（B ①，11-26）。

生产 MariaDB 关了快照隔离（11-23，``--innodb-snapshot-isolation=OFF``）之后，同一种并发冲突不再只报 1020（Frappe 抛
``QueryDeadlockError``），还会由 Frappe 保存前比对抛 ``TimestampMismatchError``（417）。各处自己写的 ``except frappe.QueryDeadlockError``
只认前一种：入口层的三次循环接不住、保存点里会把后一种当普通错误吞掉、后台会记成出错再延后。11-26 已统一改成
``pearnly_core.transaction.conflicts``：入口层用 ``conflicts.attempt`` / ``retried``，保存点、后台按 ``conflicts.is_conflict(error)`` 判断。

规则：``apps/`` 产品代码（测试、补丁除外；``transaction/conflicts.py`` 本身除外）里，``except`` 的类型是 ``frappe.QueryDeadlockError``、
或元组里含它的，失败——除非 ``except`` 那一行标注 ``# conflict-ok: 原因``（写明为什么只认死锁就对：带条件的 UPDATE 抢占、只读、
只插入、本来就把所有异常往外抛等），或登记在 ``scripts/conflict-catch-reviewed.tsv``（文件、所在函数、原因；给不宜为一句标注改动的文件，
例如改了要连带真机记录的入口）。原因不许空；登记了却找不到的条目也算失败。

    python3 scripts/check-conflict-catch.py          # 检查（CI）
    python3 scripts/check-conflict-catch.py --list   # 列出全部接冲突处与标注
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = ("/tests/", "/patches/", "/test_", "transaction/conflicts.py")
MARK = "# conflict-ok:"
REVIEWED = ROOT / "scripts" / "conflict-catch-reviewed.tsv"


def reviewed(path: Path = REVIEWED) -> dict:
    """``{(文件, 函数): 原因}``。"""
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                rel, function, reason = (line.split("\t") + ["", ""])[:3]
                out[(rel.strip(), function.strip())] = reason.strip()
    return out


def _names_deadlock(node) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Tuple):
        return any(_names_deadlock(item) for item in node.elts)
    if isinstance(node, ast.Attribute):
        return node.attr == "QueryDeadlockError"
    if isinstance(node, ast.Name):
        return node.id == "QueryDeadlockError"
    return False


def findings(root: Path = ROOT, registry=None):
    """回 ``[(相对路径, 行号, 标注原因或 None)]``：每一个只认死锁的 ``except``（行内标注或登记表）。"""
    registry = reviewed(root / "scripts" / "conflict-catch-reviewed.tsv") if registry is None else registry
    out = []
    for path in sorted((root / "apps").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if any(part in "/" + rel for part in SKIP):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "QueryDeadlockError" not in text:
            continue
        lines = text.split("\n")
        tree = ast.parse(text, filename=rel)
        owner = {}
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(function):
                    owner[id(child)] = function.name
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _names_deadlock(node.type):
                line = lines[node.lineno - 1]
                reason = line.split(MARK, 1)[1].strip() if MARK in line else registry.get((rel, owner.get(id(node), "")))
                out.append((rel, node.lineno, reason))
    return out


def stale_registry(root: Path = ROOT, registry=None):
    """登记了、却已找不到那一处只认死锁的 ``except`` 的条目。"""
    registry = reviewed(root / "scripts" / "conflict-catch-reviewed.tsv") if registry is None else registry
    found = set()
    for rel, _line, _reason in findings(root, {}):
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    isinstance(n, ast.ExceptHandler) and _names_deadlock(n.type) for n in ast.walk(function)):
                found.add((rel, function.name))
    return [key for key in registry if key not in found]


def main(argv) -> int:
    found = findings()
    if "--list" in argv:
        for rel, line, reason in found:
            print(f"{rel}:{line}  {reason or '（未标注）'}")
        return 0
    bad = [(rel, line) for rel, line, reason in found if not reason]
    for rel, function in stale_registry():
        print(f"scripts/conflict-catch-reviewed.tsv: {rel} {function} 已找不到只认死锁的 except，删掉这条登记")
        bad.append((rel, 0))
    for rel, line in [item for item in bad if item[1]]:
        print(f"{rel}:{line}: 只认 QueryDeadlockError——关隔离后同一种冲突还会报保存前比对。入口层用 conflicts.attempt / retried，"
              f"保存点与后台用 conflicts.is_conflict(error)；确实只该认死锁的，在这一行标「{MARK} 原因」")
    print(f"check-conflict-catch: {len(found)} 处只认死锁，未标注 {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
