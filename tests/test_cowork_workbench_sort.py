"""Cowork 录入工作台列表（history.api.workbench）：原生筛选条件与列头排序互不影响；看一份原件里的票时固定按页序。
验收 10-25：筛选条件的代码曾截走排序那一支，有条件时列头排序失效。"""
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))


class Row(dict):
    """框架回的行：键也能当属性读。"""
    __getattr__ = dict.get


class Workbench(unittest.TestCase):
    def setUp(self):
        self.calls = []
        frappe = types.ModuleType("frappe")
        frappe.get_list = lambda doctype, **kw: self.calls.append(kw) or []
        frappe.whitelist = lambda **kw: (lambda fn: fn)
        frappe.session = types.SimpleNamespace(user="a@x")
        frappe.cache = types.SimpleNamespace(get_value=lambda key: None, set_value=lambda *a, **k: None)
        package = types.ModuleType("pearnly_cowork")
        access = types.ModuleType("pearnly_cowork.access")
        access.scope = types.SimpleNamespace(require_read=lambda: None)
        history = types.ModuleType("pearnly_cowork.history")
        storage = types.SimpleNamespace(DOCTYPE="Cowork OCR History", PARTY_FIELDS=(), PUSH_FIELDS=(),
                                        read=lambda name: {"name": name}, project=lambda doc: dict(self.detail, **doc))
        self.detail, self.marked = {"last_push_status": "pending", "last_push_record": "CWP-1"}, []

        def mark(rows):
            self.marked.append([row["name"] for row in rows])
            for row in rows:
                if row["name"] == "H1":
                    row["push_waiting"] = "express_open"

        # 「在等 Express 关闭」的只读标记经钩子登记（history 域不导入 push；`push.express_wait.mark` 另有 test_cowork_express_wait）：
        # 这里用真的 `history/push_state.py`，只看列表、详情都过它
        frappe._ = str
        frappe.get_hooks = lambda name: ["pearnly_cowork.push.express_wait.mark"] if name == "cowork_history_push_marks" else []
        frappe.get_attr = lambda path: {"pearnly_cowork.push.express_wait.mark": mark}[path]
        history.document_policy = history.duplicates = history.removal = types.SimpleNamespace()
        history.storage = storage
        contracts = types.ModuleType("pearnly_core.express_rules.contracts.history")
        contracts.normalize_posting_kind = lambda value: value
        modules = {"frappe": frappe, "pearnly_cowork": package, "pearnly_cowork.access": access,
                   "pearnly_cowork.history": history, "pearnly_core.express_rules": types.ModuleType("x"),
                   "pearnly_core.express_rules.contracts": types.ModuleType("y"),
                   "pearnly_core.express_rules.contracts.history": contracts}
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location(
            "pearnly_cowork.history.push_state", ROOT / "apps/pearnly_cowork/pearnly_cowork/history/push_state.py")
        history.push_state = sys.modules[spec.name] = importlib.util.module_from_spec(spec)
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(history.push_state)
        spec = importlib.util.spec_from_file_location(
            "workbench_under_test", ROOT / "apps/pearnly_cowork/pearnly_cowork/history/api.py")
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)

    def test_sorting_still_applies_when_filter_conditions_are_given(self):
        self.api.workbench(sort_by="invoice_date", sort_order="asc",
                           conditions='[["invoice_date", ">", "2000-01-01"]]')
        call = self.calls[0]
        self.assertEqual(call["order_by"], "invoice_date asc, name desc")
        self.assertIn(["invoice_date", ">", "2000-01-01"], call["filters"])

    def test_one_source_file_stays_in_page_order(self):
        self.api.workbench(source_file="F-1", sort_by="invoice_date", sort_order="asc")
        self.assertEqual(self.calls[0]["order_by"], "source_index asc, name asc")
        self.api.workbench(source_file="F-1", sort_by="invoice_date",
                           conditions='[["posting_kind", "!=", "sales"]]')
        self.assertEqual(self.calls[1]["order_by"], "source_index asc, name asc")

    def test_the_page_and_the_detail_carry_the_express_wait_mark(self):
        # 列表这一页的行、按金额排的那一支、详情都带只读的 push_waiting（11-12：有人开着 Express 时网页只显示「录入中」）
        rows = [{"name": "H1", "total_amount": "5"}, {"name": "H2", "total_amount": "7"}]
        self.api.frappe.get_list = lambda doctype, **kw: [Row(row) for row in rows]
        for arguments in ({}, {"sort_by": "total_amount", "sort_order": "asc"}):
            items = self.api.workbench(**arguments)["items"]
            self.assertEqual([(row["name"], row.get("push_waiting")) for row in items],
                             [("H1", "express_open"), ("H2", None)], arguments)
        self.assertEqual(self.marked, [["H1", "H2"], ["H1", "H2"]])     # 每次只标这一页，一次
        self.assertEqual(self.api.detail("H1")["push_waiting"], "express_open")
        self.assertNotIn("push_waiting", self.api.detail("H2"))

    def test_the_mark_is_registered_to_the_push_ledger_query(self):
        hooks = (ROOT / "apps/pearnly_cowork/pearnly_cowork/hooks.py").read_text(encoding="utf-8")
        self.assertIn('cowork_history_push_marks = ["pearnly_cowork.push.express_wait.mark"]', hooks)


if __name__ == "__main__":
    unittest.main()
