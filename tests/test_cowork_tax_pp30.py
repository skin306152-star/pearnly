"""ภ.พ.30 草稿（`tax.periods` / `tax.pp30` / `tax.input_vat`）的纯函数检查：不连库、不依赖 Frappe。

税期与佛历、申报期限、税率表；ISVAT 回读行取数口径（税期、软删、作废、别的月）；第 1–16 栏；
进项 6 个月期限、简易税票、买方、重复；销项期间与税率（POS 按天汇总行不逐行核税率）。
"""
import ast
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "pearnly_cowork"), str(ROOT / "apps" / "pearnly_core")]

from pearnly_cowork.tax import input_vat, periods, pp30, thai_words  # noqa: E402

COMPANY = "0105556000009"
SELLER = "0105546015062"  # MOD-11 合法


def row(side="P", period="2026-09", tax_date="2026-09-10", refnum="T1", name="Supplier", tax_id=SELLER,
        amount="1000.00", vat="70.00", **extra):
    return {"side": side, "rectyp": "3", "period": period, "tax_date": tax_date, "doc_date": tax_date,
            "docnum": extra.pop("docnum", "RR" + refnum), "refnum": refnum, "name": name, "prefix": "",
            "tax_id": tax_id, "branch": "00000", "amount": amount, "vat": vat, "status": extra.pop("status", "N"),
            "deleted": extra.pop("deleted", False), **extra}


def selection(*rows, year=2026, month=9):
    records, errors = pp30.records(rows)
    return pp30.select(records, year, month), errors


def codes(issues, index=None):
    return [item["code"] for item in issues if index is None or item.get("index") == index]


class Periods(unittest.TestCase):
    def test_tax_months_accept_gregorian_and_buddhist_years(self):
        for text in ("2026-09", "2026/9", "09/2569", "2569-09", " 9/2026 "):
            self.assertEqual(periods.parse_period(text), (2026, 9), text)
        for text in ("", "2026-13", "13/2569", "Sept 2026", None):
            with self.assertRaises(ValueError):
                periods.parse_period(text)

    def test_month_arithmetic_crosses_years(self):
        self.assertEqual(periods.shift(2025, 11, 6), (2026, 5))
        self.assertEqual(periods.shift(2026, 1, -1), (2025, 12))
        self.assertEqual(periods.between((2025, 11), (2026, 5)), 6)
        self.assertEqual(periods.bounds(2028, 2), (date(2028, 2, 1), date(2028, 2, 29)))

    def test_dates_and_buddhist_display(self):
        self.assertEqual(periods.parse_date("15/09/2569"), date(2026, 9, 15))
        self.assertEqual(periods.parse_date("2026-09-15 10:00:00"), date(2026, 9, 15))
        self.assertIsNone(periods.parse_date("2026-02-30"))
        self.assertEqual(periods.be("2026-09-05"), "05/09/2569")
        self.assertEqual(periods.be_month(2026, 9), "09/2569")

    def test_deadlines_follow_the_paper_and_e_filing_rules(self):
        self.assertEqual(periods.deadlines("pp30", 2026, 9), {"paper": date(2026, 10, 15), "efiling": date(2026, 10, 23)})
        self.assertEqual(periods.deadlines("pnd", 2026, 12), {"paper": date(2027, 1, 7), "efiling": date(2027, 1, 15)})
        # 电子申报延期只到法定截止日 2027-01-31：之后不猜
        self.assertIsNone(periods.deadlines("pnd", 2027, 1)["efiling"])

    def test_vat_rate_comes_from_the_dated_table(self):
        self.assertEqual(periods.vat_rate("2026-09-01"), Decimal("0.07"))
        self.assertEqual(periods.vat_rate("2027-09-30"), Decimal("0.07"))
        self.assertIsNone(periods.vat_rate("2027-10-01"))
        self.assertIsNone(periods.vat_rate(""))


