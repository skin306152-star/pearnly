"""Offline regressions for the migrated general-ledger workbook reader.

Runs in the lab bench (openpyxl and the reconciliation package need the bench
environment). Every fixture is built in memory; nothing is read from disk and no
network or model call happens.
"""

import io
import unittest

import openpyxl

from pearnly_cowork.reconciliation import gl_sheets
from pearnly_cowork.reconciliation.bank_matching import GlRow

DATE_H = ["วันที่", "เลขที่เอกสาร", "คำอธิบาย", "เดบิต", "เครดิต", "คงเหลือ", "รหัสบัญชี"]
EN_H = ["Date", "Doc", "Description", "Debit", "Credit", "Balance", "Account"]


def workbook(rows, sheet_title=None):
	book = openpyxl.Workbook()
	sheet = book.active
	if sheet_title:
		sheet.title = sheet_title
	for row in rows:
		sheet.append(list(row))
	stream = io.BytesIO()
	book.save(stream)
	return stream.getvalue()


def gl_row(**overrides):
	"""A GL row with the fields the running-balance helper touches."""
	fields = {"date": None, "doc_no": "", "account_code": "", "description": "",
		"debit": 0.0, "credit": 0.0}
	fields.update(overrides)
	return GlRow(**fields)


class ColumnMapping(unittest.TestCase):
	def test_the_four_languages_are_recognised(self):
		self.assertEqual(gl_sheets.map_gl_columns(DATE_H),
			{"date": 0, "doc_no": 1, "description": 2, "debit": 3, "credit": 4,
				"balance": 5, "account": 6})
		self.assertEqual(gl_sheets.map_gl_columns(EN_H)["doc_no"], 1)
		self.assertEqual(gl_sheets.map_gl_columns(
			["日期", "凭证", "摘要", "借方", "贷方", "余额"])["credit"], 4)

	def test_the_first_hit_wins_for_each_role(self):
		col_map = gl_sheets.map_gl_columns(["Date", "Trans Date", "Debit", "Dr"])
		self.assertEqual((col_map["date"], col_map["debit"]), (0, 2))

	def test_a_header_needs_a_date_and_a_money_column(self):
		# A single net-amount ledger has no debit/credit column in the fixed
		# dictionary, so it is not a header here — the deployed code handed those
		# files to the template-learning layer (see the module docstring).
		self.assertNotIn("amount", gl_sheets.map_gl_columns(
			["Date", "Description", "Amount", "Balance"]))
		self.assertEqual(gl_sheets.find_gl_header(
			[["Date", "Description", "Amount", "Balance"]])[0], -1)
		self.assertEqual(gl_sheets.find_gl_header([["Balance", "Deposit"]])[0], -1)
		self.assertEqual(gl_sheets.find_gl_header([DATE_H])[0], 0)

	def test_a_header_is_found_below_the_first_rows(self):
		rows = [["K BANK"], ["เลขที่บัญชี", "123-4"], DATE_H]
		self.assertEqual(gl_sheets.find_gl_header(rows)[0], 2)

	def test_only_the_first_ten_rows_are_scanned(self):
		rows = [["nothing"]] * 11 + [DATE_H]
		self.assertEqual(gl_sheets.find_gl_header(rows)[0], -1)


class AccountCodeExtraction(unittest.TestCase):
	def test_a_bare_account_number_is_read(self):
		self.assertEqual(gl_sheets.extract_account_code("5301"), "5301")

	def test_a_tiered_account_number_keeps_its_suffix(self):
		self.assertEqual(gl_sheets.extract_account_code("5301-01"), "5301-01")
		self.assertEqual(gl_sheets.extract_account_code("5301–02"), "5301–02")

	def test_the_number_is_taken_out_of_a_voucher_or_a_description(self):
		self.assertEqual(gl_sheets.extract_account_code("PV-5301/2569"), "5301")
		self.assertEqual(gl_sheets.extract_account_code("ค่าอาหาร 5301"), "5301")

	def test_a_longer_number_is_not_chopped_into_an_account(self):
		self.assertEqual(gl_sheets.extract_account_code("12345678"), "")
		self.assertEqual(gl_sheets.extract_account_code("1.5301"), "")
		self.assertEqual(gl_sheets.extract_account_code(""), "")


class RunningBalance(unittest.TestCase):
	def test_a_debit_lifts_the_balance_and_a_credit_lowers_it(self):
		rows = [
			gl_row(debit=100.0, credit=0.0),
			gl_row(debit=0.0, credit=50.0),
		]
		gl_sheets.attach_running_balance(rows, 1000.0)
		self.assertEqual([row.balance for row in rows], [1100.0, 1050.0])


