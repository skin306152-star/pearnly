"""Offline regressions for the decoded-row contract of the native income store."""

import unittest
from decimal import Decimal

from pearnly_cowork.reconciliation import income_recon_contracts as contracts


class GlRowContract(unittest.TestCase):
	def test_the_voucher_number_is_normalized_on_the_way_in(self):
		row = contracts.gl_row({"doc_no": " GL 1 ", "credit": "1,070.00"})
		self.assertEqual((row["doc_no"], row["norm_doc_no"]), ("GL 1", "gl1"))
		self.assertEqual((row["debit"], row["credit"]), (Decimal(0), Decimal("1070.00")))
		given = contracts.gl_row({"doc_no": "GL 1", "norm_doc_no": "custom"})
		self.assertEqual(given["norm_doc_no"], "custom")

	def test_a_row_without_a_printed_voucher_number_is_kept_as_the_deployed_run_kept_it(self):
		# The deployed core indexed only rows with a normalized number, but it
		# never refused one that had none: it counted it as a ledger-only amount.
		row = contracts.gl_row({"credit": "1", "description": "subtotal"})
		self.assertEqual((row["doc_no"], row["norm_doc_no"]), ("", ""))

	def test_dates_stay_the_printed_text_and_other_codes_are_trimmed(self):
		row = contracts.gl_row({"doc_no": "GL1", "date": "01/06/2569", "account_code": " 4110-01 ",
			"description": " sale "})
		self.assertEqual((row["date"], row["account_code"], row["description"]),
			("01/06/2569", "4110-01", "sale"))

	def test_negative_ledger_amounts_are_reversals_on_the_other_side(self):
		# 2026-09-28 录制：总账写成 (1,000.00) / -1,000.00 整份对账失败
		from decimal import Decimal
		row = contracts.gl_row({"doc_no": "GL1", "credit": "-1000.00"})
		self.assertEqual((row["debit"], row["credit"]), (Decimal("1000.00"), Decimal(0)))
		row = contracts.gl_row({"doc_no": "GL1", "debit": "-5", "credit": "20"})
		self.assertEqual((row["debit"], row["credit"]), (Decimal(0), Decimal("25")))

	def test_rejections_cover_precision_and_unknown_fields(self):
		cases = [
			({"doc_no": "GL1", "credit": "1.1234567"},
				"reconciliation.amount_precision:income_gl.credit"),
			({"doc_no": "GL1", "credit": "1e400"}, "reconciliation.amount_invalid:income_gl.credit"),
			({"doc_no": "GL1", "customer_name": "ACME"},
				"reconciliation.row_unknown_field:income_gl"),
			({"doc_no": "x" * 301}, "reconciliation.text_too_long:income_gl.doc_no"),
			({"doc_no": "GL1", "date": "x" * 41}, "reconciliation.text_too_long:income_gl.date"),
		]
		for payload, expected in cases:
			with self.subTest(payload=payload):
				with self.assertRaises(contracts.ReconciliationContractError) as caught:
					contracts.gl_row(payload)
				self.assertEqual(str(caught.exception), expected)

	def test_non_mapping_row_is_refused(self):
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			contracts.gl_row(["not", "a", "row"])
		self.assertEqual(str(caught.exception), "reconciliation.row_invalid:income_gl")


class VatRowContract(unittest.TestCase):
	def test_the_deployed_report_keys_are_kept_verbatim(self):
		row = contracts.vat_row({"report_ref_no": " GL1 ", "report_reference_no": "REF",
			"report_invoice_no": "INV", "report_date": "01/06/2569",
			"report_buyer_name": " ACME ", "report_amount_pre_vat": "1,070.00"})
		self.assertEqual(sorted(row), sorted(contracts.VAT_KEYS))
		self.assertEqual((row["report_ref_no"], row["report_buyer_name"]),
			("GL1", "ACME"))
		self.assertEqual(row["report_amount_pre_vat"], Decimal("1070.00"))

	def test_a_credit_note_amount_is_allowed_and_an_absent_amount_is_zero(self):
		self.assertEqual(contracts.vat_row({"report_amount_pre_vat": "-107.00"})[
			"report_amount_pre_vat"], Decimal("-107.00"))
		self.assertEqual(contracts.vat_row({})["report_amount_pre_vat"], Decimal(0))
		self.assertEqual(contracts.vat_row({"report_amount_pre_vat": ""})[
			"report_amount_pre_vat"], Decimal(0))

	def test_a_partial_row_is_filled_with_empty_text(self):
		row = contracts.vat_row({"report_ref_no": "GL1"})
		self.assertEqual((row["report_reference_no"], row["report_invoice_no"],
			row["report_date"], row["report_buyer_name"]), ("", "", "", ""))

	def test_rejections_cover_the_ledger_fields_and_unknown_keys(self):
		cases = [
			({"doc_no": "GL1"}, "reconciliation.row_unknown_field:income_vat"),
			({"report_amount_pre_vat": "1.1234567"},
				"reconciliation.amount_precision:income_vat.report_amount_pre_vat"),
			({"report_date": "x" * 41}, "reconciliation.text_too_long:income_vat.report_date"),
			({"report_buyer_name": "x" * 301},
				"reconciliation.text_too_long:income_vat.report_buyer_name"),
		]
		for payload, expected in cases:
			with self.subTest(payload=payload):
				with self.assertRaises(contracts.ReconciliationContractError) as caught:
					contracts.vat_row(payload)
				self.assertEqual(str(caught.exception), expected)

	def test_non_mapping_row_is_refused(self):
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			contracts.vat_row("report_ref_no=GL1")
		self.assertEqual(str(caught.exception), "reconciliation.row_invalid:income_vat")


