"""白名单接口有没有守卫的离线静态判断（回归基线 B 的接口权限矩阵，接进 CI 的那一半；10-27 主会话）。

接口权限矩阵（`scripts/regression/api_matrix.py`，回归实验室里按岗位账号真调）记下「调用过程中有没有调到守卫函数」；这里在
不起站点的前提下做同一件事的静态版：从每个 ``@frappe.whitelist()`` 接口出发，沿本仓代码的调用（按 import 解析到本仓函数，深度有限）
找有没有调到守卫——函数名同矩阵的口径（require / guard / has_permission / only_for / check / allowed / deny / permitted），
另把框架按权限拼条件的 ``frappe.get_list`` 算作守卫。找不到守卫的接口要么修，要么登记在
``tests/baseline/unguarded_endpoints.toml``（只许本人数据 / 公开信息，写明理由）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ("pearnly_core", "pearnly_erp_portal", "pearnly_cowork", "pearnly_dms", "pearnly_control")
GUARD = re.compile(r"(^|_)(require|guard|has_permission|only_for|check|allowed|deny|permitted|can_)|^get_list$")
DEPTH = 6


PACKAGES = set()


def _modules():
    out = {}
    for app in APPS:
        base = ROOT / "apps" / app
        for path in (base / app).rglob("*.py"):
            text = str(path)
            if path.name.startswith("test") or "/tests/" in text or "/patches/" in text:
                continue
            module = ".".join(path.relative_to(base).with_suffix("").parts)
            if module.endswith(".__init__"):
                module = module[: -len(".__init__")]
                PACKAGES.add(module)
            try:
                out[module] = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
    return out


class Index:
    def __init__(self):
        self.trees = _modules()
        self.functions = {}  # "模块.函数" / "模块.类.方法" → FunctionDef
        self.imports = {}  # 模块 → {本地名: 目标（模块或 模块.函数）}
        self.classes = {}  # "模块.类" → [基类表达式]
        self.instances = {}  # 模块 → {模块级变量: "模块.类"}（如 `TASKS = TaskStore(...)`，调用 `TASKS.detail` 落到类的方法）
        for module, tree in self.trees.items():
            names = {}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions[f"{module}.{node.name}"] = node
                elif isinstance(node, ast.ClassDef):
                    self.classes[f"{module}.{node.name}"] = node.bases
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            self.functions[f"{module}.{node.name}.{item.name}"] = item
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        names[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
                elif isinstance(node, ast.ImportFrom):
                    source = self._absolute(module, node.module, node.level)
                    for alias in node.names:
                        names[alias.asname or alias.name] = f"{source}.{alias.name}" if source else alias.name
            self.imports[module] = names
        for module, tree in self.trees.items():
            found = {}
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.targets) == 1 \
                        and isinstance(node.targets[0], ast.Name):
                    cls = self._class(module, node.value.func)
                    if cls:
                        found[node.targets[0].id] = cls
            self.instances[module] = found

    def _class(self, module, expr):
        """一个表达式指向的本仓类（「模块.类」），解析不了回 None。"""
        if isinstance(expr, ast.Name):
            local = f"{module}.{expr.id}"
            if local in self.classes:
                return local
            target = self.imports.get(module, {}).get(expr.id)
            return target if target in self.classes else None
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            head = self.imports.get(module, {}).get(expr.value.id)
            target = f"{head}.{expr.attr}" if head else None
            return target if target in self.classes else None
        return None

    def _method(self, cls, method, depth=0):
        """类的方法（找不到沿基类找）。"""
        if f"{cls}.{method}" in self.functions:
            return f"{cls}.{method}"
        if depth > 4:
            return None
        module = cls.rsplit(".", 1)[0]
        for base in self.classes.get(cls, []):
            parent = self._class(module, base)
            if parent:
                found = self._method(parent, method, depth + 1)
                if found:
                    return found
        return None

    def _absolute(self, module, target, level):
        if not level:
            return target or ""
        parts = module.split(".")
        if module not in PACKAGES:
            parts = parts[:-1]  # 普通模块：from . 指它所在的包
        parts = parts[: len(parts) - (level - 1)] if level > 1 else parts
        return ".".join(parts + ([target] if target else []))

    def whitelisted(self):
        """``{接口: (FunctionDef, 模块, allow_guest)}``。"""
        out = {}
        for name, node in self.functions.items():
            if name.rsplit(".", 1)[0] in self.classes:
                continue
            for deco in node.decorator_list:
                call = deco if isinstance(deco, ast.Call) else None
                func = call.func if call else deco
                if getattr(func, "attr", None) == "whitelist" or getattr(func, "id", None) == "whitelist":
                    guest = bool(call and any(k.arg == "allow_guest" and getattr(k.value, "value", False) is True
                                              for k in call.keywords))
                    out[name] = (node, name.rsplit(".", 1)[0], guest)
        return out

    def _resolve(self, module, func, cls=None):
        """一个调用的目标解析成本仓的「模块.函数」/「模块.类.方法」（解析不了回 None）。``cls``：所在的类（解析 ``self.x``）。"""
        names = self.imports.get(module, {})
        if (cls and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call)
                and getattr(func.value.func, "id", None) == "super"):
            for base in self.classes.get(cls, []):  # super().x(...)：落到基类的方法
                parent = self._class(module, base)
                found = parent and self._method(parent, func.attr)
                if found:
                    return found
            return None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in ("self", "cls") and cls:
                return self._method(cls, func.attr)
            instance = self.instances.get(module, {}).get(func.value.id)
            if instance:
                return self._method(instance, func.attr)
        if isinstance(func, ast.Name):
            if f"{module}.{func.id}" in self.functions:
                return f"{module}.{func.id}"
            target = names.get(func.id)
            return target if target in self.functions else None
        if isinstance(func, ast.Attribute):
            chain, value = [func.attr], func.value
            while isinstance(value, ast.Attribute):
                chain.insert(0, value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                head = names.get(value.id, value.id if f"{module}.{value.id}" not in self.functions else None)
                if head:
                    target = ".".join([head] + chain)
                    if target in self.functions:
                        return target
        return None

    def _home(self, name):
        """函数 / 方法所在的 (模块, 类)。"""
        head = name.rsplit(".", 1)[0]
        if head in self.classes:
            return head.rsplit(".", 1)[0], head
        return head, None

    def guarded(self, name, module=None, depth=DEPTH, seen=None) -> bool:
        seen = set() if seen is None else seen
        if name in seen or depth < 0:
            return False
        seen.add(name)
        module, cls = self._home(name)
        node = self.functions[name]
        for item in ast.walk(node):
            # 调用守卫函数；或者直接抛权限错（raise / frappe.throw(…, frappe.PermissionError)）
            if isinstance(item, ast.Call):
                label = getattr(item.func, "attr", None) or getattr(item.func, "id", None) or ""
                if GUARD.search(label):
                    return True
            if (isinstance(item, ast.Attribute) and item.attr == "PermissionError") or (
                    isinstance(item, ast.Name) and item.id == "PermissionError"):
                return True
            # 按点号路径字符串交出去的本仓函数（如导出 `exports.deliver(build="…output.workbook")`）
            if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value in self.functions:
                if item.value != name and self.guarded(item.value, depth=depth - 1, seen=seen):
                    return True
            # 沿本仓函数往下找：直接调用的，和当参数传进去的（如 `_call(service.detail, name)`）
            if isinstance(item, (ast.Name, ast.Attribute)) and isinstance(getattr(item, "ctx", None), ast.Load):
                target = self._resolve(module, item, cls)
                if target and target != name and self.guarded(target, depth=depth - 1, seen=seen):
                    return True
        # 装饰器里的守卫（如 @require_role(...)）
        for deco in node.decorator_list:
            label = getattr(getattr(deco, "func", deco), "attr", None) or getattr(getattr(deco, "func", deco), "id", None) or ""
            if GUARD.search(label):
                return True
        return False


def unguarded_endpoints():
    index = Index()
    return sorted(name for name, (node, module, guest) in index.whitelisted().items()
                  if not guest and not index.guarded(name))


if __name__ == "__main__":
    for name in unguarded_endpoints():
        print(name)
