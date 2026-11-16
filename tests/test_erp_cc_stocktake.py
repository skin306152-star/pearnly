"""ERP 版 CC 的盘点工具（盘点 2026-11-08 P1-1，`pearnly_erp_portal.inventory.cc_tools`）的离线检查：

- 声明：ERP 自己在 hooks 声明（ADR-009），名字 ``erp_stocktake_*``，形状照 Cowork 的 ``cowork_stocktake_*``（同名工具同一个步骤名、
  同一份译文）；只有结束盘点先出「允许」卡片（用户 11-12），其余不要（主会话 11-08 第 4 条）；步骤名有四语译文。
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
from unittest import mock

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
for name, module in (("pearnly_erp_portal", types.ModuleType("pearnly_erp_portal")),
                     ("pearnly_erp_portal.inventory", types.ModuleType("pearnly_erp_portal.inventory")),
                     ("pearnly_erp_portal.masters", types.ModuleType("pearnly_erp_portal.masters"))):
    module.__path__ = [str(ERP / name.split(".", 1)[1].replace(".", "/"))] if "." in name else [str(ERP)]
    sys.modules[name] = module
sys.modules["pearnly_erp_portal.inventory.count"] = count
sys.modules["pearnly_erp_portal.masters.cc_tools"] = cc_masters
sys.modules["pearnly_erp_portal.masters.company"] = companies
sys.modules["pearnly_erp_portal.masters"].company = companies
sys.modules["pearnly_erp_portal.inventory"].count = count
sys.modules["pearnly_erp_portal.masters"].cc_tools = cc_masters

# 收发存导出（``erp_stock_card_export``）：网页导出同一个 ``card_export.build``、对话附件 ``assistant.files``
card_export = types.ModuleType("pearnly_erp_portal.inventory.card_export")
card_export.ARGS = ("report", "format", "date_from", "date_to", "product_from", "product_to", "warehouse_from",
                    "warehouse_to")
card_export.content_type = lambda output: {"xlsx": "application/xlsx", "pdf": "application/pdf"}.get(output)
sys.modules["pearnly_erp_portal.inventory.card_export"] = card_export
sys.modules["pearnly_erp_portal.inventory"].card_export = card_export
chat_files = types.ModuleType("pearnly_core.assistant.files")
for name in ("pearnly_core", "pearnly_core.assistant"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["pearnly_core.assistant.files"] = chat_files
sys.modules["pearnly_core.assistant"].files = chat_files

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

    def test_the_close_card_lists_the_count_and_what_is_counted_and_changes_nothing(self):
        """结束先出「允许」卡片（用户 11-12）：卡上是哪份盘点、已盘几项、未盘几项；出卡片只读，不结束。"""
        count.get_count = _record("get_count", {"name": "SC-1", "title": "Nov", "item_total": 5, "counted_total": 3})
        with mock.patch.object(frappe, "_", lambda text: text, create=True):
            rows = tools.preview_stocktake_close({"count": "SC-1"})
        self.assertEqual([(row["field"], row["after"]) for row in rows],
                         [("nav.stockCount", "Nov"), ("stock.count.countedRows", "3"), ("stock.count.rowUncounted", "2")])
        self.assertEqual([c[0] for c in CALLS], ["get_count"])  # 只读，不调结束
        # 问句下面那一行事实：这份盘点所在仓的公司名（不写公司、仓的编号）
        count.get_count = _record("get_count", {"name": "SC-1", "title": "Nov", "warehouse": "WH-0001"})
        names = {("ERP Warehouse", "WH-0001", "company"): "CO-0001", ("ERP Company", "CO-0001", "company_name"): "T9 Sincer Co."}
        with mock.patch.object(frappe.db, "get_value", lambda doctype, name, field: names.get((doctype, name, field)), create=True):
            self.assertEqual(tools.note_stocktake_close({"count": "SC-1"}), "T9 Sincer Co.")
            count.get_count = _record("get_count", {"name": "SC-1", "title": "Nov", "warehouse": ""})
            self.assertEqual(tools.note_stocktake_close({"count": "SC-1"}), "")

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


class StockCardExport(unittest.TestCase):
    """收发存导出：调网页导出同一个 ``build``（权限、范围由它核），在对话挂的那家公司下生成，文件放进对话；拒绝回原因不抛。"""

    def setUp(self):
        CALLS.clear()
        cc_masters.FILED = "C-1"
        card_export.build = _record("build", {"filename": "stock-card.pdf", "content": b"%PDF"})
        chat_files.add_to_conversation = _record("add", "/mnt/session/outputs/stock-card.pdf")

    def test_the_web_export_runs_under_the_filed_company_and_the_file_lands_in_the_chat(self):
        out = run("stock_card_export", report="by_warehouse", format="pdf", date_from="2026-10-01",
                  date_to="2026-10-31", product_from="", warehouse_from="W01")
        self.assertEqual(out["status"], "exported")
        self.assertTrue(out["files_added_to_chat"])
        build, add = CALLS
        self.assertEqual(build[2], {"report": "by_warehouse", "format": "pdf", "date_from": "2026-10-01",
                                    "date_to": "2026-10-31", "warehouse_from": "W01", "_acting": "C-1"})  # 空的不传
        self.assertEqual(add[1], ("stock-card.pdf", b"%PDF", "application/pdf"))

    def test_refusals_and_unknown_companies_come_back_as_reasons(self):
        card_export.build = _record("build", error=ValidationError("Invalid value"))
        self.assertEqual(run("stock_card_export", report="all", format="xlsx", date_from="x", date_to="y"),
                         {"status": "not_exported", "reason": "Invalid value"})
        out = run("stock_card_export", report="all", format="xlsx", date_from="2026-10-01", date_to="2026-10-31",
                  company="C-9")
        self.assertEqual(out["status"], "not_exported")
        self.assertEqual([c[0] for c in CALLS], ["build"])  # 认不出的公司不生成
        card_export.build = _record("build", {"filename": "stock-card.xlsx", "content": b"PK"})
        out = run("stock_card_export", report="summary", format="xlsx", date_from="2026-10-01", date_to="2026-10-31",
                  company="C-2")
        self.assertEqual((out["status"], CALLS[-1][2]["_acting"]), ("exported", "C-2"))


class Declaration(unittest.TestCase):
    def test_erp_declares_them_with_cowork_s_shape_words_and_no_allow_card(self):
        hooks = (ERP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"pearnly_erp_portal.inventory.cc_tools.TOOLS"', hooks)
        self.assertIn('"pearnly_erp_portal.inventory.cc_tools.STEP_NAMES"', hooks)
        names = [tool["name"] for tool in tools.TOOLS]
        self.assertEqual(sorted(names), sorted(tools.STEP_NAMES))
        self.assertEqual(set(names), {"erp_stocktake_list", "erp_stocktake_report", "erp_stocktake_create",
                                      "erp_stocktake_count", "erp_stocktake_close", "erp_stocktake_adjust",
                                      "erp_stock_card_export"})
        by_name = {tool["name"]: tool["handler"].rpartition(".")[2] for tool in tools.TOOLS}
        self.assertEqual(by_name, {"erp_stocktake_list": "stocktake_list", "erp_stocktake_report": "stocktake_report",
                                   "erp_stocktake_create": "stocktake_create", "erp_stocktake_count": "stocktake_count",
                                   "erp_stocktake_close": "stocktake_close", "erp_stocktake_adjust": "stocktake_adjust",
                                   "erp_stock_card_export": "stock_card_export"})

        def terms(path):
            return {line.split("\t")[0]: line.split("\t")[1:4]
                    for line in (path / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()[1:]}
        erp, cowork = terms(ERP), terms(COWORK)
        source = (COWORK / "assistant/tools.py").read_text(encoding="utf-8")
        for tool in tools.TOOLS:
            # 只有「结束盘点」先出「允许」卡片（用户 11-12：删除、结束类各一张卡）；其余照旧不加
            if tool["name"] == "erp_stocktake_close":
                self.assertIs(tool["approval"], True)
                self.assertEqual(tool["preview"], "pearnly_erp_portal.inventory.cc_tools.preview_stocktake_close")
                self.assertEqual(len([c for c in erp.get(tool["ask"], []) if c.strip()]), 3, tool["ask"])
            else:
                self.assertNotIn("approval", tool)
            module, _sep, func = tool["handler"].rpartition(".")
            self.assertEqual(module, "pearnly_erp_portal.inventory.cc_tools")
            self.assertTrue(callable(getattr(tools, func)))
            for text in (tool["label"], tools.STEP_NAMES[tool["name"]]):
                self.assertTrue(all(erp.get(text, [""])[:3]), f"{text} needs zh / th / ja")
            if tool["name"] in ("erp_stocktake_adjust", "erp_stock_card_export"):
                continue  # 只有 ERP 有（Cowork 盘点不生成调整单；收发存报表是 ERP 库存的）
            twin = "cowork_" + tool["name"][len("erp_"):]
            start = source.index(f'{{"name": "{twin}", "label": "{tool["label"]}"')  # 同名工具、同一个步骤名
            end = source.find('{"name": "cowork_', start + 10)
            # 两边同一个口径：ERP 要「允许」的（结束），Cowork 同名工具也要；ERP 不要的，Cowork 也不要
            self.assertEqual('"approval"' in source[start:end], "approval" in tool, twin)
            for text in (tool["label"], tools.STEP_NAMES[tool["name"]]):
                self.assertEqual(erp[text], cowork[text], text)  # 译文与 Cowork 同一份
        props = {tool["name"]: tool["input_schema"]["properties"] for tool in tools.TOOLS}
        self.assertEqual(props["erp_stocktake_count"]["recount"], {"type": "boolean"})
        self.assertEqual(props["erp_stocktake_report"]["all_items"], {"type": "boolean"})


if __name__ == "__main__":
    unittest.main()
