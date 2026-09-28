"""共用会计规则接进 Cowork（`ocr.validation.accounting`，2026-09-27「搬到共用层」）：纯函数，不连库、不调 API。"""
import unittest
from datetime import date

from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.schema import validate_invoice
from pearnly_cowork.ocr.validation import check_amount_math
from pearnly_cowork.ocr.validation.accounting import (
    infer_withholding, invoice_number_alternative, invoice_number_range, invoice_number_unseen, normalize_amounts, number_alternative,
    number_seen, repair_from_transcript,
)
from test_cowork_engine import CONFIG, GOOD, Model, Source


def invoice(**fields):
    return validate_invoice({**GOOD, **fields}, anchor_year=2026)


class Accounting(unittest.TestCase):
    def test_amount_spellings_become_plain_numbers(self):
        value = invoice(subtotal="1,234.50", vat="(86.42)", total_amount="฿1,148.08")
        self.assertEqual(normalize_amounts(value), ["subtotal", "vat", "total_amount"])
        self.assertEqual((value.subtotal, value.vat, value.total_amount), ("1234.50", "-86.42", "1148.08"))
        self.assertEqual(normalize_amounts(invoice(subtotal="n/a")), [])

    def test_a_total_net_of_withholding_balances_and_fills_the_tax(self):
        # 语料 v2_007：3% 服务费税票，合计 = 1,946 + 136.22 − 58.38
        value = invoice(subtotal="1946.00", vat="136.22", total_amount="2023.84")
        self.assertIsNotNone(check_amount_math(value))
        self.assertIn("3% withholding", infer_withholding(value))
        self.assertEqual((value.wht_rate, value.wht_amount), ("3", "58.38"))
        self.assertIsNone(check_amount_math(value))

    def test_no_withholding_when_it_already_balances_or_no_rate_fits(self):
        self.assertIsNone(infer_withholding(invoice()))
        self.assertIsNone(infer_withholding(invoice(subtotal="100", vat="7", total_amount="3")))

    def test_the_transcript_restores_a_tax_id_and_a_one_digit_amount(self):
        value = invoice(seller_tax="0105567178204", buyer_tax="0105556086859", subtotal="100.00", vat="7.00",
                        total_amount="108.00")
        text = "Tax ID 0105567178203 ... buyer 0105556086859 ... subtotal 100.00 VAT 7.00 total 107.00"
        notes = repair_from_transcript(value, text)
        self.assertEqual((value.seller_tax, value.total_amount), ("0105567178203", "107.00"))
        self.assertEqual(len(notes), 2)

    def test_the_transcript_never_guesses(self):
        value = invoice(seller_tax="0105567178204", total_amount="108.00", subtotal="100.00", vat="7.00")
        self.assertEqual(repair_from_transcript(value, "0105567178203 0105567178211 207.00 106.99"), [])
        self.assertEqual((value.seller_tax, value.total_amount), ("0105567178204", "108.00"))

    def test_an_invoice_number_one_character_off_the_transcript_is_offered_not_replaced(self):
        # 两边各有对错（09-27 原文对、09-28 压测模型对）：不改票面，两种写法都留给复核的人
        value = invoice(invoice_number="IN26-00668")
        note = invoice_number_alternative(value, "เลขที่ IN26-00658 วันที่ 07/05/2569")
        self.assertEqual(number_alternative(note), ("IN26-00668", "IN26-00658"))
        self.assertEqual(value.invoice_number, "IN26-00668")
        self.assertIsNone(invoice_number_alternative(invoice(invoice_number="IN26-00658"), "IN26 - 00658"))  # 原文里有
        self.assertIsNone(invoice_number_alternative(invoice(invoice_number="IN26-00668"), "IN26-00658 IN26-00678"))
        self.assertIsNone(number_alternative("corrected_from_ocr_text: seller_tax 1→2"))

    def test_a_number_counts_as_seen_only_as_a_whole_number(self):
        # 2026-09-28 压测：138051304940 读成 38051304940、922/46079 读成 46079，按子串比会被放过
        self.assertFalse(number_seen("38051304940", "เลขที่ 138051304940"))
        self.assertFalse(number_seen("46079", "เลขที่ 922/46079"))
        self.assertTrue(number_seen("922/46079", "เลขที่ 922/46079"))
        self.assertTrue(number_seen("IN26-00658", "IN26 - 00658"))
        self.assertEqual(invoice_number_unseen(invoice(invoice_number="38051304940"), "เลขที่ 138051304940"),
                         "invoice_number_unseen: 38051304940")
        self.assertIsNone(invoice_number_unseen(invoice(invoice_number="138051304940"), "เลขที่ 138051304940"))

    def test_a_number_range_marks_a_list_page(self):
        # 汇总表续页被当成一张票、单号写成范围（2026-09-28 复测）；普通带连字符的单号不算
        for read in ("IV69/06-026 to IV69/06-050", "A001 ถึง A025", "INV-0101 - INV-0125"):
            self.assertEqual(invoice_number_range(invoice(invoice_number=read)), f"invoice_number_range: {read}")
        for read in ("IV69/06-026", "IV 69 - 001", "RR690617-001", "", None):
            self.assertIsNone(invoice_number_range(invoice(invoice_number=read)))

    def test_a_list_page_numbered_as_a_range_is_not_an_invoice(self):
        page = run_invoice(Source(), Model([{**GOOD, "invoice_number": "IV69/06-026 to IV69/06-050"}]),
                           None, config=CONFIG, anchor_year=2026, today=date(2026, 9, 28)).pipeline.pages[0]
        self.assertTrue(page.invoice.is_not_invoice)
        self.assertIn("invoice_number_range: IV69/06-026 to IV69/06-050", page.validation_warnings)

    def test_a_tax_id_is_restored_only_from_a_transcript_id_one_digit_away(self):
        # 2026-09-28：0105546146823（校验位不过）被换成差两位、能过校验位的 0113546146823，真值是 0105546146825
        value = invoice(seller_tax="0105546146823")
        self.assertEqual(repair_from_transcript(value, "เลขประจำตัวผู้เสียภาษี 0113546146823"), [])
        self.assertEqual(value.seller_tax, "0105546146823")

    def test_a_withholding_invoice_passes_the_direct_read_without_a_recheck(self):
        model = Model([{**GOOD, "subtotal": "1946.00", "vat": "136.22", "total_amount": "2023.84"}])
        page = run_invoice(Source(), model, None, config=CONFIG, anchor_year=2026, today=date(2026, 9, 21)).pipeline.pages[0]
        self.assertEqual(([r.stage for r in model.requests], page.layer_chain), (["direct"], ["ID"]))
        self.assertEqual(page.invoice.wht_amount, "58.38")


if __name__ == "__main__":
    unittest.main()
