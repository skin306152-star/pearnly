"""Express 回读（2026-10 CC 闭环 L1）：纯规则（读账结果校验、期初、总账、税报行、税务草稿记录）、与推送记录的比对、推送记录
取数，以及服务的发起 / 处理 / 只留最新 / 删除 / 给税务草稿的记录。不连库、不依赖 Frappe（最小替身），工作簿用替身
（真工作簿与三个解析器的验收在 `tests/test_cowork_readback_workbooks.py`，要 openpyxl）。"""
import gzip
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
for _name in [n for n in sys.modules if n.split(".")[0] == "pearnly_cowork"]:
    del sys.modules[_name]

from pearnly_cowork.push.express import pushed as pushed_rules  # noqa: E402
from pearnly_cowork.readback import contracts, diff  # noqa: E402

D = Decimal


def raw(**override):
    """CC 桌面端交回的读账结果（companion/src/companion/express_read.py 的形状）。"""
    data = {
        "version": 1, "period": "2026-05", "account_set": "T9SINCER", "read_at": "2026-10-13T05:00:00Z",
        "company": {"name": "บริษัท ซินเซียร์ จำกัด", "tax_id": "0315551000297", "branch": "00000"},
        "calendar": [{"index": 1, "slot": "", "beg": "2026-01-01", "end": "2026-01-31"}],
        "accounts": [{"code": "11-01-03-06", "name": "SAVING-K Bank", "name2": "", "group": "1", "level": "",
                      "parent": ""},
                     {"code": "41-01-00-00", "name": "รายได้จากการขาย", "name2": "", "group": "4", "level": "",
                      "parent": ""},
                     {"code": "51-03-00-00", "name": "ซื้อสุทธิ", "name2": "", "group": "5", "level": "",
                      "parent": ""}],
        "balances": [{"code": "11-01-03-06", "values": {"BEGLY": "1000.00", "DEBIT3LY": "200.00",
                                                        "CREDIT3LY": "50.00", "DEBIT1": "999.00"}},
                     {"code": "21-01-00-00", "values": {"BEGLY": "-300.00"}}],
        "movements": [
            {"code": "11-01-03-06", "month": "2025-12", "trntyp": "0", "amount": "10.00"},
            {"code": "11-01-03-06", "month": "2026-04", "trntyp": "1", "amount": "60.00"},
            {"code": "11-01-03-06", "month": "2026-05", "trntyp": "0", "amount": "535.00"},
            {"code": "11-01-03-06", "month": "2026-06", "trntyp": "0", "amount": "7.00"},
            {"code": "41-01-00-00", "month": "2026-05", "trntyp": "1", "amount": "500.00"},
        ],
        "journal": [
            {"voucher": "IV690503-001", "book": "03", "date": "2026-05-03", "descrp": "ขายเชื่อ", "docstat": "N",
             "trnstat": "P", "creby": "PEARNLY", "lines": [
                 {"seq": "2", "account": "11-01-03-06", "date": "2026-05-03", "descrp": "", "trntyp": "0",
                  "amount": "535.00", "dept": ""},
                 {"seq": "5", "account": "41-01-00-00", "date": "2026-05-03", "descrp": "ค่าน้ำแข็ง", "trntyp": "1",
                  "amount": "500.00", "dept": ""},
                 {"seq": "6", "account": "21-04-00-00", "date": "2026-05-03", "descrp": "", "trntyp": "1",
                  "amount": "35.00", "dept": ""}]},
            {"voucher": "IV690510-001", "book": "03", "date": "2026-05-10", "descrp": "ยกเลิก", "docstat": "C",
             "trnstat": "P", "creby": "AOM", "lines": [
                 {"seq": "2", "account": "11-01-03-06", "date": "2026-05-10", "descrp": "", "trntyp": "0",
                  "amount": "99.00", "dept": ""}]},
        ],
        "vat": [
            {"vatrec": "S", "rectyp": "3", "vatprd": "2026-05-01", "vatdat": "2026-05-03", "docdat": "2026-05-03",
             "docnum": "IV690503-001", "refnum": "INV-1", "descrp": "ร้าน B", "prenam": "", "taxid": "0105556012345",
             "branch": "00000", "docstat": "N", "amt01": "500.00", "vat01": "35.00", "extra": {}, "deleted": False},
            {"vatrec": "S", "rectyp": "3", "vatprd": "2026-05-01", "vatdat": "2026-05-10", "docdat": "2026-05-10",
             "docnum": "IV690510-001", "refnum": "", "descrp": "ร้าน C", "prenam": "", "taxid": "", "branch": "00000",
             "docstat": "C", "amt01": "99.00", "vat01": "6.93", "extra": {}, "deleted": False},
            {"vatrec": "P", "rectyp": "3", "vatprd": "2026-06-01", "vatdat": "2026-05-28", "docdat": "2026-04-30",
             "docnum": "RR690430-004", "refnum": "A-77", "descrp": "ร้าน A", "prenam": "บจก.",
             "taxid": "0105556099999", "branch": "00001", "docstat": "N", "amt01": "10.00", "vat01": "0.70",
             "extra": {"AMT02": "5.00"}, "deleted": False},
            {"vatrec": "P", "rectyp": "3", "vatprd": "2026-05-01", "vatdat": "2026-05-20", "docdat": "2026-05-20",
             "docnum": "RR690520-003", "refnum": "", "descrp": "", "prenam": "", "taxid": "", "branch": "",
             "docstat": "N", "amt01": "3.00", "vat01": "0.21", "extra": {}, "deleted": True},
        ],
        "documents": [
            {"table": "AR", "rectyp": "3", "docnum": "IV690503-001", "docdat": "2026-05-03", "ref": "INV-1",
             "party": "C001", "party_name": "ร้าน B", "party_taxid": "0105556012345", "flgvat": "1",
             "amount": "500.00", "netval": "500.00", "vatamt": "35.00", "netamt": "535.00", "docstat": "N",
             "userid": "PEARNLY", "deleted": False},
        ],
        "diagnostics": {"missing_tables": [], "bad_rows": {}, "unbalanced": [], "orphan_lines": 0},
    }
    data.update(override)
    return data


def parsed(**override):
    return contracts.parse(raw(**override), "2026-05")


def prototype_opening(begly, ly, lines, year, month):
    """测试机原型 xmake.opening 原样搬来当对照：BEGLY + ΣLY + 从读到的最早一年起、本月之前的行（TRNTYP '0' 借，其余贷）。"""
    first = min(day.year for day, _trntyp, _amount in lines)
    total = begly + sum(ly)
    for day, trntyp, amount in lines:
        if day.year >= first and (day.year, day.month) < (year, month):
            total += amount if trntyp == "0" else -amount
    return total


