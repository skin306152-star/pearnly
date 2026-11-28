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
tests。表名写成模块常量（``CARD = "Cowork Card"``）的按常量值认。

11-28（B ③）补三类，输出里分别标 ``[借函数]`` ``[跨函数]`` ``[判断]``（同样可登记、可标 ``overwrite-ok``）：
- **借函数读**：读不在本函数里，而是调了一个「返回普通读结果」的函数（同模块，或按 import 认出的本仓模块），如
  ``tasks._own(for_update=True)``——锁了行名、却用普通 ``get_doc`` 读出整份返回；拿它的值写回同一张表。
- **跨函数传值**：本函数普通读出的值（含借函数读的）当参数传给另一个函数，那个函数把这个参数写进同一张表（``set_value`` /
  SQL ``UPDATE`` / 给读出的文档赋值后保存），如档位 ``on_readback`` 读旧档位 → ``_set(company, tier)``。
- **判断用旧值**：写进去的是常量或别处来的值，但「写不写」由同一张表的普通读决定（写在 ``if`` 里，或前面的 ``if`` 提前
  ``return`` / ``continue``），如「还指着这条推送记录才清空」「排过欢迎信就不再排」。也认 ``frappe.db.delete``、``frappe.delete_doc``。
仍是启发式：动态表名、经类方法或回调传递的漏掉，靠评审。

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
# 11-28（B ③）三类新写法（``--extended``）：确认是误报的登记在 EXT_REVIEWED（文件、函数、表、类别、原因）；还没修的存量在
# BASELINE（文件、函数、表、类别、归属、初判、原因；初判只许「真」「未判」，误报不进基线）。两张都只许删不许无理由加。
EXT_REVIEWED = ROOT / "scripts" / "unlocked-overwrite-extended-reviewed.tsv"
BASELINE = ROOT / "scripts" / "unlocked-overwrite-baseline.tsv"
OWNERS = {"②", "④", "随各自改动"}
VERDICTS = {"真", "未判"}
READS = {"get_value", "get_values", "get_all", "get_list", "get_doc", "get_cached_doc", "get_cached_value",
         "get_single_value"}
SELECT = re.compile(r"^\W*select\b.*?\bfrom\s+`tab([^`]+)`", re.I | re.S)
LOCKED = re.compile(r"for\s+update|lock\s+in\s+share\s+mode", re.I)
CONSTANT = re.compile(r'^([A-Z][A-Z0-9_]*)\s*=\s*"([^"\n]*)"\s*(?:#.*)?$', re.M)
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

    resolver = None  # 11-28：`Index` 装上后按 import 认出别的模块里的表名常量（``tasks.DOCTYPE``、``from x import CARD``）

    def doctype(self, node) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if self.resolver is not None:
            found = self.resolver(node)
            if found:
                return found
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


_NAMES: dict[int, frozenset] = {}
_WALKED: dict[int, list] = {}


def walked(node) -> list:
    """``ast.walk`` 的结果按节点缓存（同一棵树全仓要扫好几遍）。"""
    if not hasattr(node, "lineno"):
        return list(ast.walk(node))
    key = id(node)
    if key not in _WALKED:
        _WALKED[key] = list(ast.walk(node))
    return _WALKED[key]


_CALLS: dict[int, tuple] = {}


def names(node) -> set[str]:
    """子树里的名字：由子节点的结果合并（按节点缓存，整棵树只走一遍）。"""
    temporary = not hasattr(node, "lineno")  # 循环里临时拼的目标元组：用完即回收、id 会被复用，不进缓存
    key = id(node)
    found = None if temporary else _NAMES.get(key)
    if found is None:
        own = {node.id} if isinstance(node, ast.Name) else set()
        for child in ast.iter_child_nodes(node):
            own |= names(child)
        found = frozenset(own)
        if not temporary:
            _NAMES[key] = found
    return found


