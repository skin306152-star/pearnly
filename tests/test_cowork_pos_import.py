"""POS 导出按天录销售（交接 L9）：纯规则的离线检查（不连库、不依赖 Frappe；python3.12 直接跑）。

两种真实列结构（数据自己合成）：Ocha 式按单报表（泰文表头、标题行在上、页脚合计、作废单）与 Loyverse 式收据导出
（Total collected / Taxes / Payment type / Receipt type，含退款）及商品级导出。覆盖认列与问题、逐日对平、7/107
四舍五入、付款方式拆分、票号区间与参考号 ≤15 字节，以及推送映射（载荷 3：HS + 每种非现金付款方式的转账凭证）与
推送选择（收款科目 `receipt` 问一次）。
"""
import importlib.util
import json
import sys
import types
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_cowork"), str(ROOT / "apps/pearnly_core")]

from pearnly_core.express_rules.contracts import pos as P  # noqa: E402
from pearnly_cowork.pos_import import contracts as C  # noqa: E402
from pearnly_cowork.pos_import import tables as T  # noqa: E402
from pearnly_core.express_rules.push import choices as choice_rules  # noqa: E402
from pearnly_core.express_rules.push.common import extract_line_items  # noqa: E402
from pearnly_core.express_rules.push.preflight import _unknown_account  # noqa: E402
from pearnly_core.express_rules.push.sales_mapper import build_express_sales_payload  # noqa: E402
from pearnly_core.express_rules.targets.identity import same_company_account_sets  # noqa: E402

OCHA_CSV = """รายงานยอดขายแยกตามบิล
สาขา: ร้านทดสอบ (สำนักงานใหญ่)
วันที่,เวลา,เลขที่บิล,จำนวนลูกค้า,ยอดก่อนส่วนลด,ส่วนลด,ค่าบริการ,ภาษีมูลค่าเพิ่ม,ยอดสุทธิ,ช่องทางชำระเงิน,สถานะบิล
13/10/2569,10:01,B2610130001,2,"1,070.00",0.00,0.00,70.00,"1,070.00",เงินสด,ปกติ
13/10/2569,11:15,B2610130002,1,535.00,0.00,0.00,35.00,535.00,พร้อมเพย์,ปกติ
13/10/2569,12:30,B2610130003,1,100.00,0.00,0.00,6.54,100.00,เงินสด,ยกเลิก
13/10/2569,13:40,B2610130004,4,"2,140.00",0.00,0.00,140.00,"2,140.00",บัตรเครดิต,ปกติ
14/10/2569,09:00,B2610140001,3,214.00,0.00,0.00,14.00,214.00,บัตรเครดิต,ปกติ
14/10/2569,09:30,B2610140002,1,99.00,0.00,0.00,6.48,99.00,เงินสด,ปกติ
14/10/2569,18:05,B2610140003,2,321.00,0.00,0.00,21.00,321.00,LINE MAN,ปกติ
รวม,,,,,,,293.02,"4,479.00",,
"""
# 页脚把作废单也算进去（4,479.00 = 4,379.00 + 作废的 100.00；税额 293.02 同样含作废单的 6.54），与按票算的同一口径就认

LOYVERSE_HEADERS = ["Date", "Receipt number", "Receipt type", "Gross sales", "Discounts", "Net sales", "Taxes",
                    "Total collected", "Cost of goods", "Gross profit", "Payment type", "Description", "Dining option",
                    "POS", "Store", "Cashier name", "Customer name", "Customer contacts", "Comment", "Status"]


def _receipt(at, number, kind, gross, discount, tax, collected, payment, status="Closed"):
    return [at, number, kind, gross, discount, gross - discount, tax, collected, 0, 0, payment, "", "Dine in",
            "POS 1", "Main", "Ann", "", "", "", status]


LOYVERSE_ROWS = [
    _receipt(datetime(2026, 10, 13, 9, 5), "1-1001", "Sale", 215.0, 5.0, 13.74, 210.0, "Cash"),
    _receipt(datetime(2026, 10, 13, 9, 40), "1-1002", "Sale", 65.0, 0.0, 4.25, 65.0, "Card"),
    _receipt(datetime(2026, 10, 13, 10, 2), "1-1003", "Sale", 130.0, 0.0, 8.50, 130.0, "QR Promptpay"),
    _receipt(datetime(2026, 10, 13, 11, 0), "1-1004", "Refund", 65.0, 0.0, 4.25, 65.0, "Card"),
    _receipt(datetime(2026, 10, 13, 12, 0), "1-1005", "Sale", 40.0, 0.0, 2.62, 40.0, "Cash", status="Cancelled"),
    _receipt(datetime(2026, 10, 14, 8, 0), "1-1006", "Sale", 195.0, 0.0, 12.76, 195.0, "Cash"),
]

ITEM_HEADERS = ["Date", "Receipt number", "Receipt type", "Category", "SKU", "Item", "Variant", "Modifiers applied",
                "Quantity", "Gross sales", "Discounts", "Net sales", "Cost of goods", "Gross profit", "Taxes",
                "Dining option", "POS", "Store", "Cashier name", "Customer name", "Customer contacts", "Comment",
                "Status"]


def _item(at, number, kind, sku, name, qty, gross, discount, tax):
    return [at, number, kind, "Drinks", sku, name, "", "", qty, gross, discount, gross - discount, 0, 0, tax,
            "Dine in", "POS 1", "Main", "Ann", "", "", "", "Closed"]


ITEM_ROWS = [
    _item(datetime(2026, 10, 13, 9, 5), "1-1001", "Sale", "A01", "Latte", 2, 130.0, 0.0, 8.50),
    _item(datetime(2026, 10, 13, 9, 5), "1-1001", "Sale", "A02", "Croissant", 1, 85.0, 5.0, 5.23),
    _item(datetime(2026, 10, 13, 10, 0), "1-1002", "Sale", "A01", "Latte", 1, 65.0, 0.0, 4.25),
    _item(datetime(2026, 10, 13, 11, 0), "1-1003", "Refund", "A02", "Croissant", 1, 85.0, 5.0, 5.23),
    _item(datetime(2026, 10, 14, 8, 0), "1-1004", "Sale", "A01", "Latte", 3, 195.0, 0.0, 12.76),
]


def _ocha():
    headers, rows, first = T.read(OCHA_CSV.encode("utf-8"), "ocha-2026-10.csv")
    detection = C.detect(headers, rows)
    return headers, rows, first, detection


