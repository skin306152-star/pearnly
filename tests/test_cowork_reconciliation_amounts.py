"""Offline regressions for the registered amount rule, field comparison and pass two."""

import copy
import unittest

from pearnly_cowork.reconciliation import amounts, fields
from pearnly_cowork.reconciliation.matching import (
	match_rows,
	pair_by_date_tax_amount,
	pair_by_invoice_number,
)


class AmountContract(unittest.TestCase):
	def test_source_float_rounding_is_preserved_instead_of_decimal(self):
		# Recorded in the previous batch as source-versus-Decimal investigations.
		cases = [
			("2.675", "2.68", False), ("-2.675", "-2.68", False), ("2.675", "2.69", False),
			("1.00", "1.01", False), ("1000.00", "1000.01", True), ("-1.00", "-1.01", False),
			("0.00", "0.01", True), ("1.005", "1.015", False),
		]
		for left, right, matched in cases:
			with self.subTest(left=left, right=right):
				self.assertEqual(
					amounts.within_tolerance(
						amounts.invoice_total({"amount_pre_vat": left}),
						amounts.report_total({"report_amount": right}),
					),
					matched,
				)

	def test_binary_float_rounding_is_the_contract(self):
		# 1.005 and 2.675 sit below the binary midpoint, so the source keeps them.
		self.assertEqual(amounts.invoice_total({"amount_pre_vat": "1.005"}), 1.0)
		self.assertEqual(amounts.invoice_total({"amount_pre_vat": "2.675"}), 2.67)
		self.assertEqual(amounts.invoice_total({"amount_pre_vat": "2.685"}), 2.69)

	def test_total_field_priority_commas_and_failures(self):
		self.assertEqual(amounts.invoice_total({"amount_pre_vat": "100", "total_amount": "107"}), 100.0)
		self.assertEqual(amounts.invoice_total({"total_amount": "107"}), 107.0)
		self.assertEqual(amounts.invoice_total({"amount_pre_vat": ""}), 0.0)
		self.assertEqual(amounts.invoice_total({}), 0.0)
		self.assertEqual(amounts.invoice_total({"amount_pre_vat": "1,234.50"}), 1234.5)
		self.assertEqual(amounts.invoice_total({"amount_pre_vat": "not a number"}), 0.0)
		self.assertEqual(amounts.report_total({"report_amount": "50", "amount_pre_vat": "9"}), 50.0)
		self.assertEqual(amounts.report_total({"amount_pre_vat": "9"}), 9.0)
		self.assertEqual(amounts.report_total({"report_amount": None}), 0.0)

	def test_tolerance_is_the_deployed_float_band_not_a_uniform_satang(self):
		# Binary floats make the deployed band magnitude dependent: a one-satang
		# difference sits inside it at 1000.00 but outside it at 100.00 and 1.00.
		self.assertTrue(amounts.within_tolerance(0.0, 0.01))
		self.assertTrue(amounts.within_tolerance(1000.0, 1000.01))
		self.assertFalse(amounts.within_tolerance(100.0, 100.01))
		self.assertFalse(amounts.within_tolerance(1.0, 1.01))
		self.assertTrue(amounts.within_tolerance(100.0, 100.004))

	def test_nan_and_infinite_differences_never_match(self):
		self.assertFalse(amounts.within_tolerance(float("nan"), 0.0))
		self.assertFalse(amounts.within_tolerance(float("inf"), float("inf")))


