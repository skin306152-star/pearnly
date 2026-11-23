#!/usr/bin/env python3
"""拦「同一张表：没加锁读出来，再把算好的值写回去」（11-23，MariaDB 关快照隔离）。

起因：MariaDB 11.8 默认 ``innodb_snapshot_isolation=ON``，同一瞬间取号 30 张 29 张报 1020，按条件批量改、先读后加锁也撞。
11-23 起所有 MariaDB 加 ``--innodb-snapshot-isolation=OFF``（回到 11.4 及以前 / MySQL 的行为）。代价只有一类：
一笔事务先**普通读**（不加锁）某张表的行，再把由读出的值算出来的结果**写回同一张表**——ON 时别人在中间改过这行会报 1020，
OFF 时静默覆盖别人刚写的（丢更新）。这类写法要么改成加锁读（``for_update=True``）/ 相对更新（``SET x = x + %s``），
要么确认「后写的为准」是本意，登记到 ``scripts/unlocked-overwrite-reviewed.tsv``（文件、函数、写的表、原因；不为登记去改业务文件），
或在写的那一行（调用的任一行，或紧挨着的上一行）写 ``# overwrite-ok: 原因``。登记了却已对不上（代码改了、删了）的条目报「登记过期」，同一提交删掉。

算作一处：同一个函数里
- 读：``frappe.db.get_value / get_values / get_all / get_list / get_single_value``、``frappe.get_doc / get_cached_doc /
  get_all / get_list / get_cached_value``（没有 ``for_update=True``），或 ``frappe.db.sql("SELECT … FROM `tabX` …")``
  （没有 ``FOR UPDATE`` / ``LOCK IN SHARE MODE``）；读出的变量顺着赋值往下传（含 for 循环变量）。
- 写：``frappe.db.set_value(X, …)``、读出的文档 ``doc.db_set(…)``、``frappe.db.sql("UPDATE `tabX` SET … %s …")``，
  且写入的值用到了从**同一张表 X** 读出的变量。
不算：``doc.save()``（Frappe 保存前用加锁读比对修改时间，改晚了报 TimestampMismatchError）；patches（迁移时单独执行）；
tests。表名写成模块常量（``CARD = "Cowork Card"``）的按常量值认。这是启发式：漏掉的（跨函数传值、动态表名）靠评审。

    python3 scripts/check-unlocked-overwrite.py          # 检查：有没标注的就失败
    python3 scripts/check-unlocked-overwrite.py --list   # 列出全部（含已标注的）

只读；不连网络。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_PARTS = {"node_modules", "tests", "patches", "__pycache__"}
MARK = "overwrite-ok:"
REVIEWED = ROOT / "scripts" / "unlocked-overwrite-reviewed.tsv"
READS = {"get_value", "get_values", "get_all", "get_list", "get_doc", "get_cached_doc", "get_cached_value",
         "get_single_value"}
SELECT = re.compile(r"^\W*select\b.*?\bfrom\s+`tab([^`]+)`", re.I | re.S)
LOCKED = re.compile(r"for\s+update|lock\s+in\s+share\s+mode", re.I)
UPDATE = re.compile(r"^\W*update\s+`tab([^`]+)`\s+set\s+(.*?)(?:\bwhere\b|$)", re.I | re.S)


def qual(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def locked_kw(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "for_update":
            return not (isinstance(keyword.value, ast.Constant) and not keyword.value.value)
    return False


def sql_text(call: ast.Call) -> str:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    if call.args and isinstance(call.args[0], ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in call.args[0].values)
    return ""


class Module:
    def __init__(self, tree: ast.Module):
        self.constants = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.constants[target.id] = node.value.value

    def doctype(self, node) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            # 认不出值的常量（从别的模块转来的 ``COLUMN = access.COLUMN``）按名字认：读与写用同一个名字就是同一张表
            return self.constants.get(node.id) or f"${node.id}"
        if isinstance(node, ast.Attribute) and node.attr.isupper():
            return f"${node.attr}"
        return None

    def read_doctype(self, call: ast.Call) -> str | None:
        """没加锁的读：返回读的表名；不是读、加了锁、表名认不出返回 None。"""
        name = qual(call.func)
        if not name.startswith("frappe"):
            return None
        last = name.rsplit(".", 1)[-1]
        if last == "sql" and name in ("frappe.db.sql",):
            text = sql_text(call)
            match = SELECT.search(text)
            return match.group(1) if match and "{" not in match.group(1) and not LOCKED.search(text) else None
        if last in READS and call.args and not locked_kw(call):
            return self.doctype(call.args[0])
        return None


def names(node) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def scan_function(fn, module: Module):
    statements = [n for n in ast.walk(fn) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For,
                                                             ast.comprehension))]
    tainted: dict[str, set[str]] = {}
    changed = True
    while changed:
        changed = False
        for node in statements:
            value = node.iter if isinstance(node, (ast.For, ast.comprehension)) else node.value
            if value is None:
                continue
            target = node.target if hasattr(node, "target") else ast.Tuple(elts=node.targets, ctx=ast.Store())
            sources = set()
            for call in (c for c in ast.walk(value) if isinstance(c, ast.Call)):
                doctype = module.read_doctype(call)
                if doctype:
                    sources.add(doctype)
            for used in names(value) & tainted.keys():
                sources |= tainted[used]
            for name in names(target):
                if sources - tainted.get(name, set()):
                    tainted[name] = tainted.get(name, set()) | sources
                    changed = True
    for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
        name = qual(call.func)
        written, values = None, []
        if name == "frappe.db.set_value" and call.args:
            written = module.doctype(call.args[0])
            values = list(call.args[2:]) + [k.value for k in call.keywords if k.arg not in ("update_modified",)]
        elif name.endswith(".db_set") and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
            owner = call.func.value.id
            if owner in tainted:
                written = owner  # 按文档变量认：它读自哪些表
                values = list(call.args[1:]) if len(call.args) > 1 else list(call.args)
                values += [k.value for k in call.keywords if k.arg not in ("update_modified", "notify", "commit")]
        elif name == "frappe.db.sql":
            match = UPDATE.search(sql_text(call))
            if match and "{" not in match.group(1) and ("%s" in match.group(2) or "{}" in match.group(2) or "%(" in match.group(2)):
                written = match.group(1)
                values = list(call.args[1:])
        if not written or not values:
            continue
        targets = tainted[written] if written in tainted and name.endswith(".db_set") else {written}
        hit = sorted(n for n in set().union(*(names(v) for v in values)) & tainted.keys() if tainted[n] & targets)
        if hit:
            yield call, sorted(targets)[0], hit


def marked(lines: list[str], call: ast.Call) -> bool:
    end = getattr(call, "end_lineno", call.lineno)
    return any(MARK in lines[i - 1] for i in range(call.lineno - 1, end + 1) if 0 < i <= len(lines))


def scan(root: Path = ROOT):
    found = []
    for path in sorted((root / "apps").rglob("*.py")):
        rel = path.relative_to(root)
        if SKIP_PARTS & set(rel.parts) or path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        module, lines = Module(tree), text.splitlines()
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for call, doctype, used in scan_function(fn, module):
                found.append({"path": rel.as_posix(), "line": call.lineno, "function": fn.name, "doctype": doctype,
                              "names": used, "marked": marked(lines, call)})
    unique = {(f["path"], f["line"]): f for f in found}  # 嵌套函数会被外层再扫一遍
    registry = reviewed()
    for item in unique.values():
        item["registered"] = (item["path"], item["function"], item["doctype"]) in registry
        item["marked"] = item["marked"] or item["registered"]
    return sorted(unique.values(), key=lambda f: (f["path"], f["line"]))


def reviewed(path: Path | None = None) -> dict:
    """登记表：``{(文件, 函数, 表): 原因}``。"""
    path, out = path or REVIEWED, {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 4 or not parts[3].strip():
            raise SystemExit(f"{path.name}: 每行四列（文件、函数、表、原因）：{line!r}")
        out[tuple(parts[:3])] = parts[3]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    found = scan()
    registry = reviewed()
    stale = sorted(set(registry) - {(f["path"], f["function"], f["doctype"]) for f in found})
    for path, function, doctype in stale:
        print(f"STALE {path} {function} -> {doctype}：登记过期（代码里已经没有这一处），同一提交从 {REVIEWED.name} 删掉")
    bad = [f for f in found if not f["marked"]]
    for item in (found if args.list else bad):
        flag = "ok " if item["marked"] else "NEW"
        print(f"{flag} {item['path']}:{item['line']} {item['function']} -> {item['doctype']} ({', '.join(item['names'])})")
    if stale and not bad:
        return 1
    if bad:
        print(f"FAIL {len(bad)} 处：同一张表没加锁读出、再写回算好的值。改成 for_update=True 加锁读或相对更新，"
              f"或确认后写的为准是本意并在写的那一行标 `# {MARK} 原因`。", file=sys.stderr)
        return 1
    print(f"ok 未加锁读后写回：{len(found)} 处均已标注")
    return 0


if __name__ == "__main__":
    sys.exit(main())