class ContractTests(unittest.TestCase):
    def test_cash_labels_and_method_subjects(self):
        for label in ("", "Cash", "เงินสด", "  CASH ", "现金"):
            self.assertTrue(P.is_cash(label), label)
        for label in ("Card", "พร้อมเพย์", "QR Promptpay", "LINE MAN", "Cash, Card"):
            self.assertFalse(P.is_cash(label), label)
        self.assertEqual(P.method_subject(" QR  Promptpay "), "pos|qr promptpay")
        self.assertEqual(P.method_subject("QR Promptpay"), P.method_subject("qr promptpay"))

    def test_day_ref_fits_fifteen_bytes(self):
        day = date(2026, 10, 13)
        self.assertEqual(P.day_ref("B001", "B001", day), "B001")
        self.assertEqual(P.day_ref("1001", "1345", day), "1001-1345")
        self.assertEqual(P.day_ref("R2510120001", "R2510120345", day), "R2510120001-345")
        self.assertEqual(P.day_ref("POS01-BR0042-001", "POS01-BR0042-987", day), "ABB691013")
        # 像日期的区间不拿来当参考号（推送映射会把它当成没有票号）
        self.assertEqual(P.day_ref("12", "10-2026", day), "ABB691013")
        for first, last in (("1-1001", "1-1999"), ("INV202610130001", "INV202610139999"), ("A", "B" * 30)):
            self.assertLessEqual(len(P.day_ref(first, last, day).encode("cp874")), 15)

    def test_audit_round_trips_and_checks_the_split(self):
        block = P.audit("B1", "B9", 9, [("เงินสด", Decimal("100.5")), ("Card", Decimal("20"))], layout="f" * 64)
        page = {"_extraction_audit": block}
        found = P.facts(page)
        self.assertEqual((found.first, found.last, found.count, found.range), ("B1", "B9", 9, "B1-B9"))
        self.assertEqual([(p.label, p.amount, p.cash) for p in found.payments],
                         [("เงินสด", Decimal("100.50"), True), ("Card", Decimal("20.00"), False)])
        self.assertEqual(P.check(found, "120.50"), "")
        self.assertEqual(P.check(found, "121.50"), P.MISMATCH)
        self.assertIsNone(P.facts({"_extraction_audit": {}}))
        self.assertIsNone(P.facts({}))
        broken = json.loads(json.dumps(block))
        broken["pos"]["payments"][0]["amount"] = "1.234"
        with self.assertRaises(P.PosFactsError):
            P.facts({"_extraction_audit": broken})
        self.assertTrue(P.present(page) and P.present({"_extraction_audit": broken}))
        self.assertFalse(P.present({"_extraction_audit": {}}) or P.present({}) or P.present(None))

    def test_long_receipt_numbers_are_cut_to_the_helper_limit(self):
        # 外卖平台单号、订单 UUID：小助手收票号起止最长 40 字，云端按同一上限截，不推到小助手才失败
        uuid_like = "ORD-" + "7f3a9c2e-55b1-4d0e-9a61-3c8e2b7d9f10-0001"
        block = P.audit(uuid_like, uuid_like[:-1] + "9", 2, [("Cash", Decimal("10"))])
        self.assertEqual(len(block["pos"]["first"]), P.NUMBER_MAX)
        found = P.facts({"_extraction_audit": block})
        self.assertLessEqual(max(len(found.first), len(found.last)), P.NUMBER_MAX)
        stored = json.loads(json.dumps(block))
        stored["pos"]["first"] = "X" * 80  # 以前存下的长号读出来也截
        self.assertEqual(len(P.facts({"_extraction_audit": stored}).first), P.NUMBER_MAX)


class OchaLayoutTests(unittest.TestCase):
    def test_columns_are_found_under_the_report_title(self):
        headers, rows, first, detection = _ocha()
        self.assertEqual(first, 4)  # 第 3 行是表头，数据从文件第 4 行起
        self.assertEqual(detection.questions, [])
        self.assertEqual(detection.layout["columns"], {
            "date": "วันที่", "receipt": "เลขที่บิล", "vat": "ภาษีมูลค่าเพิ่ม", "amount": "ยอดสุทธิ",
            "payment": "ช่องทางชำระเงิน", "status": "สถานะบิล"})  # 「จำนวนลูกค้า」不是数量、「ยอดก่อนส่วนลด」不是金额
        self.assertTrue(C.valid_layout(detection.layout))

    def test_daily_totals_equal_the_pos_and_split_by_payment(self):
        headers, rows, first, detection = _ocha()
        found, voids = C.parse(headers, rows, detection.layout, anchor_year=2026, first_row=first)
        self.assertEqual(voids, 1)  # B2610130003 作废不算；页脚「รวม」行跳过
        self.assertEqual([d.day for d in found], [date(2026, 10, 13), date(2026, 10, 14)])  # 佛历 2569 → 2026
        first_day, second_day = found
        self.assertEqual((first_day.first, first_day.last, first_day.count), ("B2610130001", "B2610130004", 3))
        self.assertEqual((first_day.gross, first_day.vat, first_day.net),
                         (Decimal("3745.00"), Decimal("245.00"), Decimal("3500.00")))  # 税额 = 逐票税额之和
        self.assertEqual(first_day.payments, [("เงินสด", Decimal("1070.00")), ("พร้อมเพย์", Decimal("535.00")),
                                              ("บัตรเครดิต", Decimal("2140.00"))])
        self.assertEqual(sum(amount for _label, amount in second_day.payments), second_day.gross)
        self.assertEqual(first_day.rows, [(4, 5), (7, 7)])
        for day in found:
            self.assertEqual(day.net + day.vat, day.gross)
            self.assertLessEqual(len(day.ref.encode("cp874")), 15)

    def test_a_day_becomes_one_simplified_tax_invoice_to_the_cash_customer(self):
        headers, rows, first, detection = _ocha()
        found, _voids = C.parse(headers, rows, detection.layout, anchor_year=2026, first_row=first)
        page = C.day_page(found[0], 1, layout_fingerprint=C.fingerprint(headers))
        fields = page["fields"]
        self.assertEqual((fields["document_type"], fields["buyer_name"], fields["direction"],
                          fields["posting_payment_manual"]), ("simplified_tax_invoice", "", "sales", "cash"))
        self.assertEqual((fields["invoice_number"], fields["date"], fields["subtotal"], fields["vat"],
                          fields["total_amount"]), ("B2610130001-4", "2026-10-13", "3500.00", "245.00", "3745.00"))
        self.assertEqual(fields["notes"], "B2610130001-B2610130004")
        self.assertEqual(page["_layer_chain"], ["pos_import"])
        facts = P.facts(page)
        self.assertEqual(P.check(facts, fields["total_amount"]), "")
        self.assertEqual([p.label for p in facts.non_cash()], ["พร้อมเพย์", "บัตรเครดิต"])