class FieldComparisonContract(unittest.TestCase):
	def test_amount_parse_failure_messages_stay_distinct(self):
		self.assertEqual(fields.compare_field("amount_pre_vat", "x", "1"),
			{"matched": False, "delta": "金额无法解析", "category": "amount_parse_error"})
		self.assertEqual(fields.compare_field("vat_amount", "x", "1"),
			{"matched": False, "delta": "税额无法解析", "category": "amount_parse_error"})

	def test_amount_delta_text_is_report_minus_invoice(self):
		self.assertEqual(fields.compare_field("amount_pre_vat", "100.00", "100.50"),
			{"matched": False, "delta": "差 +0.50", "category": "amount_diff"})
		self.assertEqual(fields.compare_field("vat_amount", "7.00", "6.00"),
			{"matched": False, "delta": "差 -1.00", "category": "vat_diff"})
		self.assertEqual(fields.compare_field("amount_pre_vat", "100.00", "100.004"),
			{"matched": True, "delta": "", "category": ""})
		self.assertEqual(fields.compare_field("net_amount", "1,000.00", "1000.004"),
			{"matched": True, "delta": "", "category": ""})

	def test_date_bands_and_unparsable_dates(self):
		self.assertEqual(fields.compare_field("date", "15/03/2026", "15/03/2569"),
			{"matched": True, "delta": "", "category": ""})
		self.assertEqual(fields.compare_field("invoice_date", "15/03/2026", "16/03/2026"),
			{"matched": False, "delta": "差 +1 天", "category": "date_diff"})
		self.assertEqual(fields.compare_field("document_date", "15/03/2026", "05/04/2026"),
			{"matched": False, "delta": "差 +21 天(跨期)", "category": "date_period_mismatch"})
		self.assertEqual(fields.compare_field("date", "bad", "15/03/2026"),
			{"matched": False, "delta": "日期无法解析", "category": "date_parse_error"})

	def test_blank_identifiers_never_match(self):
		self.assertEqual(fields.compare_field("invoice_no", None, None),
			{"matched": False, "delta": "None ≠ None", "category": "invoice_no_mismatch"})
		self.assertEqual(fields.compare_field("buyer_name", "", ""),
			{"matched": False, "delta": " ≠ ", "category": "name_mismatch"})
		self.assertEqual(fields.compare_field("buyer_tax_id", "", ""),
			{"matched": True, "delta": "", "category": ""})
		self.assertEqual(fields.compare_field("buyer_branch", "", None),
			{"matched": True, "delta": "", "category": ""})

	def test_unnamed_fields_fall_back_to_normalized_string_equality(self):
		self.assertEqual(fields.compare_field("note", "  OK ", "ok"),
			{"matched": True, "delta": "", "category": ""})
		self.assertEqual(fields.compare_field("note", "a", "b"),
			{"matched": False, "delta": "a ≠ b", "category": ""})

	def test_compare_all_fields_keeps_deployed_order_and_buyer_skip(self):
		invoice = {"invoice_date": "15/03/2026", "invoice_no": "INV1", "buyer_name": "ACME",
			"buyer_tax_id": "0107536000188", "buyer_branch": "0", "amount_pre_vat": "100.00",
			"vat_amount": "7.00"}
		report = {"report_date": "15/03/2026", "report_invoice_no": "INV1",
			"report_buyer_name": "ACME", "report_buyer_tax_id": "0107536000189",
			"report_buyer_branch": "00012", "report_amount_pre_vat": "100.00",
			"report_vat_amount": "7.00"}
		full = fields.compare_all_fields(invoice, report)
		self.assertEqual(list(full["fields"]), [name for name, _, _ in fields.FIELD_MAP])
		self.assertEqual(full["categories"], ["branch_mismatch", "tax_id_mismatch"])
		self.assertTrue(full["has_diff"])
		skipped = fields.compare_all_fields(invoice, report, skip_buyer=True)
		self.assertEqual(skipped["categories"], [])
		self.assertFalse(skipped["has_diff"])
		self.assertNotIn("buyer_tax_id", skipped["fields"])
		self.assertNotIn("buyer_branch", skipped["fields"])
		self.assertEqual(list(skipped["fields"]),
			["date", "invoice_no", "buyer_name", "amount_pre_vat", "vat_amount"])