class WholeWorkbook(unittest.TestCase):
	def test_the_opening_comes_from_the_balance_column(self):
		result = gl_sheets.parse_gl_workbook(workbook([
			DATE_H,
			["01/06/2569", "ยอดยกมา", "", "", "", 39749.85, ""],
			["02/06/2569", "PV-0001", "ค่าอาหาร 5301", 100.0, "", 39849.85, "5301"],
			["03/06/2569", "", "โอนเงิน", "", 50.0, 39799.85, "5301"],
		]), "gl.xlsx")
		self.assertTrue(result["ok"])
		self.assertEqual(result["opening"], 39749.85)
		self.assertEqual(result["closing"], 39799.85)
		self.assertTrue(result["closing_printed"])
		self.assertEqual(result["accounts"], ["5301"])
		self.assertEqual(result["row_count"], 2)
		self.assertEqual([row.balance for row in result["rows"]], [39849.85, 39799.85])

	def test_the_opening_falls_back_to_credit_minus_debit(self):
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Debit", "Credit"],
			["01/06/2569", "ยอดยกมา", "", 500.0],
			["02/06/2569", "ค่าอาหาร", 100.0, ""],
		]), "gl.xlsx")
		self.assertEqual(result["opening"], 500.0)
		# No balance column: the closing is the old formula.
		self.assertEqual(result["closing"], 400.0)
		self.assertFalse(result["closing_printed"])

	def test_a_blank_date_inherits_the_previous_one(self):
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Debit", "Credit"],
			["01/06/2569", "วันแรก", 100.0, ""],
			["", "วันเดียวกัน", 200.0, ""],
		]), "gl.xlsx")
		self.assertEqual([row.date.isoformat() for row in result["rows"]],
			["2026-06-01", "2026-06-01"])

	def test_a_row_before_any_date_is_dropped(self):
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Debit", "Credit"],
			["", "ไม่มีวันที่", 100.0, ""],
			["01/06/2569", "มีวันที่", 200.0, ""],
		]), "gl.xlsx")
		self.assertEqual([row.description for row in result["rows"]], ["มีวันที่"])

	def test_carried_forward_and_subtotal_rows_are_skipped(self):
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Debit", "Credit"],
			["01/06/2569", "ค่าอาหาร", 100.0, ""],
			["02/06/2569", "ยอดยกไป", "", "", ],
			["03/06/2569", "รวมทั้งสิ้น", 100.0, ""],
			["04/06/2569", "Subtotal", 100.0, ""],
		]), "gl.xlsx")
		self.assertEqual([row.description for row in result["rows"]], ["ค่าอาหาร"])

	def test_a_row_with_no_movement_is_not_a_transaction(self):
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Debit", "Credit"],
			["01/06/2569", "ศูนย์", "", ""],
			["02/06/2569", "ค่าอาหาร", 100.0, ""],
		]), "gl.xlsx")
		self.assertEqual(result["row_count"], 1)

	def test_the_account_code_filter_keeps_one_account(self):
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Debit", "Credit", "Account"],
			["01/06/2569", "ค่าเช่า", 100.0, "", "5301-01"],
			["02/06/2569", "ค่าอาหาร", 200.0, "", "6201-01"],
		]), "gl.xlsx", account_code="5301")
		self.assertEqual([row.account_code for row in result["rows"]], ["5301-01"])

	def test_an_account_code_is_taken_from_the_description_when_the_column_is_empty(self):
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Debit", "Credit"],
			["01/06/2569", "ค่าอาหาร 5301", 100.0, ""],
		]), "gl.xlsx")
		self.assertEqual(result["accounts"], ["5301"])
		self.assertEqual(result["rows"][0].account_code, "5301")

	def test_a_file_without_a_usable_header_is_refused(self):
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Amount", "Balance"],
			["01/06/2569", "ค่าอาหาร", 100.0, 100.0],
		]), "gl.xlsx")
		self.assertFalse(result["ok"])
		self.assertEqual(result["error_code"], "gl_headers_not_found")

	def test_bytes_that_are_not_a_workbook_fall_through_to_the_csv_reader(self):
		# The deployed loader ends with a CSV attempt whose decoder accepts any
		# byte, so arbitrary bytes become one text row and the file is refused for
		# having no GL header rather than for being unreadable.
		result = gl_sheets.parse_gl_workbook(b"not a workbook at all", "gl.xlsx")
		self.assertFalse(result["ok"])
		self.assertEqual(result["error_code"], "gl_headers_not_found")

	def test_an_empty_file_says_it_cannot_be_read(self):
		result = gl_sheets.parse_gl_workbook(b"", "gl.xlsx")
		self.assertFalse(result["ok"])
		self.assertTrue(result["error"].startswith("Cannot read Excel/CSV: "))

	def test_csv_bytes_are_read_with_their_extension(self):
		content = "วันที่,คำอธิบาย,เดบิต,เครดิต\n01/06/2569,ค่าอาหาร,100,\n".encode("utf-8")
		result = gl_sheets.parse_gl_workbook(content, "gl.csv")
		self.assertTrue(result["ok"])
		self.assertEqual(result["row_count"], 1)
		self.assertEqual(result["rows"][0].debit, 100.0)

	def test_csv_bytes_are_read_without_the_extension_too(self):
		content = "Date,Description,Debit,Credit\n01/06/2569,ค่าอาหาร,100,\n".encode("utf-8")
		result = gl_sheets.parse_gl_workbook(content, "gl.xlsx")
		self.assertTrue(result["ok"])
		self.assertEqual(result["row_count"], 1)


if __name__ == "__main__":
	unittest.main()