class Records(unittest.TestCase):
    def test_amounts_become_two_place_decimals_from_float_text(self):
        records, errors = pp30.records([row(amount=1070.0000000001, vat="74.899999999")])
        self.assertEqual(errors, [])
        self.assertEqual((records[0].amount, records[0].vat), (Decimal("1070.00"), Decimal("74.90")))
        self.assertEqual(records[0].period, (2026, 9))

    def test_unreadable_rows_are_reported_not_guessed(self):
        records, errors = pp30.records([row(), {"side": "X", "amount": "1", "vat": "0"}, row(amount="abc"), "junk"])
        self.assertEqual(len(records), 1)
        self.assertEqual([error["index"] for error in errors], [2, 3, 4])

    def test_only_this_tax_month_counts_and_the_rest_is_explained(self):
        chosen, _errors = selection(
            row(refnum="A"), row(side="S", refnum="B"),
            row(refnum="C", deleted=True), row(refnum="D", status="C"),
            row(refnum="E", period="2026-10"),          # 票据日在本月、税期在 10 月
            row(refnum="F", period="2026-08", tax_date="2026-08-30"),  # 与本月无关
            row(refnum="G", period=""))
        self.assertEqual([r.refnum for r in chosen["purchases"]], ["A"])
        self.assertEqual([r.refnum for r in chosen["sales"]], ["B"])
        self.assertEqual([(r.refnum, reason) for r, reason in chosen["excluded"]],
                         [("C", "row_deleted"), ("D", "row_voided"), ("E", "row_other_period"),
                          ("G", "row_period_missing")])

    def test_report_rows_follow_date_then_number(self):
        chosen, _ = selection(row(refnum="B2", tax_date="2026-09-20"), row(refnum="A1", tax_date="2026-09-20"),
                              row(refnum="Z9", tax_date="2026-09-01"))
        self.assertEqual([r.refnum for r in chosen["purchases"]], ["Z9", "A1", "B2"])


class Boxes(unittest.TestCase):
    def boxes(self, sales, purchases, **options):
        records, _ = pp30.records([*(row(side="S", refnum=f"S{i}", amount=a, vat=v) for i, (a, v) in enumerate(sales)),
                                   *(row(refnum=f"P{i}", amount=a, vat=v) for i, (a, v) in enumerate(purchases))])
        chosen = pp30.select(records, 2026, 9)
        return pp30.boxes(chosen["sales"], chosen["purchases"], **options)

    def test_tax_payable_equals_the_express_sums(self):
        values = self.boxes([("10000.00", "700.00"), ("5000.10", "350.01")], [("2000.00", "140.00")])
        self.assertEqual(values["1"], Decimal("15000.10"))
        self.assertEqual(values["4"], Decimal("15000.10"))
        self.assertEqual(values["5"], Decimal("1050.01"))
        self.assertEqual((values["6"], values["7"]), (Decimal("2000.00"), Decimal("140.00")))
        self.assertEqual((values["8"], values["9"], values["11"], values["12"]),
                         (Decimal("910.01"), Decimal("0.00"), Decimal("910.01"), Decimal("0.00")))
        self.assertEqual(pp30.outcome(values), "payable")
        for late in pp30.LATE_ONLY:
            self.assertIsNone(values[late])

    def test_excess_brought_forward_is_used_before_paying(self):
        values = self.boxes([("10000.00", "700.00")], [("5000.00", "350.00")], excess_brought_forward="500")
        self.assertEqual((values["8"], values["10"], values["11"], values["12"]),
                         (Decimal("350.00"), Decimal("500.00"), Decimal("0.00"), Decimal("150.00")))
        values = self.boxes([("10000.00", "700.00")], [("5000.00", "350.00")], excess_brought_forward="100")
        self.assertEqual((values["11"], values["12"]), (Decimal("250.00"), Decimal("0.00")))

    def test_overpaid_month_adds_the_carried_excess(self):
        values = self.boxes([("1000.00", "70.00")], [("5000.00", "350.00")], excess_brought_forward="30")
        self.assertEqual((values["8"], values["9"], values["11"], values["12"]),
                         (Decimal("0.00"), Decimal("280.00"), Decimal("0.00"), Decimal("310.00")))
        self.assertEqual(pp30.outcome(values), "overpaid")

    def test_zero_vat_sales_and_exempt_sales(self):
        values = self.boxes([("1000.00", "70.00"), ("3000.00", "0.00")], [], exempt_sales="500")
        self.assertEqual((values["1"], values["2"], values["3"], values["4"]),
                         (Decimal("4500.00"), Decimal("3000.00"), Decimal("500.00"), Decimal("1000.00")))
        values = self.boxes([("1000.00", "70.00"), ("3000.00", "0.00")], [], zero_vat_as="exempt")
        self.assertEqual((values["2"], values["3"], values["4"]), (Decimal("0.00"), Decimal("3000.00"), Decimal("1000.00")))
        with self.assertRaises(ValueError):
            self.boxes([], [], zero_vat_as="other")

    def test_nothing_this_month_is_nil(self):
        self.assertEqual(pp30.outcome(self.boxes([], [])), "nil")