class SecondPassContract(unittest.TestCase):
	def setUp(self):
		self.invoice = [{"id": "i1", "invoice_no": "IV1", "invoice_date": "15/03/2026",
			"buyer_tax_id": "0-1075 3600018-8", "amount_pre_vat": "100.00"}]

	def pass_two(self, report):
		return pair_by_date_tax_amount(self.invoice, [report], [0], [0])

	def test_all_three_keys_are_required(self):
		base = {"row_no": 1, "report_invoice_no": "OTHER", "report_date": "15/03/2026",
			"report_buyer_tax_id": "0107536000188", "report_amount": "100.00"}
		self.assertEqual(len(self.pass_two(base).pairs), 1)
		for change in ({"report_date": "16/03/2026"}, {"report_buyer_tax_id": ""},
				{"report_buyer_tax_id": "0107536000189"}, {"report_amount": "100.02"},
				{"report_date": "not a date"}, {"report_amount": "not a number"}):
			with self.subTest(change=change):
				result = self.pass_two({**base, **change})
				self.assertEqual(result.pairs, [])
				self.assertEqual(result.unmatched_invoice_indices, [0])
				self.assertEqual(result.unmatched_report_indices, [0])

	def test_one_satang_tolerance_is_inclusive_and_source_rounded(self):
		base = {"row_no": 1, "report_date": "15/03/2026", "report_buyer_tax_id": "0107536000188"}
		self.assertEqual(len(self.pass_two({**base, "report_amount": "100.004"}).pairs), 1)
		self.assertEqual(self.pass_two({**base, "report_amount": "100.02"}).pairs, [])
		# The deployed float band is magnitude dependent: one satang at 100.00 is
		# 0.010000000000005116 and therefore just outside the 0.01 literal.
		self.assertEqual(self.pass_two({**base, "report_amount": "100.01"}).pairs, [])
		# 2.675 rounds down in binary float, so it pairs against 2.67.
		rows = [{"id": "i1", "invoice_no": "IV1", "invoice_date": "15/03/2026",
			"buyer_tax_id": "0107536000188", "amount_pre_vat": "2.675"}]
		reports = [{**base, "report_amount": "2.67"}]
		self.assertEqual(len(pair_by_date_tax_amount(rows, reports, [0], [0]).pairs), 1)

	def test_empty_tax_ids_never_pair_even_when_both_are_blank(self):
		rows = [{"id": "i1", "invoice_no": "IV1", "invoice_date": "15/03/2026",
			"buyer_tax_id": "", "amount_pre_vat": "100.00"}]
		reports = [{"row_no": 1, "report_date": "15/03/2026", "report_buyer_tax_id": "",
			"report_amount": "100.00"}]
		self.assertEqual(pair_by_date_tax_amount(rows, reports, [0], [0]).pairs, [])

	def test_duplicate_candidates_are_consumed_in_ascending_row_order(self):
		reports = [{"row_no": number, "report_date": "15/03/2026",
			"report_buyer_tax_id": "0107536000188", "report_amount": "100.00"}
			for number in (8, 3, 5)]
		result = pair_by_date_tax_amount(self.invoice, reports, [0], [0, 1, 2])
		self.assertEqual([pair["report_row_no"] for pair in result.pairs], [8])
		self.assertEqual(result.unmatched_report_indices, [1, 2])

	def test_pair_shape_and_confidence_match_the_contract(self):
		report = {"row_no": 7, "report_date": "15/03/2026",
			"report_buyer_tax_id": "0107536000188", "report_amount": "100.00"}
		self.assertEqual(self.pass_two(report).pairs, [{
			"invoice_idx": 0, "report_idx": 0, "invoice_id": "i1", "report_row_no": 7,
			"pair_confidence": 0.95, "pass": 2,
		}])

	def test_first_pass_rows_are_not_offered_again(self):
		rows = [
			{"id": "i1", "invoice_no": "A1", "invoice_date": "15/03/2026",
				"buyer_tax_id": "0107536000188", "amount_pre_vat": "100.00"},
			{"id": "i2", "invoice_no": "A2", "invoice_date": "15/03/2026",
				"buyer_tax_id": "0107536000188", "amount_pre_vat": "100.00"},
		]
		reports = [
			{"row_no": 1, "report_invoice_no": "A1", "report_date": "15/03/2026",
				"report_buyer_tax_id": "0107536000188", "report_amount": "100.00"},
			{"row_no": 2, "report_invoice_no": "OTHER", "report_date": "15/03/2026",
				"report_buyer_tax_id": "0107536000188", "report_amount": "100.00"},
		]
		first = pair_by_invoice_number(rows, reports)
		self.assertEqual([pair["report_row_no"] for pair in first.pairs], [1])
		second = pair_by_date_tax_amount(rows, reports,
			first.unmatched_invoice_indices, first.unmatched_report_indices)
		self.assertEqual([pair["report_row_no"] for pair in second.pairs], [2])
		self.assertEqual(second.unmatched_report_indices, [])


