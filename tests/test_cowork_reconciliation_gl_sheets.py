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

	def test_the_opening_falls_back_to_debit_minus_credit(self):
		# 2026-09-29 有意改正：没有余额列时期初 = 借 − 贷、期末 = 期初 + 借 − 贷（银行科目借增贷减，与逐行余额同向）。
		# 旧线上是「贷 − 借」，期初借方 1,000 读成 −1,000，与逐行余额差一倍。
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Debit", "Credit"],
			["01/06/2569", "ยอดยกมา", 1000.0, ""],
			["02/06/2569", "ค่าอาหาร", "", 100.0],
			["03/06/2569", "รับเงิน", 500.0, ""],
		]), "gl.xlsx")
		self.assertEqual(result["opening"], 1000.0)
		self.assertEqual(result["closing"], 1400.0)
		self.assertEqual([row.balance for row in result["rows"]], [900.0, 1400.0])
		self.assertFalse(result["closing_printed"])

	def test_a_month_without_movement_reads_as_the_printed_opening(self):
		# 2026-09-29 有意改变：Express 的储蓄 / 定期户多数月份 0 笔，只印期初和合计。旧线上整单判失败（no_rows）；
		# 现在只要印了期初就照样对账，银行那边每一笔都显示为总账未记。
		from pearnly_cowork.reconciliation import bank_intake

		quiet = workbook([DATE_H, ["01/02/2569", "ยอดยกมา", "", "", "", 1000.0, ""], ["", "รวม", 0, 0, "", "", ""]])
		result = bank_intake.read_ledger_file(quiet, "gl.xlsx", model_rows=lambda *_a: self.fail("no model for a workbook"))
		self.assertTrue(result["ok"])
		self.assertEqual((result["row_count"], result["opening"], result["closing"]), (0, 1000.0, 1000.0))
		# 连期初都没有的空表不是“本月无发生额”，照旧读不出。
		empty = bank_intake.read_ledger_file(workbook([DATE_H]), "gl.xlsx", model_rows=lambda *_a: None)
		self.assertFalse(empty["ok"])

	def test_the_opening_printed_on_the_account_line(self):
		# Express 版式：科目标题行（代码 + 名称）余额列就是期初，没有「ยอดยกมา」字样。有发生额时与印出的期末核对。
		header = ["วันที่", "สมุด", "ใบสำคัญ", "คำอธิบาย", "เดบิท", "เครดิต", "สถานะ", "ยอดคงเหลือ"]
		result = gl_sheets.parse_gl_workbook(workbook([header,
			["11-01-03-06 SAVING-K Bank", "", "", "", "", "", "", 1000.0],
			["05/02/2569", "RV", "RV001", "รับเงิน", 500.0, "", "", 1500.0],
			["06/02/2569", "PV", "PV001", "จ่าย", "", 200.0, "", 1300.0],
			["", "", "", "รวม", 500.0, 200.0, "", 1300.0]]), "gl.xlsx")
		self.assertEqual(result["row_count"], 2)  # the printed total line is not a transaction
		self.assertEqual((result["opening"], result["closing"], result["opening_found"]), (1000.0, 1300.0, True))
		self.assertEqual([row.balance for row in result["rows"]], [1500.0, 1300.0])
		quiet = gl_sheets.parse_gl_workbook(workbook([header, ["11-01-03-06 SAVING-K Bank", "", "", "", "", "", "", 1000.0],
			["", "", "", "รวม", 0, 0, "", 1000.0]]), "gl.xlsx")
		self.assertEqual((quiet["row_count"], quiet["opening"], quiet["closing"]), (0, 1000.0, 1000.0))
		# 对不上印出的期末就不采信，照旧期初 0（交给对账的期初差提示）。
		wrong = gl_sheets.parse_gl_workbook(workbook([header, ["11-01-03-06 SAVING-K Bank", "", "", "", "", "", "", 999.0],
			["05/02/2569", "RV", "RV001", "รับเงิน", 500.0, "", "", 1500.0]]), "gl.xlsx")
		self.assertEqual((wrong["opening"], wrong["opening_found"]), (0.0, False))

	def test_a_ledger_of_every_account_is_read_by_section(self):
		# Mr.erp 全科目总账：科目标题行「1111-01  เงินสด」后面的行都归这个科目；单号「690531-001」不是科目（B01）。
		header = ["วันที่", "สมุด", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "ยอดคงเหลือ"]
		book = workbook([header,
			["1111-01  เงินสด", "", "", "", "", "", 50.0],
			["2569-05-02 00:00:00", "รับ", "690531-001", "ขายสด", 10.0, "", 60.0],
			["รวม", "", "", "", 10.0, 0, ""],
			["1112-02  ธนาคารกสิกร", "", "", "", "", "", 1000.0],
			["2569-05-03 00:00:00", "จ่าย", "690531-002", "ค่าไฟ", "", 100.0, 900.0],
			["2569-05-04 00:00:00", "รับ", "690531-003", "รับชำระ", 300.0, "", 1200.0],
			["รวม", "", "", "", 300.0, 100.0, ""]])
		everything = gl_sheets.parse_gl_workbook(book, "gl.xlsx")
		self.assertEqual(everything["accounts"], ["1111-01", "1112-02"])
		self.assertEqual(everything["row_count"], 3)
		bank = gl_sheets.parse_gl_workbook(book, "gl.xlsx", "1112-02")
		self.assertEqual((bank["row_count"], bank["opening"], bank["closing"]), (2, 1000.0, 1200.0))
		self.assertEqual([row.balance for row in bank["rows"]], [900.0, 1200.0])

	def test_the_bank_account_is_chosen_by_its_match_with_the_statement(self):
		from datetime import date

		from pearnly_cowork.reconciliation.bank_recon_service import likely_account
		from pearnly_cowork.reconciliation.bank_statement import StatementRow

		def gl(code, day, debit=0.0, credit=0.0):
			return gl_row(date=date(2026, 5, day), account_code=code, debit=debit, credit=credit)

		cash = {"ok": True, "rows": [gl("1111-01", 2, debit=10.0), gl("1111-01", 3, credit=100.0)]}
		bank = {"ok": True, "rows": [gl("1112-02", 3, credit=100.0), gl("1112-02", 4, debit=300.0)]}
		statement = [StatementRow(date=date(2026, 5, 3), description="", withdrawal=100.0, deposit=0.0, balance=900.0),
			StatementRow(date=date(2026, 5, 5), description="", withdrawal=0.0, deposit=300.0, balance=1200.0)]
		self.assertEqual(likely_account(statement, [cash, bank]), "1112-02")
		self.assertEqual(likely_account(statement, [bank]), "")  # one account: nothing to choose
		self.assertEqual(likely_account(statement[:1], [cash, bank]), "")  # a tie is not a choice

	def test_zero_balances_on_every_row_are_not_printed_balances(self):
		# Mr.erp 导出每行余额都是 0：期末按期初 + 借 − 贷，科目期初照用（B01）。
		header = ["วันที่", "สมุด", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "ยอดคงเหลือ"]
		result = gl_sheets.parse_gl_workbook(workbook([header, ["1113-01  เงินฝาก", "", "", "", "", "", 1000.0],
			["2569-05-03 00:00:00", "จ่าย", "V1", "ค่าไฟ", "", 100.0, 0],
			["2569-05-04 00:00:00", "รับ", "V2", "รับชำระ", 300.0, "", 0]]), "gl.xlsx", "1113-01")
		self.assertEqual((result["opening"], result["closing"], result["closing_printed"]), (1000.0, 1200.0, False))

	def test_a_year_to_date_ledger_is_cut_to_the_statement_months(self):
		from datetime import date

		from pearnly_cowork.reconciliation.bank_recon_service import within_statement_period
		from pearnly_cowork.reconciliation.bank_statement import StatementRow

		rows = [gl_row(date=date(2026, 1, 5), debit=500.0), gl_row(date=date(2026, 5, 3), credit=100.0),
			gl_row(date=date(2026, 5, 31), debit=50.0), gl_row(date=date(2026, 6, 2), debit=9.0)]
		ledger = {"ok": True, "rows": rows, "row_count": 4, "opening": 1000.0, "closing": 1459.0}
		statement = [StatementRow(date=date(2026, 5, 3), description="", withdrawal=100.0, deposit=0.0, balance=0.0),
			StatementRow(date=date(2026, 5, 20), description="", withdrawal=0.0, deposit=1.0, balance=0.0)]
		cut, period = within_statement_period(statement, ledger)
		self.assertEqual(period, ("2026-05-01", "2026-05-31", "1,500.00"))
		self.assertEqual((cut["opening"], cut["closing"], cut["row_count"]), (1500.0, 1450.0, 2))
		self.assertEqual([row.balance for row in cut["rows"]], [1400.0, 1450.0])
		same, none = within_statement_period(statement, {**ledger, "rows": rows[1:3]})
		self.assertIsNone(none)

	def test_a_negative_amount_is_a_reversal_on_the_other_side(self):
		# 2026-09-29：「(500.00)」写在借方是冲销，记成贷方 500（旧线上取绝对值记成借方 500，余额差 1,000）。
		result = gl_sheets.parse_gl_workbook(workbook([
			["Date", "Description", "Debit", "Credit", "Balance"],
			["01/06/2569", "ยอดยกมา", "", "", 1000.0],
			["02/06/2569", "รับเงิน", 500.0, "", 1500.0],
			["03/06/2569", "กลับรายการ", "(500.00)", "", 1000.0],
		]), "gl.xlsx")
		self.assertEqual([(row.debit, row.credit) for row in result["rows"]], [(500.0, 0.0), (0.0, 500.0)])
		self.assertEqual([row.balance for row in result["rows"]], [1500.0, 1000.0])

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