class InputChecks(unittest.TestCase):
    def check(self, *rows, documents=None, month=9, year=2026):
        chosen, _ = selection(*rows, year=year, month=month)
        return chosen, input_vat.check(chosen, documents or {}, COMPANY, year, month)

    def test_seller_tax_id_missing_or_invalid(self):
        _c, issues = self.check(row(refnum="A", tax_id=""), row(refnum="B", tax_id="0105546015063"),
                                row(refnum="C", tax_id="105546015062"))  # 读丢开头 0：补回后合法
        self.assertIn("input_tax_id_missing", codes(issues, 1))
        self.assertIn("input_tax_id_invalid", codes(issues, 2))
        self.assertNotIn("input_tax_id_invalid", codes(issues, 3))
        self.assertEqual(input_vat.tax_id_state("0105546015062"), "ok")

    def test_six_month_window_counts_from_the_month_after_the_invoice(self):
        _c, issues = self.check(row(refnum="A", tax_date="2026-03-31"),   # M+6：可抵，提醒注记
                                row(refnum="B", tax_date="2026-02-28"),   # M+7：超期
                                row(refnum="C", tax_date="2026-10-01"),   # 早于开票月
                                row(refnum="D", tax_date="2026-09-01"))   # 当月
        self.assertEqual([i["level"] for i in issues if i["code"] == "input_claimed_later"], ["info"])
        self.assertEqual(codes(issues, 1).count("input_claimed_later"), 1)
        over = next(i for i in issues if i["code"] == "input_over_six_months")
        self.assertEqual((over["index"], over["level"], over["detail"]["last_month"]), (2, "error", "2026-08"))
        self.assertIn("input_before_invoice", codes(issues, 3))
        self.assertFalse({"input_claimed_later", "input_over_six_months", "input_before_invoice"} & set(codes(issues, 4)))

    def test_window_crosses_the_year(self):
        _c, issues = self.check(row(refnum="A", tax_date="2025-11-15", period="2026-05"), month=5)
        self.assertIn("input_claimed_later", codes(issues))
        _c, issues = self.check(row(refnum="A", tax_date="2025-11-15", period="2026-06"), month=6)
        self.assertIn("input_over_six_months", codes(issues))

    def test_abbreviated_and_non_tax_invoices_are_not_claimable(self):
        documents = {
            input_vat.document_key(SELLER, "T2"): {"document_type": "simplified_tax_invoice"},
            input_vat.document_key(SELLER, "T3"): {"document_type": "receipt"},
            input_vat.document_key(SELLER, "T4"): {"document_type": "tax_invoice", "buyer_tax": "", "buyer_name": ""},
            input_vat.document_key(SELLER, "T5"): {"document_type": "tax_invoice", "buyer_tax": "0105556999995"},
            input_vat.document_key(SELLER, " t6 "): {"document_type": "tax_invoice", "buyer_tax": COMPANY},
            input_vat.document_key(SELLER, "T7"): {"document_type": "credit_note"},
        }
        _c, issues = self.check(row(refnum="T1", name="ซื้อตามใบกำกับภาษีอย่างย่อ"), row(refnum="T2"),
                                row(refnum="T3"), row(refnum="T4"), row(refnum="T5"), row(refnum="T6"),
                                row(refnum="T7"), row(refnum="T8"), documents=documents)
        self.assertIn("input_abbreviated", codes(issues, 1))
        self.assertIn("input_abbreviated", codes(issues, 2))
        self.assertIn("input_not_tax_invoice", codes(issues, 3))
        self.assertIn("input_buyer_missing", codes(issues, 4))
        self.assertIn("input_buyer_other", codes(issues, 5))
        self.assertEqual(codes(issues, 6), [])
        self.assertEqual(codes(issues, 7), [])
        self.assertEqual(codes(issues, 8), ["input_no_document"])

    def test_the_readback_link_wins_over_the_number(self):
        records = [row(refnum="T1", history="HIS-1")]
        _c, issues = self.check(*records, documents={"HIS-1": {"document_type": "receipt"}})
        self.assertIn("input_not_tax_invoice", codes(issues))

    def test_duplicates_are_flagged_after_the_first(self):
        _c, issues = self.check(row(refnum="T1"), row(refnum="t 1", docnum="RR9"))
        self.assertNotIn("input_duplicate", codes(issues, 1))
        duplicate = next(i for i in issues if i["code"] == "input_duplicate")
        self.assertEqual((duplicate["index"], duplicate["detail"]["first_row"]), (2, "1"))

    def test_claimable_leaves_out_error_rows_only(self):
        chosen, issues = self.check(row(refnum="A", amount="100.00", vat="7.00"),
                                    row(refnum="B", tax_date="2026-01-05", amount="200.00", vat="14.00"),
                                    row(refnum="C", tax_id="", amount="300.00", vat="21.00"))
        self.assertEqual(input_vat.claimable(chosen["purchases"], issues), (Decimal("400.00"), Decimal("28.00")))
        self.assertEqual(pp30.total(chosen["purchases"]), (Decimal("600.00"), Decimal("42.00")))