class LoyverseLayoutTests(unittest.TestCase):
    def test_receipt_export_with_refund_and_cancelled_receipt(self):
        detection = C.detect(LOYVERSE_HEADERS, LOYVERSE_ROWS)
        self.assertEqual(detection.questions, [])
        self.assertEqual({k: detection.layout["columns"][k] for k in ("date", "receipt", "amount", "vat", "payment",
                                                                      "kind", "status")},
                         {"date": "Date", "receipt": "Receipt number", "amount": "Total collected", "vat": "Taxes",
                          "payment": "Payment type", "kind": "Receipt type", "status": "Status"})
        found, voids = C.parse(LOYVERSE_HEADERS, LOYVERSE_ROWS, detection.layout, anchor_year=2026)
        self.assertEqual(voids, 1)
        day = found[0]
        # 退款单记负数、不进票号区间；作废单不算
        self.assertEqual((day.first, day.last, day.count, day.refunds), ("1-1001", "1-1003", 3, 1))
        self.assertEqual((day.gross, day.vat), (Decimal("340.00"), Decimal("22.24")))
        self.assertEqual(day.payments, [("Cash", Decimal("210.00")), ("QR Promptpay", Decimal("130.00"))])
        # 刷卡当天收 65、退 65：净 0 不出凭证

    def test_item_export_asks_once_whether_net_sales_include_vat(self):
        detection = C.detect(ITEM_HEADERS, ITEM_ROWS)
        self.assertEqual([q["kind"] for q in detection.questions], ["amount_basis"])
        self.assertEqual(detection.layout["columns"]["amount"], "Net sales")
        answered = C.apply_answers(ITEM_HEADERS, ITEM_ROWS, {"amount_basis": "inclusive"})
        self.assertEqual(answered.questions, [])
        found, _ = C.parse(ITEM_HEADERS, ITEM_ROWS, answered.layout, anchor_year=2026, with_items=True)
        self.assertEqual(found[0].items, [{"name": "Latte", "sku": "A01", "qty": Decimal("3"),
                                           "amount": Decimal("195.00")}])  # 可颂卖一退一：净 0 不留
        fields = C.invoice_fields(found[0])
        self.assertEqual(fields["items"], [{"name": "Latte", "qty": "3", "price": "65.00", "subtotal": "195.00"}])
        # 不含税的金额：加上税额才是含税合计
        exclusive = C.apply_answers(ITEM_HEADERS, ITEM_ROWS, {"amount_basis": "exclusive"}).layout
        found, _ = C.parse(ITEM_HEADERS, ITEM_ROWS, exclusive, anchor_year=2026)
        self.assertEqual(found[1].gross, Decimal("207.76"))

    def test_free_items_stay_as_zero_amount_lines(self):
        """赠品 / 免费加料（数量为正、0 元）：存货模式下作为 0 元明细出库，不让整份导出失败；推送摊税前额时尾差不落在它上面。"""
        headers = ["Date", "Receipt number", "Item", "SKU", "Quantity", "Total collected", "Payment type"]
        rows = [["2026-10-13", "1-1001", "Free cookie", "C01", 1, 0.0, "Cash"],
                ["2026-10-13", "1-1001", "Latte", "A01", 1, 65.0, "Cash"],
                ["2026-10-13", "1-1002", "Latte", "A01", 2, 130.0, "Card"]]
        layout = C.detect(headers, rows).layout
        self.assertEqual(C.detect(headers, rows).questions, [])
        found, _ = C.parse(headers, rows, layout, anchor_year=2026, with_items=True)
        self.assertEqual([(line["name"], line["qty"], line["amount"]) for line in found[0].items],
                         [("Free cookie", Decimal("1"), Decimal("0.00")), ("Latte", Decimal("3"), Decimal("195.00"))])
        fields = C.invoice_fields(found[0])
        self.assertEqual(fields["items"][0], {"name": "Free cookie", "qty": "1", "price": "0.00", "subtotal": "0.00"})
        detail = extract_line_items(fields, Decimal(fields["subtotal"]), total=Decimal(fields["total_amount"]),
                                    item_mode="stock_sale")
        self.assertEqual(detail["status"], "ok")
        self.assertEqual([item["amount"] for item in detail["items"]], ["0.00", fields["subtotal"]])
        # 退款多过销售（数量为负）仍拦
        refund = rows + [["2026-10-13", "1-1003", "Free cookie", "C01", -2, 0.0, "Cash"]]
        with self.assertRaises(C.PosError) as raised:
            C.parse(headers, refund, layout, anchor_year=2026, with_items=True)
        self.assertEqual(raised.exception.code, "pos.items_not_positive")

    def test_amount_level_unless_stock_and_items(self):
        layout = C.apply_answers(ITEM_HEADERS, ITEM_ROWS, {"amount_basis": "inclusive"}).layout
        found, _ = C.parse(ITEM_HEADERS, ITEM_ROWS, layout, anchor_year=2026)
        self.assertEqual(found[0].items, [])
        self.assertEqual(C.invoice_fields(found[0])["items"], [])