class Requests(unittest.TestCase):
    def test_months_across_years_and_leap_years(self):
        self.assertEqual(contracts.month_range("2026-12"), (date(2026, 12, 1), date(2026, 12, 31)))
        self.assertEqual(contracts.month_range("2028-02"), (date(2028, 2, 1), date(2028, 2, 29)))
        self.assertEqual(contracts.month_range("2026-02"), (date(2026, 2, 1), date(2026, 2, 28)))
        for bad in ("2026-13", "2026-5", "05/2026", "", None):
            with self.assertRaises(contracts.ContractError):
                contracts.period(bad)

    def test_account_prefixes_and_read_parameters(self):
        self.assertEqual(contracts.prefixes(["11-01", " 11-01", "4"]), ("11-01", "4"))
        self.assertEqual(contracts.prefixes(None), ())
        for bad in (["11 01"], "11-01", [1], ["x" * 21], ["1"] * 31):
            with self.assertRaises(contracts.ContractError):
                contracts.prefixes(bad)
        params = contracts.read_params("S:\\EXP69\\T9", "S:\\EXP69", "2026-05", ["RR1", "rr1", "", "X" * 21])
        self.assertEqual(params, {"account_set": "S:\\EXP69\\T9", "account_root": "S:\\EXP69", "period": "2026-05",
                                  "docnums": ["RR1"]})
        self.assertEqual(len(contracts.read_params("a", "", "2026-05", [f"D{n}" for n in range(5000)])["docnums"]),
                         contracts.DOCNUMS_MAX)
        # 点名的单据号放得进按需请求的参数上限（每条 ≤ 20 字）
        request_contracts = _load("request_contracts_for_readback", APP / "companion/request_contracts.py")
        biggest = contracts.read_params("S:\\" + "x" * 400, "S:\\" + "y" * 400, "2026-05",
                                        [f"{n:020d}" for n in range(contracts.DOCNUMS_MAX)])
        self.assertEqual(request_contracts.params(biggest), biggest)
        with self.assertRaises(contracts.ContractError):
            contracts.read_params("", "", "2026-05")


class Parse(unittest.TestCase):
    def test_amounts_become_two_decimal_decimals_and_dates_dates(self):
        data = parsed()
        self.assertEqual(data["balances"]["11-01-03-06"]["DEBIT3LY"], D("200.00"))
        self.assertEqual(data["vat"][0]["vatdat"], date(2026, 5, 3))
        self.assertEqual(data["journal"][0]["lines"][1]["amount"], D("500.00"))
        self.assertEqual(contracts.amount("1.005"), D("1.01"))
        self.assertEqual(data["accounts"]["41-01-00-00"]["name"], "รายได้จากการขาย")
        # 账套设置与对方档科目：没交回就是空（CC 桌面版照载荷写的那种）
        self.assertEqual(data["settings"], {"cash": "", "stock": "", "purchases": "", "periodic": False})
        self.assertEqual(data["documents"][0]["party_account"], "")
        settings = {"cash": "11-01-01-00", "stock": "11-04-00-00", "purchases": "51-03-00-00", "periodic": True}
        document = dict(raw()["documents"][0], party_account="11-03-01-00")
        data = parsed(settings=settings, documents=[document])
        self.assertEqual((data["settings"], data["documents"][0]["party_account"]), (settings, "11-03-01-00"))
        self.assertFalse(parsed(settings={"periodic": "N"})["settings"]["periodic"])

    def test_bad_results_are_refused(self):
        for override in ({"version": 2}, {"period": "2026-06"}, {"movements": [{"code": "1", "month": "2026-5",
                                                                                 "trntyp": "0", "amount": "1"}]},
                         {"balances": [{"code": "1", "values": {"BEGLY": "abc"}}]},
                         {"balances": [{"code": "1", "values": {"BEGLY": "NaN"}}]},
                         {"balances": [{"code": "1", "values": {"BEGLY": 1.5}}]},
                         {"balances": [{"code": "1", "values": {"BEGLY": "1E+30"}}]},
                         {"settings": []}, {"settings": {"cash": "1" * 31}},
                         {"balances": [{"code": "1", "values": {"x y": "1"}}]},
                         {"documents": [{"table": "XX"}]}, {"vat": [{"vatdat": "2026-13-01"}]},
                         {"journal": [{"voucher": "V" * 31}]}, {"accounts": "x"}, {"company": []}):
            with self.assertRaises(contracts.ContractError, msg=override):
                parsed(**override)
        with self.assertRaises(contracts.ContractError):
            contracts.parse([], "2026-05")


class OpeningAndLedger(unittest.TestCase):
    def test_opening_is_begly_plus_last_year_plus_earlier_lines_like_the_prototype(self):
        data = parsed()
        # 1000 + (200 − 50) + 2025-12 借 10 + 2026-04 贷 60；本月与之后的行不算；DEBIT1（今年的桶）不算
        self.assertEqual(contracts.opening("11-01-03-06", data), D("1100.00"))
        lines = [(date(2025, 12, 31), "0", D("10.00")), (date(2026, 4, 2), "1", D("60.00")),
                 (date(2026, 5, 3), "0", D("535.00")), (date(2026, 6, 1), "0", D("7.00"))]
        self.assertEqual(contracts.opening("11-01-03-06", data),
                         prototype_opening(D("1000.00"), [D("150.00")], lines, 2026, 5))
        self.assertEqual(contracts.opening("21-01-00-00", data), D("-300.00"))
        self.assertEqual(contracts.opening("99-99", data), D("0.00"))
        # 非 1 月起账（11 月起，CY 跨 2025–2026）：期初按日历月累计，与会计期间怎么划无关，口径不变
        november = [{"index": 1, "slot": "", "beg": "2025-11-01", "end": "2025-11-30"},
                    {"index": 3, "slot": "", "beg": "2026-01-01", "end": "2026-01-31"},
                    {"index": 1, "slot": "NY", "beg": "2026-11-01", "end": "2026-11-30"}]
        self.assertEqual(contracts.opening("11-01-03-06", parsed(calendar=november)), D("1100.00"))
        self.assertEqual(contracts.opening("11-01-03-06", contracts.parse(raw(period="2026-01"), "2026-01")),
                         prototype_opening(D("1000.00"), [D("150.00")], lines, 2026, 1))

    def test_ledger_lists_accounts_with_an_opening_or_lines_in_order_with_running_balances(self):
        accounts = contracts.ledger(parsed())
        self.assertEqual([item["code"] for item in accounts], ["11-01-03-06", "21-01-00-00", "21-04-00-00",
                                                               "41-01-00-00"])
        bank = accounts[0]
        self.assertEqual((bank["name"], bank["opening"], bank["debit"], bank["credit"], bank["closing"]),
                         ("SAVING-K Bank", D("1100.00"), D("535.00"), D("0.00"), D("1635.00")))
        # 作废的凭证（IV690510-001）不列；行的说明空着用凭证头的
        self.assertEqual([(line["voucher"], line["descrp"], line["balance"]) for line in bank["lines"]],
                         [("IV690503-001", "ขายเชื่อ", D("1635.00"))])
        revenue = accounts[-1]
        self.assertEqual((revenue["opening"], revenue["credit"], revenue["closing"], revenue["lines"][0]["debit"]),
                         (D("0.00"), D("500.00"), D("-500.00"), None))  # 贷方科目余额为负
        self.assertEqual([item["code"] for item in contracts.ledger(parsed(), ("11-01", "41"))],
                         ["11-01-03-06", "41-01-00-00"])

    def test_lines_sort_by_date_voucher_then_line_number(self):
        journal = [{"voucher": voucher, "book": "00", "date": day, "descrp": "", "docstat": "N", "creby": "",
                    "lines": [{"seq": seq, "account": "11", "date": day, "descrp": "", "trntyp": "0",
                               "amount": "1.00", "dept": ""} for seq in ("10", "2")]}
                   for voucher, day in (("JV2", "2026-05-02"), ("JV1", "2026-05-02"), ("JV0", "2026-05-09"))]
        lines = contracts.ledger(parsed(journal=journal, balances=[], movements=[]))[0]["lines"]
        self.assertEqual([(line["voucher"], line["seq"]) for line in lines],
                         [("JV1", "2"), ("JV1", "10"), ("JV2", "2"), ("JV2", "10"), ("JV0", "2"), ("JV0", "10")])

    def test_warnings_from_the_computer_and_the_data(self):
        diag = {"missing_tables": ["GLBAL"], "bad_rows": {"ISVAT": 2}, "unbalanced": ["HP1"], "orphan_lines": 3}
        journal = raw()["journal"][:1]
        journal[0] = {**journal[0], "lines": journal[0]["lines"] + [{"seq": "9", "account": "1", "date": "2026-05-03",
                                                                   "descrp": "", "trntyp": "X", "amount": "1"}]}
        codes = [item["code"] for item in contracts.warnings(parsed(diagnostics=diag, journal=journal))]
        self.assertEqual(codes, ["tables_missing", "unreadable_rows", "unbalanced_vouchers", "lines_without_voucher",
                                 "unknown_debit_credit", "other_vat_columns"])
        # 电脑交回的诊断形状不对：当没有，不挡出表（以前 bad_rows 是列表时抛 AttributeError，回读一直停在 reading）
        odd = {"missing_tables": "GLBAL", "bad_rows": ["ISVAT"], "unbalanced": "HP1", "orphan_lines": "3"}
        self.assertEqual([item["code"] for item in contracts.warnings(parsed(diagnostics=odd))], ["other_vat_columns"])