def calls_in(node) -> tuple:
    """子树里的调用（同 :func:`names`，按节点缓存）。"""
    temporary = not hasattr(node, "lineno")
    key = id(node)
    found = None if temporary else _CALLS.get(key)
    if found is None:
        own = [node] if isinstance(node, ast.Call) else []
        for child in ast.iter_child_nodes(node):
            own.extend(calls_in(child))
        found = tuple(own)
        if not temporary:
            _CALLS[key] = found
    return found


def scan_function(fn, module: Module):
    statements = [n for n in walked(fn) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For,
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
            for call in calls_in(value):
                doctype = module.read_doctype(call)
                if doctype:
                    sources.add(doctype)
            for used in names(value) & tainted.keys():
                sources |= tainted[used]
            for name in names(target):
                if sources - tainted.get(name, set()):
                    tainted[name] = tainted.get(name, set()) | sources
                    changed = True
    for call in calls_in(fn):
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


# ── 11-28（B ③）：借函数读、跨函数传值、判断用旧值 ─────────────────────────────────────────────────────────

DOC_CALLS = {"get_doc", "new_doc", "read", "get_cached_doc", "get_document"}
SAVES = {"save", "insert", "db_update"}
WRITE_HELPERS = {"write", "persist", "save", "store"}  # ``write(doc)`` 这类「存这份文档」的薄包装
DELETES = {"frappe.db.delete", "frappe.delete_doc"}


def module_name(rel: Path) -> str:
    """``apps/<app>/<app>/x/y.py`` → ``<app>.x.y``。"""
    parts = rel.with_suffix("").parts
    parts = parts[2:] if len(parts) > 2 and parts[0] == "apps" else parts
    return ".".join(p for p in parts if p != "__init__")


class Index:
    """全仓的函数摘要：谁「返回普通读的结果」（读了哪张表）、谁「把哪个参数写进哪张表」。按名字解析同模块与 import 进来的本仓函数。"""

    def __init__(self):
        self.functions: dict[str, ast.FunctionDef] = {}
        self.modules: dict[str, tuple[Module, dict]] = {}  # 模块名 → (常量, import 别名表)
        self.home: dict[str, str] = {}  # 函数全名 → 模块名
        self.returns: dict[str, set] = {}
        self.writes: dict[str, dict] = {}
        self.flows: dict[int, "Flow"] = {}

    def add(self, name: str, tree: ast.Module):
        aliases = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        aliases[alias.asname] = alias.name
        module = Module(tree)
        module.resolver = lambda node, aliases=aliases: self.constant(node, aliases)
        self.modules[name] = (module, aliases)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[f"{name}.{node.name}"] = node
                self.home[f"{name}.{node.name}"] = name

    def add_constants(self, name: str, constants: dict):
        module = Module(ast.Module(body=[], type_ignores=[]))
        module.constants = constants
        self.modules[name] = (module, {})

    def constant(self, node, aliases) -> str | None:
        """别的本仓模块里的字符串常量：``mod.NAME``（``mod`` 是 import 进来的模块）或 ``from mod import NAME``。"""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            owner = self.modules.get(aliases[node.value.id])
            return owner[0].constants.get(node.attr) if owner else None
        if isinstance(node, ast.Name) and node.id in aliases:
            module, _, attr = aliases[node.id].rpartition(".")
            owner = self.modules.get(module)
            return owner[0].constants.get(attr) if owner else None
        return None

    def resolve(self, module: str, func) -> str | None:
        _consts, aliases = self.modules[module]
        if isinstance(func, ast.Name):
            if f"{module}.{func.id}" in self.functions:
                return f"{module}.{func.id}"
            target = aliases.get(func.id)
            return target if target in self.functions else None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = aliases.get(func.value.id)
            target = f"{base}.{func.attr}" if base else None
            return target if target in self.functions else None
        return None

    def build(self, rounds: int = 30):
        """摘要按调用关系传到不动点：一个函数的摘要变了，只重算调它的函数（每个函数最多算 ``rounds`` + 1 遍）。"""
        callers: dict[str, set] = {}
        for full, fn in self.functions.items():
            module = self.home[full]
            for call in calls_in(fn):
                target = self.resolve(module, call.func)
                if target:
                    callers.setdefault(target, set()).add(full)
        # 先算被调的、再算调它的（后序；pending 从尾部取，所以倒过来放），少走几遍重排
        callees: dict[str, set] = {}
        for target, sources in callers.items():
            for source in sources:
                callees.setdefault(source, set()).add(target)
        order, seen = [], set()
        for root in self.functions:
            stack = [(root, iter(sorted(callees.get(root, ()))))]
            if root in seen:
                continue
            seen.add(root)
            while stack:
                node, children = stack[-1]
                child = next(children, None)
                if child is None:
                    stack.pop()
                    order.append(node)
                elif child not in seen:
                    seen.add(child)
                    stack.append((child, iter(sorted(callees.get(child, ())))))
        pending, counts = order[::-1], {}
        queued = set(pending)
        while pending:
            full = pending.pop()
            queued.discard(full)
            counts[full] = counts.get(full, 0) + 1
            flow = Flow(self.functions[full], self, self.home[full])
            self.flows[id(self.functions[full])] = flow  # 最后一次算的就是不动点上的（摘要再变会重排它）
            returns, writes = flow.returned(), flow.param_writes()
            if returns != self.returns.get(full, set()) or writes != self.writes.get(full, {}):
                self.returns[full], self.writes[full] = returns, writes
                for caller in callers.get(full, ()):
                    if caller not in queued and counts.get(caller, 0) <= rounds:
                        pending.append(caller)
                        queued.add(caller)


KEY_CALLS = READS | DOC_CALLS | {"sql", "exists", "count", "get_document", "_own", "_card", "_record", "_row"}


_VALUE_NAMES: dict[int, frozenset] = {}


def value_names(node) -> set[str]:
    """表达式里的名字，但不算只当主键传进读调用的（``get_doc(X, name)`` 里的 ``name``：拿它找行，不是拿它的值去写）。"""
    if not hasattr(node, "lineno"):
        return frozenset(_value_names(node))
    key = id(node)
    if key not in _VALUE_NAMES:
        _VALUE_NAMES[key] = frozenset(_value_names(node))
    return _VALUE_NAMES[key]


def _value_names(node) -> set[str]:
    out, stack = set(), [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Call) and qual(current.func).rsplit(".", 1)[-1] in KEY_CALLS:
            stack.append(current.func)
            continue
        if isinstance(current, ast.Attribute) and current.attr == "name":
            continue  # ``row.name``：行号是主键，不是读出来要写回的值
        if (isinstance(current, ast.Subscript) and isinstance(current.slice, ast.Constant)
                and current.slice.value == "name"):
            continue
        if isinstance(current, ast.Name):
            out.add(current.id)
        stack.extend(ast.iter_child_nodes(current))
    return out


def _targets(node):
    """赋值的各个目标（展开元组）。"""
    for target in getattr(node, "targets", None) or [getattr(node, "target", None)]:
        if isinstance(target, (ast.Tuple, ast.List)):
            yield from target.elts
        elif target is not None:
            yield target


def _parents(fn):
    parents = {}
    for node in walked(fn):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _terminates(body) -> bool:
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))


