"""POS 导出按天录销售（交接 L9）：纯规则的离线检查（不连库、不依赖 Frappe；python3.12 直接跑）。

两种真实列结构（数据自己合成）：Ocha 式按单报表（泰文表头、标题行在上、页脚合计、作废单）与 Loyverse 式收据导出
（Total collected / Taxes / Payment type / Receipt type，含退款）及商品级导出。覆盖认列与问题、逐日对平、7/107
四舍五入、付款方式拆分、票号区间与参考号 ≤15 字节，以及推送映射（载荷 3：HS + 每种非现金付款方式的转账凭证）与
推送选择（收款科目 `receipt` 问一次）。
"""
import json
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_cowork"), str(ROOT / "apps/pearnly_core")]

from pearnly_cowork.contracts import pos as P  # noqa: E402
from pearnly_cowork.pos_import import contracts as C  # noqa: E402
from pearnly_cowork.pos_import import tables as T  # noqa: E402
from pearnly_cowork.push.express import choices as choice_rules  # noqa: E402
from pearnly_cowork.push.express.preflight import _unknown_account  # noqa: E402
from pearnly_cowork.push.express.sales_mapper import build_express_sales_payload  # noqa: E402

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
รวม,,,,,,,292.02,"4,479.00",,
"""

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

    def test_transfer_accounts_are_checked_against_the_chart(self):
        payload = self._payload().payload
        chart = {line["acc"] for line in payload["lines"]} | {"11-02-01-01"}
        self.assertEqual(_unknown_account(payload, chart), "11-02-01-02")
        self.assertIsNone(_unknown_account(payload, chart | {"11-02-01-02"}))


if __name__ == "__main__":
    unittest.main()
