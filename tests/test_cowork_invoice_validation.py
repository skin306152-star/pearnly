"""Deterministic invoice decisions; no provider or Site is needed."""
from datetime import date, timedelta
from decimal import Decimal, getcontext
import unittest

from pearnly_cowork.ocr.schema import Page, validate_invoice
from pearnly_cowork.ocr.validation import (
    aggregate_page_confidence, bucket_confidence, check_amount_math,
    check_field_in_l1_text, count_invoice_no_candidates, credit_note_review_reason,
    evaluate_sanity, evaluate_soft_flags, evaluate_triggers, field_confidences,
    find_field_min_word_conf, infer_missing_discount, validate_invoice_date, validate_invoice_sources,
)


def invoice(**fields):
    return validate_invoice(fields, anchor_year=2026)


def page(text="", words=(), confidence=.99):
    box = {"vertices": [(0, 0), (20, 0), (20, 10), (0, 10)]}
    blocks = []
    if words:
        entries = [{"text": text, "confidence": conf, "bbox": box} for text, conf in words]
        paragraph = {"text": " ".join(w["text"] for w in entries), "confidence": confidence,
                     "bbox": box, "words": entries}
        blocks = [{"text": paragraph["text"], "confidence": confidence, "bbox": box,
                   "paragraphs": [paragraph]}]
    return Page(page_number=1, width=20, height=10, full_text=text, avg_confidence=confidence, blocks=blocks)


