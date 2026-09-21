"""Offline regressions for the migrated decoded-page conversion of bank parsing.

The file readers, the OCR call and model extraction stay in the legacy project;
these cases cover the decoded input the legacy parser hands over and the row
contract that native storage then validates.
"""

import copy
import unittest
from datetime import date, datetime
from decimal import Decimal

from pearnly_cowork.reconciliation import bank_parse as parsing
from pearnly_cowork.reconciliation import bank_recon_contracts as contracts


def statement_document(entries, bank_name="Kasikornbank", opening=None, closing=None):
	return {"pages": [{"document": {"bank_name": bank_name, "opening_balance": opening,
		"closing_balance": closing, "entries": entries}}]}


def gl_document(*pages):
	return {"pages": [{"document": {"entries": entries}} for entries in pages]}


class AmountReader(unittest.TestCase):
	def test_deployed_amount_reader_handles_separators_and_signs(self):
		for value, expected in (
				(None, 0.0), ("", 0.0), ("—", 0.0), ("-", 0.0), ("  ", 0.0),
				("1,234.50", 1234.5), ("(1,234.50)", -1234.5), ("-75.25", -75.25),
				("115.586.50", 115586.5), ("\u00a012\u00a0", 12.0), ("abc", 0.0),
				(12.345, 12.35), (True, 0.0), ("1e3", 1000.0)):
			with self.subTest(value=value):
				self.assertEqual(parsing.to_float(value), expected)

	def test_rounding_keeps_the_deployed_two_places(self):
		self.assertEqual(parsing.to_float("2.675"), 2.67)
		self.assertEqual(parsing.to_float("0.005"), 0.01)


class DateReader(unittest.TestCase):
	def test_iso_and_calendar_dates(self):
		for value, expected in (
				(None, None), ("", None), ("not a date", None),
				("2026-06-01", date(2026, 6, 1)), ("2568-12-01", date(2025, 12, 1)),
				("2568-12-01 00:00:00", date(2025, 12, 1)),
				("2568-12-01T09:30:00", date(2025, 12, 1)),
				("15/06/2026", date(2026, 6, 15)), ("15-06-2026", date(2026, 6, 15)),
				("15/06/69", date(2026, 6, 15)), ("15.06.2026", date(2026, 6, 15)),
				("31/02/2026", None), ("2026-06", None)):
			with self.subTest(value=value):
				self.assertEqual(parsing.parse_date(value), expected)

	def test_printed_month_names_convert_buddhist_years(self):
		# Two deployed quirks decide this branch: the ISO-datetime fix truncates the
		# string at its first space, and the month keys are lower case, so an
		# upper-case English month never matches.
		for value, expected in (
				("15มกราคม2567", date(2024, 1, 15)),
				("15ม.ค.67", date(2024, 1, 15)),
				("15ม.ค.43", date(2000, 1, 15)),
				("15jan2026", date(2026, 1, 15)),
				("15january2026", date(2026, 1, 15)),
				("31ก.พ.2568", None),
				("15Jan2026", None),
				("15 มกราคม 2567", None),
				("15 ม.ค. 67", None),
				("15 Jan 2026", None)):
			with self.subTest(value=value):
				self.assertEqual(parsing.parse_date(value), expected)

	def test_datetime_and_date_cells_convert_buddhist_years(self):
		self.assertEqual(parsing.parse_date(datetime(2569, 6, 1, 12, 30)), date(2026, 6, 1))
		self.assertEqual(parsing.parse_date(date(2569, 6, 1)), date(2026, 6, 1))
		self.assertEqual(parsing.parse_date(date(2026, 6, 1)), date(2026, 6, 1))


class BankDetection(unittest.TestCase):
	def test_signature_order_and_generic_fallback(self):
		self.assertEqual(parsing.detect_bank("Kasikornbank statement"), "kbank")
		self.assertEqual(parsing.detect_bank("TMBThanachart ttb"), "ttb")
		self.assertEqual(parsing.detect_bank("ธนาคารกรุงเทพ"), "bbl")
		self.assertEqual(parsing.detect_bank("ธนาคารไทยพาณิชย์"), "scb")
		self.assertEqual(parsing.detect_bank("no bank here"), "generic")
		self.assertEqual(parsing.detect_bank(""), "generic")
		self.assertEqual(tuple(parsing.BANK_SIGNATURES),
			("kbank", "bbl", "kkp", "ktb", "scb", "bay", "ttb"))