class RulesTests(unittest.TestCase):
    HEADERS = ["Date", "Bill No", "Amount", "Payment"]

    def test_no_vat_column_takes_seven_over_one_hundred_seven_rounded_half_up(self):
        rows = [["2026-10-13", "1", "100.00", "Cash"], ["2026-10-13", "2", "0.07", "Cash"],
                ["2026-10-14", "3", "1.07", "QR"], ["2026-10-15", "4", "53.50", "Cash"]]
        detection = C.detect(self.HEADERS, rows)
        self.assertEqual(detection.questions, [])
        self.assertEqual(detection.layout["date_order"], "ymd")
        found, _ = C.parse(self.HEADERS, rows, detection.layout, anchor_year=2026)
        self.assertEqual([(d.gross, d.vat, d.net) for d in found], [
            (Decimal("100.07"), Decimal("6.55"), Decimal("93.52")),   # 100.07 × 7/107 = 6.5468… → 6.55
            (Decimal("1.07"), Decimal("0.07"), Decimal("1.00")),
            (Decimal("53.50"), Decimal("3.50"), Decimal("50.00"))])   # 尾差全部落在税前额
        self.assertEqual(found[1].payments, [("QR", Decimal("1.07"))])

    def test_ambiguous_day_and_month_is_asked_once(self):
        rows = [["01/02/2026", "1", "10.00", "Cash"], ["02/02/2026", "2", "20.00", "Cash"]]
        detection = C.detect(self.HEADERS, rows)
        self.assertEqual([q["kind"] for q in detection.questions], ["date_order"])
        layout = C.apply_answers(self.HEADERS, rows, {"date_order": "mdy"}).layout
        found, _ = C.parse(self.HEADERS, rows, layout, anchor_year=2026)
        self.assertEqual([d.day for d in found], [date(2026, 1, 2), date(2026, 2, 2)])
        layout = C.apply_answers(self.HEADERS, rows, {"date_order": "dmy"}).layout
        found, _ = C.parse(self.HEADERS, rows, layout, anchor_year=2026)
        self.assertEqual([d.day for d in found], [date(2026, 2, 1), date(2026, 2, 2)])

    def test_dates_amounts_and_two_digit_buddhist_years(self):
        self.assertEqual(C.parse_date("13/10/69", "dmy", 2026), date(2026, 10, 13))
        self.assertEqual(C.parse_date("13/10/26", "dmy", 2026), date(2026, 10, 13))
        self.assertEqual(C.parse_date("13 ต.ค. 2569", "dmy", 2026), date(2026, 10, 13))
        self.assertEqual(C.parse_date("Oct 13, 2026", "dmy", 2026), date(2026, 10, 13))
        self.assertEqual(C.parse_date("2026-10-13T14:32:00", "dmy", 2026), date(2026, 10, 13))
        self.assertEqual(C.parse_date(46308, "dmy", 2026), date(2026, 10, 13))  # Excel 序列日期
        self.assertIsNone(C.parse_date("รวม", "dmy", 2026))
        self.assertEqual(C.money("1,234.50"), Decimal("1234.50"))
        self.assertEqual(C.money("(10.00)"), Decimal("-10.00"))
        self.assertEqual(C.money("10.00-"), Decimal("-10.00"))
        self.assertEqual(C.money("฿ 99"), Decimal("99.00"))
        self.assertEqual(C.money(0.1 + 0.2), Decimal("0.30"))
        self.assertIsNone(C.money(""))

    def test_a_missing_column_and_unknown_status_values_are_asked(self):
        headers = ["Date", "Bill No", "Paid", "State"]
        rows = [["2026-10-13", "1", "100.00", "OPEN"], ["2026-10-13", "2", "50.00", "X"]]
        detection = C.detect(headers, rows)
        kinds = {q["kind"]: q for q in detection.questions}
        self.assertEqual(kinds["column"]["field"], "amount")
        self.assertIn("Paid", kinds["column"]["options"])
        self.assertEqual(kinds["status_values"]["values"], ["OPEN", "X"])
        answered = C.apply_answers(headers, rows, {"columns": {"amount": "Paid"},
                                                   "status_values": {"OPEN": "sale", "X": "void"}})
        self.assertEqual(answered.questions, [])
        found, voids = C.parse(headers, rows, answered.layout, anchor_year=2026)
        self.assertEqual((found[0].gross, voids), (Decimal("100.00"), 1))

    def test_mixed_receipt_number_styles_on_one_day_sort_without_error(self):
        rows = [["2026-10-13", "INV0002", "20.00", "Cash"], ["2026-10-13", "0001", "10.00", "Cash"],
                ["2026-10-13", "0010", "5.00", "Cash"], ["2026-10-13", "A-7", "1.00", "Cash"]]
        layout = C.detect(self.HEADERS, rows).layout
        found, _ = C.parse(self.HEADERS, rows, layout, anchor_year=2026)
        self.assertEqual((found[0].first, found[0].last, found[0].count), ("0001", "INV0002", 4))
        self.assertLess(C.natural_key("0002"), C.natural_key("0010"))
        self.assertLess(C.natural_key("B9"), C.natural_key("B10"))

    def test_a_remembered_layout_asks_again_for_new_status_values_only(self):
        headers = ["Date", "Bill No", "Amount", "State"]
        first = [["2026-10-13", "1", "100.00", "Closed"], ["2026-10-13", "2", "50.00", "ปกติ"]]
        remembered = C.detect(headers, first).layout
        self.assertEqual(C.pending(headers, first, remembered).questions, [])
        later = first + [["2026-10-14", "3", "30.00", "HOLD"], ["2026-10-14", "4", "40.00", "Comp"]]
        detection = C.pending(headers, later, remembered)
        self.assertEqual([(q["kind"], q["values"]) for q in detection.questions], [("status_values", ["HOLD", "Comp"])])
        with self.assertRaises(C.PosError):
            C.parse(headers, later, remembered, anchor_year=2026)  # 不问就读会失败
        answered = C.apply_answers(headers, later, {"status_values": {"HOLD": "void", "Comp": "sale"}}, base=remembered)
        self.assertEqual(answered.questions, [])
        self.assertEqual(answered.layout["columns"], remembered["columns"])  # 在记住的版式上补，不重新认列
        self.assertEqual(answered.layout["status_values"], {"hold": "void", "comp": "sale"})
        found, voids = C.parse(headers, later, answered.layout, anchor_year=2026)
        self.assertEqual(([d.gross for d in found], voids), ([Decimal("150.00"), Decimal("40.00")], 1))
        partly = C.apply_answers(headers, later, {"status_values": {"HOLD": "void"}}, base=remembered)
        self.assertEqual([q["values"] for q in partly.questions], [["Comp"]])

    def test_fingerprint_is_stable_and_follows_the_headers(self):
        self.assertEqual(C.fingerprint(["Date", "", "Bill No"]), C.fingerprint([" date ", "Bill  No", ""]))
        self.assertNotEqual(C.fingerprint(["Date", "Bill No"]), C.fingerprint(["Date", "Receipt No"]))

    def test_refused_inputs(self):
        with self.assertRaises(C.PosError) as raised:
            T.read(b"anything", "sales.xls")
        self.assertEqual(raised.exception.code, "pos.xls_not_supported")
        with self.assertRaises(C.PosError) as raised:
            T.read(b"anything", "sales.pdf")
        self.assertEqual(raised.exception.code, "pos.type_unsupported")
        layout = C.detect(self.HEADERS, [["2026-10-13", "1", "10.00", "QR"]]).layout
        with self.assertRaises(C.PosError) as raised:
            C.parse(self.HEADERS, [["2026-10-13", "1", "10.00", "QR"], ["2026-10-13", "2", "-30.00", "QR"],
                                   ["2026-10-13", "3", "40.00", "Cash"]], layout, anchor_year=2026)
        self.assertEqual(raised.exception.code, "pos.payment_negative")
        many = [[f"2026-{1 + n // 28:02d}-{1 + n % 28:02d}", str(n), "1.07", "Cash"] for n in range(C.MAX_DAYS + 1)]
        with self.assertRaises(C.PosError) as raised:
            C.parse(self.HEADERS, many, layout, anchor_year=2026)
        self.assertEqual(raised.exception.code, "pos.too_many_days")
        with self.assertRaises(C.PosError) as raised:
            C.parse(self.HEADERS, [["2026-10-13", "1", "-5.00", "Cash"]], layout, anchor_year=2026)
        self.assertEqual(raised.exception.code, "pos.day_not_positive")


