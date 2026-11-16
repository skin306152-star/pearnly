"""列表查询只用锁定版 Frappe 收的参数（11-16 回归实验室：`history/removal.py` 给 ``frappe.get_all`` 传了 ``for_update=True``，
锁定的 Frappe 16.33 抛 ``TypeError: DatabaseQuery.execute() got an unexpected keyword argument 'for_update'``，CC 与录入页删单据全坏；
单测用的假库不报错，没拦住）。

全仓扫 ``apps/`` 的 Python：``get_all`` / ``get_list`` 调用不许带 ``for_update``。要锁行用 ``frappe.db.get_value(..., for_update=True)``、
``frappe.get_doc(..., for_update=True)`` 或原生 SQL ``FOR UPDATE``（本仓写法，见 `companion/credentials.py`）。只读源码，不连库。
"""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIST_CALLS = {"get_all", "get_list"}
UNSUPPORTED = {"for_update"}


def offenders(root=ROOT) -> list:
    found = []
    for path in sorted((root / "apps").rglob("*.py")):
        if "node_modules" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LIST_CALLS:
                bad = [k.arg for k in node.keywords if k.arg in UNSUPPORTED]
                if bad:
                    found.append(f"{path.relative_to(root)}:{node.lineno} {node.func.attr}({', '.join(bad)}=...)")
    return found


class ListQueryKwargs(unittest.TestCase):
    def test_no_list_query_passes_an_argument_the_locked_frappe_rejects(self):
        self.assertEqual(offenders(), [], "get_all / get_list 不收 for_update：改用 get_value / get_doc 的 for_update 或原生 SQL FOR UPDATE")

    def test_the_scan_catches_a_call_spread_over_lines(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / "apps" / "x").mkdir(parents=True)
            (root / "apps" / "x" / "m.py").write_text("import frappe\nrows = frappe.get_all(\"D\", filters={},\n"
                                                       "    for_update=True)\nok = frappe.db.get_value(\"D\", \"n\", \"f\", "
                                                       "for_update=True)\n", encoding="utf-8")
            self.assertEqual(offenders(root), ["apps/x/m.py:2 get_all(for_update=...)"])


if __name__ == "__main__":
    unittest.main()
