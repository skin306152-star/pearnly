"""Offline regressions for the migrated bank-statement workbook reader.

Runs in the lab bench (openpyxl and the reconciliation package need the bench
environment). Every fixture is built in memory; nothing is read from disk and no
network or model call happens.
"""

import io
import unittest

import openpyxl

from pearnly_cowork.reconciliation import bank_parse, bank_sheets, table_io


def workbook(sheets):
	book = openpyxl.Workbook()
	book.remove(book.active)
	for name, rows in sheets:
		sheet = book.create_sheet(title=name)
		for row in rows:
			sheet.append(list(row))
	stream = io.BytesIO()
	book.save(stream)
	return stream.getvalue()


HEADER = ["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"]
HEADER_EN = ["Date", "Description", "Withdrawal", "Deposit", "Balance"]


class HeaderHit(unittest.TestCase):
	def test_a_short_ascii_hint_must_be_a_whole_word(self):
		# The KTB case: "Init Br." is a branch column, and "in" is inside it.
		self.assertFalse(table_io.header_hit("Init Br.", {"in", "out", "cr", "dr"}))
		self.assertTrue(table_io.header_hit("Money In", {"in"}))
		self.assertTrue(table_io.header_hit("เงินเข้า", {"เงินเข้า"}))

	def test_a_longer_hint_matches_as_a_substring(self):
		self.assertTrue(table_io.header_hit("Running Balance", {"balance"}))
		self.assertTrue(table_io.header_hit("ยอดคงเหลือยกมา", {"ยอดคงเหลือ"}))

	def test_an_empty_header_never_matches(self):
		self.assertFalse(table_io.header_hit("", {"in"}))
		self.assertFalse(table_io.header_hit(None, {"in"}))


class ColumnMapping(unittest.TestCase):
	def test_the_four_languages_are_recognised(self):
		self.assertEqual(bank_sheets.map_statement_columns(HEADER),
			{"date": 0, "description": 1, "withdrawal": 2, "deposit": 3, "balance": 4})
		self.assertEqual(bank_sheets.map_statement_columns(HEADER_EN),
			{"date": 0, "description": 1, "withdrawal": 2, "deposit": 3, "balance": 4})
		self.assertEqual(bank_sheets.map_statement_columns(
			["日期", "摘要", "取款", "存款", "余额"])["balance"], 4)

	def test_a_single_signed_amount_column_is_recognised(self):
		col_map = bank_sheets.map_statement_columns(["Date", "Details", "Amount", "Balance"])
		self.assertEqual(col_map, {"date": 0, "description": 1, "amount": 2, "balance": 3})

	def test_the_first_hit_wins_for_each_role(self):
		col_map = bank_sheets.map_statement_columns(["Date", "Trans Date", "Balance", "Balance"])
		self.assertEqual((col_map["date"], col_map["balance"]), (0, 2))

	def test_a_header_is_found_below_the_first_rows(self):
		rows = [["Account No.", "8258-1"], ["", ""]] + [HEADER]
		self.assertEqual(bank_sheets.find_statement_header(rows), (2, {"date": 0,
			"description": 1, "withdrawal": 2, "deposit": 3, "balance": 4}))

	def test_a_header_needs_a_date_a_balance_and_a_money_column(self):
		self.assertEqual(bank_sheets.find_statement_header([HEADER])[0], 0)
		self.assertEqual(bank_sheets.find_statement_header([["Date", "Balance"]])[0], -1)
		self.assertEqual(bank_sheets.find_statement_header([["Balance", "Deposit"]])[0], -1)

	def test_only_the_first_sixteen_rows_are_scanned(self):
		rows = [["nothing"]] * 17 + [HEADER]
		self.assertEqual(bank_sheets.find_statement_header(rows)[0], -1)


