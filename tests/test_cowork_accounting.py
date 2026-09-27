"""共用会计规则接进 Cowork（`ocr.validation.accounting`，2026-09-27「搬到共用层」）：纯函数，不连库、不调 API。"""
import unittest
from datetime import date

from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.schema import validate_invoice
from pearnly_cowork.ocr.validation import check_amount_math
from pearnly_cowork.ocr.validation.accounting import (
    infer_withholding, normalize_amounts, repair_from_transcript, repair_invoice_number,
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

    def test_invoice_number_takes_the_transcript_spelling_only_when_unique(self):
        value = invoice(invoice_number="IN26-00668")
        self.assertIn("IN26-00658", repair_invoice_number(value, "เลขที่ IN26-00658 วันที่ 07/05/2569"))
        self.assertEqual(value.invoice_number, "IN26-00658")
        self.assertIsNone(repair_invoice_number(invoice(invoice_number="IN26-00658"), "IN26 - 00658"))  # 原文里有
        ambiguous = invoice(invoice_number="IN26-00668")
        self.assertIsNone(repair_invoice_number(ambiguous, "IN26-00658 IN26-00678"))
        self.assertEqual(ambiguous.invoice_number, "IN26-00668")

    def test_a_withholding_invoice_passes_the_direct_read_without_a_recheck(self):
        model = Model([{**GOOD, "subtotal": "1946.00", "vat": "136.22", "total_amount": "2023.84"}])
        page = run_invoice(Source(), model, None, config=CONFIG, anchor_year=2026, today=date(2026, 9, 21)).pipeline.pages[0]
        self.assertEqual(([r.stage for r in model.requests], page.layer_chain), (["direct"], ["ID"]))
        self.assertEqual(page.invoice.wht_amount, "58.38")


if __name__ == "__main__":
    unittest.main()