class VatRowsAndRecords(unittest.TestCase):
    def test_reports_take_the_tax_month_zero_the_cancelled_and_skip_deleted(self):
        data = parsed()
        sales = contracts.vat_rows(data, "S")
        self.assertEqual([(row["invoice"], row["ref"], row["base"], row["vat"], row["void"]) for row in sales],
                         [("INV-1", "IV690503-001", D("500.00"), D("35.00"), False),
                          ("IV690510-001", "IV690510-001", D("0.00"), D("0.00"), True)])
        # 进项：RR690430-004 的税期在下月（不进本月税报）；RR690520-003 软删
        self.assertEqual(contracts.vat_rows(data, "P"), [])
        self.assertEqual(contracts.totals(sales), {"count": 2, "before_vat": "500.00", "vat": "35.00",
                                                   "total": "535.00"})

    def test_records_for_tax_drafts_follow_the_agreed_shape(self):
        records = contracts.vat_records(parsed(), {"iv690503-001": "H-1"})
        self.assertEqual([record["docnum"] for record in records],
                         ["RR690520-003", "RR690430-004", "IV690503-001", "IV690510-001"])
        purchase = records[1]
        self.assertEqual(purchase, {"side": "P", "rectyp": "3", "period": "2026-06", "tax_date": "2026-05-28",
                                    "doc_date": "2026-04-30", "docnum": "RR690430-004", "refnum": "A-77",
                                    "name": "ร้าน A", "prefix": "บจก.", "tax_id": "0105556099999", "branch": "00001",
                                    "amount": "10.00", "vat": "0.70", "status": "N", "deleted": False})
        self.assertTrue(records[0]["deleted"])
        self.assertEqual(records[2]["history"], "H-1")
        self.assertNotIn("history", records[3])
        self.assertEqual(records[3]["status"], "C")
        # 本月税务记录的单号（顺延抵扣的进项 RR690430-004 单据日在 4 月）：按它找推送记录，记录才带得上 history
        self.assertEqual(diff.tax_numbers(parsed()), ["IV690503-001", "IV690510-001", "RR690430-004", "RR690520-003"])


def pushed_item(**override):
    item = {"record": "CWP-1", "history": "H-1", "docnum": "IV690503-001", "direction": "sales", "doctype": "IV",
            "date": "2026-05-03", "ref_no": "INV-1", "party_code": "C001", "party_name": "ร้าน B",
            "party_tax_id": "0105556012345", "base": "500.00", "vat": "35.00", "total": "535.00",
            "lines": [{"acc": "11-01-03-06", "side": "D", "amount": "535.00"},
                      {"acc": "41-01-00-00", "side": "C", "amount": "500.00"},
                      {"acc": "21-04-00-00", "side": "C", "amount": "35.00"}], "stock": False}
    item.update(override)
    return item