class OutputChecks(unittest.TestCase):
    def check(self, *rows, month=9, year=2026):
        chosen, _ = selection(*rows, year=year, month=month)
        return input_vat.check(chosen, {}, COMPANY, year, month)

    def test_invoice_date_must_be_in_the_tax_month(self):
        issues = self.check(row(side="S", refnum="A", tax_date="2026-08-31"))
        self.assertEqual(codes(issues), ["output_outside_period"])

    def test_vat_against_the_rate_with_two_satang_tolerance(self):
        issues = self.check(row(side="S", refnum="A", amount="1000.00", vat="70.02"),
                            row(side="S", refnum="B", amount="1000.00", vat="70.03"),
                            row(side="S", refnum="C", amount="1000.00", vat="0.00"),
                            row(side="S", refnum="D", amount="-500.00", vat="-35.00"))
        self.assertEqual(codes(issues, 1), [])
        rate = next(i for i in issues if i["code"] == "output_rate")
        self.assertEqual((rate["index"], rate["detail"]["expected_vat"]), (2, "70.00"))
        self.assertEqual(codes(issues, 3), ["output_zero_vat"])
        self.assertEqual(codes(issues, 4), [])

    def test_daily_abbreviated_rows_are_summed_but_not_rate_checked(self):
        chosen, _ = selection(row(side="S", refnum="0001-0150", name="ขายตามใบกำกับภาษีอย่างย่อ 0001-0150",
                                  amount="14018.69", vat="981.34", tax_id=""))
        issues = input_vat.check(chosen, {}, COMPANY, 2026, 9)
        self.assertEqual(issues, [])
        self.assertEqual(pp30.total(chosen["sales"]), (Decimal("14018.69"), Decimal("981.34")))

    def test_unknown_rate_is_said_once(self):
        issues = self.check(row(side="S", refnum="A", period="2027-10", tax_date="2027-10-02"),
                            row(side="S", refnum="B", period="2027-10", tax_date="2027-10-03"), year=2027, month=10)
        self.assertEqual(codes(issues), ["output_rate_unknown"])

    def test_buyer_tax_id_only_flagged_when_present_and_wrong(self):
        issues = self.check(row(side="S", refnum="A", tax_id=""), row(side="S", refnum="B", tax_id="0105546015063"))
        self.assertEqual(codes(issues), ["output_tax_id_invalid"])

    def test_excluded_rows_are_explained_line_by_line(self):
        issues = self.check(row(refnum="A", status="C"), row(refnum="B", period="2026-10"))
        self.assertEqual(codes(issues), ["row_voided", "row_other_period"])
        self.assertEqual(next(i for i in issues if i["code"] == "row_other_period")["period"], "2026-10")
        self.assertEqual(input_vat.counts(issues), {"row_voided": 1, "row_other_period": 1})