class TotalRowTests(unittest.TestCase):
    """只有自己写着票号的行才是票；导出带的合计 / 小计行当核对数（2026-10-13 测试机：页脚合计行被当成上一张票的续行，
    每天销售翻倍，合计全算进上一张票的付款方式）。列结构照那份真实导出（标题行、页脚「รวม (ไม่รวมบิลยกเลิก)」）。"""
    HEADERS = ["วันที่", "เวลา", "เลขที่ใบเสร็จ", "สถานะ", "รายการสินค้า", "จำนวนชิ้น", "ยอดสุทธิ (รวม VAT)", "VAT 7%",
               "ยอดก่อน VAT", "ช่องทางชำระเงิน"]
    DAY = datetime(2026, 9, 28)

    def _rows(self, footer=(1076, 70.39)):
        day = self.DAY
        rows = [[day, "10:07", "R-001", "สำเร็จ", "คลีนซิ่ง x1", 1, 299, 19.56, 279.44, "QR พร้อมเพย์"],
                [day, "10:15", "R-002", "สำเร็จ", "โฟม x1", 1, 259, 16.94, 242.06, "โอนเงิน"],
                [day, "10:47", "R-003", "ยกเลิก", "มาสคาร่า x1", 1, 100, 6.54, 93.46, "เงินสด"],
                [day, "11:24", "R-004", "สำเร็จ", "โฟม x2", 2, 518, 33.89, 484.11, "โอนเงิน"]]
        if footer:
            rows.append([None, None, None, None, "รวม (ไม่รวมบิลยกเลิก)", 4, footer[0], footer[1], None, None])
        return rows

    def _parse(self, rows, headers=None):
        headers = headers or self.HEADERS
        detection = C.detect(headers, rows)
        self.assertEqual(detection.questions, [])
        return C.parse(headers, rows, detection.layout, anchor_year=2026, first_row=6)

    def test_the_footer_is_not_a_receipt_and_takes_no_payment_method(self):
        layout = C.detect(self.HEADERS, self._rows()).layout
        self.assertEqual((layout["columns"]["amount"], layout["columns"]["vat"]), ("ยอดสุทธิ (รวม VAT)", "VAT 7%"))
        found, voids = self._parse(self._rows())
        day = found[0]
        self.assertEqual((voids, day.count, day.first, day.last), (1, 3, "R-001", "R-004"))
        self.assertEqual((day.gross, day.vat, day.net), (Decimal("1076.00"), Decimal("70.39"), Decimal("1005.61")))
        # 以前页脚并进 R-004：当天翻倍、页脚的 1,076 全算进 R-004 的「โอนเงิน」
        self.assertEqual(day.payments, [("QR พร้อมเพย์", Decimal("299.00")), ("โอนเงิน", Decimal("777.00"))])
        self.assertEqual(day.rows, [(6, 7), (9, 9)])  # 作废的第 8 行、页脚那一行（第 10 行）都不在这一天的行里
        # 没有页脚照旧
        self.assertEqual(self._parse(self._rows(footer=None))[0][0].gross, Decimal("1076.00"))

    def test_a_footer_that_does_not_add_up_stops_the_file(self):
        for footer in ((1076.01, 70.39), (1076, 70.38), (2152, 140.78)):
            with self.assertRaises(C.PosError, msg=footer) as raised:
                self._parse(self._rows(footer=footer))
            self.assertEqual((raised.exception.code, raised.exception.details), ("pos.total_mismatch", {"row": 10}))
        # Ocha 式导出页脚算错一分税额（292.02 ≠ 293.02）：整份停下，不建票
        headers, rows, first = T.read(OCHA_CSV.replace("293.02", "292.02").encode("utf-8"), "ocha.csv")
        with self.assertRaises(C.PosError) as raised:
            C.parse(headers, rows, C.detect(headers, rows).layout, anchor_year=2026, first_row=first)
        self.assertEqual((raised.exception.code, raised.exception.details), ("pos.total_mismatch", {"row": 11}))
        terms = (ROOT / "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv").read_text(encoding="utf-8")
        line = next(row for row in terms.splitlines() if row.endswith("\tpos.total_mismatch"))
        self.assertTrue(all(part.strip() for part in line.split("\t")[:4]))

    # 商品级导出、票号只写在每张票的第一行（合并单元格）：后面的商品行票号空（L9：「有的话还有商品、数量、单价」）
    ITEM_HEADERS = ["Date", "Receipt number", "Item", "SKU", "Quantity", "Total collected", "Payment type", "Status"]

    def _item_rows(self, subtotals=False):
        receipts = [
            [["2026-10-13", "1-1001", "Latte", "A01", 2, 130.0, "Card", "Closed"],
             ["", "", "Croissant", "A02", 1, 80.0, "", ""],
             ["", "", "Free cookie", "C01", 1, 0.0, "", ""]],  # 赠品：有数量、0 元，照样是续行
            [["2026-10-13", "1-1002", "Latte", "A01", 1, 65.0, "Cash", "Closed"],
             ["2026-10-14", "", "Croissant", "A02", 2, 160.0, "QR", "Void"]],  # 续行的日期、付款方式、状态都不看
            [["2026-10-14", "1-1003", "Latte", "A01", 3, 195.0, "Card", "Closed"],
             ["", "", "", "", "", "", "QR", ""],  # 只有付款方式：不是续行
             ["", "", "Croissant", "A02", 1, 80.0, "", ""]],
        ]
        rows = []
        for group in receipts:
            rows += group
            if subtotals:
                amount = sum(row[5] for row in group if row[5] != "")
                rows.append(["", "", f"รวมบิล {group[0][1]}", "", "", amount, "", ""])
        rows.append(["", "", "", "", "หมายเหตุ: ราคารวม VAT", "", "", ""])  # 备注写着合计字样、没有金额：不核对
        rows.append(["Total", "", "", "", 11, 710.0, "", ""])
        return rows

    def _item_parse(self, rows, with_items=False):
        layout = C.detect(self.ITEM_HEADERS, rows).layout
        self.assertEqual((layout["columns"]["item"], layout["columns"]["qty"]), ("Item", "Quantity"))
        return C.parse(self.ITEM_HEADERS, rows, layout, anchor_year=2026, with_items=with_items, first_row=2)

    def test_item_rows_without_a_receipt_number_continue_the_receipt_above(self):
        found, voids = self._item_parse(self._item_rows(), with_items=True)
        self.assertEqual(voids, 0)
        first, second = found
        self.assertEqual([(d.day, d.gross, d.count, d.first, d.last) for d in found],
                         [(date(2026, 10, 13), Decimal("435.00"), 2, "1-1001", "1-1002"),
                          (date(2026, 10, 14), Decimal("275.00"), 1, "1-1003", "1-1003")])
        # 付款方式只看票自己那一行：1-1002 续行写的 QR 不算，整张票 225 是现金
        self.assertEqual(first.payments, [("Card", Decimal("210.00")), ("Cash", Decimal("225.00"))])
        self.assertEqual(second.payments, [("Card", Decimal("275.00"))])
        self.assertEqual([(line["name"], line["qty"], line["amount"]) for line in first.items],
                         [("Free cookie", Decimal("1"), Decimal("0.00")), ("Latte", Decimal("3"), Decimal("195.00")),
                          ("Croissant", Decimal("3"), Decimal("240.00"))])
        self.assertEqual([(line["name"], line["qty"]) for line in second.items],
                         [("Latte", Decimal("3")), ("Croissant", Decimal("1"))])
        self.assertEqual(first.rows, [(2, 6)])
        receipts, _ = C.receipts(self.ITEM_HEADERS, self._item_rows(),
                                 C.detect(self.ITEM_HEADERS, self._item_rows()).layout, anchor_year=2026)
        self.assertEqual([(r.number, r.amount, len(r.lines)) for r in receipts],
                         [("1-1001", Decimal("210.00"), 3), ("1-1002", Decimal("225.00"), 2),
                          ("1-1003", Decimal("275.00"), 2)])
        # 页脚对不上（续行接错、漏读都会这样）：整份停下
        broken = self._item_rows()
        broken[-1][5] = 709.99
        with self.assertRaises(C.PosError) as raised:
            self._item_parse(broken)
        self.assertEqual((raised.exception.code, raised.exception.details), ("pos.total_mismatch", {"row": 11}))

    def test_item_rows_with_a_subtotal_per_receipt(self):
        rows = self._item_rows(subtotals=True)
        found, _ = self._item_parse(rows, with_items=True)
        self.assertEqual([(d.gross, d.count) for d in found], [(Decimal("435.00"), 2), (Decimal("275.00"), 1)])
        self.assertEqual(found[0].payments, [("Card", Decimal("210.00")), ("Cash", Decimal("225.00"))])
        # 写着合计字样的行一律是合计行，不会被当成续行；合计行之后的商品行不再接到上一张票
        self.assertEqual(sum(1 for row in rows if C.total_label(row[2])), 3)
        late = rows[:4] + [["", "", "Croissant", "A02", 1, 80.0, "", ""]] + rows[4:]
        self.assertEqual(self._item_parse(late)[0][0].gross, Decimal("435.00"))
        wrong = [list(row) for row in rows]
        wrong[3][5] = 200.0  # 1-1001 的小计应为 210
        with self.assertRaises(C.PosError) as raised:
            self._item_parse(wrong)
        self.assertEqual((raised.exception.code, raised.exception.details), ("pos.total_mismatch", {"row": 5}))

    def test_rows_that_are_not_item_lines_never_join_a_receipt(self):
        headers = ["Date", "Receipt number", "Total collected", "Payment type"]
        rows = [["2026-10-13", "B1", 65.0, "Card"],
                ["", "", 80.0, "QR"],  # 没有商品一栏的导出：票号空的行不是续行
                ["2026-10-13", "B2", 65.0, ""]]
        found, _ = C.parse(headers, rows, C.detect(headers, rows).layout, anchor_year=2026)
        self.assertEqual((found[0].gross, found[0].payments), (Decimal("130.00"), [("Card", Decimal("65.00")),
                                                                                  ("Cash", Decimal("65.00"))]))

    def test_subtotals_per_day_nested_subtotals_and_a_grand_total(self):
        headers = ["Date", "Bill No", "Total collected", "VAT", "Payment", "Status"]
        rows = [["13/10/2026", "B1", "107.00", "7.00", "Cash", "Closed"],
                ["13/10/2026", "B2", "214.00", "14.00", "QR", "Closed"],
                ["13/10/2026", "B3", "53.50", "3.50", "Cash", "Void"],
                ["", "Subtotal", "321.00", "21.00", "", ""],  # 字样写在票号一栏
                [],
                headers,  # 分页重复的表头
                ["14/10/2026", "B4", "535.00", "35.00", "QR", "Closed"],
                ["14/10/2026", "B5", "1,070.00", "70.00", "QR", "Closed"],
                ["รวม QR", "", "1,605.00", "105.00", "", ""],  # 当天按付款方式的小计（与上一行合计之间的那几张）
                ["14/10/2026", "B6", "10.70", "0.70", "Transfer", "Closed"],
                ["รวม Transfer", "", "10.70", "0.70", "", ""],
                ["รวมวันที่ 14/10/2026", "", "1,615.70", "105.70", "", ""],  # 当天合计（紧挨着的那一天）
                ["Grand Total", "", "1,936.70", "126.70", "", ""],  # 整份
                ["หมายเหตุ: ยอดรวมไม่รวมบิลยกเลิก", "", "", "", "", ""]]  # 只有字样、没有金额：不核对
        detection = C.detect(headers, rows)
        self.assertEqual(detection.questions, [])  # 重复的表头、合计行里的空状态不当成要问的状态值
        found, voids = C.parse(headers, rows, detection.layout, anchor_year=2026)
        self.assertEqual(voids, 1)
        self.assertEqual([(d.day, d.gross, d.vat, d.count) for d in found],
                         [(date(2026, 10, 13), Decimal("321.00"), Decimal("21.00"), 2),
                          (date(2026, 10, 14), Decimal("1615.70"), Decimal("105.70"), 3)])
        self.assertEqual(found[1].payments, [("QR", Decimal("1605.00")), ("Transfer", Decimal("10.70"))])
        broken = [list(row) for row in rows]
        broken[11][2] = "1,615.80"  # 当天合计差 0.10
        with self.assertRaises(C.PosError) as raised:
            C.parse(headers, broken, detection.layout, anchor_year=2026, first_row=2)
        self.assertEqual((raised.exception.code, raised.exception.details), ("pos.total_mismatch", {"row": 13}))

    def test_total_labels_and_receipt_numbers(self):
        for label in ("รวม (ไม่รวมบิลยกเลิก)", "Grand Total", "Sub-total", "SUBTOTAL:", "Totals", "ยอดรวม", "合计",
                      "小计", "合計", "Sum"):
            self.assertTrue(C.total_label(label), label)
        for value in ("R6909-00837", "Totally", "Summary", "สำเร็จ", 13620.5, None, datetime(2026, 9, 28)):
            self.assertFalse(C.total_label(value), value)
        self.assertEqual(C.ticket(" R6909-00837 ", "เลขที่ใบเสร็จ"), "R6909-00837")
        self.assertEqual(C.ticket(1001, "Bill No"), "1001")
        for value in ("", None, "รวม", "Total", "เลขที่ใบเสร็จ"):
            self.assertEqual(C.ticket(value, "เลขที่ใบเสร็จ"), "", value)

    def test_a_vat_header_with_its_rate_is_the_vat_amount_but_a_rate_column_is_not(self):
        layout = C.detect(["Date", "Bill No", "Total (incl. VAT 7%)", "VAT 7%"],
                          [["2026-10-13", "1", "107", "7"]]).layout
        self.assertEqual((layout["columns"]["amount"], layout["columns"]["vat"]), ("Total (incl. VAT 7%)", "VAT 7%"))
        for rate in ("VAT %", "VAT rate", "อัตราภาษี 7%"):
            layout = C.detect(["Date", "Bill No", "Amount", rate], [["2026-10-13", "1", "107", "7"]]).layout
            self.assertNotIn("vat", layout["columns"], rate)