class Compare(unittest.TestCase):
    def changes(self, item, **override):
        found = diff.compare([item], parsed(**override))
        return [(row["change"], row["field"], row["pushed"], row["now"]) for row in found["changes"]], found

    def test_an_untouched_document_has_no_changes(self):
        changes, found = self.changes(pushed_item())
        self.assertEqual(changes, [])
        self.assertEqual(found["counts"], {"pushed": 1, "unchanged": 1, "changed": 0, "deleted": 0, "missing": 0,
                                           "voided": 0, "direct": 0})

    def test_each_changed_field_is_listed_with_before_and_after(self):
        document = dict(raw()["documents"][0], docdat="2026-05-04", rectyp="1", party="C009", party_name="ร้าน X",
                        ref="INV-2", netval="400.00", vatamt="28.00", netamt="428.00")
        changes, found = self.changes(pushed_item(), documents=[document])
        self.assertEqual(changes, [
            ("changed", "date", "2026-05-03", "2026-05-04"), ("changed", "kind", "IV", "HS"),
            ("changed", "party", "C001 ร้าน B", "C009 ร้าน X"), ("changed", "ref_no", "INV-1", "INV-2"),
            ("changed", "before_vat", "500.00", "400.00"), ("changed", "vat", "35.00", "28.00"),
            ("changed", "total", "535.00", "428.00")])
        self.assertEqual(found["counts"]["changed"], 1)
        self.assertEqual(found["changes"][0]["history"], "H-1")

    def test_vat_record_and_entries(self):
        journal = raw()["journal"]
        journal[0] = {**journal[0], "lines": journal[0]["lines"][:2] + [
            {"seq": "6", "account": "21-04-00-00", "date": "2026-05-03", "descrp": "", "trntyp": "1",
             "amount": "30.00", "dept": ""}, {"seq": "7", "account": "53-00", "date": "2026-05-03", "descrp": "",
                                              "trntyp": "1", "amount": "5.00", "dept": ""}]}
        changes, _found = self.changes(pushed_item(), journal=journal, vat=raw()["vat"][1:])
        self.assertEqual(changes, [
            ("changed", "vat_record", "35.00", ""),
            ("changed", "entries", "11-01-03-06 +535.00; 21-04-00-00 -35.00; 41-01-00-00 -500.00",
             "11-01-03-06 +535.00; 21-04-00-00 -30.00; 41-01-00-00 -500.00; 53-00 -5.00")])
        vat = dict(raw()["vat"][0], amt01="499.00")
        changes, _found = self.changes(pushed_item(stock=True), journal=journal, vat=[vat])
        # 存货单只比表头（小助手另写重分类分录）
        self.assertEqual(changes, [("changed", "vat_before_vat", "500.00", "499.00")])

    def test_deleted_missing_and_cancelled(self):
        deleted = dict(raw()["documents"][0], deleted=True)
        self.assertEqual(self.changes(pushed_item(), documents=[deleted])[0], [("deleted", "", "", "")])
        self.assertEqual(self.changes(pushed_item(), documents=[])[0], [("missing", "", "", "")])
        voided = dict(raw()["documents"][0], docstat="C")
        changes, found = self.changes(pushed_item(), documents=[voided])
        self.assertEqual((changes, found["counts"]["voided"]), ([("voided", "", "", "")], 1))

    def test_a_purchase_changed_from_credit_to_cash(self):
        document = {"table": "AP", "rectyp": "1", "docnum": "RR690502-001", "docdat": "2026-05-02", "ref": "INV-9",
                    "party": "S001", "party_name": "ร้าน A", "party_taxid": "", "flgvat": "1", "amount": "1000.00",
                    "netval": "1000.00", "vatamt": "0.00", "netamt": "1000.00", "docstat": "N", "userid": "PEARNLY",
                    "deleted": False}
        item = pushed_item(docnum="RR690502-001", direction="purchase", doctype="RR", date="2026-05-02",
                           ref_no="INV-9", party_code="S001", base="1000.00", vat="0.00", total="1000.00", lines=[])
        changes, _found = self.changes(item, documents=[document])
        self.assertEqual(changes, [("changed", "kind", "RR", "HP")])
        self.assertEqual(diff.kind(dict(document, rectyp="9", docnum="PS690502-001")), "PS")

    def test_a_new_party_is_compared_by_tax_id(self):
        document = dict(raw()["documents"][0], party_taxid="0999999999999")
        changes, _found = self.changes(pushed_item(party_code=""), documents=[document])
        self.assertEqual(changes, [("changed", "party", "0105556012345 ร้าน B", "0999999999999 ร้าน B")])

    def test_entries_made_directly_in_express(self):
        documents = raw()["documents"] + [
            {"table": "AP", "rectyp": "1", "docnum": "HP690512-001", "docdat": "2026-05-12", "ref": "R9",
             "party": "S9", "party_name": "ร้าน D", "party_taxid": "", "flgvat": "1", "amount": "100.00",
             "netval": "100.00", "vatamt": "7.00", "netamt": "107.00", "docstat": "N", "userid": "AOM",
             "deleted": False},
            {"table": "AP", "rectyp": "1", "docnum": "HP690513-001", "docdat": "2026-05-13", "ref": "",
             "party": "", "party_name": "", "party_taxid": "", "flgvat": "", "amount": "1.00", "netval": "1.00",
             "vatamt": "0", "netamt": "1.00", "docstat": "C", "userid": "AOM", "deleted": False}]
        journal = raw()["journal"] + [
            {"voucher": "JV690531-001", "book": "00", "date": "2026-05-31", "descrp": "ค่าธรรมเนียม", "docstat": "N",
             "trnstat": "P", "creby": "BEE", "lines": [
                 {"seq": "1", "account": "53-00", "date": "2026-05-31", "descrp": "", "trntyp": "0", "amount": "20",
                  "dept": ""},
                 {"seq": "2", "account": "11-01-03-06", "date": "2026-05-31", "descrp": "", "trntyp": "1",
                  "amount": "20", "dept": ""}]},
            {"voucher": "HP690512-001", "book": "01", "date": "2026-05-12", "descrp": "", "docstat": "N",
             "trnstat": "P", "creby": "AOM", "lines": []}]
        found = diff.compare([pushed_item()], parsed(documents=documents, journal=journal))
        self.assertEqual(found["direct"], [
            {"docnum": "HP690512-001", "date": "2026-05-12", "kind": "HP", "party": "S9 ร้าน D", "before_vat": "100.00",
             "vat": "7.00", "total": "107.00", "entered_by": "AOM", "book": ""},
            {"docnum": "JV690531-001", "date": "2026-05-31", "kind": "", "party": "", "before_vat": "", "vat": "",
             "total": "20.00", "entered_by": "BEE", "book": "00"}])
        self.assertEqual(found["counts"]["direct"], 2)
        self.assertEqual(diff.month_numbers(parsed(documents=documents, journal=journal)),
                         ["HP690512-001", "HP690513-001", "IV690503-001", "IV690510-001", "JV690531-001"])


    def test_a_push_found_only_for_its_tax_record_is_not_compared(self):
        # 顺延抵扣的进项：单据日在 4 月，只因 5 月的税务记录按单号查来（为了 history），4 月的单不在这次读的账里
        item = pushed_item(docnum="RR690430-004", direction="purchase", doctype="RR", date="2026-04-30", lines=[])
        found = diff.compare([pushed_item(), item], parsed())
        self.assertEqual((found["changes"], found["counts"]["pushed"], found["counts"]["missing"]), ([], 1, 0))


def entry_case(doctype, pushed_lines, now_lines, settings=None, party_account=""):
    """一张推送过的单（载荷的分录 pushed_lines）与它在 Express 里的凭证（now_lines：[(科目, 借正贷负)]）的比对结果。"""
    purchase = doctype in ("RR", "HP")
    number = f"{doctype}690512-001"
    rectyp = "1" if doctype in ("HP", "HS") else "3"
    document = {"table": "AP" if purchase else "AR", "rectyp": rectyp, "docnum": number, "docdat": "2026-05-12",
                "ref": "A-77", "party": "P001", "party_name": "ร้าน A", "party_taxid": "",
                "party_account": party_account, "flgvat": "1", "amount": "100.00", "netval": "100.00", "vatamt": "7.00", "netamt": "107.00",
                "docstat": "N", "userid": "PEARNLY", "deleted": False}
    vat = {"vatrec": "P" if purchase else "S", "rectyp": rectyp, "vatprd": "2026-05-01", "vatdat": "2026-05-12",
           "docdat": "2026-05-12", "docnum": number, "refnum": "A-77", "descrp": "ร้าน A", "prenam": "", "taxid": "",
           "branch": "00000", "docstat": "N", "amt01": "100.00", "vat01": "7.00", "extra": {}, "deleted": False}
    voucher = {"voucher": number, "book": "01", "date": "2026-05-12", "descrp": "", "docstat": "N", "trnstat": "P",
               "creby": "PEARNLY", "lines": [
                   {"seq": str(seq), "account": account, "date": "2026-05-12", "descrp": "",
                    "trntyp": "0" if value > 0 else "1", "amount": f"{abs(value):.2f}", "dept": ""}
                   for seq, (account, value) in enumerate(now_lines)]}
    item = pushed_item(docnum=number, direction="purchase" if purchase else "sales", doctype=doctype,
                       date="2026-05-12", ref_no="A-77", party_code="P001", party_name="ร้าน A", base="100.00",
                       vat="7.00", total="107.00",
                       lines=[{"acc": acc, "side": side, "amount": amount} for acc, side, amount in pushed_lines])
    found = diff.compare([item], parsed(documents=[document], vat=[vat], journal=[voucher], settings=settings or {}))
    return [(row["field"], row["pushed"], row["now"]) for row in found["changes"]]