class Flow:
    """一个函数里的值从哪些表的普通读来（``direct``：本函数自己读的；``helper``：调了返回普通读结果的函数）。"""

    _static: dict[int, dict] = {}

    def __init__(self, fn, index: Index, module: str):
        self.fn, self.index, self.module = fn, index, module
        self.consts = index.modules[module][0]
        cache = Flow._static.setdefault(id(fn), {})
        if "direct" not in cache:
            cache["direct"] = self._taint(use_helpers=False)
        self.direct = cache["direct"]
        self.helper = self._taint(use_helpers=True) if self._calls_known() else self.direct
        self._cache = cache

    def _calls_known(self) -> bool:
        """本函数调了全仓里「返回普通读结果」的函数没有（没调就和只看本函数的一样，省一遍传播）。"""
        cache = Flow._static.setdefault(id(self.fn), {})
        if "targets" not in cache:
            cache["targets"] = [self.index.resolve(self.module, c.func) for c in calls_in(self.fn)]
        return any(target and self.index.returns.get(target) for target in cache["targets"])

    def _sources(self, value, use_helpers):
        found = set()
        for call in calls_in(value):
            doctype = self.consts.read_doctype(call)
            if doctype:
                found.add(doctype)
            elif use_helpers:
                # 带 ``for_update=True`` 调用也照样看它返回的是不是普通读（``tasks._own`` 锁了行名、整份仍是普通 get_doc）
                target = self.index.resolve(self.module, call.func)
                if target:
                    found |= self.index.returns.get(target, set())
        return found

    def _taint(self, use_helpers):
        cache = Flow._static.setdefault(id(self.fn), {})
        if "reads" not in cache:
            cache["reads"] = any(self.consts.read_doctype(c) for c in calls_in(self.fn))
        if not cache["reads"] and not (use_helpers and self._calls_known()):
            return {}  # 没有普通读、也没调到返回普通读结果的函数：什么都染不上
        statements = [n for n in walked(self.fn) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For,
                                                                     ast.comprehension, ast.withitem))]
        tainted: dict[str, set] = {}
        changed = True
        while changed:
            changed = False
            for node in statements:
                if isinstance(node, ast.withitem):
                    value, target = node.context_expr, node.optional_vars
                    if target is None:
                        continue
                else:
                    value = node.iter if isinstance(node, (ast.For, ast.comprehension)) else node.value
                    target = node.target if hasattr(node, "target") else ast.Tuple(elts=node.targets, ctx=ast.Store())
                if value is None:
                    continue
                sources = self._sources(value, use_helpers)
                for used in names(value) & tainted.keys():
                    sources |= tainted[used]
                for name in names(target):
                    if sources - tainted.get(name, set()):
                        tainted[name] = tainted.get(name, set()) | sources
                        changed = True
        return tainted

    def tables_of(self, node, tainted) -> set:
        out = self._sources(node, tainted is self.helper)
        for name in names(node) & tainted.keys():
            out |= tainted[name]
        return out

    def returned(self) -> set:
        """``return`` 出去的值来自哪些表的普通读（含借函数读的）。"""
        out = set()
        for node in walked(self.fn):
            if isinstance(node, ast.Return) and node.value is not None:
                out |= self.tables_of(node.value, self.helper)
        return out

    def docs(self) -> dict:
        if "docs" not in self._cache:
            self._cache["docs"] = self._docs()
        return self._cache["docs"]

    def _docs(self) -> dict:
        """文档变量 → 表：``doc = frappe.get_doc(X, …)`` / ``new_doc(X)`` / ``read(X, …)``，且本函数里存了它。"""
        found = {}
        for node in walked(self.fn):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Call) and node.value.args):
                last = qual(node.value.func).rsplit(".", 1)[-1]
                doctype = self.consts.doctype(node.value.args[0]) if last in DOC_CALLS else None
                if doctype:
                    found[node.targets[0].id] = doctype
        saved = set()
        for call in calls_in(self.fn):
            if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.attr in SAVES:
                saved.add(call.func.value.id)
            elif isinstance(call.func, ast.Name) and call.func.id in WRITE_HELPERS and call.args \
                    and isinstance(call.args[0], ast.Name):
                saved.add(call.args[0].id)
        return {name: doctype for name, doctype in found.items() if name in saved}

    def writes(self):
        if "writes" not in self._cache:
            self._cache["writes"] = list(self._writes())
        return self._cache["writes"]

    def _writes(self):
        """本函数里的写：``(调用或赋值节点, 表, 写进去的值表达式列表)``。"""
        docs = self.docs()
        for node in walked(self.fn):
            if isinstance(node, ast.Call):
                name = qual(node.func)
                if name == "frappe.db.set_value" and node.args:
                    doctype = self.consts.doctype(node.args[0])
                    if doctype:
                        yield node, doctype, list(node.args[2:]) + [k.value for k in node.keywords
                                                                     if k.arg not in ("update_modified",)]
                elif name == "frappe.db.sql":
                    match = UPDATE.search(sql_text(node))
                    if match and "{" not in match.group(1):
                        yield node, match.group(1), list(node.args[1:])
                elif name in DELETES and node.args:
                    doctype = self.consts.doctype(node.args[0])
                    if doctype:
                        yield node, doctype, []
                elif (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                      and node.func.value.id in docs and node.func.attr in ("update", "set", "db_set", "append")):
                    yield node, docs[node.func.value.id], list(node.args) + [k.value for k in node.keywords]
            elif isinstance(node, ast.Assign):
                for target in _targets(node):
                    if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                            and target.value.id in docs):
                        yield node, docs[target.value.id], [node.value]
                        break

    def param_writes(self) -> dict:
        """参数 → 它（顺着赋值）被写进的表。也顺着调用传给别的写参数的函数。"""
        if "flows" not in self._cache:
            self._cache["flows"] = self._param_flows()
        flows = self._cache["flows"]
        if "own_writes" not in self._cache:
            own = {}
            for _node, doctype, values in self.writes():
                used = set().union(*(value_names(v) for v in values)) if values else set()
                for param, carried in flows.items():
                    if used & carried:
                        own.setdefault(param, set()).add(doctype)
            self._cache["own_writes"] = own
        out = {param: set(tables) for param, tables in self._cache["own_writes"].items()}
        for call in calls_in(self.fn):
            target = self.index.resolve(self.module, call.func)
            if not target or not self.index.writes.get(target):
                continue
            for arg_param, arg in _bound(self.index.functions[target], call):
                tables = self.index.writes[target].get(arg_param)
                if not tables:
                    continue
                for param, carried in flows.items():
                    if value_names(arg) & carried:
                        out.setdefault(param, set()).update(tables)
        return out

    def _param_flows(self) -> dict:
        params = [a.arg for a in self.fn.args.args + self.fn.args.kwonlyargs]
        flows = {p: {p} for p in params}
        statements = [n for n in walked(self.fn) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For))]
        changed = True
        while changed:
            changed = False
            for node in statements:
                value = node.iter if isinstance(node, ast.For) else node.value
                if value is None:
                    continue
                target = node.target if hasattr(node, "target") else ast.Tuple(elts=node.targets, ctx=ast.Store())
                used = value_names(value)
                for param, carried in flows.items():
                    if used & carried:
                        new = names(target) - carried
                        if new:
                            carried |= new
                            changed = True
        return flows


