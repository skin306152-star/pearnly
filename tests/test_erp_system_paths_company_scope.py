"""系统路径不按当前账号核公司（10-30 生产事故：testerp 迁移在 v1_19 报「Not permitted」，ERP 停服约 9 分钟）。

迁移、定时任务、Express 同步以 Administrator（或排任务的人）跑，`masters.company` 里「按当前账号」的那几个函数
（`document_company` / `allowed_companies` / `active_company` / `scope` / `company_options`，不带 user 参数时）
只认他的可用范围——Administrator 只有启用的公司，testerp 有一家停用公司，补丁走到它就报错。

纯静态（ast，不要 Frappe）：从 hooks 里的系统入口（patches.txt、after_install / after_migrate、scheduler_events、
pearnly_cc_desktop、pearnly_tenant_ready、识别回调）出发，沿模块级函数调用（含函数内 import、字符串里的任务路径）往下走，
走到上面那几个函数就报出整条路径。靠参数或身份切换保证安全的调用边登记在 SAFE_EDGES（写明原因），只许减少。
"""
import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
PKG = "pearnly_erp_portal"
COMPANY = f"{PKG}.masters.company"
# 函数 → 「当前账号」参数的位置（没给就是按当前账号）
SESSION_SCOPED = {"document_company": 1, "allowed_companies": 0, "active_company": 0, "scope": 0, "company_options": 0}
HOOK_KEYS = ("after_install", "after_migrate", "scheduler_events", "pearnly_cc_desktop", "pearnly_tenant_ready",
             "pearnly_ocr_handlers", "pearnly_ocr_failure_handlers")
SAFE_EDGES = {
    ("express.masters._stock", "inventory.stock.apply_movement"):
        "传 system=True：公司按数据核（仓库属于这家公司），不走 _movement_company / company_of",
    ("express.push.requeue_unmapped", "inventory.warehouses.names_of"):
        "公司总是给了（同步那家），names_of 只在没给公司时才取当前公司",
    ("integrations.intake.run", "integrations.intake.totals"):
        "识别任务先 frappe.set_user(发单员工)，之后就是那位员工的用户路径",
}
DOTTED = re.compile(rf"{PKG}(\.\w+)+")


def _module(path):
    parts = list(path.relative_to(APP).with_suffix("").parts)
    return ".".join([PKG] + (parts[:-1] if parts[-1] == "__init__" else parts))


def _aliases(module, tree, fn):
    out = {}
    for node in list(tree.body) + list(ast.walk(fn)):
        if isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            if node.level:
                base = ".".join(module.split(".")[:-node.level] + [node.module])
            for alias in node.names:
                out[alias.asname or alias.name] = f"{base}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
    return out


def _target(call, aliases, module, funcs):
    func, chain = call.func, []
    while isinstance(func, ast.Attribute):
        chain.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return None
    head = func.id
    if not chain:
        return aliases.get(head) or (f"{module}.{head}" if f"{module}.{head}" in funcs else None)
    return f"{aliases[head]}.{'.'.join(reversed(chain))}" if head in aliases else None


def _session_scoped(name, call):
    short = name.rsplit(".", 1)[-1]
    if not name.startswith(COMPANY + ".") or short not in SESSION_SCOPED:
        return False
    position = SESSION_SCOPED[short]
    return len(call.args) <= position and not any(keyword.arg == "user" for keyword in call.keywords)


def build():
    trees, funcs = {}, set()
    for path in APP.rglob("*.py"):
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        module = _module(path)
        trees[module] = ast.parse(path.read_text(encoding="utf-8"))
        funcs.update(f"{module}.{node.name}" for node in trees[module].body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    edges, sinks = {}, {}
    for module, tree in trees.items():
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name, aliases, out = f"{module}.{fn.name}", _aliases(module, tree, fn), set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    target = _target(node, aliases, module, funcs)
                    if target and _session_scoped(target, node):
                        sinks.setdefault(name, set()).add(target)
                    elif target in funcs:
                        out.add(target)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and DOTTED.fullmatch(node.value):
                    if node.value in funcs:
                        out.add(node.value)
            edges[name] = out
    return funcs, edges, sinks


def entries(funcs):
    found = {f"{line.strip()}.execute" for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
             if line.strip().startswith(f"{PKG}.patches.")}
    tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in HOOK_KEYS for t in node.targets):
            found.update(sub.value for sub in ast.walk(node.value)
                         if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value in funcs)
    return found


def short(name):
    return name.removeprefix(PKG + ".")


class SystemPathsCompanyScope(unittest.TestCase):
    def setUp(self):
        self.funcs, self.edges, self.sinks = build()
        self.entries = entries(self.funcs)

    def test_entries_are_found(self):
        self.assertIn(f"{PKG}.patches.v1_19_terminal_main_warehouse.execute", self.entries)
        self.assertIn(f"{PKG}.express.masters.sync_all", self.entries)
        self.assertIn(f"{PKG}.access.setup.sync_roles", self.entries)

    def test_no_system_path_reaches_a_current_account_company_check(self):
        problems = []
        for entry in sorted(self.entries):
            stack, seen = [(entry, [entry])], {entry}
            while stack:
                name, path = stack.pop()
                for sink in sorted(self.sinks.get(name, ())):
                    problems.append(" → ".join(short(step) for step in path + [sink]))
                for target in sorted(self.edges.get(name, ())):
                    if target in seen or (short(name), short(target)) in SAFE_EDGES:
                        continue
                    seen.add(target)
                    stack.append((target, path + [target]))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_express_stock_sync_posts_as_a_system_path(self):
        tree = ast.parse((APP / "express/masters.py").read_text(encoding="utf-8"))
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_stock")
        calls = [node for node in ast.walk(fn) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "apply_movement"]
        self.assertTrue(calls)
        for call in calls:
            flags = {keyword.arg: getattr(keyword.value, "value", None) for keyword in call.keywords}
            self.assertEqual((flags.get("system"), flags.get("internal")), (True, True))

    def test_safe_edges_still_exist(self):
        for caller, callee in SAFE_EDGES:
            with self.subTest(edge=(caller, callee)):
                self.assertIn(f"{PKG}.{callee}", self.edges.get(f"{PKG}.{caller}", ()))


if __name__ == "__main__":
    unittest.main()