CASH, BANK, AP, AP_SUPPLIER, AR, INPUT_VAT, OUTPUT_VAT = (
    "11-01-01-00", "11-01-03-06", "21-02-01-00", "21-01-00-02", "11-03-01-00", "11-05-00-00", "21-04-00-00")
SETTINGS = {"cash": CASH, "stock": "11-04-00-00", "purchases": "51-03-00-00", "periodic": False}
PURCHASE = [("51-03-00-00", "D", "100.00"), (INPUT_VAT, "D", "7.00"), (AP, "C", "107.00")]
SALE = [(AR, "D", "107.00"), ("41-01-00-00", "C", "100.00"), (OUTPUT_VAT, "C", "7.00")]


class EntriesAsWritten(unittest.TestCase):
    """分录按 CC 桌面版实际写进 Express 的科目比（`dbf_writer._write_all` / `dbf_sales._write_all` 的换法），没人动过的单
    不报改过；新旧两种写法都认，换成别的科目照报。"""

    def test_a_cash_purchase_and_a_cash_sale_paid_from_the_cash_account_are_unchanged(self):
        self.assertEqual(entry_case("HP", PURCHASE, [("51-03-00-00", 100), (INPUT_VAT, 7), (CASH, -107)], SETTINGS), [])
        self.assertEqual(entry_case("HS", SALE, [(CASH, 107), ("41-01-00-00", -100), (OUTPUT_VAT, -7)], SETTINGS), [])
        # 2026-10-08 以前的 CC 桌面版照载荷写（贷应付）：也不算改过
        self.assertEqual(entry_case("HP", PURCHASE, [("51-03-00-00", 100), (INPUT_VAT, 7), (AP, -107)], SETTINGS), [])

    def test_credit_documents_use_the_partys_control_account(self):
        now = [("51-03-00-00", 100), (INPUT_VAT, 7), (AP_SUPPLIER, -107)]
        self.assertEqual(entry_case("RR", PURCHASE, now, SETTINGS, party_account=AP_SUPPLIER), [])
        sale = [("11-03-02-00", 107), ("41-01-00-00", -100), (OUTPUT_VAT, -7)]
        self.assertEqual(entry_case("IV", SALE, sale, SETTINGS, party_account="11-03-02-00"), [])
        # 对方档上没挂这个科目：照报改过
        self.assertEqual(entry_case("RR", PURCHASE, now, SETTINGS)[0][0], "entries")

    def test_a_periodic_account_set_books_stock_purchases_to_the_purchases_account(self):
        pushed = [("11-04-00-00", "D", "100.00"), (INPUT_VAT, "D", "7.00"), (AP, "C", "107.00")]
        now = [("51-03-00-00", 100), (INPUT_VAT, 7), (AP, -107)]
        self.assertEqual(entry_case("RR", pushed, now, dict(SETTINGS, periodic=True)), [])
        self.assertEqual(entry_case("RR", pushed, [("11-04-00-00", 100), (INPUT_VAT, 7), (AP, -107)],
                                    dict(SETTINGS, periodic=True)), [])
        # 永续盘存的账套不换：记到购货科目是会计改的
        self.assertEqual(entry_case("RR", pushed, now, SETTINGS), [
            ("entries", "11-04-00-00 +100.00; 11-05-00-00 +7.00; 21-02-01-00 -107.00",
             "11-05-00-00 +7.00; 21-02-01-00 -107.00; 51-03-00-00 +100.00")])

    def test_a_payment_moved_to_another_account_or_a_reclassified_expense_is_a_change(self):
        self.assertEqual(entry_case("HP", PURCHASE, [("51-03-00-00", 100), (INPUT_VAT, 7), (BANK, -107)], SETTINGS), [
            ("entries", "11-01-01-00 -107.00; 11-05-00-00 +7.00; 51-03-00-00 +100.00",
             "11-01-03-06 -107.00; 11-05-00-00 +7.00; 51-03-00-00 +100.00")])
        self.assertEqual(entry_case("HP", PURCHASE, [("52-08-00-00", 100), (INPUT_VAT, 7), (CASH, -107)], SETTINGS), [
            ("entries", "11-01-01-00 -107.00; 11-05-00-00 +7.00; 51-03-00-00 +100.00",
             "11-01-01-00 -107.00; 11-05-00-00 +7.00; 52-08-00-00 +100.00")])
        changed = entry_case("HP", PURCHASE, [("51-03-00-00", 100), (INPUT_VAT, 7), (CASH, -100)], SETTINGS)
        self.assertEqual(changed[0][1], "11-01-01-00 -107.00; 11-05-00-00 +7.00; 51-03-00-00 +100.00")


class PushedRecords(unittest.TestCase):
    def test_a_push_record_gives_what_the_comparison_needs(self):
        payload = {"direction": "purchase", "doctype": "rr", "docdate_be": "690515", "ref_no": " INV-9 ",
                   "supplier": {"code": "", "name": "ร้าน A", "tax_id": "0105556012345"}, "base_amount": "1,000",
                   "vat_amount": 70, "total_amount": "1070.00",
                   "lines": [{"acc": "51-03", "side": "D", "amount": "1000"}, {"acc": "", "side": "D"},
                             {"acc": "21-01", "side": "X", "amount": "1"}]}
        response = {"express_docnum": "RR690515-001", "meta": {"party_code": "S0042"},
                    "line_modes": [{"mode": "stock_item"}]}
        item = pushed_rules.project("CWP-1", "H-1", "RR690515-001", payload, response)
        self.assertEqual(item, {"record": "CWP-1", "history": "H-1", "docnum": "RR690515-001",
                                "direction": "purchase", "doctype": "RR", "date": "2026-05-15", "ref_no": "INV-9",
                                "party_code": "S0042", "party_name": "ร้าน A", "party_tax_id": "0105556012345",
                                "base": "1000.00", "vat": "70.00", "total": "1070.00",
                                "lines": [{"acc": "51-03", "side": "D", "amount": "1000.00"}], "stock": True})
        self.assertIsNone(pushed_rules.project("CWP-2", "H", "X", {"direction": "other"}, {}))
        self.assertIsNone(pushed_rules.project("CWP-2", "H", "", payload, {}))
        self.assertEqual(pushed_rules.be_date("700229"), None)  # 2027 不是闰年
        self.assertEqual(pushed_rules.be_date("710229"), date(2028, 2, 29))
        self.assertTrue(pushed_rules.in_window(item, date(2026, 5, 1), date(2026, 5, 31)))
        self.assertFalse(pushed_rules.in_window(item, date(2026, 6, 1), date(2026, 6, 30)))
        self.assertFalse(pushed_rules.in_window(item, None, None))

    def test_a_long_supplier_invoice_number_is_compared_as_written(self):
        # REFNUM 只有 15 字节：CC 桌面版先去分隔符、还放不下留后 15 字节（`purchase_adapter.fit_ref_no`）
        self.assertEqual(pushed_rules.fit_ref_no("G0012-69-0045871"), "G0012690045871")
        self.assertEqual(pushed_rules.fit_ref_no(" INV-9 "), "INV-9")
        self.assertEqual(pushed_rules.fit_ref_no("AB/CD.EF GH-IJKLMNOPQ"), "CDEFGHIJKLMNOPQ")
        self.assertEqual(pushed_rules.fit_ref_no("ใบกำกับภาษีเลขที่-12345"), "ภาษีเลขที่12345")
        payload = {"direction": "purchase", "doctype": "RR", "docdate_be": "690512", "ref_no": "G0012-69-0045871",
                   "supplier": {"code": "S001"}, "lines": []}
        item = pushed_rules.project("CWP-1", "H-1", "RR690512-001", payload, {})
        self.assertEqual(item["ref_no"], "G0012690045871")
        document = {"table": "AP", "rectyp": "3", "docnum": "RR690512-001", "docdat": "2026-05-12",
                    "ref": "G0012690045871", "party": "S001", "party_name": "", "party_taxid": "", "flgvat": "1",
                    "amount": "0", "netval": "0", "vatamt": "0", "netamt": "0", "docstat": "N", "userid": "PEARNLY",
                    "deleted": False}
        self.assertEqual(diff.compare([item], parsed(documents=[document]))["changes"], [])
        # 销项的号是自家开的票：照原样（超长 CC 桌面版直接拦下，不缩）
        sale = dict(payload, direction="sales", ref_no="IV-2026-05-000001", customer={"code": "C1"})
        self.assertEqual(pushed_rules.project("CWP-2", "H", "IV1", sale, {})["ref_no"], "IV-2026-05-000001")