class HeaderAndBatchContract(unittest.TestCase):
	def test_header_defaults_to_the_deployed_tolerance(self):
		value = contracts.header(None)
		self.assertEqual((value["amount_tolerance"], value["gl_account_code"]),
			(contracts.AMOUNT_TOLERANCE, ""))
		self.assertEqual(contracts.header("")["amount_tolerance"], contracts.AMOUNT_TOLERANCE)
		given = contracts.header({"amount_tolerance": "0.5", "gl_account_code": " 4110-01 "})
		self.assertEqual((given["amount_tolerance"], given["gl_account_code"]),
			(Decimal("0.5"), "4110-01"))

	def test_header_refuses_an_unknown_key_a_bad_tolerance_and_a_non_mapping(self):
		cases = [
			({"mystery": 1}, "reconciliation.header_invalid"),
			({"amount_tolerance": "1000.01"}, "reconciliation.amount_tolerance_invalid"),
			({"amount_tolerance": "-1"}, "reconciliation.amount_negative:header.amount_tolerance"),
		]
		for payload, expected in cases:
			with self.subTest(payload=payload):
				with self.assertRaises(contracts.ReconciliationContractError) as caught:
					contracts.header(payload)
				self.assertEqual(str(caught.exception), expected)
		self.assertEqual(contracts.header({"amount_tolerance": "1000"})["amount_tolerance"],
			Decimal(1000))
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.header(["gl_account_code"])

	def test_row_batch_requires_both_sides_and_is_bounded(self):
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			contracts.decode_rows("not a list", [])
		self.assertEqual(str(caught.exception), "reconciliation.rows_invalid")
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.decode_rows([], {"report_ref_no": "GL1"})
		too_many = [{"report_ref_no": "GL1"} for _ in range(contracts.MAX_ROWS + 1)]
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			contracts.decode_rows([], too_many)
		self.assertEqual(str(caught.exception), "reconciliation.rows_too_many")
		self.assertEqual(contracts.decode_rows([], []), ([], []))

	def test_title_validation(self):
		self.assertEqual(contracts.title_text(" August 2569 "), "August 2569")
		with self.assertRaises(contracts.ReconciliationContractError):
			contracts.title_text("")


class DigestContract(unittest.TestCase):
	def setUp(self):
		self.header = contracts.header({"amount_tolerance": "0.01", "gl_account_code": "4110-01"})
		self.gl = contracts.decode_rows([{"doc_no": "GL1", "credit": "1070"}], [])[0]
		self.vat = contracts.decode_rows([], [{"report_ref_no": "GL1",
			"report_amount_pre_vat": "1070"}])[1]

	def digest(self, header=None, gl=None, vat=None):
		return contracts.digest(header or self.header,
			self.gl if gl is None else gl, self.vat if vat is None else vat)

	def test_same_input_same_digest_and_any_change_is_detected(self):
		baseline = self.digest()
		self.assertEqual(baseline, self.digest())
		changed_header = contracts.header({"amount_tolerance": "0.5"})
		changed_gl = contracts.decode_rows([{"doc_no": "GL1", "credit": "1071"}], [])[0]
		changed_vat = contracts.decode_rows([], [{"report_ref_no": "GL1",
			"report_amount_pre_vat": "1071"}])[1]
		self.assertNotEqual(baseline, self.digest(header=changed_header))
		self.assertNotEqual(baseline, self.digest(gl=changed_gl))
		self.assertNotEqual(baseline, self.digest(vat=changed_vat))
		self.assertNotEqual(baseline, self.digest(gl=[], vat=[]))


class MatchingConversionContract(unittest.TestCase):
	def test_ledger_rows_convert_to_the_deployed_row_shape(self):
		rows = contracts.decode_rows([{"doc_no": "GL 1", "date": "01/06/2569",
			"account_code": "4110-01", "description": "sale", "credit": "1070.00"}], [])[0]
		item = contracts.matching_gl_rows(rows)[0]
		self.assertEqual((item.doc_no, item.norm_doc_no, item.date, item.account_code,
			item.description, item.debit, item.credit),
			("GL 1", "gl1", "01/06/2569", "4110-01", "sale", 0.0, 1070.0))
		self.assertIsInstance(item.credit, float)

	def test_vat_rows_convert_to_the_keys_the_core_reads(self):
		rows = contracts.decode_rows([], [{"report_ref_no": "GL 1", "report_date": "01/06/2569",
			"report_buyer_name": "ACME", "report_amount_pre_vat": "-107.00"}])[1]
		item = contracts.matching_vat_rows(rows)[0]
		self.assertEqual((item["report_ref_no"], item["report_amount_pre_vat"]),
			("GL 1", -107.0))
		self.assertIsInstance(item["report_amount_pre_vat"], float)


if __name__ == "__main__":
	unittest.main()
