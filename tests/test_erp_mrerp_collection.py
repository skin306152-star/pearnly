"""ERP 收款写进 MR.ERP（服务端直写，`pearnly_erp_portal.collection.mrerp` + 共用层 `pearnly_core.mrerp.push.receipt` /
`xlsx.receipt` / `receivables`）的离线检查。不连库、不连 MR.ERP；凭据只用假值。

- 开关关着（:data:`MRERP_LEDGER_ENABLED` = False）：收款的 MR.ERP 入口全挡，``handles`` 不查库；收款服务里每处分流都先问
  ``ledger.handles``，Express 公司走原来那条一字不变；
- 开着（测试里打补丁）：选账套、列未收发票（读选单页落快照）、全额 / 部分 / 两张发票一笔 / 转账进银行科目 / 带折扣，导入表逐列
  与平衡、第 20 列有值；销售员对上 / 对不上回落默认；收款菜单多个要选、单个直接用、没关联的不选；写后读回拿到单号、受理了却
  查不到记结果不明；已写入的撤回转人工、单号与事实留着。

共用层的导入表、选单页解析与菜单规则只用标准库，CI 直接跑；导入执行对假 MR.ERP 站点（`FakeSite`，要 requests）的那组在生产镜像
的候选检查里跑（缺 requests 时跳过）。
"""
from __future__ import annotations