# ── 服务（Frappe 与各域服务用替身） ────────────────────────────────────────


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value

    def update(self, values=None, **more):
        dict.update(self, values or {}, **more)


class ServiceHarness:
    def __init__(self, folder):
        self.rows, self.after, self.requests = {}, [], {}
        self.user = "a@example.com"
        self.calls = []
        self.presence = {}
        self.pushed = []
        self.broken = False
        frappe = types.ModuleType("frappe")

        class ValidationError(Exception):
            pass

        class PermissionError(Exception):
            pass

        class DoesNotExistError(Exception):
            pass

        frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError = (
            ValidationError, PermissionError, DoesNotExistError)
        frappe._ = lambda text: text
        frappe.session = types.SimpleNamespace(user=self.user)
        frappe.clear_messages = lambda: None
        frappe.log_error = lambda title=None, message=None: self.calls.append(("log_error", title))
        frappe.get_traceback = lambda: "Traceback"

        def throw(message, exc=None):
            raise (exc or ValidationError)(message)

        frappe.throw = throw
        frappe.get_site_path = lambda *parts: str(pathlib.Path(folder, *parts))
        frappe.new_doc = lambda doctype: Row(name=None, _new=True)
        frappe.get_doc = lambda doctype, name, for_update=False: (self.rows[name] if doctype.endswith("Readback")
                                                                  else Row(name=name))
        frappe.db = types.SimpleNamespace(
            sql=self.sql, exists=lambda doctype, name: name in self.rows,
            delete=lambda doctype, filters: self.rows.pop(filters["name"], None),
            after_commit=types.SimpleNamespace(add=self.after.append))
        utils = types.ModuleType("frappe.utils")
        utils.get_datetime = lambda value: value
        sys.modules["frappe"], sys.modules["frappe.utils"] = frappe, utils
        self.frappe = frappe
        stubs = {
            "pearnly_cowork.access.scope": dict(require=lambda feature: self.calls.append(("scope", feature))),
            "pearnly_cowork.companion.request_service": dict(
                create=self.create, status=self.request_status, result=self.result,
                drop=lambda name: self.calls.append(("drop", name)) or self.requests.pop(name, None),
                waiting=lambda connection, kind, known="": dict(self.presence)),
            "pearnly_cowork.connections.access": dict(require_connection=lambda doc: self.calls.append(
                ("connection", doc.name))),
            "pearnly_cowork.connections.service": dict(
                targets=lambda name: {"account_sets": [{"source_id": "S:\\EXP69\\T9SINCER", "label": "T9 SINCERE",
                                                        "attributes": {"path": "S:\\EXP69\\T9SINCER",
                                                                       "root": "S:\\EXP69"}}]},
                adapter_of=lambda name: "mrerp" if name == "M1" else "express",
                detail=lambda name: {"config": {"companion_version": "1.5.0"}}),
            "pearnly_cowork.push.application.service": dict(pushed_express=self.pushed_express),
            "pearnly_cowork.readback.workbooks": dict(
                general_ledger=self.general_ledger,
                vat_report=lambda side, company, period, rows: json.dumps(["VAT", side, len(rows)]).encode(),
                changes=lambda found, labels: json.dumps(["CH", found["counts"], labels["sheet_changes"]]).encode()),
        }
        for name in ("pearnly_cowork.access", "pearnly_cowork.companion", "pearnly_cowork.connections",
                     "pearnly_cowork.push", "pearnly_cowork.push.application"):
            sys.modules[name] = types.ModuleType(name)
        for name, attrs in stubs.items():
            module = types.ModuleType(name)
            module.__dict__.update(attrs)
            sys.modules[name] = module
            parent, _, child = name.rpartition(".")
            if parent in sys.modules:
                setattr(sys.modules[parent], child, module)
        for name in ("access", "storage", "service"):
            sys.modules.pop("pearnly_cowork.readback." + name, None)
        access = _load("pearnly_cowork.readback.access", APP / "readback/access.py")
        access.write = self.write
        access.read = lambda name, for_update=False: self.rows[name]
        self.storage = _load("pearnly_cowork.readback.storage", APP / "readback/storage.py")
        self.service = _load("pearnly_cowork.readback.service", APP / "readback/service.py")

    # Frappe 替身
    def write(self, row):
        if row.get("_new"):
            row.pop("_new")
            row.name = row.readback_id
        self.rows[row.name] = row
        return row

    def sql(self, query, values=()):
        query = " ".join(query.split())
        if "lookup = %s AND actor = %s AND status IN" in query:
            lookup, actor, *statuses = values
            found = sorted((row for row in self.rows.values() if row.lookup == lookup and row.actor == actor
                            and row.status in statuses), key=lambda row: row.requested_at, reverse=True)
            return [(found[0].name,)] if found else []
        if "lookup = %s AND actor = %s AND name != %s AND requested_at <= %s" in query:
            lookup, actor, name, at = values
            return [(row.name,) for row in list(self.rows.values())
                    if row.lookup == lookup and row.actor == actor and row.name != name and row.requested_at <= at]
        raise AssertionError(query)

    def commit(self):
        for callback in self.after:
            callback()
        self.after.clear()

    # 按需请求替身
    def create(self, kind, connection, params, key):
        name = hashlib.sha256(key.encode()).hexdigest()[:32]
        self.requests[name] = {"request": name, "kind": kind, "status": "requested", "code": "", "params": params}
        self.calls.append(("create", kind, connection, params, key))
        return dict(self.requests[name])

    def request_status(self, name):
        if name not in self.requests:
            raise self.frappe.DoesNotExistError("companion.on_demand_missing")
        return dict(self.requests[name])

    def result(self, name):
        return self.requests[name]["content"], {}

    def finish(self, name, data):
        self.requests[name].update(status="done", content=gzip.compress(json.dumps(data).encode()))

    def pushed_express(self, connection, key, first, last, docnums=None):
        self.calls.append(("pushed", connection, key, first, last, docnums))
        return [item for item in self.pushed if item["docnum"] in (docnums or ())
                or first.isoformat() <= item["date"] <= last.isoformat()]

    def general_ledger(self, company, period, accounts, printed):
        if self.broken:  # 出表时的意外（例如说明里有 Excel 不收的控制字符）
            raise ValueError("cannot write this cell")
        return json.dumps(["GL", period, [a["code"] for a in accounts], printed.isoformat()]).encode()