class RememberedLayoutTests(unittest.TestCase):
    """自动记下的版式（认得出、没问人）按现在的认列规则重认、换上并记下；人答过的不动（2026-10-13：测试机记下的那份没有
    「VAT 7%」税额列，当天税额按 7/107 算，9 月 29 日差一分）。"""

    def _service(self, layout, origin):
        remembered = []
        layouts = types.SimpleNamespace(
            remembered=lambda workspace, company, fingerprint: layout,
            origin=lambda workspace, company, fingerprint: origin,
            revision=lambda found: "rev" if found else "",
            remember=lambda workspace, company, fingerprint, headers, found, origin="person":
                remembered.append((found, origin)))
        stub = types.SimpleNamespace
        modules = {"frappe": stub(_=lambda text: text), "pearnly_cowork.access": stub(scope=stub()),
                   "pearnly_cowork.connections": stub(service=stub()), "pearnly_cowork.files": stub(service=stub()),
                   "pearnly_cowork.history": stub(storage=stub()),
                   "pearnly_cowork.history.recognition": stub(persist_batch=None),
                   "pearnly_cowork.ocr.pages": stub(assess_confidence=None),
                   "pearnly_cowork.pos_import": stub(contracts=C, layouts=layouts, tables=T),
                   "pearnly_core.express_rules.targets.identity": stub(account_set_company=None,
                                                           normalize_express_account_key=None)}
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(
                "pos_service_under_test", ROOT / "apps/pearnly_cowork/pearnly_cowork/pos_import/service.py")
            service = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(service)
        return service, remembered

    def test_an_auto_layout_follows_the_current_rules_and_a_person_layout_stays(self):
        headers, rows = TotalRowTests.HEADERS, TotalRowTests()._rows()
        current = C.detect(headers, rows).layout
        old = json.loads(json.dumps(current))
        old["columns"].pop("vat")  # 以前「VAT 7%」整列被当成税率列排除
        self.assertTrue(C.valid_layout(old))
        service, remembered = self._service(old, "auto")
        layout, questions, _fingerprint, _revision = service._layout("W", "C|co", headers, rows)
        self.assertEqual((layout["columns"]["vat"], questions), ("VAT 7%", []))
        self.assertEqual(remembered, [(current, "auto")])
        service, remembered = self._service(current, "auto")
        self.assertEqual(service._layout("W", "C|co", headers, rows)[0], current)
        self.assertEqual(remembered, [])  # 一样就不重写
        service, remembered = self._service(old, "person")
        self.assertEqual((service._layout("W", "C|co", headers, rows)[0], remembered), (old, []))
        # 重认要问（这份导出日 / 月分不清）：照旧用记住的
        ambiguous = [["01/02/2026", "1", "10.00", "Cash"], ["02/02/2026", "2", "20.00", "Cash"]]
        plain = ["Date", "Bill No", "Amount", "Payment"]
        kept = {**C.detect(plain, ambiguous).layout, "date_order": "mdy"}
        service, remembered = self._service(kept, "auto")
        self.assertEqual((service._layout("W", "C|co", plain, ambiguous)[0], remembered), (kept, []))