class AccountAndOpening(unittest.TestCase):
	def test_the_account_number_comes_from_the_label_row_above_the_header(self):
		rows = [["Account No.", "984-2-99825-8"], HEADER]
		self.assertEqual(bank_sheets.extract_sheet_account(rows, 1), "984-2-99825-8")

	def test_the_sheet_name_is_the_fallback_when_it_looks_like_an_account(self):
		self.assertEqual(bank_sheets.extract_sheet_account([HEADER], 0, "984-2-99825-8"),
			"984-2-99825-8")
		self.assertEqual(bank_sheets.extract_sheet_account([HEADER], 0, "Statement"), "")
		# 只有标签旁的算印着的账号；sheet 名退回不算（按月分表时是 Jan 2026 这类）。
		self.assertEqual(bank_sheets.labelled_sheet_account([["Account No.", "984-2-99825-8"], HEADER], 1),
			"984-2-99825-8")
		self.assertEqual(bank_sheets.labelled_sheet_account([HEADER], 0), "")

	def test_a_labelled_opening_above_the_header_is_read(self):
		rows = [["ยกมา", "", "-7,409,714.58"], HEADER]
		self.assertEqual(bank_sheets.scan_preheader_opening(rows, 1), -7409714.58)

	def test_a_zero_opening_is_read_as_zero_not_as_missing(self):
		rows = [["ยกมา", "0.00"], HEADER]
		self.assertEqual(bank_sheets.scan_preheader_opening(rows, 1), 0.0)

	def test_no_labelled_opening_means_none(self):
		self.assertIsNone(bank_sheets.scan_preheader_opening([["hello", "world"], HEADER], 1))


class SheetParsing(unittest.TestCase):
	def test_rows_carry_the_account_and_the_printed_date(self):
		rows = [HEADER, ["01/06/2569", "โอนเงิน", "100.00", "", "900.00"]]
		parsed, opening, closing = bank_sheets.parse_statement_sheet(rows, 0,
			bank_sheets.map_statement_columns(HEADER), "stm.xlsx", "8258")
		self.assertEqual(len(parsed), 1)
		self.assertEqual((parsed[0].withdrawal, parsed[0].deposit), (100.0, 0.0))
		self.assertEqual(parsed[0].account_no, "8258")
		self.assertEqual(str(parsed[0].date), "2026-06-01")
		self.assertIsNone(opening)
		self.assertEqual(closing, 900.0)

	def test_a_row_without_a_date_inherits_the_previous_one(self):
		rows = [HEADER, ["01/06/2569", "โอนเงิน", "100.00", "", "900.00"],
			["", "ค่าอาหาร", "50.00", "", "850.00"]]
		parsed, _opening, _closing = bank_sheets.parse_statement_sheet(rows, 0,
			bank_sheets.map_statement_columns(HEADER), "stm.xlsx")
		self.assertEqual(str(parsed[1].date), "2026-06-01")

	def test_the_opening_row_is_read_and_kept_out_of_the_rows(self):
		rows = [HEADER, ["", "ยอดยกมา", "", "", "-39.15"],
			["02/06/2569", "โอนเงิน", "100.00", "", "60.85"]]
		parsed, opening, _closing = bank_sheets.parse_statement_sheet(rows, 0,
			bank_sheets.map_statement_columns(HEADER), "stm.xlsx")
		self.assertEqual(opening, -39.15)
		self.assertEqual(len(parsed), 1)

	def test_a_signed_amount_column_splits_into_deposit_and_withdrawal(self):
		header = ["Date", "Details", "Amount", "Balance"]
		rows = [header, ["01/06/2569", "โอนเงิน", "100.00", "100.00"],
			["02/06/2569", "ค่าอาหาร", "-40.00", "60.00"]]
		parsed, _opening, _closing = bank_sheets.parse_statement_sheet(rows, 0,
			bank_sheets.map_statement_columns(header), "stm.xlsx")
		self.assertEqual((parsed[0].deposit, parsed[0].withdrawal), (100.0, 0.0))
		self.assertEqual((parsed[1].deposit, parsed[1].withdrawal), (0.0, 40.0))

	def test_a_summary_row_is_skipped_before_it_can_touch_the_closing(self):
		rows = [HEADER, ["01/06/2569", "โอนเงิน", "100.00", "", "900.00"],
			["02/06/2569", "รวมรายการ", "", "9999.00", "10899.00"]]
		parsed, _opening, closing = bank_sheets.parse_statement_sheet(rows, 0,
			bank_sheets.map_statement_columns(HEADER), "stm.xlsx")
		self.assertEqual(len(parsed), 1)
		self.assertEqual(closing, 900.0)

	def test_a_row_without_movement_is_skipped(self):
		rows = [HEADER, ["01/06/2569", "โอนเงิน", "", "", "900.00"]]
		parsed, _opening, _closing = bank_sheets.parse_statement_sheet(rows, 0,
			bank_sheets.map_statement_columns(HEADER), "stm.xlsx")
		self.assertEqual(parsed, [])

	def test_a_real_zero_closing_is_not_replaced_by_the_previous_row(self):
		# A sweep account legitimately ends at 0; only an EMPTY cell falls back.
		zero = [HEADER, ["01/06/2569", "โอนเงิน", "", "100.00", "100.00"],
			["02/06/2569", "Sweep", "100.00", "", 0.0]]
		empty = [HEADER, ["01/06/2569", "โอนเงิน", "", "100.00", "100.00"],
			["02/06/2569", "Sweep", "100.00", "", None]]
		self.assertEqual(bank_sheets.parse_statement_sheet(zero, 0,
			bank_sheets.map_statement_columns(HEADER), "stm.xlsx")[2], 0.0)
		self.assertEqual(bank_sheets.parse_statement_sheet(empty, 0,
			bank_sheets.map_statement_columns(HEADER), "stm.xlsx")[2], 100.0)

	def test_a_missing_balance_column_does_not_crash(self):
		header = ["Date", "Details", "Withdrawal", "Deposit"]
		rows = [header, ["01/06/2569", "โอนเงิน", "100.00", ""]]
		parsed, _opening, _closing = bank_sheets.parse_statement_sheet(rows, 0,
			bank_sheets.map_statement_columns(header), "stm.xlsx")
		self.assertEqual(parsed[0].balance, 0.0)