class Service(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.h = ServiceHarness(self.folder.name)
        self.service = self.h.service
        self.h.pushed = [pushed_item()]

    def tearDown(self):
        for name in [n for n in sys.modules if n.split(".")[0] in ("pearnly_cowork", "frappe")]:
            del sys.modules[name]
        sys.modules["pearnly_cowork.readback.contracts"] = contracts
        sys.modules["pearnly_cowork.readback.diff"] = diff

    def _start(self, **kwargs):
        made = self.service.request("EXP1", "s:/exp69/t9sincer/", "2026-05", kwargs.get("accounts"))
        return made, self.h.rows[made["readback"]]

    def test_a_request_names_the_pushed_documents_and_waits_for_the_computer(self):
        made, row = self._start(accounts=["11-01"])
        self.assertEqual((made["status"], made["account_set"], made["period"]), ("reading", "T9 SINCERE", "2026-05"))
        create = next(call for call in self.h.calls if call[0] == "create")
        self.assertEqual(create[1:4], ("express_read", "EXP1", {
            "account_set": "S:\\EXP69\\T9SINCER", "account_root": "S:\\EXP69", "period": "2026-05",
            "docnums": ["IV690503-001"]}))
        self.assertEqual(create[4], "readback:" + made["readback"])
        self.assertEqual((row.account_set_key, json.loads(row.params_json)),
                         ("s:\\exp69\\t9sincer", {"accounts": ["11-01"], "folder": "T9SINCER"}))
        # 还在读时再请求同一份：回同一份，不另建；总账要的科目不同才另读一份
        again = self.service.request("EXP1", "S:\\EXP69\\T9SINCER", "2026-05", ["11-01"])
        self.assertEqual(again["readback"], made["readback"])
        self.assertEqual(len([call for call in self.h.calls if call[0] == "create"]), 1)
        whole = self.service.request("EXP1", "S:\\EXP69\\T9SINCER", "2026-05")
        self.assertNotEqual(whole["readback"], made["readback"])
        # 先发起的那份后读完：不删后发起、还在读的那份
        self.h.finish(row.request, raw())
        self.assertEqual(self.service.status(made["readback"])["status"], "ready")
        self.assertEqual(set(self.h.rows), {made["readback"], whole["readback"]})

    def test_an_offline_or_old_computer_gets_no_request(self):
        self.h.presence = {"status": "waiting_for_cc", "reason": "offline"}
        self.assertEqual(self.service.request("EXP1", "S:\\EXP69\\T9SINCER", "2026-05"),
                         {"status": "waiting_for_cc", "reason": "offline"})
        self.assertEqual(self.h.rows, {})
        for args, code in ((("M1", "S:\\EXP69\\T9SINCER", "2026-05"), "cowork.readback.not_express"),
                           (("EXP1", "S:\\EXP69\\OTHER", "2026-05"), "cowork.readback.account_missing"),
                           (("EXP1", "S:\\EXP69\\T9SINCER", "2026-5"), "cowork.readback.period_invalid")):
            with self.assertRaises(self.h.frappe.ValidationError) as caught:
                self.service.request(*args)
            self.assertEqual(str(caught.exception), code)

    def test_a_finished_read_becomes_four_files_and_only_the_latest_is_kept(self):
        made, row = self._start()
        self.assertEqual(self.service.status(made["readback"])["status"], "reading")
        self.h.finish(row.request, raw())
        ready = self.service.status(made["readback"])
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["files"], ["changes", "general_ledger", "purchase_vat", "sales_vat"])
        self.assertEqual(ready["summary"]["sales_vat"], {"count": 2, "before_vat": "500.00", "vat": "35.00",
                                                          "total": "535.00"})
        self.assertEqual((ready["summary"]["read_at"], ready["summary"]["pushed"]["unchanged"]),
                         ("2026-10-13 12:00", 1))
        name, content = self.service.file(made["readback"], "general_ledger")
        self.assertEqual(name, "T9SINCER-GL-2026-05.xlsx")
        self.assertEqual(json.loads(content)[3], "2026-10-13")  # 打印日期按曼谷时间
        self.assertIn(("drop", row.request), self.h.calls)  # 结果取走即删
        # 推送记录按本月回读到的单据 / 凭证号与本月税务记录的单号再查一遍
        self.assertEqual(self.h.calls[-1 - [c[0] for c in reversed(self.h.calls)].index("pushed")][5],
                         ["IV690503-001", "IV690510-001", "RR690430-004", "RR690520-003"])
        records = self.service.vat_records("EXP1", "S:\\EXP69\\T9SINCER", 2026, 5)
        self.assertEqual((records["ref"], records["read_at"], records["header"]),
                         (made["readback"], "2026-10-13T05:00:00Z",
                          {"company": "บริษัท ซินเซียร์ จำกัด", "tax_id": "0315551000297", "branch": "00000"}))
        self.assertEqual(records["records"][2]["history"], "H-1")
        self.assertIsNone(self.service.vat_records("EXP1", "S:\\EXP69\\T9SINCER", 2026, 6))
        self.assertEqual(self.service.data(made["readback"])["history"], {"IV690503-001": "H-1"})
        # 再读一次同一月：新的一份做完后，旧的连文件删掉
        second, second_row = self._start()
        self.assertNotEqual(second["readback"], made["readback"])
        self.h.finish(second_row.request, raw())
        self.assertEqual(self.service.status(second["readback"])["status"], "ready")
        self.h.commit()
        self.assertEqual(set(self.h.rows), {second["readback"]})
        self.assertFalse(pathlib.Path(self.folder.name, "private", "cowork_readback", made["readback"]).exists())
        self.assertTrue(pathlib.Path(self.folder.name, "private", "cowork_readback", second["readback"]).exists())

    def test_failures_and_bad_data_are_kept_as_reasons(self):
        made, row = self._start()
        self.h.requests[row.request].update(status="failed", code="account_busy")
        self.assertEqual(self.service.status(made["readback"]), {
            "readback": made["readback"], "status": "failed", "period": "2026-05", "account_set": "T9 SINCERE",
            "code": "account_busy"})
        second, second_row = self._start()
        self.h.finish(second_row.request, raw(version=9))
        self.assertEqual(self.service.status(second["readback"])["code"], "cowork.readback.version_unsupported")
        third, third_row = self._start()
        del self.h.requests[third_row.request]
        self.assertEqual(self.service.status(third["readback"])["code"], "request_missing")

    def test_a_carried_over_input_vat_record_gets_its_recognition_record(self):
        # RR690430-004：单据日 4 月、税票日 5 月（税期 6 月），推送时按 4 月推的；发起时不点名，处理时按税务记录的单号补查
        self.h.pushed.append(pushed_item(record="CWP-9", history="H-9", docnum="RR690430-004", direction="purchase",
                                         doctype="RR", date="2026-04-30", lines=[]))
        made, row = self._start()
        create = next(call for call in self.h.calls if call[0] == "create")
        self.assertEqual(create[3]["docnums"], ["IV690503-001"])
        self.h.finish(row.request, raw())
        ready = self.service.status(made["readback"])
        self.assertEqual((ready["status"], ready["summary"]["pushed"]["pushed"], ready["changes"]), ("ready", 1, []))
        records = {item["docnum"]: item for item in
                   self.service.vat_records("EXP1", "S:\\EXP69\\T9SINCER", 2026, 5)["records"]}
        self.assertEqual((records["RR690430-004"]["history"], records["IV690503-001"]["history"]), ("H-9", "H-1"))
        self.assertNotIn("history", records["RR690520-003"])

    def test_a_processing_error_fails_the_readback_instead_of_leaving_it_reading(self):
        made, row = self._start()
        self.h.broken = True
        self.h.finish(row.request, raw())
        failed = self.service.status(made["readback"])
        self.assertEqual((failed["status"], failed["code"]), ("failed", "process_failed"))
        self.assertIn(("log_error", "Cowork readback processing failed"), self.h.calls)
        self.assertIn(("drop", row.request), self.h.calls)
        self.assertFalse(pathlib.Path(self.folder.name, "private", "cowork_readback", made["readback"]).exists())
        self.assertEqual(self.service.status(made["readback"])["status"], "failed")  # 不再重算
        # 同一月再请求：另读一份，不用等 7 天
        self.h.broken = False
        again, again_row = self._start()
        self.assertNotEqual(again["readback"], made["readback"])
        self.h.finish(again_row.request, raw(diagnostics={"bad_rows": ["ISVAT"], "missing_tables": "GLBAL"}))
        self.assertEqual(self.service.status(again["readback"])["status"], "ready")

    def test_only_the_requester_sees_or_deletes_a_readback(self):
        made, row = self._start()
        self.h.finish(row.request, raw())
        self.service.status(made["readback"])
        self.h.frappe.session.user = "b@example.com"
        for call in (self.service.status, self.service.delete, lambda name: self.service.file(name, "changes")):
            with self.assertRaises(self.h.frappe.DoesNotExistError):
                call(made["readback"])
        self.assertIsNone(self.service.vat_records("EXP1", "S:\\EXP69\\T9SINCER", 2026, 5))
        self.h.frappe.session.user = "a@example.com"
        self.assertEqual(self.service.delete(made["readback"]), {"readback": made["readback"], "deleted": True})
        self.h.commit()
        self.assertEqual(self.h.rows, {})
        self.assertFalse(pathlib.Path(self.folder.name, "private", "cowork_readback", made["readback"]).exists())
        with self.assertRaises(self.h.frappe.DoesNotExistError):
            self.service.status("x" * 32)