CONFIG = {"account_set": "DATAT", "revenue_acc": "41-01-00-00", "vat_output_acc": "21-05-01-00", "ar_acc": "11-03-01-00"}


def _history(page, **over):
    fields = page["fields"]
    history = {"id": "H1", "invoice_date": fields["date"], "invoice_no": fields["invoice_number"],
               "total_amount": fields["total_amount"], "fields": dict(fields), "pages": [page]}
    history.update(over)
    return history


class PushTests(unittest.TestCase):
    def setUp(self):
        headers, rows, first, detection = _ocha()
        self.days, _ = C.parse(headers, rows, detection.layout, anchor_year=2026, first_row=first)
        self.page = C.day_page(self.days[0], 1)
        self.receipts = {P.method_subject("พร้อมเพย์"): "11-02-01-01", P.method_subject("บัตรเครดิต"): "11-02-01-02"}

    def _payload(self, history=None, receipts=None):
        config = {**CONFIG, "choices": {"receipts": self.receipts if receipts is None else receipts}}
        return build_express_sales_payload(history or _history(self.page), config=config, today=date(2026, 10, 20))

    def test_a_pos_day_is_a_cash_sale_with_a_transfer_per_non_cash_payment(self):
        result = self._payload()
        self.assertTrue(result.ok, result.reason)
        payload = result.payload
        self.assertEqual((payload["doctype"], payload["payload_version"], payload["ref_no"]),
                         ("HS", P.PAYLOAD_VERSION, "B2610130001-4"))
        self.assertEqual(payload["customer"]["code"], "เงินสด")
        self.assertEqual((payload["base_amount"], payload["vat_amount"], payload["total_amount"]),
                         ("3500.00", "245.00", "3745.00"))
        self.assertEqual(payload["pos"], {"first": "B2610130001", "last": "B2610130004", "count": 3, "docnum": "auto",
                                          "cash": "1070.00",
                                          "transfers": [{"acc": "11-02-01-01", "amount": "535.00", "label": "พร้อมเพย์"},
                                                        {"acc": "11-02-01-02", "amount": "2140.00",
                                                         "label": "บัตรเครดิต"}]})
        debits = [line for line in payload["lines"] if line["side"] == "D"]
        self.assertEqual([line["amount"] for line in debits], ["3745.00"])  # 一条借方（小助手换成现金科目）

    def test_a_missing_receiving_account_or_an_edited_total_stops_the_day(self):
        self.assertEqual(self._payload(receipts={}).reason, P.ACCOUNT_MISSING)
        edited = _history(self.page, total_amount="3800.00")
        edited["fields"].update(total_amount="3800.00", subtotal="3555.00")
        self.assertEqual(self._payload(history=edited).reason, P.MISMATCH)

    def test_an_all_cash_day_has_no_transfers(self):
        headers = ["Date", "Bill No", "Amount"]
        found, _ = C.parse(headers, [["2026-10-13", "1", "107.00"]], C.detect(headers, [["2026-10-13", "1", "1"]]).layout,
                           anchor_year=2026)
        payload = self._payload(history=_history(C.day_page(found[0], 1)), receipts={}).payload
        self.assertEqual((payload["pos"]["transfers"], payload["pos"]["count"]), ([], 1))

    def test_a_day_with_more_cash_refunded_than_taken_still_balances(self):
        """当天现金退款多过现金收款（现金净额为负）：照常推，借银行 1000 / 贷现金 1000，载荷带负的现金净额，
        各转账 + 现金 = 本单含税额（小助手据此核，见 companion/tests/test_pos_sales_writer.py 同一组数）。"""
        headers = ["Date", "Bill No", "Amount", "Payment", "Receipt type"]
        rows = [["2026-10-13", "1", "1000.00", "Card", "Sale"], ["2026-10-13", "2", "50.00", "Cash", "Sale"],
                ["2026-10-13", "3", "150.00", "Cash", "Refund"]]
        found, _ = C.parse(headers, rows, C.detect(headers, rows).layout, anchor_year=2026)
        day = found[0]
        self.assertEqual((day.gross, day.payments), (Decimal("900.00"), [("Card", Decimal("1000.00")),
                                                                         ("Cash", Decimal("-100.00"))]))
        page = C.day_page(day, 1)
        payload = self._payload(history=_history(page), receipts={P.method_subject("Card"): "11-02-01-02"}).payload
        self.assertEqual(payload["pos"]["cash"], "-100.00")
        self.assertEqual(payload["pos"]["transfers"], [{"acc": "11-02-01-02", "amount": "1000.00", "label": "Card"}])
        moved = sum(Decimal(row["amount"]) for row in payload["pos"]["transfers"])
        self.assertEqual(moved + Decimal(payload["pos"]["cash"]), Decimal(payload["total_amount"]))

    def test_only_express_takes_pos_days(self):
        self.assertTrue(P.present(self.page))
        self.assertEqual(P.EXPRESS_ONLY, "pos_express_only")
        terms = (ROOT / "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv").read_text(encoding="utf-8")
        self.assertIn("\tintake.error." + P.EXPRESS_ONLY + "\n", terms)

    def test_ordinary_sales_payloads_are_unchanged(self):
        history = {"id": "H2", "invoice_date": "2026-10-13", "invoice_no": "IV001", "total_amount": "107.00",
                   "fields": {"buyer_name": "บริษัท ลูกค้า จำกัด", "buyer_tax": "0105551234567", "subtotal": "100.00",
                              "vat": "7.00", "invoice_number": "IV001"},
                   "pages": [{"fields": {"invoice_number": "IV001"}}]}
        payload = build_express_sales_payload(history, config=CONFIG, today=date(2026, 10, 20)).payload
        self.assertNotIn("pos", payload)
        self.assertEqual(payload["payload_version"], 1)

    def test_the_receiving_account_is_asked_once_per_payment_method(self):
        facts = P.facts(self.page)
        receipts = [(payment.subject, payment.label) for payment in facts.non_cash()]
        config = {"reported_accounts": [{"code": "11-02-01-01"}, {"code": "11-02-01-02"}]}
        fields = self.page["fields"]
        first = choice_rules.resolve(fields, direction="sales", posting_kind="service", config=config, remembered={},
                                     receipts=receipts)
        self.assertEqual([(n["kind"], n["label"]) for n in first["needed"]],
                         [("receipt", "พร้อมเพย์"), ("receipt", "บัตรเครดิต")])
        remembered = {"receipt": {receipts[0][0]: "11-02-01-01", receipts[1][0]: "11-09-09-09"}}
        again = choice_rules.resolve(fields, direction="sales", posting_kind="service", config=config,
                                     remembered=remembered, receipts=receipts)
        self.assertEqual(again["applied"]["receipts"], {receipts[0][0]: "11-02-01-01"})
        self.assertEqual([(n["kind"], n["current"]) for n in again["needed"]], [("receipt", "11-09-09-09")])
        self.assertIn("receipt", choice_rules.KINDS)

    def test_a_receiving_account_chosen_in_another_year_of_the_company_is_reused(self):
        """收款科目按公司问一次：同一家公司别的年度账套记过、科目又在本账套科目表里的直接带出。"""
        rows = [{"source_id": "C:\\EXP\\SHOP68", "attributes": {"company": "Shop Co., Ltd."}},
                {"source_id": "C:\\EXP\\SHOP69", "attributes": {"company": " shop co., ltd. "}},
                {"source_id": "C:\\EXP\\OTHER69", "attributes": {"company": "Other"}},
                {"source_id": "C:\\EXP\\NONAME", "attributes": {}}]
        self.assertEqual(same_company_account_sets(rows, "c:/exp/shop69/"), ["c:\\exp\\shop68"])
        self.assertEqual(same_company_account_sets(rows, "C:\\EXP\\NONAME"), [])
        facts = P.facts(self.page)
        receipts = [(payment.subject, payment.label) for payment in facts.non_cash()]
        borrowed = {receipts[0][0]: "11-02-01-01", receipts[1][0]: "11-09-09-09"}
        chart = [{"code": "11-02-01-01"}, {"code": "11-02-01-02"}]
        remembered = {"receipt": {}}
        choice_rules.borrow_receipts(remembered, receipts, borrowed, chart)
        self.assertEqual(remembered["receipt"], {receipts[0][0]: "11-02-01-01"})  # 不在本账套科目表里的不带
        own = {"receipt": {receipts[0][0]: "11-02-01-02"}}
        choice_rules.borrow_receipts(own, receipts, borrowed, chart)
        self.assertEqual(own["receipt"], {receipts[0][0]: "11-02-01-02"})  # 本账套记过的优先
        unknown_chart = {"receipt": {}}
        choice_rules.borrow_receipts(unknown_chart, receipts, borrowed, [])
        self.assertEqual(unknown_chart["receipt"], {})  # 科目表没报上来不借

    def test_transfer_accounts_are_checked_against_the_chart(self):
        payload = self._payload().payload
        chart = {line["acc"] for line in payload["lines"]} | {"11-02-01-01"}
        self.assertEqual(_unknown_account(payload, chart), "11-02-01-02")
        self.assertIsNone(_unknown_account(payload, chart | {"11-02-01-02"}))


if __name__ == "__main__":
    unittest.main()
