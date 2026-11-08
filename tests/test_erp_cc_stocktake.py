"""ERP 版 CC 的盘点工具（盘点 2026-11-08 P1-1，`pearnly_erp_portal.inventory.cc_tools`）的离线检查：

- 声明：ERP 自己在 hooks 声明（ADR-009），名字 ``erp_stocktake_*``，形状照 Cowork 的 ``cowork_stocktake_*``（同名工具同一个步骤名、
  同一份译文），都不要「允许」（主会话 11-08 第 4 条）；步骤名有四语译文。
- 只调 ERP 盘点页同一组服务函数（``inventory.count``），不自己读写库；公司按对话挂的那家，给了别家照 ``masters.cc_tools.resolve`` 认。
- 结束先取审阅令牌再结束；调整单只生成草稿；按码记数遇到几件同码的回候选、码不在系统里回原因；recount 先作废再记；
  业务拒绝回 not_saved 不抛。

不连库：Frappe、盘点服务、公司解析用内存替身。
"""
from __future__ import annotations

import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ERP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
COWORK = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))


class ValidationError(Exception):
    pass


class PermissionError_(Exception):
    pass


class DoesNotExistError(Exception):
    pass


DB = types.SimpleNamespace(commits=0, rollbacks=0)
frappe = types.ModuleType("frappe")
frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError = ValidationError, PermissionError_, DoesNotExistError
frappe.db = types.SimpleNamespace(commit=lambda: setattr(DB, "commits", DB.commits + 1),
                                  rollback=lambda: setattr(DB, "rollbacks", DB.rollbacks + 1))
frappe.clear_messages = lambda: None
sys.modules["frappe"] = frappe

CALLS = []
ACTING = []  # 正在「代」哪家公司（替身 masters.company.acting 压栈）


def _record(name, result=None, error=None):
    def fn(*args, **kwargs):
        CALLS.append((name, args, {**kwargs, "_acting": ACTING[-1] if ACTING else None}))
        if error:
            raise error
        return result() if callable(result) else result
    return fn


count = types.ModuleType("pearnly_erp_portal.inventory.count")
cc_masters = types.ModuleType("pearnly_erp_portal.masters.cc_tools")
cc_masters.FILED = "C-1"
cc_masters.filed = lambda: cc_masters.FILED


def _resolve(wanted, filed="", write=False):
    if not wanted:
        return filed, None
    return (wanted, None) if wanted in ("C-1", "C-2") else ("", "unknown company: pass an id from erp_companies")


cc_masters.resolve = _resolve
companies = types.ModuleType("pearnly_erp_portal.masters.company")


import contextlib  # noqa: E402


@contextlib.contextmanager
def _acting(company, user=None):
    if company and company not in ("C-1", "C-2"):
        raise PermissionError_("Not permitted")
    ACTING.append(company or (ACTING[-1] if ACTING else None))
    try:
        yield company
    finally:
        ACTING.pop()


companies.acting = _acting
stock = types.ModuleType("pearnly_erp_portal.inventory.stock")
report = types.ModuleType("pearnly_erp_portal.inventory.report")
inv_contracts = types.ModuleType("pearnly_erp_portal.inventory.contracts")
inv_contracts.PRODUCT_VIEW = "product"
products_service = types.ModuleType("pearnly_erp_portal.products.service")
for name, module in (("pearnly_erp_portal", types.ModuleType("pearnly_erp_portal")),
                     ("pearnly_erp_portal.inventory", types.ModuleType("pearnly_erp_portal.inventory")),
                     ("pearnly_erp_portal.masters", types.ModuleType("pearnly_erp_portal.masters"))):
    module.__path__ = [str(ERP / name.split(".", 1)[1].replace(".", "/"))] if "." in name else [str(ERP)]
    sys.modules[name] = module
sys.modules["pearnly_erp_portal.inventory.count"] = count
sys.modules["pearnly_erp_portal.masters.cc_tools"] = cc_masters
sys.modules["pearnly_erp_portal.masters.company"] = companies
for _name, _module in (("pearnly_erp_portal.inventory.stock", stock), ("pearnly_erp_portal.inventory.report", report),
                       ("pearnly_erp_portal.inventory.contracts", inv_contracts),
                       ("pearnly_erp_portal.products.service", products_service)):
    sys.modules[_name] = _module