def _bound(fn, call):
    params = [a.arg for a in fn.args.args]
    for position, arg in enumerate(call.args):
        if isinstance(arg, ast.Starred) or position >= len(params):
            break
        yield params[position], arg
    for keyword in call.keywords:
        if keyword.arg:
            yield keyword.arg, keyword.value


def scan_extended(fn, index: Index, module: str, base_hits=None):
    """三类新写法：``(节点, 表, 用到的名字, 类别)``。只报同一函数里基础检查（:func:`scan_function`）没报的。"""
    flow = index.flows.get(id(fn)) or Flow(fn, index, module)
    base = {(call.lineno, doctype) for call, doctype, _used in (base_hits if base_hits is not None
                                                                else scan_function(fn, flow.consts))}
    seen = set()

    def hit(node, doctype, used, kind):
        key = (node.lineno, doctype)
        if key in base or key in seen:
            return None
        seen.add(key)
        return node, doctype, sorted(used), kind

    # 借函数读：写回的值来自「返回普通读结果」的函数
    for node, doctype, values in flow.writes():
        if not values or isinstance(node, ast.Assign):
            continue  # 给读出的文档赋值后保存：保存前比对兜底，不算
        used = {n for n in set().union(*(names(v) for v in values)) if doctype in flow.helper.get(n, set())}
        used |= {"(调用)"} if any(doctype in flow._sources(v, True) for v in values) else set()
        if used:
            found = hit(node, doctype, used, "借函数")
            if found:
                yield found
    # 跨函数传值
    for call in calls_in(fn):
        target = index.resolve(module, call.func)
        if not target or not index.writes.get(target):
            continue
        for param, arg in _bound(index.functions[target], call):
            tables = index.writes[target].get(param, set())
            stale = flow.tables_of(arg, flow.helper) & tables
            for doctype in sorted(stale):
                used = {n for n in names(arg) if doctype in flow.helper.get(n, set())} or {"(调用)"}
                found = hit(call, doctype, used, "跨函数")
                if found:
                    yield found
    # 判断用旧值（父节点表只在有写、且读得到旧值时才算）
    writes = flow.writes()
    if not writes:
        return
    parents = _parents(fn)
    for node, doctype, _values in writes:
        if isinstance(node, ast.Assign):
            continue
        chain, current = [], node
        while current in parents:
            chain.append(current)
            current = parents[current]
        tests = []
        for child in chain:
            parent = parents.get(child)
            if isinstance(parent, ast.If) and child is not parent.test:
                tests.append(parent.test)
            for field in ("body", "orelse", "finalbody"):
                block = getattr(parent, field, None) if parent is not None else None
                if isinstance(block, list) and child in block:
                    for sibling in block[:block.index(child)]:
                        if isinstance(sibling, ast.If) and (_terminates(sibling.body) or _terminates(sibling.orelse)):
                            tests.append(sibling.test)
        for test in tests:
            if doctype in flow.tables_of(test, flow.helper):
                used = {n for n in names(test) if doctype in flow.helper.get(n, set())} or {"(调用)"}
                found = hit(node, doctype, used, "判断")
                if found:
                    yield found
                break