class InvoiceValidationTests(unittest.TestCase):
    def test_amount_tolerance_is_inclusive_and_discount_has_two_interpretations(self):
        self.assertIsNone(check_amount_math(invoice(subtotal="100", vat="7", total_amount="107.50")))
        self.assertIsNotNone(check_amount_math(invoice(subtotal="100", vat="7", total_amount="107.51")))
        for total in ("106.30", "96.30"):
            self.assertIsNone(check_amount_math(invoice(subtotal="100", discount="10", vat="6.30", total_amount=total)))

    def test_core_amount_parser_is_strict_but_discount_parser_is_permissive(self):
        self.assertEqual(check_amount_math(invoice(subtotal="1,000", vat="70", total_amount="1070")),
                         "amount field not numeric (parse failed)")
        self.assertIsNone(check_amount_math(invoice(subtotal="2000", vat="7", discount="฿1,000", total_amount="1007")))
        self.assertIsNone(check_amount_math(invoice(subtotal="100", total_amount="100")))

    def test_nonfinite_amounts_fail_closed_instead_of_float_nan_comparisons(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            self.assertEqual(check_amount_math(invoice(subtotal=value, vat="7", total_amount="107")),
                             "amount field not numeric (parse failed)")

    def test_large_money_difference_is_not_lost_to_float_or_default_decimal_precision(self):
        for sub, total in (("9007199254740993", "9007199254740992"), ("1" + "0" * 40 + "1", "1" + "0" * 41)):
            precision = getcontext().prec
            self.assertIsNotNone(check_amount_math(invoice(subtotal=sub, vat="0", total_amount=total)))
            self.assertEqual(getcontext().prec, precision)

    def test_missing_vat_discount_and_vat_inclusive_rows(self):
        self.assertEqual(evaluate_sanity(invoice(subtotal="100", discount="20", total_amount="80")), [])
        self.assertTrue(evaluate_sanity(invoice(subtotal="100", total_amount="50")))
        value = invoice(subtotal="100", vat="7", total_amount="107", items=[{"subtotal": "50"}, {"subtotal": "57"}])
        self.assertEqual(evaluate_sanity(value), [])

    def test_negative_money_credit_note_and_tax_checks(self):
        self.assertTrue(any("为负数" in text for text in evaluate_sanity(invoice(total_amount="-1"))))
        credit = invoice(document_type="credit_note", total_amount="-1")
        self.assertEqual(evaluate_sanity(credit), [])
        self.assertIsNotNone(credit_note_review_reason(credit))
        self.assertIsNone(credit_note_review_reason(invoice(is_not_invoice=True, document_type="credit_note")))
        reasons = evaluate_sanity(invoice(seller_tax="0105556086859", buyer_tax="0105556086859"))
        self.assertTrue(any("卖方税号 == 买方税号" in text for text in reasons))
        self.assertTrue(any("MOD-11" in text for text in evaluate_sanity(invoice(seller_tax="1111111111111"))))

    def test_same_page_extras_receive_core_and_symmetric_row_checks(self):
        value = invoice(subtotal="100", vat="7", total_amount="107", items=[{"subtotal": "40"}, {"subtotal": "40"}])
        self.assertEqual(evaluate_sanity(value), [])
        value.additional_invoices = [invoice(total_amount="-10")]
        reasons = evaluate_sanity(value)
        self.assertTrue(any(text.startswith("同页第2张") and "为负数" in text for text in reasons))
        self.assertTrue(any(text.startswith("同页第1张") and "跨票错配" in text for text in reasons))

    def test_noninvoice_short_circuits_all_sanity_including_extras(self):
        value = invoice(is_not_invoice=True, total_amount="-1", additional_invoices=[{"total_amount": "-2"}])
        self.assertEqual(evaluate_sanity(value), [])

    def test_discount_inference_is_explicit_and_retains_review_reason(self):
        first = invoice(subtotal="100", vat="6.30", total_amount="96.30")
        reviewed = first.model_copy(deep=True)
        reason = infer_missing_discount(reviewed)
        self.assertTrue(reason.startswith("discount_inferred:"))
        self.assertEqual(reviewed.discount, "10.00")
        self.assertEqual(first.discount, "")
        self.assertIsNone(infer_missing_discount(invoice(subtotal="100", vat="6.30", total_amount="96.30", discount="0")))

    def test_dates_use_explicit_today_and_source_window(self):
        today = date(2026, 9, 21)
        for days, warned in ((1, False), (2, True), (-365 * 5, False), (-365 * 5 - 1, True)):
            result = validate_invoice_date(invoice(date=(today + timedelta(days=days)).isoformat()), today=today)
            self.assertEqual(bool(result), warned)
        self.assertEqual(validate_invoice_date(invoice(date="2026-02-30"), today=today), [])
        with self.assertRaises(TypeError):
            validate_invoice_date(invoice())
        with self.assertRaises(ValueError):
            validate_invoice_date(invoice(), today=None)

    def test_amount_source_warnings_do_not_erase_evidence(self):
        value = invoice(total_amount="6091", source_refs={
            "total_amount": {"value": "6091", "source_column": "Description:", "source_text": "6091"},
            "date": {"value": "6091", "source_column": "Description"},
        })
        before = value.model_dump()
        warnings = validate_invoice_sources(value, today=date(2026, 9, 21))
        self.assertEqual(len(warnings), 1)
        self.assertIn("description column", warnings[0])
        self.assertEqual(value.model_dump(), before)

    def test_normalization_word_confidence_and_short_value_behavior(self):
        evidence = page("IV-69/00179 ฿1,780.00", [("IV69/00179", .7), ("1,780.00", .9)])
        self.assertTrue(check_field_in_l1_text(evidence, "IV6900179"))
        self.assertTrue(check_field_in_l1_text(evidence, "999"))
        self.assertFalse(check_field_in_l1_text(evidence, "9999"))
        self.assertEqual(find_field_min_word_conf(evidence, "IV69-00179"), .7)
        self.assertIsNone(find_field_min_word_conf(evidence, "179"))

    def test_triggers_soft_flags_and_optional_pattern_evidence_remain_separate(self):
        value = invoice(invoice_number="IV69/00179", total_amount="1780.00", seller_tax="1234")
        evidence = page("IV69/00179 1780.00 1234", [("IV69/00179", .7)])
        self.assertEqual(evaluate_triggers(evidence, value), [])
        flags = evaluate_soft_flags(evidence, value)
        self.assertTrue(any("low word conf" in text for text in flags))
        self.assertTrue(any("seller_tax format invalid" in text for text in flags))
        self.assertEqual(evaluate_triggers(evidence, value, pattern_anomaly="pattern changed"), ["pattern changed"])
        self.assertEqual(field_confidences(evidence, value), {"invoice_number": .7})

    def test_legal_number_requirement_and_missed_invoice_gate(self):
        self.assertIn("invoice_number missing", evaluate_triggers(page("1000"), invoice(total_amount="1000")))
        self.assertEqual(evaluate_triggers(page("1000"), invoice(document_type="receipt", total_amount="1000")), [])
        evidence = page("IV69/00179 IV69/00189")
        self.assertEqual(count_invoice_no_candidates(evidence.full_text, "IV69/00179"), 2)
        missing = invoice(invoice_number="IV69/00179")
        self.assertTrue(any("possible_missed_invoice" in text for text in evaluate_triggers(evidence, missing)))

    def test_confidence_thresholds_and_penalties_are_explicit(self):
        self.assertEqual(bucket_confidence(.98, False), "auto")
        self.assertEqual(bucket_confidence(.9, False), "yellow_confirm")
        self.assertEqual(bucket_confidence(1, True), "needs_review")
        self.assertEqual(bucket_confidence(.98, False, auto_threshold=.99), "yellow_confirm")
        self.assertAlmostEqual(aggregate_page_confidence(page(confidence=0), ["trigger"], True, ["soft"]), .65)


if __name__ == "__main__":
    unittest.main()
