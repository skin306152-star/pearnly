"""排队/反射用的**点分路径字符串**必须真能落到文件与函数上。

2026-09-21 踩到过：文件从 `masters/warm.py` 挪到 `adapter/warm.py` 之后，
`frappe.enqueue("pearnly_dms.masters.warm.refresh")` 那串仍然指着旧位置 ——
代码能导入、架构检查全绿、镜像构建也成功，**只有运行到那一步才炸**
（后台 worker 里 ModuleNotFoundError，前台只看到"什么都没发生"）。

规则做成机械检查：扫 app 源码里像 `pearnly_dms.x.y.z` 的字符串，
凡出现在 `enqueue(...)` / `get_attr(...)` / `run_guarded(...)` 这类"交给别的进程解析"
的调用里，就要求：

1. 模块部分**在仓库里有对应文件**；
2. 末段**在那个文件里定义过**（函数 / 类 / 赋值）。

**故意不 import**：CI 的这一步没有 frappe，import 会误报；
而这个检查要挡的是"文件挪了、字符串没改"，纯 AST + 路径就够。
"""

import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "apps"

# 这些调用接受"点分路径字符串"，会在**另一个进程/另一时刻**才解析。
CALLS_TAKING_PATHS = {"enqueue", "get_attr", "run_guarded", "import_string", "resolve"}
SEGMENT = r"[A-Za-z_]\w*"
APPS_WITH_FRAPPE = ("dms", "core", "cowork", "erp_portal", "control")


def _app_roots() -> dict:
    """包名 → 包目录（`pearnly_dms` → `apps/pearnly_dms/pearnly_dms`）。"""
    roots = {}
    for app_dir in APPS.iterdir():
        if not app_dir.is_dir():
            continue
        for pkg in app_dir.iterdir():
            if pkg.is_dir() and pkg.name.startswith("pearnly_") and (pkg / "__init__.py").is_file():
                roots[pkg.name] = pkg
    return roots


def _defined_names(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _resolve(dotted: str, roots: dict):
    """点分路径 → (模块文件, 末段属性名)；找不到文件回 (None, 属性)。"""
    parts = dotted.split(".")
    attr = ""
    while parts:
        root = roots.get(parts[0])
        if root is None:
            return None, attr
        candidate = root.joinpath(*parts[1:])
        if candidate.with_suffix(".py").is_file():
            return candidate.with_suffix(".py"), attr
        if (candidate / "__init__.py").is_file():
            return candidate / "__init__.py", attr
        attr = parts.pop() + (("." + attr) if attr else "")
    return None, attr


class QueueTargets(unittest.TestCase):
    def test_literal_targets_resolve(self):
        pattern = re.compile(rf"^pearnly_({'|'.join(APPS_WITH_FRAPPE)})\.({SEGMENT}(?:\.{SEGMENT})*)$")
        roots = _app_roots()

        bad, checked = [], 0
        for path in sorted(APPS.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                bad.append(f"{path.relative_to(ROOT)}: 语法错误 {exc}")
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
                if name not in CALLS_TAKING_PATHS:
                    continue
                first = node.args[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                if not pattern.match(first.value):
                    continue
                checked += 1
                module_file, attr = _resolve(first.value, roots)
                where = f"{path.relative_to(ROOT)}:{node.lineno} {first.value}"
                if module_file is None:
                    bad.append(f"{where} → 仓库里找不到对应模块")
                    continue
                leaf = attr.split(".")[-1]
                if leaf and leaf not in _defined_names(module_file):
                    bad.append(f"{where} → {module_file.relative_to(ROOT)} 里没有定义 {leaf}")

        self.assertGreater(checked, 0, "一个排队目标都没扫到，说明扫描规则失效了")
        self.assertEqual([], bad, "排队目标解析不到：\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