def marked(lines: list[str], call: ast.Call) -> bool:
    end = getattr(call, "end_lineno", call.lineno)
    return any(MARK in lines[i - 1] for i in range(call.lineno - 1, end + 1) if 0 < i <= len(lines))


def scan(root: Path = ROOT, *, extended: bool = False):
    # 缓存按节点 id 记：上一次扫描的树释放后 id 会被新节点复用，每次扫描先清空（code-review 11-28）
    for cache in (_NAMES, _CALLS, _WALKED, _VALUE_NAMES, Flow._static):
        cache.clear()
    found, parsed, index = [], [], Index()
    for path in sorted((root / "apps").rglob("*.py")):
        rel = path.relative_to(root)
        if SKIP_PARTS & set(rel.parts) or path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        if "frappe" not in text and "pearnly_" not in text and "from ." not in text:
            # 不碰 Frappe、也不调本仓别的模块的（纯规则、契约）读写不了库、也转不到写库的函数：不做语法分析，
            # 只记顶层字符串常量（表名常量常放在这里）
            index.add_constants(module_name(rel), dict(CONSTANT.findall(text)))
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        parsed.append((rel, tree, text.splitlines()))
        index.add(module_name(rel), tree)
    if extended:
        index.build()
    for rel, tree, lines in parsed:
        module, name = index.modules[module_name(rel)][0], module_name(rel)
        for fn in (n for n in walked(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            base_hits = list(scan_function(fn, module))
            for call, doctype, used in base_hits:
                found.append({"path": rel.as_posix(), "line": call.lineno, "function": fn.name, "doctype": doctype,
                              "names": used, "marked": marked(lines, call), "kind": ""})
            if not extended:
                continue
            for node, doctype, used, kind in scan_extended(fn, index, name, base_hits):
                found.append({"path": rel.as_posix(), "line": node.lineno, "function": fn.name, "doctype": doctype,
                              "names": used, "marked": marked(lines, node), "kind": kind})
    unique = {(f["path"], f["line"]): f for f in found}  # 嵌套函数会被外层再扫一遍
    registry = reviewed()
    ext_registry = keyed(EXT_REVIEWED, 5) if extended else {}
    baseline = keyed(BASELINE, 7) if extended else {}
    for item in unique.values():
        if item["kind"]:
            key = (item["path"], item["function"], item["doctype"], item["kind"])
            item["registered"] = key in ext_registry
            item["baseline"] = key in baseline
            item["marked"] = item["marked"] or item["registered"] or item["baseline"]
            continue
        item["registered"] = (item["path"], item["function"], item["doctype"]) in registry
        item["marked"] = item["marked"] or item["registered"]
    return sorted(unique.values(), key=lambda f: (f["path"], f["line"]))


def keyed(path: Path, columns: int) -> dict:
    """三类新写法的两张表：``{(文件, 函数, 表, 类别): 其余各栏}``。"""
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != columns or not parts[-1].strip():
            raise SystemExit(f"{path.name}: 每行 {columns} 列、最后一列原因不许空：{line!r}")
        if columns == 7 and (parts[4] not in OWNERS or parts[5] not in VERDICTS):
            raise SystemExit(f"{path.name}: 归属只能是 {sorted(OWNERS)}、初判只能是 {sorted(VERDICTS)}"
                             f"（误报不进基线，登记到 {EXT_REVIEWED.name}）：{line!r}")
        out[tuple(parts[:4])] = parts[4:]
    return out


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
    parser.add_argument("--extended", action="store_true",
                        help="另查三类新写法（借函数读、跨函数传值、判断用旧值），按误报登记与存量基线核")
    args = parser.parse_args(argv)
    found = scan(extended=args.extended)
    registry = reviewed()
    plain = [f for f in found if not f["kind"]]
    stale = sorted(set(registry) - {(f["path"], f["function"], f["doctype"]) for f in plain})
    for path, function, doctype in stale:
        print(f"STALE {path} {function} -> {doctype}：登记过期（代码里已经没有这一处），同一提交从 {REVIEWED.name} 删掉")
    if args.extended:
        seen = {(f["path"], f["function"], f["doctype"], f["kind"]) for f in found if f["kind"]}
        for table in (EXT_REVIEWED, BASELINE):
            for key in sorted(set(keyed(table, 5 if table is EXT_REVIEWED else 7)) - seen):
                stale.append(key)
                print(f"STALE [{key[3]}] {key[0]} {key[1]} -> {key[2]}：已经没有这一处（修好了就是基线减少），"
                      f"同一提交从 {table.name} 删掉")
    bad = [f for f in found if not f["marked"]]
    for item in (found if args.list else bad):
        flag = "ok " if item["marked"] else "NEW"
        kind = f"[{item['kind']}] " if item.get("kind") else ""
        print(f"{flag} {kind}{item['path']}:{item['line']} {item['function']} -> {item['doctype']} ({', '.join(item['names'])})")
    if stale and not bad:
        return 1
    if bad:
        print(f"FAIL {len(bad)} 处：同一张表没加锁读出、再写回算好的值。改成 for_update=True 加锁读或相对更新，"
              f"或确认后写的为准是本意并在写的那一行标 `# {MARK} 原因`。", file=sys.stderr)
        return 1
    if args.extended:
        extra = [f for f in found if f["kind"]]
        print(f"ok 未加锁读后写回：{len(plain)} 处均已标注；三类新写法 {len(extra)} 处，其中存量基线 "
              f"{sum(1 for f in extra if f.get('baseline'))} 处、误报登记 {sum(1 for f in extra if f.get('registered'))} 处，新增 0")
        return 0
    print(f"ok 未加锁读后写回：{len(found)} 处均已标注")
    return 0


if __name__ == "__main__":
    sys.exit(main())