import ast
import datetime
import io
import pathlib
import re
import sys
import types
import unittest
import zipfile
from decimal import Decimal
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
ERP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
for path in (ROOT / "apps/pearnly_core", ROOT / "apps/pearnly_erp_portal"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pearnly_core.mrerp import masters as master_rules, menus, needs, receivables  # noqa: E402
from pearnly_core.mrerp.push import modules  # noqa: E402
from pearnly_core.mrerp.push.xlsx import receipt as sheet  # noqa: E402

USER = "fakeuser"
PASSWORD = "fakepw"


def receipt(**extra):
    base = {"number": "COL261116-AB12", "date": "2026-11-16", "branch": "00000", "dept": "D001", "job": "00001",
            "employee": "สมชาย ใจดี", "customer": "C0001", "cash_account": "1111-01", "cash": "100.00",
            "petty_account": "1111-02", "petty": "0", "other1_account": "1111-02", "other1": "0",
            "other2_account": "1111-02", "other2": "0", "other3_account": "1111-02", "other3": "0",
            "notes": ["", "", "COL261116-AB12"], "bills": [{"docnum": "SI000001-1", "amount": "100.00"}]}
    base.update(extra)
    return base


def cells(blob, number):
    """导入表第 ``number`` 张表 → {格子: 值}（共享字符串换回原文；数字照原样）。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        strings = [re.sub(r"<[^>]+>", "", s) for s in re.findall(r"<si>(.*?)</si>", zf.read("xl/sharedStrings.xml").decode())]
        xml = zf.read(f"xl/worksheets/sheet{number}.xml").decode()
    out = {}
    for ref, attrs, value in re.findall(r'<c r="([A-Z]+\d+)"([^>]*)><v>([^<]*)</v></c>', xml):
        out[ref] = strings[int(value)] if 't="s"' in attrs else value
    return out


class Sheet(unittest.TestCase):
    """导入表（Sheet1 20 列 + 核销表 + 支票表头）与平衡。"""

    def test_twenty_columns_with_the_twentieth_filled_and_the_three_sheets(self):
        values = cells(sheet.generate(receipt()), 1)
        self.assertEqual([values[f"{chr(65 + i)}1"] for i in range(20)], sheet.HEADERS_1)
        self.assertEqual(values["T2"], "COL261116-AB12")  # 第 20 列有值，MR.ERP 才数得到 20 列
        self.assertEqual((values["A2"], values["B2"], values["C2"], values["F2"], values["G2"]),
                         ("COL261116-AB12", "2026-11-16", "00000", "สมชาย ใจดี", "C0001"))
        self.assertEqual((values["H2"], values["I2"]), ("1111-01", "100"))
        for col in "JLNP":
            self.assertEqual(values[f"{col}2"], "1111-02")  # 零用金、其他 1–3：有效科目 + 0
        for col in "KMOQ":
            self.assertEqual(values[f"{col}2"], "0")
        bills = cells(sheet.generate(receipt()), 2)
        self.assertEqual((bills["A1"], bills["B1"], bills["C1"]), tuple(sheet.HEADERS_2))
        self.assertEqual((bills["A2"], bills["B2"], bills["C2"]), ("COL261116-AB12", "SI000001-1", "100"))
        cheques = cells(sheet.generate(receipt()), 3)
        self.assertEqual([cheques[f"{c}1"] for c in "ABCDE"], sheet.HEADERS_3)
        self.assertEqual(len(cheques), 5)  # 支票只写表头

    def test_relationship_files_stay_relative(self):
        with zipfile.ZipFile(io.BytesIO(sheet.generate(receipt()))) as zf:
            rels = zf.read("xl/_rels/workbook.xml.rels").decode()
        self.assertNotIn('Target="/xl', rels)

    def test_balance_full_partial_two_invoices_transfer_and_discount(self):
        self.assertEqual(sheet.check(receipt()), "")
        self.assertEqual(sheet.check(receipt(cash="40.00", bills=[{"docnum": "SI000001-1", "amount": "40.00"}])), "")
        two = receipt(cash="250.00", bills=[{"docnum": "SI000001-1", "amount": "100.00"},
                                            {"docnum": "SI000001-2", "amount": "150.00"}])
        self.assertEqual(sheet.check(two), "")
        self.assertEqual(len([k for k in cells(sheet.generate(two), 2) if k.startswith("B")]), 3)
        transfer = receipt(cash_account="1112-01")
        self.assertEqual(cells(sheet.generate(transfer), 1)["H2"], "1112-01")  # 转账：银行科目进现金列
        discounted = receipt(cash="95.00", other2_account="5300-09", other2="-5.00")  # 折扣在「其他 2」记负数
        self.assertEqual(sheet.check(discounted), "")
        self.assertEqual((cells(sheet.generate(discounted), 1)["N2"], cells(sheet.generate(discounted), 1)["O2"]),
                         ("5300-09", "-5"))
        self.assertEqual(sheet.check(receipt(cash="99.00")), sheet.UNBALANCED)

    def test_rules_before_upload(self):
        self.assertEqual(sheet.check(receipt(number="X" * 19)), sheet.NUMBER_INVALID)
        self.assertEqual(sheet.check(receipt(bills=[])), sheet.BILLS_EMPTY)
        self.assertEqual(sheet.check(receipt(cash="1.001")), sheet.AMOUNT_INVALID)
        self.assertEqual(sheet.check(receipt(notes=["", "", ""])), sheet.FIELD_MISSING)
        self.assertEqual(sheet.check(receipt(employee="")), sheet.FIELD_MISSING)

    def test_salesman_matches_by_code_or_name_without_spaces_else_nothing(self):
        staff = [{"code": "E01", "name": "สมชาย ใจดี"}, {"code": "E02", "name": "สมหญิง ขายเก่ง"}]
        self.assertEqual(sheet.employee_name(staff, "E02"), "สมหญิง ขายเก่ง")
        self.assertEqual(sheet.employee_name(staff, "สมหญิง  ขายเก่ง"), "สมหญิง ขายเก่ง")
        self.assertEqual(sheet.employee_name(staff, "ไม่มีคนนี้"), "")
        self.assertEqual(sheet.employee_name(staff, ""), "")


SELBILL = """<div id="tab-arad"><p><span>เลือก</span><span>เลขที่</span></p>
<p><span><input type="checkbox" name="chkidaradh[]" value="1"></span><span>AD-1</span><span>01/11/2569</span>
<span>01/11/2569</span><span>50.00</span><span>50.00</span></p></div>
<div id="tab-artran"><p><span>เลือก</span><span>เลขที่</span></p>
<p><span><input type="checkbox" name="chkidartran[]" value="7"><label></label></span><span>SI000001-1</span>
<span>15/11/2569</span><span>15/12/2569</span><span>1,070.00</span><span>870.00</span></p></div>
<div id="tab-arsd"><p class="nodata">No Data</p></div>
<div id="tab-arsc"><p><span><input type="checkbox" name="chkidarsch[]" value="3"></span><span>SC-1</span>
<span>16/11/2569</span><span>16/11/2569</span><span>10.00</span><span>10.00</span></p></div>"""


class Receivables(unittest.TestCase):
    def test_open_bills_are_credit_sales_and_debit_notes_with_dates_and_balances(self):
        found = receivables.parse_bills(SELBILL)
        self.assertEqual(found, [{"docnum": "SI000001-1", "rectyp": "2", "docdat": "2026-11-15", "net": "1070.00",
                                  "received": "200.00", "remaining": "870.00"}])

    def test_a_bounced_page_is_unreadable_not_empty(self):
        self.assertIsNone(receivables.parse_bills('<script>alert("Login")</script>'))
        self.assertEqual(receivables.parse_bills('<div id="tab-artran"><p class="nodata">No Data</p></div>'), [])

    def test_customer_id_by_exact_code(self):
        payload = {"inpdupdata": [[9, "C0001", "ลูกค้า"], [10, "C00011", "อีก"]]}
        self.assertEqual(receivables.customer_id(payload, "C0001"), "9")
        self.assertEqual(receivables.customer_id(payload, "C9"), "")


class MenusAndDefaults(unittest.TestCase):
    PAGES = {"impfarev": [{"idmenu": "169", "label": "รับชำระหนี้", "linked": True},
                          {"idmenu": "170", "label": "รับชำระ-เก่า", "linked": False}]}

    def test_receipt_module_and_page(self):
        found = modules.get_module("receipt")
        self.assertEqual((found.path, found.idmenu, found.listing_module), ("impfarev", 375, "farev"))
        self.assertEqual(menus.PAGE_OF["receipt"], "impfarev")
        self.assertNotIn("receipt", menus.KINDS)  # 发票的几类不变
        self.assertNotIn("impfarev", menus.PAGES)  # 读菜单任务只读单据三页

    def test_single_linked_menu_is_used_and_an_unlinked_one_never(self):
        menu, reason = menus.resolve(self.PAGES["impfarev"], "")
        self.assertEqual((menu["idmenu"], reason), ("169", None))
        self.assertEqual(menus.resolve(self.PAGES["impfarev"], "170"), (None, menus.CHANGED))
        self.assertEqual(menus.resolve([self.PAGES["impfarev"][1]], ""), (None, menus.NOT_LINKED))
        observed = menus.observe(None, "15:1", self.PAGES, at="t")
        self.assertIsNone(menus.choose(None, "15:1", "receipt", "170", observed, actor="u", at="t"))

    def test_several_linked_menus_need_one_choice_remembered(self):
        pages = {"impfarev": [{"idmenu": "169", "label": "A", "linked": True}, {"idmenu": "171", "label": "B", "linked": True}]}
        observed = menus.observe(None, "15:1", pages, at="t")
        view = menus.view(None, observed, "15:1")
        self.assertTrue(view["kinds"]["receipt"]["needed"])
        self.assertEqual(needs.needed({**view, "defaults": {"known": True, "fields": {}}}, "receipt")["menus"], ["receipt"])
        chosen = menus.choose(None, "15:1", "receipt", "171", observed, actor="u", at="t")
        self.assertEqual(menus.chosen_map(chosen, "15:1"), {"receipt": "171"})
        self.assertFalse(menus.view(chosen, observed, "15:1")["kinds"]["receipt"]["needed"])

    def test_documents_still_ask_only_their_own(self):
        self.assertEqual(needs.used(None)[0], menus.KINDS)
        self.assertNotIn("discount_account", needs.used(None)[1])
        self.assertEqual(needs.used("receipt"), (("receipt",), ("branch", "dept", "job", "employee")))

    def test_discount_account_is_the_single_discount_account_else_a_choice(self):
        chart = [{"code": "1112-01", "name": "เงินฝากธนาคาร"}, {"code": "5300-09", "name": "ส่วนลดจ่าย"}]
        self.assertEqual(master_rules.resolve_field({"chart": chart}, {}, "discount_account")[0]["code"], "5300-09")
        two = chart + [{"code": "5300-10", "name": "ส่วนลดจ่ายพิเศษ"}]
        self.assertEqual(master_rules.resolve_field({"chart": two}, {}, "discount_account"),
                         (None, master_rules.CHOICE_REQUIRED))


# ── ERP 一侧（Frappe 内存替身，同 test_erp_mrerp_direct）──────────────────────────────────────────


class Row(dict):
    __getattr__ = dict.get


class PermissionError_(Exception):
    pass


class Db:
    def __init__(self):
        self.tables, self.touched = {}, 0

    def table(self, doctype):
        return self.tables.setdefault(doctype, {})

    def get_value(self, doctype, name, fields, as_dict=False, for_update=False):
        self.touched += 1
        row = self.table(doctype).get(name)
        if row is None:
            return None
        if isinstance(fields, str):
            return row.get(fields)
        return Row({f: row.get(f) for f in fields}) if as_dict else [row.get(f) for f in fields]

    def set_value(self, doctype, name, values, value=None, update_modified=True):
        values = values if isinstance(values, dict) else {values: value}
        self.table(doctype)[name].update(values)

    def exists(self, doctype, name):
        return name in self.table(doctype)

    def commit(self):
        pass

    def rollback(self):
        pass


class Cache:
    def __init__(self):
        self.values = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value

    def delete_value(self, key):
        self.values.pop(key, None)


DB, CACHE, ENQUEUED = Db(), Cache(), []
NOW = datetime.datetime(2026, 11, 16, 10, 0, 0)


class Doc(Row):
    def update(self, values):  # noqa: D401 — frappe 文档的 update
        dict.update(self, values)

    def save(self, ignore_permissions=False):
        DB.table("ERP Collection")[self["name"]].update({k: v for k, v in self.items() if k not in ("invoices", "payments")})


def _get_doc(doctype, name, for_update=False):
    row = DB.table(doctype)[name]
    doc = Doc(row)
    doc["invoices"] = [Row(r) for r in row.get("invoices") or []]
    doc["payments"] = [Row(r) for r in row.get("payments") or []]
    return doc


frappe = types.ModuleType("frappe")
frappe.__path__ = []
frappe.db, frappe.cache = DB, CACHE
frappe._ = lambda text, *a, **k: text
frappe.PermissionError = PermissionError_
frappe.ValidationError = ValueError
frappe.session = types.SimpleNamespace(user="collector@x")
frappe.local = types.SimpleNamespace(site="erp.test", lang="en")
frappe.get_roles = lambda user=None: []
frappe.enqueue = lambda method, **kw: ENQUEUED.append((method, kw))
frappe.throw = lambda message, exc=ValueError: (_ for _ in ()).throw(exc(message))
frappe.get_doc = _get_doc
ERRORS = []
frappe.log_error = lambda *a, **k: ERRORS.append((a, k))
frappe.get_traceback = lambda: ""
def _matches(row, filters):
    for key, want in (filters or {}).items():
        if isinstance(want, list) and want[0] == "in" and row.get(key) not in want[1]:
            return False
        if isinstance(want, list) and want == ["is", "not set"] and row.get(key):
            return False
        if not isinstance(want, list) and row.get(key) != want:
            return False
    return True


frappe.get_all = lambda doctype, filters=None, **kw: [Row(r) for r in DB.table(doctype).values() if _matches(r, filters)]
frappe.utils = types.ModuleType("frappe.utils")
frappe.utils.now_datetime = lambda: NOW
frappe.utils.get_datetime = lambda v: v if isinstance(v, datetime.datetime) else datetime.datetime.fromisoformat(str(v))
frappe.utils.password = types.ModuleType("frappe.utils.password")
frappe.utils.password.get_decrypted_password = lambda doctype, name, field, raise_exception=False: \
    DB.table(doctype).get(name, {}).get(field) or ""
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", frappe.utils)
sys.modules.setdefault("frappe.utils.password", frappe.utils.password)

import pearnly_erp_portal.mrerp as mrerp_switch  # noqa: E402
from pearnly_erp_portal.collection import contracts, mrerp as ledger  # noqa: E402
from pearnly_erp_portal.mrerp import catalog as mrerp_catalog  # noqa: E402

BOOK = {"key": "15:1", "label": "TEST2026", "connection": "C1", "period_start": "2026-01-01",
        "period_end": "2026-12-31", "next_end": None, "comidyear": "15"}
mrerp_catalog.books_of = lambda company: [dict(BOOK)]
mrerp_catalog.no_book_text = lambda company, day: f"no MR.ERP account set for {day}"


def fake_pick(company, day, wanted=None):
    if wanted:
        return {"book": dict(BOOK) if wanted == BOOK["key"] else None, "reason": "" if wanted == BOOK["key"] else
                "express.account_set_missing", "options": [], "subject": ""}
    ok = BOOK["period_start"] <= str(day)[:10] <= BOOK["period_end"]
    return {"book": dict(BOOK) if ok else None, "reason": "" if ok else "express.account_set_missing", "options": [],
            "subject": ""}


mrerp_catalog.pick = fake_pick

# 收款里只在函数里载入的几个邻居：快照与写账回执换成记录调用的替身
SNAPSHOT, PUSHED, TOLD = [], [], []
_snapshot = types.ModuleType("pearnly_erp_portal.collection.snapshot")
_snapshot.READY, _snapshot.READING, _snapshot.FAILED = "ready", "reading", "failed"
_snapshot.request_key = lambda book_key, customer="", docnums=(): f"erp_ar_open:{book_key}:{customer}"
_snapshot.replace = lambda company, book_key, items, read_at=None, customer="": SNAPSHOT.append(
    (company, book_key, customer, items)) or len(items)
_push = types.ModuleType("pearnly_erp_portal.collection.push")
_push._set = lambda name, values: PUSHED.append((name, dict(values))) or DB.table("ERP Collection")[name].update(values)
_push._tell = lambda doc: TOLD.append(doc["name"])
sys.modules["pearnly_erp_portal.collection.snapshot"] = _snapshot
sys.modules["pearnly_erp_portal.collection.push"] = _push

CHART = [{"code": "1111-01", "name": "เงินสด"}, {"code": "1112-01", "name": "เงินฝากธนาคาร-กรุงเทพ"},
         {"code": "1112-02", "name": "เงินฝากธนาคาร-กสิกร"}, {"code": "1130-01", "name": "ลูกหนี้การค้า"},
         {"code": "5300-09", "name": "ส่วนลดจ่าย"}]
SMALL = {"branch": [{"code": "00000", "name": "สำนักงานใหญ่"}], "dept": [{"code": "D001", "name": "บัญชี"}],
         "job": [{"code": "00001", "name": "ทั่วไป"}], "employee": [{"code": "E01", "name": "สมชาย ใจดี"}]}


def reset(menu_pages=None, masters=True):
    import json

    for item in (DB.tables, CACHE.values, ENQUEUED, SNAPSHOT, PUSHED, TOLD):
        item.clear()
    DB.table("ERP Company")["CO-1"] = {"name": "CO-1", "company_name": "บริษัท เรา จำกัด", "tax_id": "0105559000001",
                                      "branch": "00000", "express_enabled": 0, "mrerp_enabled": 1,
                                      "mrerp_connection": "C1", "mrerp_account_set": "15:1", "mrerp_company": "tax:x",
                                      "mrerp_linked_at": datetime.datetime(2026, 11, 1), "mrerp_menus": None,
                                      "mrerp_defaults": None}
    DB.table("ERP Company")["EX-1"] = {"name": "EX-1", "express_enabled": 1, "mrerp_enabled": 0}
    pages = menu_pages if menu_pages is not None else {"impfarev": [{"idmenu": "169", "label": "รับชำระหนี้",
                                                                     "linked": True}]}
    DB.table("ERP MRERP Connection")["C1"] = {
        "name": "C1", "system_url": "https://mrerp.example", "enabled": 1, "credential_username": USER,
        "credential_password": PASSWORD,
        "menu_options": json.dumps(menus.observe(None, "15:1", pages, at="t")),
        "master_options": json.dumps(master_rules.observe(None, "15:1", {**SMALL, "chart": CHART}, at="t"))
        if masters else None}


def collection(name="COL261116-AB12", status=contracts.QUEUED, cash="0.00", payments=(), invoices=None, **extra):
    DB.table("ERP Collection")[name] = {
        "name": name, "company": "CO-1", "status": status, "book_key": "15:1", "doc_date": "2026-11-16",
        "customer": "C0001", "salesman": "", "department": "", "cash": cash, "job": None, "docnum": "", "reason": "",
        "total": "100.00", "invoices": invoices or [{"docnum": "SI000001-1", "amount": "100.00"}],
        "payments": list(payments), **extra}
    return _get_doc("ERP Collection", name)


class SwitchedOff(unittest.TestCase):
    def setUp(self):
        reset()

    def test_handles_is_false_without_reading_the_database(self):
        self.assertIs(mrerp_switch.MRERP_LEDGER_ENABLED, False)
        before = DB.touched
        self.assertFalse(ledger.handles("CO-1"))
        self.assertEqual(DB.touched, before)

    def test_jobs_and_poll_do_nothing(self):
        collection()
        ledger.run("COL261116-AB12")
        ledger.run_read("CO-1", "15:1", "C0001")
        ledger.poll()
        self.assertEqual((PUSHED, ENQUEUED, SNAPSHOT), ([], [], []))
        self.assertEqual(DB.table("ERP Collection")["COL261116-AB12"]["status"], contracts.QUEUED)

    def test_every_branch_in_the_collection_service_asks_handles_first(self):
        """Express 公司的收款一字不变：服务里每处 MR.ERP 分流都在 ``ledger.handles`` 之后（关着时它回假、不查库）。"""
        source = (ERP / "collection/service.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                    getattr(node.func.value, "id", "") == "ledger" and node.func.attr not in ("handles", "voided", "voided_facts"):
                with self.subTest(call=ast.unparse(node)):
                    fn = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                              and f.lineno <= node.lineno <= f.end_lineno)
                    before = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                              and n.func.attr == "handles" and n.lineno <= node.lineno]
                    self.assertTrue(before)
        push_source = (ERP / "collection/push.py").read_text(encoding="utf-8")
        self.assertLess(push_source.index("ledger.handles(doc.company)"), push_source.index("service._book_row(doc)"))

    def test_voided_facts_only_for_a_withdrawn_mrerp_write(self):
        self.assertFalse(ledger.voided(collection(status=contracts.WITHDRAWN, docnum="")))
        self.assertFalse(ledger.voided(collection(status=contracts.WRITTEN, docnum="FR1116-1")))


@patch.object(mrerp_switch, "MRERP_LEDGER_ENABLED", True)
class SwitchedOn(unittest.TestCase):
    def setUp(self):
        reset()

    def test_only_the_mrerp_company_is_handled(self):
        self.assertTrue(ledger.handles("CO-1"))
        self.assertFalse(ledger.handles("EX-1"))

    def test_book_follows_the_collection_date(self):
        with patch.object(ledger, "wanted", lambda row: None):
            self.assertEqual(ledger.pick({"name": "CO-1"}, "2026-11-16")["book"]["key"], "15:1")
            self.assertEqual(ledger.pick({"name": "CO-1"}, "2025-11-16")["reason"], "express.account_set_missing")
        self.assertEqual(ledger.periods({"name": "CO-1"}), [{"key": "15:1", "label": "TEST2026", "short": "TEST2026",
                                                            "start": "2026-01-01", "end": "2026-12-31",
                                                            "last": "2026-12-31"}])

    def test_methods_are_cash_the_deposit_accounts_and_the_discount_account(self):
        found = ledger.catalog("CO-1", dict(BOOK))
        self.assertTrue(found["cash"])
        self.assertEqual([item["prefix"] for item in found["transfers"]], ["1112-01", "1112-02"])
        self.assertEqual(found["discount"]["code"], "5300-09")

    def test_no_discount_without_a_discount_account(self):
        import json

        DB.table("ERP MRERP Connection")["C1"]["master_options"] = json.dumps(master_rules.observe(
            None, "15:1", {**SMALL, "chart": CHART[:4]}, at="t"))
        self.assertIsNone(ledger.catalog("CO-1", dict(BOOK))["discount"])

    def test_masters_not_read_yet_queue_a_read(self):
        reset(masters=False)
        with self.assertRaises(contracts.CollectionError) as caught:
            ledger.catalog("CO-1", dict(BOOK))
        self.assertEqual(caught.exception.code, ledger.READING)
        self.assertEqual(ENQUEUED[0][0], "pearnly_erp_portal.mrerp.choices.run_read")

    def test_several_linked_menus_stop_before_the_draft(self):
        reset({"impfarev": [{"idmenu": "169", "label": "A", "linked": True}, {"idmenu": "171", "label": "B", "linked": True}]})
        with self.assertRaises(contracts.CollectionError) as caught:
            ledger.catalog("CO-1", dict(BOOK))
        self.assertEqual(caught.exception.code, ledger.MENU_NEEDED)
        self.assertEqual([o["code"] for o in caught.exception.detail["options"]], ["169", "171"])

    def test_one_money_method_per_collection(self):
        found = ledger.catalog("CO-1", dict(BOOK))
        two = {"invoices": [{"docnum": "SI000001-1", "amount": "100.00"}], "cash": "40.00",
               "methods": [{"prefix": "1112-01", "amount": "60.00"}]}
        open_rows = {"SI000001-1": {"available": "100.00", "remaining": "100.00"}}
        with self.assertRaises(contracts.CollectionError) as caught:
            ledger.check_methods(contracts.check(two, open_rows, found))
        self.assertEqual(caught.exception.code, ledger.ONE_METHOD)
        ledger.check_methods(contracts.check({**two, "cash": "0", "methods": [{"prefix": "1112-01", "amount": "95.00"},
                                                                            {"prefix": "5300-09", "amount": "5.00"}]},
                                             open_rows, found))  # 转账 + 折扣可以

    def test_open_invoices_are_read_in_the_background_and_landed_in_the_snapshot(self):
        self.assertEqual(ledger.request("CO-1", dict(BOOK), "C0001"), "reading")
        self.assertEqual(ENQUEUED[-1][0], "pearnly_erp_portal.collection.mrerp.run_read")
        self.assertEqual(ledger.request("CO-1", dict(BOOK), "C0001"), "reading")  # 在读的不重发
        self.assertEqual(len(ENQUEUED), 1)
        reads = []
        read = lambda lease, key, customer, menu="": reads.append((lease["username"], key, customer, menu)) or {  # noqa: E731
            "items": receivables.parse_bills(SELBILL), "menus": {}, "error": None}
        with _network(), patch.object(receivables, "read", read):
            ledger.run_read("CO-1", "15:1", "C0001")
        self.assertEqual(reads, [(USER, "15:1", "C0001", "")])
        self.assertEqual(SNAPSHOT[0][:3], ("CO-1", "15:1", "C0001"))
        self.assertEqual([(i["docnum"], i["remaining"], i["customer"]) for i in SNAPSHOT[0][3]],
                         [("SI000001-1", "870.00", "C0001")])
        self.assertEqual(ledger.request("CO-1", dict(BOOK), "C0001"), "ready")

    def test_prepare_full_partial_two_invoices_transfer_discount(self):
        doc = collection(cash="100.00")
        payload = ledger.prepare(doc)["payload"]
        self.assertEqual((payload["money"], payload["menu"], payload["notes"][2], payload["number"]),
                         ({"kind": "cash", "account": "", "amount": "100.00"}, "", "COL261116-AB12", "COL261116-AB12"))
        transfer = ledger.prepare(collection(name="COL2", payments=[
            {"role": contracts.TRANSFER, "code": "1112-02", "prefix": "1112-02", "amount": "95.00"},
            {"role": contracts.DISCOUNT, "code": "5300-09", "prefix": "5300-09", "amount": "5.00"}],
            invoices=[{"docnum": "SI000001-1", "amount": "60.00"}, {"docnum": "SI000001-2", "amount": "40.00"}]))["payload"]
        self.assertEqual(transfer["money"], {"kind": "transfer", "account": "1112-02", "amount": "95.00"})
        self.assertEqual(transfer["discount"], {"amount": "5.00", "account": "5300-09"})
        self.assertEqual(transfer["bills"], [{"docnum": "SI000001-1", "amount": "60.00"},
                                             {"docnum": "SI000001-2", "amount": "40.00"}])
        mixed = collection(name="COL3", cash="10.00", payments=[{"role": contracts.TRANSFER, "code": "1112-01",
                                                                  "amount": "90.00"}])
        self.assertEqual(ledger.prepare(mixed), {"failed": ledger.ONE_METHOD})

    def test_written_records_the_mrerp_number_and_rereads_the_customer(self):
        collection(cash="100.00")
        with _network(), _execute({"status": "success", "docnum": "FR1116-COL261116-AB12", "menus": {},
                                   "masters": {}, "meta": {"verify": {"state": "ok"}}}) as calls:
            ledger.run("COL261116-AB12")
        saved = DB.table("ERP Collection")["COL261116-AB12"]
        self.assertEqual((saved["status"], saved["docnum"], saved["reason"]),
                         (contracts.WRITTEN, "FR1116-COL261116-AB12", ""))
        self.assertEqual(calls[0]["secrets"], {"username": USER, "password": PASSWORD})
        self.assertEqual(calls[0]["target"], {"account_set": "15:1"})
        self.assertEqual(ENQUEUED[-1][0], "pearnly_erp_portal.collection.mrerp.run_read")  # 写后按客户重读
        self.assertEqual(TOLD, ["COL261116-AB12"])

    def test_accepted_but_not_found_is_result_unknown_and_not_resent(self):
        collection(cash="100.00")
        with _network(), _execute({"status": "unknown", "reason": "ERR_MRERP_READBACK_NOT_FOUND", "menus": {},
                                   "masters": {}}):
            ledger.run("COL261116-AB12")
        saved = DB.table("ERP Collection")["COL261116-AB12"]
        self.assertEqual(saved["status"], contracts.FAILED)
        self.assertTrue(saved["reason"].startswith("mrerp.result_unknown"))
        ENQUEUED.clear()
        ledger.poll()
        self.assertEqual(ENQUEUED, [])  # 失败的（含结果不明）不自动重排

    def test_a_menu_choice_from_the_import_is_menu_needed(self):
        self.assertEqual(ledger.settle({"status": "failed", "reason": "ERR_MRERP_MENU_CHOICE_REQUIRED"})["reason"],
                         ledger.MENU_NEEDED)

    def test_an_account_in_an_operator_entry_waits_in_the_queue(self):
        collection(cash="100.00")
        with _network(held=True), _execute({"status": "success"}) as calls:
            ledger.run("COL261116-AB12")
        self.assertEqual(calls, [])
        self.assertEqual(DB.table("ERP Collection")["COL261116-AB12"]["status"], contracts.QUEUED)

    def test_withdrawing_a_written_collection_hands_it_to_a_person_with_the_facts(self):
        doc = collection(status=contracts.WRITTEN, docnum="FR1116-COL261116-AB12", cash="100.00")
        self.assertTrue(ledger.withdraw(doc))
        saved = DB.table("ERP Collection")["COL261116-AB12"]
        self.assertEqual((saved["status"], saved["reason"], saved["docnum"]),
                         (contracts.WITHDRAWN, "express.voided_after_write", "FR1116-COL261116-AB12"))
        facts = ledger.voided_facts(_get_doc("ERP Collection", "COL261116-AB12"))
        self.assertEqual(facts, {"docnum": "FR1116-COL261116-AB12", "menu": "รับชำระหนี้",
                                 "invoices": [{"docnum": "SI000001-1", "amount": "100.00"}], "total": "100.00"})
        with self.assertRaises(contracts.CollectionError) as caught:
            ledger.withdraw(collection(name="COL4", status=contracts.WRITING))
        self.assertEqual(caught.exception.code, contracts.BUSY)
        self.assertIsNone(ledger.withdraw(collection(name="COL5", status=contracts.QUEUED)))


class _network:
    """导入 / 读取任务里才载入的共用层换成替身：账号占用。"""

    def __init__(self, held=False):
        self.held = held

    def __enter__(self):
        session_module = types.ModuleType("pearnly_core.mrerp.session")
        session_module.lock_key = lambda url, user: f"{url}|{user}"
        lock_module = types.ModuleType("pearnly_core.session_lock")
        lock_module.operator_holds = lambda key: self.held
        self.patch = patch.dict(sys.modules, {"pearnly_core.mrerp.session": session_module,
                                              "pearnly_core.session_lock": lock_module})
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


class _execute:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def __enter__(self):
        module = types.ModuleType("pearnly_core.mrerp.push.receipt")
        module.execute = lambda payload, target, secrets, url: self.calls.append(
            {"payload": payload, "target": target, "secrets": secrets}) or self.outcome
        self.patch = patch.dict(sys.modules, {"pearnly_core.mrerp.push.receipt": module})
        self.patch.start()
        return self.calls

    def __exit__(self, *exc):
        self.patch.stop()


# ── 导入执行对假 MR.ERP 站点（要 requests；CI 没有时跳过，候选检查在生产镜像里跑）──────────────────────────

try:
    import requests  # noqa: F401
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False


@unittest.skipUnless(HAVE_REQUESTS, "needs requests (runs in the candidate checks)")
class Execute(unittest.TestCase):
    """`pearnly_core.mrerp.push.receipt.execute` 对假站点：导入 5 步、回读认单号、销售员回落默认、菜单与结果不明。"""

    def _site(self, **kw):
        return FakeSite(**kw)

    def _run(self, site, **payload):
        import contextlib
        import json
        import socket

        import requests
        from pearnly_core.mrerp import account as mrerp_account
        from pearnly_core.mrerp import kick_mark, session_cache
        from pearnly_core.mrerp.push import receipt as importer

        def send(session, method, url, **kwargs):
            r = site(session, method, url, **kwargs)
            for hook in session.hooks["response"]:
                hook(r)
            return r

        body = {"number": "COL261116-AB12", "date": "2026-11-16", "customer": "C0001", "salesman": "", "department": "",
                "money": {"kind": "cash", "account": "", "amount": "100.00"}, "discount": {"amount": "0.00", "account": ""},
                "bills": [{"docnum": "SI000001-1", "amount": "100.00"}], "notes": ["", "", "COL261116-AB12"],
                "menu": "", "defaults": {}}
        body.update(payload)
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        with patch.object(requests.Session, "request", autospec=True, side_effect=send), \
                patch.object(socket, "getaddrinfo", return_value=public), \
                patch.object(mrerp_account, "account_lock", lambda *a, **k: contextlib.nullcontext(True)), \
                patch.object(kick_mark, "state", lambda account: None), \
                patch.object(session_cache, "get", lambda key: None), \
                patch.object(session_cache, "put", lambda key, value: None):
            return importer.execute(json.loads(json.dumps(body)), {"account_set": "15:1"},
                                    {"username": USER, "password": PASSWORD}, FakeSite.BASE)

    def test_cash_receipt_lands_and_is_read_back_with_its_number(self):
        site = self._site()
        outcome = self._run(site)
        self.assertEqual((outcome["status"], outcome["docnum"]), ("success", "FRCOL261116-AB12"))
        self.assertEqual(outcome["meta"]["verify"]["state"], "ok")
        row = cells(site.files[0], 1)
        self.assertEqual((row["H2"], row["F2"], row["J2"], row["L2"], row["N2"], row["P2"]),
                         ("1111-01", "สมชาย ใจดี", "1111-02", "4200-01", "5300-09", "1150-05"))
        self.assertEqual(site.uploads, [("impfarev", "169")])

    def test_transfer_goes_into_the_cash_column_and_a_known_salesman_is_kept(self):
        site = self._site()
        self._run(site, money={"kind": "transfer", "account": "1112-01", "amount": "100.00"}, salesman="E02")
        row = cells(site.files[0], 1)
        self.assertEqual((row["H2"], row["F2"]), ("1112-01", "สมหญิง ขายเก่ง"))

    def test_an_unknown_salesman_falls_back_to_the_default_employee(self):
        site = self._site()
        self._run(site, salesman="ไม่มีคนนี้")
        self.assertEqual(cells(site.files[0], 1)["F2"], "สมชาย ใจดี")

    def test_several_menus_need_a_choice_and_nothing_is_uploaded(self):
        site = self._site(menus=[("169", "A", True), ("171", "B", True)])
        outcome = self._run(site)
        self.assertEqual((outcome["status"], outcome["reason"]), ("failed", menus.CHOICE_REQUIRED))
        self.assertEqual(site.uploads, [])
        self.assertEqual(self._run(self._site(menus=[("169", "A", True), ("171", "B", True)]), menu="171")["status"],
                         "success")

    def test_accepted_but_not_in_the_list_is_unknown(self):
        outcome = self._run(self._site(lands=False))
        self.assertEqual((outcome["status"], outcome["reason"]), ("unknown", "ERR_MRERP_READBACK_NOT_FOUND"))


class FakeSite:
    """收款导入页、选单与 farev 列表的假 MR.ERP 站点（同 `test_cowork_mrerp_push.FakeMrErp` 的写法）。"""

    BASE = "https://mrerp.example.com"  # 假站点地址（仓库公开：不写真实站点）
    LISTS = {"fabillrev": {"txtothcombrh": [[1, "00000", "สำนักงานใหญ่"]], "txtothdept_h": [[1, "D001", "บัญชี"]],
                           "txtothsuborder_h": [[1, "00001", "ทั่วไป"]],
                           "txtemp": [[1, "E01", "นาย", "สมชาย ใจดี"], [2, "E02", "นาง", "สมหญิง ขายเก่ง"]]},
             "armas": {"txtacfile": [[1, "1111-01", "เงินสด"], [2, "1112-01", "เงินฝากธนาคาร"],
                                     [3, "5300-09", "ส่วนลดจ่าย"]], "inpdupdata": [[9, "C0001", "ลูกค้า"]]}}
    REFERENCE = "".join(f'<input type="text" name="txtacfile[{i}]" value="{code}">'
                        for i, code in ((1, "1111-02"), (2, "1111-01"), (5, "1150-05"), (18, "5300-09"), (20, "4200-01")))

    def __init__(self, menus=(("169", "รับชำระหนี้", True),), lands=True):
        self.menus, self.lands = list(menus), lands
        self.uploads, self.files, self.listed, self.accepted, self.pending = [], [], [], False, ""

    def __call__(self, session, method, url, **kwargs):
        import json

        import requests

        path = url.replace(self.BASE, "").split("?")[0]

        def reply(text="", content=None):
            r = requests.Response()
            r.url, r.status_code, r.encoding = url, 200, "utf-8"
            r._content = content if content is not None else text.encode()
            return r

        module = path.strip("/").split("/")[0]
        if path == "/login/checklogin.php":
            session.cookies.set("PHPSESSID", "abc")
            self.accepted = kwargs["data"]["txtpasswords"] == PASSWORD
            return reply()
        if path == "/login/mainmenu.php":
            return reply("<p>menu</p>") if self.accepted else reply('<form><input name="txtusers"></form>')
        if path == "/impfarev/formupload.php":
            options = "".join(f'<option value="{v}" data-chklac="{1 if ok else 2}">{label}</option>'
                              for v, label, ok in self.menus)
            return reply(f'<input type="hidden" name="idus" value="15"><select name="selmenu">{options}</select>')
        if path.endswith("/component/bshlistboxdata.php"):
            return reply(json.dumps(self.LISTS.get(module) or {}))
        if path == "/acreffile/acreffileform.php":
            return reply(self.REFERENCE)
        if path == "/artran/allform.php":
            return reply('<input type="text" id="txtothcombrh" value="00000">')
        if path == "/impfarev/component/uploadexcel.php":
            self.uploads.append((module, kwargs["data"]["selmenu"]))
            self.files.append(kwargs["files"]["uploadfile"][1])
            self.pending = cells(self.files[-1], 1)["A2"]
            return reply("")
        if path == "/impfarev/formrdpc.php":
            return reply('<form id="frmimport1"><input type="hidden" name="idus1" value="15">'
                         '<input type="checkbox" name="cbimport[2]" value="2" checked></form>')
        if path == "/impfarev/component/importpc.php":
            if self.lands:
                self.listed.append("FR" + self.pending)
            return reply("1")
        if path == "/farev/allview.php":
            return reply('<div id="showdata"></div>')
        if path == "/farev/component/showdata.php":
            want = kwargs["data"]["searchdataval"]
            rows = [d for d in self.listed if want in d]
            return reply("".join(f"<p><span>{d}</span><span>16/11/2569</span><span>ลูกค้า</span><span>100.00</span></p>"
                                 for d in rows) or '<p id="nodata">No Data</p>')
        return reply("<html></html>")


if __name__ == "__main__":
    unittest.main()
