"""Offline invoice schema contracts; no Frappe, provider, Site or IO."""
from copy import deepcopy
from decimal import Decimal
import unittest

from pydantic import ValidationError
from pearnly_cowork.ocr.schema import (
    BoundingBox, FieldRef, LineItem, Page, PipelinePageResult, PipelineResult,
    ThaiInvoice, validate_invoice,
)
from pearnly_cowork.ocr.pages import flatten_pipeline_pages


def invoice(**values):
    return validate_invoice(values, anchor_year=2026)


class InvoiceSchemaTests(unittest.TestCase):
    def test_all_source_fields_and_defaults_remain(self):
        expected = "document_type is_not_invoice is_copy_or_duplicate invoice_number date date_raw " \
            "seller_name seller_tax seller_addr seller_branch buyer_name buyer_tax buyer_addr buyer_branch " \
            "subtotal vat wht_rate wht_amount discount total_amount payment_received cash_amount change_amount " \
            "payment_method currency items notes category additional_invoices source_refs"
        self.assertEqual(set(ThaiInvoice.model_fields), set(expected.split()))
        value = invoice()
        self.assertIsNone(value.total_amount)
        self.assertEqual(value.items, [])
        self.assertEqual(value.currency, "")
        self.assertEqual(value.document_type, "tax_invoice")

    def test_amounts_stay_strings_and_cash_does_not_replace_payable(self):
        value = invoice(subtotal=100, vat=7.0, total_amount=107, cash_amount=200,
                        change_amount=93, discount=None, payment_received=107)
        self.assertEqual((value.subtotal, value.vat, value.total_amount), ("100", "7.0", "107"))
        self.assertEqual((value.cash_amount, value.change_amount, value.payment_received), ("200", "93", "107"))
        self.assertEqual(value.discount, "")
        self.assertEqual(LineItem(name=None, qty=True, price=0, subtotal=3.5).model_dump(),
                         {"name": "", "qty": "true", "price": "0", "subtotal": "3.5"})

    def test_optional_string_truthiness_and_required_bool_coercion_match_source(self):
        value = invoice(invoice_number="", date="", total_amount="", seller_name=False,
                        is_not_invoice=None, is_copy_or_duplicate=None, items=None, additional_invoices=None)
        self.assertEqual((value.invoice_number, value.date, value.total_amount), (None, None, None))
        self.assertEqual(value.seller_name, "false")
        self.assertFalse(value.is_not_invoice)
        self.assertEqual(value.additional_invoices, [])
        with self.assertRaises(ValidationError):
            invoice(total_amount=True)

    def test_document_type_fallback_is_source_behavior(self):
        self.assertEqual(invoice(document_type=None).document_type, "tax_invoice")
        self.assertEqual(invoice(document_type="unknown").document_type, "other")
        self.assertEqual(invoice(document_type="credit_note").document_type, "credit_note")

    def test_provenance_nulls_and_typed_references_are_retained(self):
        reference = FieldRef(value="1,000.00", source_column="Total", source_page=2)
        value = invoice(source_refs={"total_amount": reference, "vat": None, "subtotal": [],
                                    "date": {"source_text": None, "source_column": None,
                                             "source_page": None, "confidence": None}})
        self.assertEqual(set(value.source_refs), {"total_amount", "date"})
        self.assertEqual(value.source_refs["total_amount"].value, "1,000.00")
        self.assertEqual(value.source_refs["date"].source_page, 0)
        self.assertEqual(value.source_refs["date"].confidence, 0.0)
        self.assertEqual(invoice(source_refs=None).source_refs, {})
        value.source_refs["total_amount"].source_text = "edited"
        self.assertEqual(reference.source_text, "")

    def test_year_only_correction_keeps_model_month_day(self):
        for raw, expected in (("01/02/69", "2026-12-31"), ("01/02/2569", "2026-12-31"),
                              ("01/02/2026", "2026-12-31"), ("01/02/27", "2027-12-31"),
                              ("01/02/28", "2020-12-31")):
            self.assertEqual(invoice(date="2020-12-31", date_raw=raw).date, expected)
        self.assertIsNone(invoice(date_raw="21/09/2569").date)
        self.assertEqual(invoice(date="2020-1-2", date_raw="21/09/2569").date, "2020-1-2")

    def test_anchor_is_required_and_not_accepted_from_untrusted_payload(self):
        with self.assertRaises(ValidationError):
            ThaiInvoice.model_validate({"date": "2020-01-01", "_anchor_year": 2026})
        for invalid in (None, True, "2026", 0, 9999):
            with self.assertRaises(ValueError):
                validate_invoice({}, anchor_year=invalid)
        first = invoice(date="2020-01-01", date_raw="01/01/69")
        with self.assertRaises(ValidationError):
            PipelinePageResult.model_validate({"page_number": 1, "invoice": first},
                                              context={"anchor_year": 2025})
        self.assertEqual((first._anchor_year, first.date), (2026, "2026-01-01"))

    def test_same_page_siblings_receive_fixed_year_and_preserve_nested_source_behavior(self):
        value = invoice(date="2020-01-01", date_raw="01/01/69", additional_invoices=[{
            "date": "2020-02-02", "date_raw": "02/02/2569", "seller_branch": "00018",
            "additional_invoices": [{"date": "2020-03-03", "date_raw": "03/03/2026"}],
        }])
        self.assertEqual(value.additional_invoices[0].date, "2026-02-02")
        self.assertEqual(value.additional_invoices[0].additional_invoices[0].date, "2026-03-03")
        self.assertEqual(value.additional_invoices[0].seller_branch, "00018")

    def test_schema_parse_is_independent_and_does_not_replace_first_evidence(self):
        raw = {"total_amount": "100.00", "additional_invoices": [{"total_amount": "200.00"}]}
        saved = deepcopy(raw)
        value = validate_invoice(raw, anchor_year=2026)
        reread = validate_invoice(value, anchor_year=2026)
        reread.total_amount = "300.00"
        reread.additional_invoices[0].total_amount = "400.00"
        self.assertEqual(value.total_amount, "100.00")
        self.assertEqual(value.additional_invoices[0].total_amount, "200.00")
        self.assertEqual(raw, saved)

    def test_result_wrapper_keeps_explicit_anchor_and_physical_page(self):
        value = invoice(invoice_number="A1", additional_invoices=[{"invoice_number": "A2"}])
        page = PipelinePageResult(page_number=3, invoice=value, layer2_input_tokens=10)
        pages = flatten_pipeline_pages([page])
        self.assertEqual([p["page_index"] for p in pages], [3, 3])
        self.assertEqual([p["fields"]["invoice_number"] for p in pages], ["A1", "A2"])
        self.assertEqual(page.invoice._anchor_year, 2026)
        self.assertNotIn("_anchor_year", page.model_dump(mode="json")["invoice"])

    def test_result_slice_rejects_unimplemented_document_families(self):
        for fields in ({"document_type": "general_ledger"}, {"document": {"entries": []}}):
            with self.assertRaises(ValidationError):
                PipelinePageResult(page_number=1, invoice=invoice(), **fields)

    def test_pipeline_cost_is_finite_decimal_with_json_string(self):
        value = PipelineResult(pages=[], page_count=0, elapsed_ms=0, estimated_cost_thb="0.0100003")
        self.assertEqual(value.estimated_cost_thb, Decimal("0.0100003"))
        self.assertEqual(value.model_dump(mode="json")["estimated_cost_thb"], "0.0100003")
        for invalid in ("NaN", "Infinity", "-0.1"):
            with self.assertRaises(ValidationError):
                PipelineResult(pages=[], page_count=0, elapsed_ms=0, estimated_cost_thb=invalid)

    def test_page_and_evidence_geometry_keep_source_bounds(self):
        with self.assertRaises(ValidationError):
            BoundingBox(vertices=[(0, 0)] * 3)
        with self.assertRaises(ValidationError):
            FieldRef(source_page=-1)
        with self.assertRaises(ValidationError):
            Page(page_number=0, width=0, height=0, full_text="", avg_confidence=0, blocks=[])


if __name__ == "__main__":
    unittest.main()
