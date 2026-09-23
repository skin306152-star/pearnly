"""Offline regressions for deployed VAT normalization and first-pass pairing."""

import copy
import unittest
from datetime import date

from pearnly_cowork.reconciliation import normalization as normalize
from pearnly_cowork.reconciliation.matching import pair_by_invoice_number


class NormalizationContract(unittest.TestCase):
	def test_invoice_number_only_normalizes_visual_separators(self):
		self.assertEqual(normalize.normalize_invoice_no(" ＩＮＶ-00/12 "), "inv0012")
		self.assertNotEqual(normalize.normalize_invoice_no("INV001"), normalize.normalize_invoice_no("IV001"))
		self.assertNotEqual(normalize.normalize_invoice_no("INV001"), normalize.normalize_invoice_no("INV1"))
		self.assertEqual(normalize.normalize_invoice_no(None), "")

	def test_name_and_branch_normalization_preserves_deployed_aliases(self):
		self.assertEqual(normalize.normalize_name(" ACME\n CO \t"), "acmeco")
		for value in (None, "", "00000", "สำนักงานใหญ่", "สนญ", "head office", "HQ"):
			with self.subTest(value=value):
				self.assertEqual(normalize.normalize_branch(value), "00000")
		self.assertEqual(normalize.normalize_branch("12"), "00012")
		self.assertEqual(normalize.normalize_branch("Eastern"), "eastern")

	def test_tax_identifier_check_and_fuzzy_distance_are_evidence_only(self):
		self.assertEqual(normalize.normalize_tax_id("0-1075 3600018-8"), "0107536000188")
		self.assertTrue(normalize.mod11_check("0107536000188"))
		self.assertFalse(normalize.mod11_check("0107536000189"))
		self.assertEqual(normalize.tax_id_fuzzy_distance("123456", "123456"), 0)
		self.assertEqual(normalize.tax_id_fuzzy_distance("123456", "123457"), 1)
		self.assertEqual(normalize.tax_id_fuzzy_distance("", "123456"), 99)

	def test_two_digit_years_follow_thai_fiscal_convention(self):
		for value in ("15/03/69", "15/03/2569", "15/03/2026", "2026-03-15", "2026/03/15", "15-03-2026"):
			with self.subTest(value=value):
				self.assertEqual(normalize.parse_date(value), date(2026, 3, 15))
		self.assertEqual(normalize.parse_date("01/01/00"), date(1957, 1, 1))
		self.assertIsNone(normalize.parse_date("31/02/2026"))
		self.assertIsNone(normalize.parse_date("invalid"))

	def test_source_invalid_converted_leap_day_is_not_silently_reinterpreted(self):
		# The source accepts 29/02/2568 as a leap day, then raises replacing its
		# year with 2025. Record this limitation instead of changing semantics.
		with self.assertRaises(ValueError):
			normalize.parse_date("29/02/2568")


class FirstPassContract(unittest.TestCase):
	def test_normalized_number_pairs_even_when_other_fields_differ(self):
		invoices = [{"id": "i1", "invoice_no": "INV-001", "amount_pre_vat": "100", "buyer_tax_id": "a"}]
		reports = [{"row_no": 5, "report_invoice_no": "inv001", "report_amount": "999", "report_buyer_tax_id": "b"}]
		out = pair_by_invoice_number(invoices, reports)
		self.assertEqual(out.pairs, [{
			"invoice_idx": 0, "report_idx": 0, "invoice_id": "i1", "report_row_no": 5,
			"pair_confidence": 1.0, "pass": 1,
		}])
		self.assertEqual(out.unmatched_invoice_indices, [])
		self.assertEqual(out.unmatched_report_indices, [])

	def test_duplicate_rows_consume_once_in_original_input_order(self):
		invoices = [{"id": name, "invoice_no": "X1"} for name in ("i1", "i2", "i3")]
		reports = [{"row_no": number, "report_invoice_no": "X1"} for number in (8, 3)]
		out = pair_by_invoice_number(invoices, reports)
		self.assertEqual([(row["invoice_id"], row["report_row_no"]) for row in out.pairs], [("i1", 8), ("i2", 3)])
		self.assertEqual(out.unmatched_invoice_indices, [2])
		self.assertEqual(out.unmatched_report_indices, [])

	def test_report_fallback_and_blank_keys(self):
		out = pair_by_invoice_number(
			[{"id": "i1", "invoice_no": "A"}, {"id": "blank", "invoice_no": ""}],
			[{"row_no": 1, "invoice_no": "a"}, {"row_no": 2, "report_invoice_no": ""}],
		)
		self.assertEqual(len(out.pairs), 1)
		self.assertEqual(out.unmatched_invoice_indices, [1])
		self.assertEqual(out.unmatched_report_indices, [1])

	def test_partial_pass_never_performs_fuzzy_or_amount_matching(self):
		invoices = [{"id": "i", "invoice_no": "INV001", "invoice_date": "2026-03-15", "buyer_tax_id": "123", "amount_pre_vat": "100"}]
		reports = [{"row_no": 1, "report_invoice_no": "IV001", "report_date": "2026-03-15", "report_buyer_tax_id": "123", "report_amount": "100"}]
		before = copy.deepcopy((invoices, reports))
		out = pair_by_invoice_number(invoices, reports)
		self.assertEqual(out.pairs, [])
		self.assertEqual(out.unmatched_invoice_indices, [0])
		self.assertEqual(out.unmatched_report_indices, [0])
		self.assertEqual((invoices, reports), before)
		self.assertFalse(hasattr(out, "stats"))
		self.assertFalse(hasattr(out, "invoice_orphans"))

	def test_empty_inputs(self):
		out = pair_by_invoice_number([], [])
		self.assertEqual((out.pairs, out.unmatched_invoice_indices, out.unmatched_report_indices), ([], [], []))


if __name__ == "__main__":
	unittest.main()