_products_pkg = types.ModuleType("pearnly_erp_portal.products")
_products_pkg.__path__ = []
_products_pkg.service = products_service
sys.modules["pearnly_erp_portal.products"] = _products_pkg
sys.modules["pearnly_erp_portal.inventory"].stock = stock
sys.modules["pearnly_erp_portal.inventory"].report = report
sys.modules["pearnly_erp_portal.inventory"].contracts = inv_contracts
sys.modules["pearnly_erp_portal.masters"].company = companies
sys.modules["pearnly_erp_portal.inventory"].count = count
sys.modules["pearnly_erp_portal.masters"].cc_tools = cc_masters

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("pearnly_erp_portal.inventory.cc_tools", ERP / "inventory/cc_tools.py")
tools = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tools
spec.loader.exec_module(tools)

REVIEW = {"task": {"title": "Nov", "status": "Active", "warehouse_name": "Main", "item_total": 3, "counted_total": 2,
                   "difference_total": 1, "adjustment": None},
          "items": [{"code": "A", "product_name": "Apple", "book_qty": 5, "counted": 1, "counted_qty": 5, "difference": 0},
                    {"code": "B", "product_name": "Bean", "book_qty": 5, "counted": 1, "counted_qty": 3, "difference": -2},
                    {"code": "C", "product_name": "Corn", "book_qty": 2, "counted": 0, "counted_qty": None, "difference": None}],
          "entries": [], "review_token": "tok-1", "conflicts": []}


def run(tool, **values):
    return json.loads(getattr(tools, tool)(values))


class Behaviour(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        cc_masters.FILED = "C-1"
        count.list_counts = _record("list_counts", [{"name": "SC-1", "title": "Nov", "status": "Active", "extra": "x"}])
        count.count_total = _record("count_total", 1)
        count.review_count = _record("review_count", REVIEW)
        count.close_count = _record("close_count", {"name": "SC-1", "status": "Closed", "differences": 1})
        count.adjustment_draft = _record("adjustment_draft", {"name": "SE-9"})
        count.create_count = _record("create_count", {"name": "SC-2", "rows": 12})
        count.lookup_count = _record("lookup_count", {"product": {"name": "P-B"}, "rows": []})
        count.recount_product = _record("recount_product", {"voided": 2})
        count.add_entry = _record("add_entry", {"entry": "E-1", "qty": 3})

    def test_list_uses_the_filed_company_and_pages_on_the_server(self):
        out = run("stocktake_list", limit=1)
        # 盘点服务照页面口径按当前公司取仓：工具在对话挂的那家（C-1）下面执行，不再逐个函数传公司（主会话 11-08 选 A）
        self.assertEqual(CALLS[0], ("list_counts", (), {"status": None, "limit": 1, "start": 0, "_acting": "C-1"}))
        self.assertEqual(CALLS[1][2]["_acting"], "C-1")
        self.assertEqual(ACTING, [])  # 退出后还原
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["next_offset"], 1)
        self.assertNotIn("extra", out["counts"][0])

    def test_another_company_must_be_resolvable(self):
        run("stocktake_list", company="C-2")
        self.assertEqual(CALLS[0][2]["_acting"], "C-2")
        CALLS.clear()
        out = run("stocktake_list", company="C-9")
        self.assertEqual(out["status"], "not_saved")
        self.assertEqual(CALLS, [])
        cc_masters.FILED = ""
        run("stocktake_create", title="Nov")
        self.assertIsNone(CALLS[-1][2]["_acting"])  # 没挂公司：照本人当前公司
        self.assertNotIn("company", CALLS[-1][2])

    def test_report_lists_only_differences_and_uncounted_by_default(self):
        out = run("stocktake_report", count="SC-1")
        self.assertEqual([row["code"] for row in out["items"]], ["B", "C"])
        self.assertEqual(out["listed"], 2)
        self.assertEqual(len(run("stocktake_report", count="SC-1", all_items=True)["items"]), 3)

    def test_close_takes_the_review_token_first(self):
        out = run("stocktake_close", count="SC-1")
        self.assertEqual([c[0] for c in CALLS], ["review_count", "close_count"])
        self.assertEqual(CALLS[1][2]["review_token"], "tok-1")
        self.assertEqual(out["status"], "Closed")

    def test_adjust_only_makes_a_draft(self):
        out = run("stocktake_adjust", count="SC-1")
        self.assertEqual(out, {"name": "SE-9", "state": "draft"})
        source = (ERP / "inventory/cc_tools.py").read_text(encoding="utf-8")
        for forbidden in ("submit_entry", "post_count", "frappe.db.set_value", "frappe.db.sql", ".insert(", ".save(",
                          "ignore_permissions"):
            self.assertNotIn(forbidden, source)

    def test_count_by_code_and_recount(self):
        run("stocktake_count", count="SC-1", code="B", qty=3)
        self.assertEqual([c[0] for c in CALLS], ["lookup_count", "add_entry"])
        self.assertEqual(CALLS[1][2]["product"], "P-B")
        self.assertTrue(CALLS[1][2]["request_id"].startswith("cc:"))
        self.assertNotIn("source", CALLS[1][2])  # 记数来源照页面（Desk）
        CALLS.clear()
        run("stocktake_count", count="SC-1", code="B", qty=4, recount=True)
        self.assertEqual([c[0] for c in CALLS], ["lookup_count", "recount_product", "add_entry"])
        CALLS.clear()
        out = run("stocktake_count", count="SC-1", code="B")
        self.assertEqual((out["status"], [c[0] for c in CALLS]), ("not_saved", ["lookup_count"]))

    def test_unknown_and_shared_codes_come_back_as_reasons(self):
        count.lookup_count = _record("lookup_count", {"not_found": True})
        self.assertIn("not_found", run("stocktake_count", count="SC-1", code="Z", qty=1)["reason"])
        count.lookup_count = _record("lookup_count", {"candidates": [{"name": "P-1"}, {"name": "P-2"}]})
        out = run("stocktake_count", count="SC-1", code="Z", qty=1)
        self.assertEqual(len(out["candidates"]), 2)
        self.assertEqual([c[0] for c in CALLS if c[0] == "add_entry"], [])

    def test_business_refusals_are_handed_back_not_raised(self):
        count.close_count = _record("close_count", error=ValidationError("Count at least one item before finishing"))
        before = DB.rollbacks
        out = run("stocktake_close", count="SC-1")
        self.assertEqual(out, {"status": "not_saved", "reason": "Count at least one item before finishing"})
        self.assertEqual(DB.rollbacks, before + 1)