class WholeWorkbook(unittest.TestCase):
	def test_a_single_sheet_becomes_one_account(self):
		content = workbook([("8258", [["Account No.", "8258"], HEADER,
			["01/06/2569", "โอนเงิน", "100.00", "", "900.00"],
			["02/06/2569", "ค่าอาหาร", "", "50.00", "950.00"]])])
		result = bank_sheets.read_statement_workbook(content, "stm.xlsx")
		self.assertTrue(result["ok"])
		self.assertEqual(result["row_count"], 2)
		self.assertEqual(result["account_codes"], ["8258"])
		self.assertEqual(result["labelled_accounts"], ["8258"])
		self.assertFalse(result["multi_account"])
		self.assertEqual(result["parser_version"], bank_sheets.PARSER_VERSION)
		self.assertEqual(result["bank_code"], "generic")
		self.assertFalse(result["needs_review"])

	def test_each_account_uses_its_own_opening(self):
		# The KTB case: two accounts whose openings are -39 and -7,409,714.58.
		# Mixing them into one chain would invalidate both.
		content = workbook([
			("8258", [["Account No.", "8258"], HEADER,
				["01/06/2569", "ยกมา", "", "", "-39.15"],
				["02/06/2569", "โอนเงิน", "100.00", "", "60.85"]]),
			("8606", [["Account No.", "8606"], HEADER,
				["01/06/2569", "ยกมา", "", "", "-7409714.58"],
				["02/06/2569", "โอนเงิน", "", "500.00", "-7409214.58"]]),
		])
		result = bank_sheets.read_statement_workbook(content, "ktb.xlsx")
		self.assertTrue(result["multi_account"])
		self.assertEqual([account["opening"] for account in result["accounts"]], [-39.15, -7409714.58])
		self.assertEqual(result["opening"], -39.15 + -7409714.58)
		self.assertTrue(all(account["opening_known"] for account in result["accounts"]))
		self.assertEqual(result["completeness"]["ok"], True, result["completeness"])

	def test_the_opening_is_derived_when_the_bank_cleared_the_summary_block(self):
		content = workbook([("s", [HEADER, ["01/06/2569", "โอนเงิน", "100.00", "", "860.85"]])])
		result = bank_sheets.read_statement_workbook(content, "stm.xlsx")
		self.assertTrue(result["accounts"][0]["opening_known"])
		self.assertAlmostEqual(result["accounts"][0]["opening"], 960.85, places=2)

	def test_a_broken_balance_chain_surfaces_in_the_completeness_issues(self):
		content = workbook([("s", [HEADER, ["01/06/2569", "โอนเงิน", "", "100.00", "100.00"],
			["02/06/2569", "โอนเงิน", "", "50.00", "9999.00"]])])
		result = bank_sheets.read_statement_workbook(content, "stm.xlsx")
		types = [issue["type"] for issue in result["completeness"]["issues"]]
		self.assertIn("balance_break", types)
		self.assertFalse(result["completeness"]["ok"])

	def test_a_closing_that_does_not_add_up_is_reported(self):
		content = workbook([("s", [["Account No.", "8258"], HEADER,
			["01/06/2569", "ยกมา", "", "", "1000.00"],
			["02/06/2569", "โอนเงิน", "100.00", "", "900.00"]])])
		result = bank_sheets.read_statement_workbook(content, "stm.xlsx")
		issues = result["completeness"]["issues"]
		self.assertEqual([issue["type"] for issue in issues].count("closing_mismatch"), 0,
			"a consistent statement must not be reported")
		broken = workbook([("s", [["Account No.", "8258"], HEADER,
			["01/06/2569", "ยกมา", "", "", "1000.00"],
			["02/06/2569", "โอนเงิน", "100.00", "", "500.00"]])])
		self.assertIn("closing_mismatch",
			[issue["type"] for issue in bank_sheets.read_statement_workbook(broken,
				"stm.xlsx")["completeness"]["issues"]])

	def test_a_sheet_without_a_statement_table_is_skipped(self):
		content = workbook([("cover", [["hello", "world"]]), ("s", [HEADER,
			["01/06/2569", "โอนเงิน", "", "100.00", "100.00"]])])
		result = bank_sheets.read_statement_workbook(content, "stm.xlsx")
		self.assertTrue(result["ok"])
		self.assertEqual(result["sheets_parsed"], 1)

	def test_a_file_with_no_statement_table_is_refused(self):
		content = workbook([("cover", [["hello", "world"]])])
		self.assertEqual(bank_sheets.read_statement_workbook(content, "stm.xlsx"),
			{"ok": False, "error_code": "stmt_headers_not_found",
			 "error": "No bank-statement table found in any sheet"})

	def test_an_unreadable_file_is_refused(self):
		self.assertEqual(bank_sheets.read_statement_workbook(b"not a workbook", "stm.xlsx")
			["error_code"], "file_unreadable")

	def test_csv_bytes_are_read_with_their_encoding_and_delimiter(self):
		content = ("วันที่;รายการ;ถอนเงิน;ฝากเงิน;ยอดคงเหลือ\n"
			"01/06/2569;โอนเงิน;100.00;;900.00\n").encode("cp874")
		result = bank_sheets.read_statement_workbook(content, "stm.csv")
		self.assertTrue(result["ok"])
		self.assertEqual(result["row_count"], 1)
		self.assertEqual(result["rows"][0].withdrawal, 100.0)

	def test_a_csv_extension_is_not_needed_for_the_fallback(self):
		# A workbook that openpyxl cannot read falls back to CSV only for csv/tsv/txt.
		self.assertEqual(bank_sheets.read_statement_workbook(b"a;b;c", "stm.pdf")["error_code"],
			"file_unreadable")


class BankCodeFromFilename(unittest.TestCase):
	def test_a_named_file_gives_the_bank(self):
		self.assertEqual(bank_parse.bank_code_from_filename("STM TTB.pdf"), "ttb")
		self.assertEqual(bank_parse.bank_code_from_filename("kbank-june.xlsx"), "kbank")
		self.assertEqual(bank_parse.bank_code_from_filename("SCB 06-2026.csv"), "scb")

	def test_an_incidental_substring_does_not_decide_the_bank(self):
		# Word boundaries: "kb" inside another word must not be read as KBank.
		self.assertEqual(bank_parse.bank_code_from_filename("statement-kb.xlsx"), "")
		self.assertEqual(bank_parse.bank_code_from_filename("mykbankish.xlsx"), "")

	def test_thai_brand_names_are_ignored_in_filenames(self):
		self.assertEqual(bank_parse.bank_code_from_filename("ธนาคารกรุงไทย.xlsx"), "")

	def test_no_match_returns_empty(self):
		self.assertEqual(bank_parse.bank_code_from_filename("statement.xlsx"), "")
		self.assertEqual(bank_parse.bank_code_from_filename(""), "")


if __name__ == "__main__":
	unittest.main()
