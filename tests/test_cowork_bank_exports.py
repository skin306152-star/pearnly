"""Offline regressions for common bank export layouts (2026-09-30 stress corpus 05).

Each case rebuilds, in memory, the shape of one export family that used to be
misread; no corpus file, network or model call is involved. The legacy reading
of the old sample shapes stays pinned by test_cowork_bank_sheets_source_parity.
"""

import io
import unittest
from datetime import date, datetime

import openpyxl

from pearnly_cowork.reconciliation import bank_intake, bank_sheets
from pearnly_cowork.tabular import readers


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


def read(rows, name="stm.xlsx", sheet="s"):
	return bank_sheets.read_statement_workbook(workbook([(sheet, rows)]), name)


def chain_ok(rows):
	return all(abs(p.balance + r.deposit - r.withdrawal - r.balance) <= 0.01 for p, r in zip(rows, rows[1:]))


def moves(count, start=1000.0, day=1):
	"""``count`` alternating deposits/withdrawals with a running balance, oldest first."""
	balance, out = start, []
	for index in range(count):
		amount = 100.0 + index
		deposit = index % 2 == 0
		balance = round(balance + (amount if deposit else -amount), 2)
		out.append((f"{day + index // 3:02d}/06/2026", amount if deposit else 0.0, 0.0 if deposit else amount, balance))
	return out


class Headers(unittest.TestCase):
	def test_kbank_debit_and_credit_headers_are_recognised(self):
		col_map = bank_sheets.map_statement_columns(
			["วันที่ทำรายการ", "เวลา", "รายการ", "เลขที่เช็ค", "จำนวนเงินหักบัญชี", "จำนวนเงินนำฝากเข้าบัญชี", "ยอดคงเหลือ"])
		self.assertEqual((col_map["withdrawal"], col_map["deposit"], col_map["balance"]), (4, 5, 6))

	def test_krungsri_balance_header_is_recognised(self):
		col_map = bank_sheets.map_statement_columns(["วันที่/เวลา", "ถอน", "ฝาก", "ยอดเงินในบัญชี", "รหัส"])
		self.assertEqual(col_map["balance"], 3)

	def test_an_amount_column_takes_the_side_that_was_not_recognised(self):
		result = read([["Date", "Detail", "Amount", "Credit", "Balance"],
			["01/06/2026", "a", 40.0, "", 60.0], ["02/06/2026", "b", "", 10.0, 70.0]])
		self.assertEqual([(r.withdrawal, r.deposit) for r in result["rows"]], [(40.0, 0.0), (0.0, 10.0)])


class AmountsAndDates(unittest.TestCase):
	def test_a_currency_suffix_is_read_and_the_printed_minus_dropped(self):
		result = read([["Date", "Description", "Debit Amount", "Credit Amount", "Ledger Balance"],
			["01/06/2026", "a", "-50.00 THB", "0.00", "950.00 THB"],
			["02/06/2026", "b", "0.00", "20.00 THB", "970.00 THB"]])
		self.assertEqual([(r.withdrawal, r.deposit, r.balance) for r in result["rows"]],
			[(50.0, 0.0, 950.0), (0.0, 20.0, 970.0)])

	def test_a_debit_column_printed_negative_is_read_as_withdrawals_when_the_balance_confirms(self):
		rows = [["Date", "Description", "Debit Amount", "Credit Amount", "Ledger Balance"]]
		balance = 1000.0
		for index in range(6):
			balance -= 100.0
			rows.append([f"0{index + 1}/06/2026", "x", -100.0, "-", balance])
		result = read(rows)
		self.assertTrue(all(r.withdrawal == 100.0 and r.deposit == 0.0 for r in result["rows"]))
		self.assertTrue(chain_ok(result["rows"]))

	def test_excel_serial_dates_and_a_tax_column(self):
		result = read([["Date", "Description", "Amount", "Tax", "Balance"],
			[46174.0, "interest", 10.0, 0.15, 1009.85], [46175.0, "fee", -5.0, 0, 1004.85]])
		self.assertEqual(result["rows"][0].date, date(2026, 6, 1))
		self.assertEqual([r.description for r in result["rows"]][:2], ["interest", "Tax · interest"])
		self.assertTrue(chain_ok(result["rows"]))

	def test_a_line_break_between_date_and_time_is_read(self):
		result = read([["วันที่/เวลา", "ถอน", "ฝาก", "ยอดเงินในบัญชี", "คำอธิบาย"],
			["", "", "", 500.0, "ยอดเงินคงเหลือยกมา"],
			["25/04/2026\n10:00:00", "", "20.00", "520.00", "a"],
			["26/04/2026\n11:00:00", "5.00", "", "515.00", "b"],
			[],
			["ถอนเงินรวม", "5.00", "", "", "จำนวน 1 รายการ"],
			["ฝากเงินรวม", "", "20.00", "", "จำนวน 1 รายการ"]])
		self.assertEqual(result["row_count"], 2)
		self.assertEqual((result["opening"], result["closing"]), (500.0, 515.0))
		self.assertEqual(result["rows"][0].date, date(2026, 4, 25))

	def test_a_short_buddhist_year_stored_as_a_us_date_is_turned_back(self):
		# "6/1/69" typed as day/month/BE-year and stored by Excel as 1969-06-01.
		result = read([["Date", "Deposit", "Withdrawal", "Balance"],
			["05/01/2026", 10.0, "", 110.0],
			[datetime(1969, 6, 1), 10.0, "", 120.0],
			["15/01/2026", 10.0, "", 130.0]])
		self.assertEqual([r.date for r in result["rows"]], [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 15)])