def push_service(rows):
    """按真实导入加载推送服务（依赖换成替身），``frappe.db.sql`` 记下每条查询、从 rows 里按条件取。"""
    queries = []

    def sql(query, values=(), as_dict=False):
        query = " ".join(query.split())
        queries.append((query, values))
        assert "connection=%s AND account_key=%s AND status='success'" in query, query
        if "creation >= %s" in query:
            _connection, _key, since, limit, offset = values
            return [row for row in rows if row.creation >= since][offset:offset + limit]
        if "external_docnum IN" in query:
            _connection, _key, *numbers = values
            return [row for row in rows if row.external_docnum.upper() in numbers]
        raise AssertionError(query)

    frappe = types.ModuleType("frappe")
    frappe._ = lambda text, *args, **kwargs: text
    frappe.db = types.SimpleNamespace(sql=sql)
    frappe.get_doc = lambda doctype, name, **kwargs: types.SimpleNamespace(doctype=doctype, name=name)
    modules = {"frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork")}
    for name in ("access", "connections", "targets", "contracts", "contracts.history", "contracts.push", "history",
                 "push", "push.application", "push.application.provenance", "push.express", "push.express.common",
                 "push.express.prepare", "push.express.preflight", "push.express.history", "push.mrerp",
                 "push.native", "push.receipts", "targets.choice", "targets.contracts", "targets.freeze",
                 "targets.selection"):
        modules["pearnly_cowork." + name] = mock.MagicMock()
    modules["pearnly_cowork.push.express"].pushed = pushed_rules
    modules["pearnly_cowork.push.express.pushed"] = pushed_rules
    modules["pearnly_cowork.targets.identity"] = _load("targets_identity_for_readback", APP / "targets/identity.py")
    with mock.patch.dict(sys.modules, modules):
        service = _load("push_service_for_readback", APP / "push/application/service.py")
    return service, queries


def push_row(name, created, docnum, docdate_be, total="100.00", history="H"):
    payload = {"direction": "purchase", "doctype": "RR", "docdate_be": docdate_be, "ref_no": "A",
               "supplier": {"code": "S1"}, "total_amount": total, "lines": []}
    return Row(name=name, creation=created, history=history, external_docnum=docnum,
               payload_json=json.dumps(payload), response_json="{}")


class PushedExpress(unittest.TestCase):
    """推送记录不整表扫：按期间的只取月初前 92 天以后建立的，按单号的直接按单号查；同一单号取最后一次。"""

    def test_window_by_creation_and_named_numbers_by_number(self):
        rows = [push_row("P1", datetime(2026, 5, 20), "RR690512-001", "690512", "100.00", "H-1"),
                push_row("P2", datetime(2026, 6, 2), "RR690512-001", "690512", "120.00", "H-2"),  # 再推一次
                push_row("P3", datetime(2026, 5, 3), "RR690601-001", "690601"),  # 单据日不在本月
                push_row("P4", datetime(2025, 11, 2), "RR681102-001", "681102", history="H-4"),  # 早就推的、被点名
                push_row("P5", datetime(2025, 12, 1), "RR690515-009", "690515")]  # 月初前 92 天以前建的
        service, queries = push_service(rows)
        found = service.pushed_express("EXP1", "S:\\EXP69\\T9", date(2026, 5, 1), date(2026, 5, 31),
                                       ["rr681102-001", "", None])
        self.assertEqual([(item["docnum"], item["total"], item["history"]) for item in found],
                         [("RR681102-001", "100.00", "H-4"), ("RR690512-001", "120.00", "H-2")])
        window = [values for query, values in queries if "creation >= %s" in query]
        self.assertEqual(window[0][2], datetime(2026, 5, 1) - timedelta(days=92))
        named = [values for query, values in queries if "external_docnum IN" in query]
        self.assertEqual(named, [("EXP1", "s:\\exp69\\t9", "RR681102-001")])

    def test_many_named_numbers_are_asked_in_chunks_and_no_key_asks_nothing(self):
        service, queries = push_service([])
        service.pushed_express("EXP1", "S:\\EXP69\\T9", None, None, [f"RR{n:06d}" for n in range(501)])
        self.assertEqual([len(values) - 2 for query, values in queries], [500, 1])
        self.assertFalse(any("creation >= %s" in query for query, _values in queries))
        queries.clear()
        self.assertEqual(service.pushed_express("EXP1", "", date(2026, 5, 1), date(2026, 5, 31), ["X"]), [])
        self.assertEqual(queries, [])


if __name__ == "__main__":
    unittest.main()
