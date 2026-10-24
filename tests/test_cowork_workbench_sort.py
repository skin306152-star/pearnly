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
        storage = types.SimpleNamespace(DOCTYPE="Cowork OCR History", PARTY_FIELDS=(), PUSH_FIELDS=())
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


if __name__ == "__main__":
    unittest.main()