class Footers(unittest.TestCase):
	def test_rows_after_the_last_dated_movement_are_a_footer(self):
		body = moves(12)
		rows = [["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"]]
		rows += [[day, "x", withdrawal or "", deposit or "", balance] for day, deposit, withdrawal, balance in body]
		rows += [["", "", 999.0, 888.0, ""], ["", "", "", "", 123456.0]]
		result = read(rows)
		self.assertEqual(result["row_count"], 12)
		self.assertEqual(result["closing"], body[-1][3])
		self.assertEqual(result["completeness"]["issues"], [])

	def test_a_total_written_in_another_column_ends_the_table(self):
		result = read([["序号", "交易时间", "起息日期", "摘要", "收入金额", "支出金额", "余额"],
			[1, "2026-03-30 10:00:00", "30-03-2026", "a", "", "100.00", "900.00"],
			[2, "2026-03-30 11:00:00", "30-03-2026", "b", "50.00", "", "950.00"],
			["合计金额", "", "", "", "50.00", "100.00", ""],
			["", "", "", "", "", "per gl", "123456.78"]])
		self.assertEqual(result["row_count"], 2)
		self.assertEqual((result["opening"], result["closing"]), (1000.0, 950.0))

	def test_a_monthly_subtotal_without_date_or_balance_is_skipped(self):
		body = moves(12)
		rows = [["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"]]
		for index, (day, deposit, withdrawal, balance) in enumerate(body):
			rows.append([day, "x", withdrawal or "", deposit or "", balance])
			if index == 5:
				rows.append(["", "", 300.0, 400.0, ""])
		result = read(rows)
		self.assertEqual(result["row_count"], 12)
		self.assertTrue(chain_ok(result["rows"]))

	def test_a_customer_sheet_with_notes_in_the_withdraw_column(self):
		rows = [["Date", "Deposit", "Withdraw", "Amount", "Balance"]]
		balance = 1000.0
		for index in range(6):
			balance -= 10.0
			rows.append([f"0{index + 1}/01/2026", "", f"note {index}", 10.0, balance])
		result = read(rows)
		self.assertTrue(all(r.withdrawal == 10.0 for r in result["rows"]))
		self.assertEqual(result["rows"][0].description, "note 0")


class Order(unittest.TestCase):
	def test_rows_the_bank_filed_out_of_place_are_put_back_on_the_balance_chain(self):
		body = moves(12)
		shuffled = body[:3] + [body[9]] + body[3:9] + body[10:]
		rows = [["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"]]
		rows += [[day, "x", withdrawal or "", deposit or "", balance] for day, deposit, withdrawal, balance in shuffled]
		result = read(rows)
		self.assertTrue(chain_ok(result["rows"]))
		self.assertEqual(result["closing"], body[-1][3])

	def test_same_day_rows_printed_newest_first_are_turned_when_the_times_say_so(self):
		result = read([["Transaction Date and Time", "Description", "Debit Amount", "Credit Amount", "Ledger Balance"],
			["25/06/2026 16:40:12", "TAXES", "-0.47 THB", "0.00", "1003.12 THB"],
			["25/06/2026 16:40:12", "Interest Credit", "0.00", "3.12 THB", "1003.59 THB"]])
		self.assertEqual([r.description for r in result["rows"]], ["Interest Credit", "TAXES"])
		self.assertTrue(chain_ok(result["rows"]))

	def test_same_day_rows_without_printed_times_are_left_alone(self):
		result = read([["Date", "Description", "Withdrawal", "Deposit", "Balance"],
			["25/06/2026", "b", 0.47, "", 1003.12], ["25/06/2026", "a", "", 3.12, 1003.59]])
		self.assertEqual([r.description for r in result["rows"]], ["b", "a"])