class MatchingComposition(unittest.TestCase):
	def fixture(self):
		invoices = [
			{"id": "i1", "invoice_no": "INV-001", "invoice_date": "15/03/2026",
				"buyer_tax_id": "0107536000188", "amount_pre_vat": "100.00"},
			{"id": "i2", "invoice_no": "IV-002", "invoice_date": "16/03/2026",
				"buyer_tax_id": "0107536000188", "amount_pre_vat": "250.00"},
			{"id": "i3", "invoice_no": "INV-003", "invoice_date": "17/03/2026",
				"buyer_tax_id": "0107536000188", "amount_pre_vat": "999.00"},
		]
		reports = [
			{"row_no": 11, "report_invoice_no": "inv001", "report_date": "15/03/2026",
				"report_buyer_tax_id": "0107536000188", "report_amount": "100.00"},
			{"row_no": 12, "report_invoice_no": "OTHER", "report_date": "16/03/2026",
				"report_buyer_tax_id": "0107536000188", "report_amount": "250.00"},
			{"row_no": 13, "report_invoice_no": "OTHER2", "report_date": "17/03/2026",
				"report_buyer_tax_id": "0107536000188", "report_amount": "500.00"},
		]
		return invoices, reports

	def test_both_passes_compose_into_the_deployed_response(self):
		invoices, reports = self.fixture()
		result = match_rows(invoices, reports)
		self.assertEqual([(pair["pass"], pair["invoice_id"], pair["report_row_no"])
			for pair in result.pairs], [(1, "i1", 11), (2, "i2", 12)])
		self.assertEqual(result.invoice_orphans, ["i3"])
		self.assertEqual(result.report_orphans, [13])
		self.assertEqual(result.stats, {"total_invoices": 3, "total_report_rows": 3, "matched": 2,
			"invoice_orphan_count": 1, "report_orphan_count": 1, "pass1_count": 1,
			"pass2_count": 1, "pass3_count": 0})

	def test_inputs_are_never_mutated(self):
		invoices, reports = self.fixture()
		before = copy.deepcopy((invoices, reports))
		match_rows(invoices, reports)
		self.assertEqual((invoices, reports), before)

	def test_empty_inputs(self):
		result = match_rows([], [])
		self.assertEqual((result.pairs, result.invoice_orphans, result.report_orphans),
			([], [], []))
		self.assertEqual(result.stats["matched"], 0)


class ReportCandidateOrder(unittest.TestCase):
	def test_ascending_row_order_equals_the_deployed_set_iteration(self):
		"""Pass two must pick the same duplicate sibling as the deployed loop.

		The source iterated a CPython set of small integer row indices built from
		``range(n)`` and then removed first-pass rows. Iteration over those sets is
		ascending, so visiting ascending candidates reproduces its choice.
		"""
		for size in (0, 1, 2, 7, 8, 9, 31, 32, 33, 100, 129, 400, 2000):
			remaining = set(range(size))
			for index in range(0, size, 3):
				remaining.discard(index)
			with self.subTest(size=size):
				self.assertEqual(list(remaining), sorted(remaining))


if __name__ == "__main__":
	unittest.main()