class StockLookup(unittest.TestCase):
    """P2-1：在库一览与单品收发存明细（erp_stock_lookup / erp_stock_card），只读、在对话挂的那家公司下面取数、服务端分页。"""

    def setUp(self):
        CALLS.clear()
        cc_masters.FILED = "C-1"
        stock.stock_overview = _record("stock_overview", [{"code": "A1", "product_name": "Apple", "qty_on_hand": 5.0,
                                                           "company_qty": 9.0, "status": "ok", "unit_cost": None,
                                                           "warehouse": "WH-1", "name": "x"}])
        stock.stock_count = _record("stock_count", 1)
        products_service.find_product = _record("find_product", {"name": "P-1", "code": "A1"})
        report.card = _record("card", {"product_name": "Apple", "unit": "pcs", "opening": {"qty": 1.0},
                                       "closing": {"qty": 9.0},
                                       "rows": [{"date": f"2026-11-{i % 28 + 1:02d}", "kind": "in", "qty": 1.0,
                                                 "bal_qty": float(i), "doc_no": f"D{i}"} for i in range(80)]})

    def test_erp_stock_lookup_by_product_in_the_chat_s_company(self):
        out = run("stock_lookup", search="apple", limit=1)
        name, _args, kwargs = CALLS[0]
        self.assertEqual((name, kwargs["view"], kwargs["q"], kwargs["start"], kwargs["limit"], kwargs["_acting"]),
                         ("stock_overview", "product", "apple", 0, 1, "C-1"))
        self.assertEqual(CALLS[1][0], "stock_count")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["next_offset"], 1)
        self.assertNotIn("warehouse", out["rows"][0])  # 内部编号不给模型
        self.assertNotIn("unit_cost", out["rows"][0])  # 没有成本权限的空值不带
        CALLS.clear()
        run("stock_lookup", by_position=True, company="C-2")
        self.assertEqual((CALLS[0][2]["view"], CALLS[0][2]["_acting"]), (None, "C-2"))
        self.assertEqual(run("stock_lookup", company="C-9")["status"], "not_saved")

    def test_erp_stock_card_by_code_with_the_latest_rows(self):
        out = run("stock_card", product="A1", date_from="2026-11-01")
        self.assertEqual([c[0] for c in CALLS], ["find_product", "card"])
        self.assertEqual((CALLS[1][2]["product"], CALLS[1][2]["date_from"], CALLS[1][2]["_acting"]), ("P-1", "2026-11-01", "C-1"))
        self.assertEqual((out["movements"], len(out["rows"])), (80, tools.CARD_ROWS))
        self.assertEqual(out["rows"][-1]["doc_no"], "D79")  # 最近的在后面
        products_service.find_product = _record("find_product", {"ambiguous": 1, "candidates": [{"name": "P-1"}, {"name": "P-2"}]})
        self.assertEqual(len(run("stock_card", product="A1")["candidates"]), 2)
        products_service.find_product = _record("find_product", {})
        self.assertIn("not_found", run("stock_card", product="ZZ")["reason"])

    def test_read_only(self):
        source = (ERP / "inventory/cc_tools.py").read_text(encoding="utf-8")
        start = source.index("def stock_lookup(")
        end = source.index("COMPANY_PROP = ")
        for forbidden in ("apply_movement", "apply_lines", "rebuild_balance", "frappe.db.", ".insert(", ".save("):
            self.assertNotIn(forbidden, source[start:end])


