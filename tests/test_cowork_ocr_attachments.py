"""随票附属页（`ocr.validation.attachments`，2026-10-07 独立金标）：纯函数，不连库、不调 API。"""
import unittest
from datetime import date

from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.schema import validate_invoice
from pearnly_cowork.ocr.validation.attachments import attachment_reasons
from test_cowork_engine import CONFIG, GOOD, Model, Source

TAX = {**GOOD, "document_type": "tax_invoice", "seller_name": "บริษัท ก จำกัด", "seller_tax": "0105536092641"}
OTHER = {"document_type": "other", "invoice_number": "DN-7", "seller_name": "บริษัท ข จำกัด",
         "seller_tax": "0105546146825", "total_amount": "530.00"}


def read(**fields):
    return validate_invoice(fields, anchor_year=2026)


def run(*outputs):
    return run_invoice(Source(len(outputs)), Model(list(outputs)), None, config=CONFIG, anchor_year=2026,
                       today=date(2026, 10, 7)).pipeline.pages


class AttachmentReasons(unittest.TestCase):
    def test_other_pages_next_to_a_proper_invoice_are_attachments(self):
        tax = read(**TAX)
        cases = (({"invoice_number": TAX["invoice_number"]}, "same_number"),
                 ({"total_amount": ""}, "no_total"), ({"total_amount": "0.00"}, "no_total"),
                 ({"seller_tax": TAX["seller_tax"]}, "same_seller"),
                 ({"seller_tax": "", "seller_name": " บริษัท ก  จำกัด"}, "same_seller"),
                 ({"seller_tax": "", "seller_name": ""}, "no_issuer"),
                 ({}, None))  # 另一家开的、有合计的「其他」（附在汇款包里的外国商业发票）照样算票
        for change, reason in cases:
            self.assertEqual(attachment_reasons([tax, read(**{**OTHER, **change})]), [None, reason], change)

    def test_nothing_changes_without_a_proper_invoice(self):
        others = [read(**{**OTHER, "total_amount": ""}), read(**{**OTHER, "seller_name": "", "seller_tax": ""})]
        self.assertEqual(attachment_reasons(others), [None, None])
        self.assertEqual(attachment_reasons([]), [])

    def test_proper_invoices_are_never_attachments(self):
        same = [read(**TAX), read(**{**TAX, "document_type": "receipt"}), read(**{**TAX, "total_amount": ""})]
        self.assertEqual(attachment_reasons(same), [None, None, None])


class EngineAttachments(unittest.TestCase):
    def test_a_delivery_page_with_the_invoice_number_is_not_another_invoice(self):
        first, second = run(TAX, {**OTHER, "invoice_number": TAX["invoice_number"]})
        self.assertFalse(first.invoice.is_not_invoice)
        self.assertTrue(second.invoice.is_not_invoice)
        self.assertIn(f"attachment_page: same_number {TAX['invoice_number']}", second.validation_warnings)

    def test_a_same_page_attachment_is_dropped_and_the_next_invoice_moves_up(self):
        other = {**OTHER, "seller_name": "", "seller_tax": ""}
        (page,) = run({**TAX, "additional_invoices": [other, {**TAX, "invoice_number": "IV69/101"}]})
        self.assertEqual([c.invoice_number for c in page.invoice.additional_invoices], ["IV69/101"])
        (page,) = run({**other, "additional_invoices": [TAX]})
        self.assertEqual((page.invoice.invoice_number, page.invoice.additional_invoices),
                         (TAX["invoice_number"], []))
        self.assertFalse(page.invoice.is_not_invoice)

    def test_a_foreign_invoice_in_a_remittance_package_stays(self):
        pages = run(TAX, OTHER)
        self.assertEqual([p.invoice.is_not_invoice for p in pages], [False, False])
        self.assertFalse(any(w.startswith("attachment_page:") for p in pages for w in p.validation_warnings))


if __name__ == "__main__":
    unittest.main()