class StatementEntryConversion(unittest.TestCase):
	def test_rows_keep_the_deployed_fields(self):
		entries = [
			{"deposit": "1,000.00", "withdrawal": "0", "balance": "1,000.00",
				"description": "ยอดยกมา"},
			{"deposit": "0", "withdrawal": "0", "balance": "1,000.00", "description": "no movement"},
			{"deposit": "0", "withdrawal": "400.00", "balance": "600.00", "description": "SWD",
				"transaction_date_raw": "02/06/2569", "transaction_date": "2026-06-03"},
		]
		rows = parsing.statement_rows_from_entries(entries, "STM KBANK.pdf")
		self.assertEqual(len(rows), 2)
		first, second = rows
		self.assertEqual((first.date, first.description, first.withdrawal, first.deposit,
			first.balance, first.source_file), (None, "ยอดยกมา", 0.0, 1000.0, 1000.0,
			"STM KBANK.pdf"))
		self.assertEqual(first.row_hash, first.row_hash[:12])
		self.assertEqual((second.date, second.withdrawal, second.deposit, second.balance),
			(date(2026, 6, 2), 400.0, 0.0, 600.0))

	def test_normalized_date_is_the_fallback_and_a_missing_description_is_empty(self):
		# The fallback splits the normalized date as printed, without a
		# Buddhist-era conversion; only the printed-text reader converts years.
		rows = parsing.statement_rows_from_entries(
			[{"withdrawal": "10", "transaction_date": "2569-06-09"}], "stmt.pdf")
		self.assertEqual(rows[0].date, date(2569, 6, 9))
		self.assertEqual(rows[0].description, "")
		rows = parsing.statement_rows_from_entries(
			[{"withdrawal": "10", "transaction_date": "not a date"}], "stmt.pdf")
		self.assertIsNone(rows[0].date)


class GlEntryConversion(unittest.TestCase):
	def test_every_page_is_read_and_empty_rows_are_skipped(self):
		document = gl_document([
			{"debit": "0", "credit": "0", "description": "blank"},
			{"voucher_no": "GL-1", "account_code": "1000", "description": "payment",
				"credit": "400.00", "transaction_date": "2026-06-01"},
		], [
			{"voucher_no": "GL-2", "account_code": "1100", "description": "fee", "debit": "75.00",
				"transaction_date_raw": "30/06/2569"},
		])
		rows = parsing.gl_rows_from_pages(document)
		self.assertEqual(len(rows), 2)
		self.assertEqual((rows[0].doc_no, rows[0].account_code, rows[0].credit, rows[0].debit,
			rows[0].source_file), ("GL-1", "1000", 400.0, 0.0, ""))
		# The deployed adapter only reads the printed date when the normalized one
		# cannot be split, so a row carrying only printed text keeps no date.
		self.assertEqual((rows[1].date, rows[1].debit), (None, 75.0))

	def test_a_document_without_pages_is_empty(self):
		self.assertEqual(parsing.gl_rows_from_pages({"pages": None}), [])
		self.assertEqual(parsing.gl_rows_from_pages({}), [])


class StatementPipelineAdapter(unittest.TestCase):
	def test_the_balance_chain_runs_in_the_deployed_order(self):
		result = parsing.statement_from_pipeline(statement_document([
			{"withdrawal": "400.00", "deposit": "0", "balance": "1,400.00", "description": "SWD",
				"transaction_date_raw": "01/06/2569"},
			{"deposit": "400.00", "description": "Total Credit"},
		], opening="1,000.00", closing=""), "STM KBANK.pdf")
		self.assertTrue(result["ok"])
		self.assertEqual((result["row_count"], result["opening"], result["closing"],
			result["bank_code"]), (1, 1000.0, 1400.0, "kbank"))
		row = result["rows"][0]
		self.assertEqual((row.deposit, row.withdrawal, row.direction_autocorrected),
			(400.0, 0.0, True))
		self.assertIs(row.balance_ok, True)
		self.assertEqual((result["balance_warn_count"], result["amount_fixed_count"]), (0, 0))
		self.assertFalse(result["needs_review"])

	def test_closing_falls_back_to_the_last_known_balance(self):
		result = parsing.statement_from_pipeline(statement_document(
			[{"withdrawal": "400.00", "balance": "600.00", "description": "SWD"},
				{"withdrawal": "90.00", "balance": "510.00", "description": "fee"}],
			opening="1,000.00"), "stmt.pdf")
		self.assertEqual((result["opening"], result["closing"]), (1000.0, 510.0))
		empty = parsing.statement_from_pipeline(statement_document([], opening="1,000.00"),
			"stmt.pdf")
		self.assertEqual((empty["row_count"], empty["closing"]), (0, 0.0))

	def test_review_signals_follow_the_deployed_rule(self):
		flagged = parsing.statement_from_pipeline(
			{**statement_document([{"withdrawal": "10", "balance": "900.00"}]), "_needs_review": True},
			"stmt.pdf")
		self.assertTrue(flagged["needs_review"])
		# A balance that contradicts the row leaves the row unchecked and warns.
		warned = parsing.statement_from_pipeline(statement_document([
			{"withdrawal": "400.00", "balance": "600.00", "description": "SWD"},
			{"withdrawal": "90.00", "balance": "999.00", "description": "fee"},
		], opening="1,000.00"), "stmt.pdf")
		self.assertEqual(warned["balance_warn_count"], 1)
		self.assertTrue(warned["needs_review"])

	def test_a_dictionary_without_pages_is_reported_not_parsed(self):
		result = parsing.statement_from_pipeline({"pages": []}, "stmt.pdf")
		self.assertEqual((result["ok"], result["rows"], result["error"]),
			(False, [], "no pages parsed"))


