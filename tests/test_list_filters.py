"""产品列表原生筛选条件（pearnly_core.desk.list_filters）：白名单字段 + Frappe 原生运算符原样交给 get_list；
恒定值那一侧（销售记录里的收银小票）按同一口径判断。"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))

from pearnly_core.desk import list_filters  # noqa: E402

ALLOWED = {"doc_kind": "doc_kind", "supplier": "supplier", "doc_date": "creation"}


class Conditions(unittest.TestCase):
    def test_native_operators_pass_through_on_whitelisted_columns(self):
        raw = ('[["doc_kind","!=","expense"],["supplier","like","%ABC%"],["supplier","not like","%Test%"],'
               '["doc_kind","in",["receipt","invoice"]],["doc_kind","not in","quotation, void"],'
               '["doc_date",">=","2026-10-01"],["doc_date","Between",["2026-10-01","2026-10-31"]],'
               '["doc_date","Timespan","last month"],["supplier","is","set"]]')
        self.assertEqual(list_filters.conditions(raw, ALLOWED), [
            ["doc_kind", "!=", "expense"], ["supplier", "like", "%ABC%"], ["supplier", "not like", "%Test%"],
            ["doc_kind", "in", ["receipt", "invoice"]], ["doc_kind", "not in", ["quotation", "void"]],
            ["creation", ">=", "2026-10-01"], ["creation", "between", ["2026-10-01", "2026-10-31"]],
            ["creation", "timespan", "last month"], ["supplier", "is", "set"]])

    def test_unknown_fields_operators_and_shapes_are_dropped(self):
        raw = [["owner", "=", "x"], ["doc_kind", "descendants of", "x"], ["doc_kind", "!= 1 or 1=1", "x"],
               ["doc_kind", "in", []], ["doc_kind", "=", {"a": 1}], ["doc_kind"], "doc_kind", None,
               ["ERP Sale", "doc_kind", "!=", "receipt"]]
        self.assertEqual(list_filters.conditions(raw, ALLOWED), [["doc_kind", "!=", "receipt"]])
        self.assertEqual(list_filters.conditions("not json", ALLOWED), [])
        self.assertEqual(list_filters.conditions(None, ALLOWED), [])
        many = [["doc_kind", "!=", str(n)] for n in range(50)]
        self.assertEqual(len(list_filters.conditions(many, ALLOWED)), list_filters.MAX_CONDITIONS)

    def test_contains_on_a_link_field_matches_the_name_people_type(self):
        # 验收 10-25：采购「供应商 包含」按名称搜，库里存的是 SUP-xxxx；用框架原生的 Link 点号写法，不先取编号
        link = {"supplier": {"column": "supplier", "link": ("ERP Supplier", "supplier_name")}}
        out = list_filters.conditions('[["supplier","like","%ABC%"],["supplier","not like","%Nobody%"],'
                                      '["supplier","!=","SUP-00099"],["supplier","like",""]]', link)
        self.assertEqual(out, [["supplier.supplier_name", "like", "%ABC%"],
                               ["supplier.supplier_name", "not like", "%Nobody%"], ["supplier", "!=", "SUP-00099"]])

    def test_count_is_one_aggregate_query_under_permissions(self):
        import types
        from unittest.mock import patch

        calls = []
        frappe = types.ModuleType("frappe")
        frappe.get_list = lambda doctype, **kw: calls.append(("get_list", doctype, kw)) or [{"total": 73918}]
        frappe.get_all = lambda doctype, **kw: calls.append(("get_all", doctype, kw)) or []
        with patch.dict(sys.modules, {"frappe": frappe}):
            self.assertEqual(list_filters.count("ERP Customer", [["company", "=", "CO-1"]], [["tax_id", "like", "%1%"]]),
                             73918)
            self.assertEqual(list_filters.count("ERP Sale", [], ignore_permissions=True), 0)
        self.assertEqual(calls[0], ("get_list", "ERP Customer", {
            "filters": [["company", "=", "CO-1"]], "or_filters": [["tax_id", "like", "%1%"]],
            "fields": [{"COUNT": "name", "as": "total"}], "order_by": "", "limit_page_length": 0}))
        self.assertEqual(calls[1][0], "get_all")

    def test_constant_values_match_like_frappe(self):
        self.assertTrue(list_filters.matches("receipt", "!=", "invoice"))
        self.assertFalse(list_filters.matches("receipt", "!=", "receipt"))
        self.assertTrue(list_filters.matches("receipt", "like", "%REC%"))
        self.assertFalse(list_filters.matches("receipt", "not like", "%cei%"))
        self.assertTrue(list_filters.matches("pos", "in", ["pos", "line"]))
        self.assertFalse(list_filters.matches("pos", "not in", ["pos"]))
        self.assertTrue(list_filters.matches("paid", "is", "set"))
        self.assertFalse(list_filters.matches("", "is", "set"))
        self.assertTrue(list_filters.matches("paid", ">", "x"))  # 比较不了的按满足



class SalesRecords(unittest.TestCase):
    """销售记录列表：条件进销售记录那一侧的查询；收银小票恒为收据、已收款、来源收银，条件排除它时不取收银单。"""

    def setUp(self):
        import importlib.util
        import types
        from unittest.mock import patch

        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        frappe = types.ModuleType("frappe")
        frappe.utils = types.SimpleNamespace(getdate=lambda value: value, add_months=lambda value, n: value)
        sys.modules["frappe"] = frappe
        portal = types.ModuleType("pearnly_erp_portal")
        access = types.ModuleType("pearnly_erp_portal.access")
        access.guard = types.SimpleNamespace(scope_filters=lambda: {})
        access.listing = types.SimpleNamespace()
        access.rules = types.SimpleNamespace()
        inventory = types.ModuleType("pearnly_erp_portal.inventory")
        inventory.warehouses = types.SimpleNamespace()
        masters = types.ModuleType("pearnly_erp_portal.masters")
        masters.company = types.SimpleNamespace(active_company=lambda: "CO-1")
        sales = types.ModuleType("pearnly_erp_portal.sales")
        sales.access = types.SimpleNamespace()
        sales.contracts = types.SimpleNamespace(SOURCE_POS="pos", RECEIPT="receipt", SUBMITTED="submitted")
        sys.modules.update({"pearnly_erp_portal": portal, "pearnly_erp_portal.access": access,
                            "pearnly_erp_portal.inventory": inventory, "pearnly_erp_portal.masters": masters,
                            "pearnly_erp_portal.sales": sales})
        spec = importlib.util.spec_from_file_location(
            "sales_records_under_test", ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/sales/records.py")
        self.records = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.records)

    def test_conditions_reach_the_sales_document_query(self):
        filters, _or, datefield = self.records.query({"conditions": (
            '[["doc_kind","!=","quotation"],["payment_status","not in",["paid"]],["source","like","%line%"],'
            '["doc_date",">=","2026-10-01"],["buyer_name","=","x"]]')})
        self.assertEqual(filters[-4:], [["doc_kind", "!=", "quotation"], ["payment_status", "not in", ["paid"]],
                                        ["source", "like", "%line%"], [datefield, ">=", "2026-10-01"]])

    def test_receipts_are_listed_only_when_the_conditions_allow_them(self):
        applies = self.records._pos_applies
        self.assertTrue(applies({"conditions": '[["doc_kind","!=","quotation"]]'}))
        self.assertFalse(applies({"conditions": '[["doc_kind","!=","receipt"]]'}))
        self.assertFalse(applies({"conditions": '[["payment_status","not in",["paid"]]]'}))
        self.assertTrue(applies({"conditions": '[["source","in",["pos","line"]]]'}))
        self.assertFalse(applies({"conditions": '[["source","not like","%po%"]]'}))
        self.assertTrue(applies({}))

if __name__ == "__main__":
    unittest.main()