def companion_vat_description():
    """CC 桌面版写 POS 日汇总销项税记录摘要的那几个纯函数（`companion/src/companion/pos_journal.py`），原样取来执行。"""
    path = ROOT / "companion" / "src" / "companion" / "pos_journal.py"
    wanted = {"VAT_DESCRIPTION", "_ISVAT_DESCRP", "fit", "range_text", "vat_description"}
    body = [node for node in ast.parse(path.read_text(encoding="utf-8")).body
            if (isinstance(node, ast.FunctionDef) and node.name in wanted)
            or (isinstance(node, ast.Assign) and any(getattr(target, "id", "") in wanted for target in node.targets))]
    namespace = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102
    return namespace["vat_description"]


class PosDayRows(unittest.TestCase):
    """L9 × L4：POS 导出按天写进 Express 的销项税行（摘要「ใบกำกับภาษีอย่างย่อ 起-止」由 CC 桌面版写）照常计入合计、
    不逐行核税率——当天税额是逐票税额之和，与「净额 × 7%」会差；同一行换成普通摘要就会被核出差额。"""

    def test_a_pos_day_row_is_summed_but_not_rate_checked(self):
        from pearnly_cowork.pos_import import contracts as pos_rules

        headers = ["วันที่", "เลขที่บิล", "ภาษีมูลค่าเพิ่ม", "ยอดสุทธิ", "ช่องทางชำระเงิน", "สถานะบิล"]
        rows = [["13/09/2569", f"B{n:04d}", "0.65", "10.00", "เงินสด" if n % 3 else "พร้อมเพย์", "ปกติ"]
                for n in range(1, 151)]
        detection = pos_rules.detect(headers, rows)
        self.assertEqual(detection.questions, [])
        (day,), _voids = pos_rules.parse(headers, rows, detection.layout, anchor_year=2026)
        self.assertEqual((day.net, day.vat), (Decimal("1402.50"), Decimal("97.50")))
        self.assertGreater(abs(day.net * Decimal("0.07") - day.vat), input_vat.RATE_TOLERANCE)
        description = companion_vat_description()(day.first, day.last)
        self.assertEqual(description, "ใบกำกับภาษีอย่างย่อ B0001-B0150")
        self.assertIn(thai_words.ABBREVIATED, description)
        sale = row(side="S", tax_date=day.day.isoformat(), refnum=day.ref, name=description, tax_id="",
                   amount=str(day.net), vat=str(day.vat), docnum="HS690913-001")
        chosen, errors = selection(sale)
        self.assertEqual(errors, [])
        self.assertEqual(input_vat.check(chosen, {}, COMPANY, 2026, 9), [])
        self.assertEqual(pp30.total(chosen["sales"]), (day.net, day.vat))
        plain, _ = selection(dict(sale, name="ขายสด"))
        self.assertEqual(codes(input_vat.check(plain, {}, COMPANY, 2026, 9)), ["output_rate"])
        # 票号很长时摘要按 ISVAT.DESCRP 60 字节截，「อย่างย่อ」在最前面，截不掉
        long_number = "POS-STORE-0001-" + "9" * 25
        self.assertIn(thai_words.ABBREVIATED, companion_vat_description()(long_number, long_number + "1"))


if __name__ == "__main__":
    unittest.main()