class Sections(unittest.TestCase):
	def test_monthly_sheets_of_one_account_are_joined(self):
		first, second = moves(12, 1000.0), moves(12, 0.0, day=1)
		start = first[-1][3]
		second = [(day.replace("/06/", "/07/"), deposit, withdrawal, round(start + balance, 2))
			for day, deposit, withdrawal, balance in second]

		def sheet(body, opening):
			return [["ยกมา", opening], ["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"]] + [
				[day, "x", withdrawal or "", deposit or "", balance] for day, deposit, withdrawal, balance in body]

		result = bank_sheets.read_statement_workbook(
			workbook([("Jun 2569", sheet(first, 1000.0)), ("Jul 2569", sheet(second, start))]), "stm.xlsx")
		self.assertFalse(result["multi_account"])
		self.assertEqual((result["opening"], result["closing"]), (1000.0, second[-1][3]))

	def test_two_real_accounts_stay_apart_and_keep_their_own_chains(self):
		def sheet(number, body):
			return [["Account No.", number], ["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"]] + [
				[day, "x", withdrawal or "", deposit or "", balance] for day, deposit, withdrawal, balance in body]

		content = workbook([("a", sheet("984-2-99825-8", moves(12, 1000.0))),
			("b", sheet("984-2-99860-6", moves(12, 5000.0)))])
		result = bank_intake.read_statement_file(content, "stm.xlsx")
		self.assertTrue(result["multi_account"])
		self.assertEqual(result["balance_warn_count"], 0)

	def test_a_second_table_under_a_repeated_header_is_its_own_section(self):
		header = ["Date", "Description", "Withdrawal", "Deposit", "Balance"]
		rows = [header] + [[day, "x", w or "", d or "", b] for day, d, w, b in moves(12, 1000.0)]
		rows += [[]] * 3 + [header] + [[day.replace("2026", "2025"), "x", w or "", d or "", b]
			for day, d, w, b in moves(12, 70000.0)]
		result = read(rows)
		self.assertEqual(len(result["accounts"]), 2)
		self.assertTrue(all(chain_ok(account["rows"]) for account in result["accounts"]))


class HeaderlessAndCsv(unittest.TestCase):
	def test_a_headerless_inquiry_csv_with_an_amount_in_the_spare_column(self):
		lines, balance = [',,,"1,000.00",B/F,Balance Forward,,'], 1000.0
		for day in range(1, 13):
			if day == 5:
				balance -= 200.0
				lines.append(f'{day:02d}/05/2026 10:00:00,"200.00",,"{balance:,.2f}",TN,Transfer,MOBILE,1')
			else:
				balance += 50.0
				lines.append(f'{day:02d}/05/2026 10:00:00,,"50.00","{balance:,.2f}",TN,Transfer,MOBILE,1')
		result = bank_sheets.read_statement_workbook("\n".join(lines).encode("utf-8"), "inquiry.csv")
		self.assertTrue(result["ok"])
		self.assertEqual((result["opening"], result["closing"]), (1000.0, balance))
		self.assertEqual(result["rows"][4].withdrawal, 200.0)
		self.assertTrue(chain_ok(result["rows"]))

	def test_a_utf16_csv_is_decoded(self):
		text = "Date\tDescription\tWithdrawal\tDeposit\tBalance\n01/06/2026\tx\t\t10.00\t110.00\n"
		sheets = readers.load_csv_sheets(text.encode("utf-16"))
		self.assertEqual(sheets[0][1][1][4], "110.00")


class NotAStatement(unittest.TestCase):
	def test_a_general_ledger_export_is_named_as_such(self):
		result = read([["สมุดรายวัน", "เลขที่เอกสาร", "วันที่", "เดบิต", "เครดิต"],
			["JV", "JV-001", "01/06/2026", 100.0, ""]])
		self.assertEqual(result["error_code"], bank_sheets.LEDGER_NOT_STATEMENT)

	def test_a_statement_that_prints_no_records_has_no_movement(self):
		result = read([["账户：", "123"], ["没有满足条件的记录"]])
		self.assertEqual(result["error_code"], "no_movement")


if __name__ == "__main__":
	unittest.main()