class Declaration(unittest.TestCase):
    def test_erp_declares_them_with_cowork_s_shape_words_and_no_allow_card(self):
        hooks = (ERP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"pearnly_erp_portal.inventory.cc_tools.TOOLS"', hooks)
        self.assertIn('"pearnly_erp_portal.inventory.cc_tools.STEP_NAMES"', hooks)
        names = [tool["name"] for tool in tools.TOOLS]
        self.assertEqual(sorted(names), sorted(tools.STEP_NAMES))
        self.assertEqual(set(names), {"erp_stocktake_list", "erp_stocktake_report", "erp_stocktake_create",
                                      "erp_stocktake_count", "erp_stocktake_close", "erp_stocktake_adjust",
                                      "erp_stock_lookup", "erp_stock_card"})
        by_name = {tool["name"]: tool["handler"].rpartition(".")[2] for tool in tools.TOOLS}
        self.assertEqual(by_name, {"erp_stocktake_list": "stocktake_list", "erp_stocktake_report": "stocktake_report",
                                   "erp_stocktake_create": "stocktake_create", "erp_stocktake_count": "stocktake_count",
                                   "erp_stocktake_close": "stocktake_close", "erp_stocktake_adjust": "stocktake_adjust",
                                   "erp_stock_lookup": "stock_lookup", "erp_stock_card": "stock_card"})

        def terms(path):
            return {line.split("\t")[0]: line.split("\t")[1:4]
                    for line in (path / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()[1:]}
        erp, cowork = terms(ERP), terms(COWORK)
        source = (COWORK / "assistant/tools.py").read_text(encoding="utf-8")
        for tool in tools.TOOLS:
            self.assertNotIn("approval", tool)
            module, _sep, func = tool["handler"].rpartition(".")
            self.assertEqual(module, "pearnly_erp_portal.inventory.cc_tools")
            self.assertTrue(callable(getattr(tools, func)))
            for text in (tool["label"], tools.STEP_NAMES[tool["name"]]):
                self.assertTrue(all(erp.get(text, [""])[:3]), f"{text} needs zh / th / ja")
            if tool["name"] in ("erp_stocktake_adjust", "erp_stock_lookup", "erp_stock_card"):
                continue  # 只有 ERP 有（Cowork 盘点不生成调整单、不管库存）
            twin = "cowork_" + tool["name"][len("erp_"):]
            start = source.index(f'{{"name": "{twin}", "label": "{tool["label"]}"')  # 同名工具、同一个步骤名
            end = source.find('{"name": "cowork_', start + 10)
            self.assertNotIn('"approval"', source[start:end], twin)
            for text in (tool["label"], tools.STEP_NAMES[tool["name"]]):
                self.assertEqual(erp[text], cowork[text], text)  # 译文与 Cowork 同一份
        props = {tool["name"]: tool["input_schema"]["properties"] for tool in tools.TOOLS}
        self.assertEqual(props["erp_stocktake_count"]["recount"], {"type": "boolean"})
        self.assertEqual(props["erp_stocktake_report"]["all_items"], {"type": "boolean"})


if __name__ == "__main__":
    unittest.main()