class GlPipelineAdapter(unittest.TestCase):
	def test_account_filter_and_account_list(self):
		document = gl_document([
			{"voucher_no": "GL-1", "account_code": "1000", "credit": "400.00"},
			{"voucher_no": "GL-2", "account_code": "1100", "debit": "75.00"},
			{"voucher_no": "GL-3", "account_code": "", "debit": "5.00"},
		])
		all_rows = parsing.gl_from_pipeline(document)
		self.assertEqual((all_rows["row_count"], all_rows["accounts"]), (3, ["1000", "1100"]))
		filtered = parsing.gl_from_pipeline(document, "1100")
		self.assertEqual((filtered["row_count"], filtered["accounts"]),
			(1, ["1100"]))
		self.assertEqual(filtered["rows"][0].doc_no, "GL-2")
		self.assertFalse(filtered["needs_review"])
		self.assertTrue(parsing.gl_from_pipeline(
			{**document, "_needs_review": True})["needs_review"])


class InputIsOnlyRead(unittest.TestCase):
	def test_the_decoded_document_is_never_mutated(self):
		document = statement_document([{"withdrawal": "400.00", "balance": "600.00",
			"description": "SWD"}, {"deposit": "400.00", "description": "Total Credit"}],
			opening="1,000.00", closing="600.00")
		before = copy.deepcopy(document)
		parsing.statement_from_pipeline(document, "stmt.pdf")
		self.assertEqual(document, before)
		gl_pages = {"pages": [{"document": {"entries": [
			{"voucher_no": "GL-1", "account_code": "1000", "credit": "400.00"}]}}]}
		before = copy.deepcopy(gl_pages)
		parsing.gl_from_pipeline(gl_pages, "1000")
		self.assertEqual(gl_pages, before)


class NativeRowContract(unittest.TestCase):
	def test_decoded_statement_rows_are_accepted_and_keep_their_values(self):
		result = parsing.statement_from_pipeline(statement_document([
			{"withdrawal": "400.00", "balance": "600.00", "description": "SWD",
				"transaction_date_raw": "01/06/2569"}] + [
			{"withdrawal": "10.00", "balance": "590.00", "description": f"row {index}",
				"transaction_date_raw": "02/06/2569"} for index in range(3)
		], opening="1,000.00", closing="590.00"), "stmt.pdf")
		rows = [contracts.statement_row(row)
			for row in parsing.decoded_statement_rows(result["rows"])]
		self.assertEqual(len(rows), 4)
		self.assertEqual(rows[0]["date"], date(2026, 6, 1))
		self.assertEqual(rows[0]["withdrawal"], Decimal("400"))
		self.assertEqual(rows[0]["balance"], Decimal("600"))
		self.assertEqual((rows[0]["confidence"], rows[0]["balance_ok"], rows[0]["autocorrected"]),
			("high", "ok", False))

	def test_decoded_gl_rows_are_accepted_and_keep_their_values(self):
		result = parsing.gl_from_pipeline(gl_document([
			{"voucher_no": "GL-1", "account_code": "1000", "description": "payment",
				"credit": "400.00", "transaction_date": "2026-06-01"}]))
		rows = [contracts.gl_row(row) for row in parsing.decoded_gl_rows(result["rows"])]
		self.assertEqual(rows[0]["date"], date(2026, 6, 1))
		self.assertEqual((rows[0]["credit"], rows[0]["debit"], rows[0]["balance"]),
			(Decimal("400"), Decimal(0), Decimal(0)))

	def test_a_row_the_native_contract_refuses_is_an_error_not_a_silent_drop(self):
		descriptions = parsing.statement_from_pipeline(statement_document(
			[{"withdrawal": "10", "balance": "990", "description": "",
				"transaction_date_raw": "01/06/2569"}]), "stmt.pdf")
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			contracts.statement_row(parsing.decoded_statement_rows(descriptions["rows"])[0])
		self.assertEqual(str(caught.exception), "reconciliation.text_required:statement.description")
		negative = parsing.statement_from_pipeline(statement_document(
			[{"withdrawal": "(10)", "deposit": "0", "balance": "990", "description": "fee"}],
			opening="1,000.00"), "stmt.pdf")
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			contracts.statement_row(parsing.decoded_statement_rows(negative["rows"])[0])
		self.assertEqual(str(caught.exception),
			"reconciliation.amount_negative:statement.withdrawal")

	def test_an_unknown_balance_row_is_expressible_on_the_decoded_path_only(self):
		# The parsed path always carries the deployed 0.0 default for an unread
		# balance; the decoded path can mark the same row as unknown.
		parsed = parsing.statement_from_pipeline(statement_document(
			[{"withdrawal": "10", "description": "continuation"}], opening="1,000.00"), "stmt.pdf")
		self.assertEqual(parsing.decoded_statement_rows(parsed["rows"])[0]["balance"], 0.0)
		unknown = contracts.statement_row({"description": "continuation", "deposit": "10"})
		self.assertIsNone(unknown["balance"])


if __name__ == "__main__":
	unittest.main()
