"""Offline regressions for the migrated income (GL) parsing conversions."""

import unittest
from datetime import date, datetime

from pearnly_cowork.reconciliation import income_parse as parse
from pearnly_cowork.reconciliation import income_recon_contracts as contracts
from pearnly_cowork.reconciliation.recon_contracts import ReconciliationContractError


class LeafHelpers(unittest.TestCase):
	def test_amounts_are_read_the_deployed_way(self):
		cases = [
			("1,070.00", 1070.0), ("(250.00)", -250.0), ("", 0.0), ("-", 0.0), ("–", 0.0),
			("\xa0", 0.0), ("abc", 0.0), (" 12 ", 12.0), ("1.005", 1.0), (None, 0.0), (0, 0.0),
		]
		for value, expected in cases:
			with self.subTest(value=value):
				self.assertEqual(parse.to_float(value), expected)

	def test_account_codes_are_extracted_and_never_split_by_a_decimal(self):
		for value, expected in [
			("4110", "4110"), ("4110-01", "4110-01"), ("41100-001", "41100-001"),
			("411000", "411000"), ("5010-01", "5010-01"), ("419.57", None), ("411", None),
			("12345678", None), ("IV001", None), ("", None),
		]:
			with self.subTest(value=value):
				self.assertEqual(parse.extract_account_code(value), expected)

	def test_the_revenue_side_follows_the_caller_prefix(self):
		self.assertTrue(parse.is_revenue_account("4110-01"))
		self.assertTrue(parse.is_revenue_account(" 4110 ", "41"))
		self.assertFalse(parse.is_revenue_account("5010-01"))
		self.assertFalse(parse.is_revenue_account("4110", "9"))
		self.assertFalse(parse.is_revenue_account(""))

	def test_a_summary_row_is_skipped_unless_it_carries_an_account_code(self):
		self.assertTrue(parse.is_skip_row(["ยอดยกมา", "", "", ""]))
		self.assertTrue(parse.is_skip_row(["", "subtotal", ""]))
		self.assertFalse(parse.is_skip_row(["4110-01 รายได้", "", ""]))
		self.assertFalse(parse.is_skip_row(["รวมทั้งสิ้น", "4110-01", ""]))
		self.assertFalse(parse.is_skip_row(["IV001", "ขายสินค้า", "1,070.00"]))
		self.assertFalse(parse.is_skip_row([]))

	def test_the_header_map_takes_the_first_hit_of_each_field(self):
		self.assertEqual(parse.map_gl_columns(["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต",
			"รหัสบัญชี"]),
			{"date": 0, "doc_no": 1, "description": 2, "debit": 3, "credit": 4, "account": 5})
		self.assertEqual(parse.map_gl_columns(["Date", "Voucher", "Description", "Debit", "Credit",
			"Account Code"]),
			{"date": 0, "doc_no": 1, "description": 2, "debit": 3, "credit": 4, "account": 5})
		# "cr" also sits inside other words, which is the deployed header test.
		self.assertEqual(parse.map_gl_columns(["credit", "credit"]), {"credit": 0})
		self.assertEqual(parse.map_gl_columns(["", "", ""]), {})

	def test_a_row_has_an_amount_only_in_the_columns_already_known(self):
		cells = ["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00"]
		self.assertTrue(parse.row_has_amount(cells, {"debit": 3, "credit": 4}))
		self.assertTrue(parse.row_has_amount(cells, {"credit": 4}))
		self.assertFalse(parse.row_has_amount(cells, {"debit": 3}))
		self.assertFalse(parse.row_has_amount(cells, {}))
		self.assertFalse(parse.row_has_amount(cells, {"credit": 9}))

	def test_the_return_wording_marks_a_debit_line(self):
		for text in ("ลดหนี้", "รับคืน", "คืนสินค้า", "ส่งคืน", "ขายสินค้า ลดหนี้ 100"):
			with self.subTest(text=text):
				self.assertTrue(parse.is_debit_line(text))
		self.assertFalse(parse.is_debit_line("ขายสินค้า"))


class ExcelDecode(unittest.TestCase):
	def test_an_account_column_carries_the_code_on_every_row(self):
		sheets = [[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
			["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00", "4110-01"],
			["02/06/2569", "IV002", "ต้นทุน", "500.00", "0.00", "5010-01"],
			["03/06/2569", "", "no voucher", "0.00", "500.00", "4110-01"],
			["04/06/2569", "IV004", "no movement", "0.00", "0.00", "4110-01"],
			["", "", "", "", "", ""],
		]]
		result = parse.excel_rows(sheets)
		self.assertTrue(result["ok"])
		self.assertEqual(result["row_count"], 1)
		row = result["rows"][0]
		self.assertEqual((row.doc_no, row.norm_doc_no, row.date, row.account_code,
			row.description, row.debit, row.credit),
			("IV001", "iv001", "01/06/2569", "4110-01", "ขายสินค้า", 0.0, 1070.0))

	def test_a_date_cell_becomes_an_iso_string_and_shifts_its_buddhist_year(self):
		sheets = [[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
			[datetime(2569, 6, 1), "IV001", "ขายสินค้า", "0.00", "1,070.00", "4110-01"],
			[date(2024, 2, 29), "IV002", "ขายสินค้า", "0.00", "500.00", "4110-01"],
			[datetime(2025, 6, 1, 13, 30), "IV003", "ขายสินค้า", "0.00", "250.00", "4110-01"],
		]]
		rows = parse.excel_rows(sheets)["rows"]
		# 2569 is a Buddhist year and becomes 2026; a year below 2500 is taken as is.
		self.assertEqual([row.date for row in rows], ["2026-06-01", "2024-02-29", "2025-06-01"])

	def test_a_section_heading_names_the_rows_below_it(self):
		sheets = [[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต"],
			["4110-01", "", "", "", ""],
			["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00"],
			["02/06/2569", "IV002", "ขายสินค้า 2", "", "250.00"],
		]]
		rows = parse.excel_rows(sheets)["rows"]
		self.assertEqual([(row.doc_no, row.account_code, row.credit) for row in rows],
			[("IV001", "4110-01", 1070.0), ("IV002", "4110-01", 250.0)])

	def test_a_section_heading_naming_a_non_revenue_code_keeps_the_previous_one(self):
		sheets = [[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต"],
			["4110-01", "", "", "", ""],
			["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00"],
			["5010-01", "", "", "", ""],
			["02/06/2569", "IV002", "ขายสินค้า 2", "0.00", "500.00"],
		]]
		rows = parse.excel_rows(sheets)["rows"]
		self.assertEqual([row.account_code for row in rows], ["4110-01", "4110-01"])

	def test_a_coded_row_with_an_amount_is_a_data_row_not_a_heading(self):
		sheets = [[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต"],
			["4110-01", "", "", "", ""],
			["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00"],
		]]
		before_heading = parse.excel_rows([sheets[0][1:]])["rows"]
		# Without a heading the row is kept under whatever section preceded it.
		self.assertEqual(before_heading, [])

	def test_rows_without_a_printed_header_or_a_revenue_code_are_not_adopted(self):
		self.assertEqual(parse.excel_rows([[["01/06/2569", "IV001", "ขายสินค้า", "0.00",
			"1,070.00", "4110-01"]]])["rows"], [])
		without_account_column = parse.excel_rows([[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
			["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00", ""],
		]])
		self.assertEqual(without_account_column["rows"], [])

	def test_a_non_list_source_is_refused(self):
		with self.assertRaises(ReconciliationContractError):
			parse.excel_rows({"sheets": []})


class TableDecode(unittest.TestCase):
	def pages(self):
		return [{"text": "4110-01 รายได้จากการขาย", "tables": [[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต"],
			["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00"],
			["ยอดยกมา", "", "", "", ""],
			["02/06/2569", "IV002", "ขายสินค้า 2", "0.00", "500.00"],
			["4110-02 รายได้อื่น", "", "", "", ""],
			["03/06/2569", "IV003", "ขายสินค้า 3", "0.00", "100.00"],
		]]}]

	def test_a_framed_page_keeps_its_rows_and_its_headings(self):
		result = parse.table_rows(self.pages())
		self.assertEqual([(row.doc_no, row.account_code, row.credit) for row in result["rows"]],
			[("IV001", "4110-01", 1070.0), ("IV002", "4110-01", 500.0), ("IV003", "4110-02", 100.0)])
		self.assertEqual(result["diag"]["method"], "tables")
		self.assertEqual(result["diag"]["header_found"], True)
		self.assertEqual(result["diag"]["accounts_seen"], ["4110-01", "4110-02"])
		self.assertEqual(result["diag"]["tables_seen"], 1)

	def test_the_table_cell_text_is_kept_as_printed(self):
		pages = [{"text": "4110-01 รายได้", "tables": [[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต"],
			[datetime(2569, 6, 1), "IV001", "ขายสินค้า", "0.00", "1,070.00"],
		]]}]
		self.assertEqual(parse.table_rows(pages)["rows"][0].date, "2569-06-01 00:00:00")

	def test_rows_before_any_heading_are_not_adopted(self):
		pages = [{"text": "", "tables": [[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต"],
			["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00"],
		]]}]
		self.assertEqual(parse.table_rows(pages)["rows"], [])

	def test_a_page_that_is_not_a_mapping_is_refused(self):
		with self.assertRaises(ReconciliationContractError):
			parse.table_rows(["not a page"])
		with self.assertRaises(ReconciliationContractError):
			parse.table_rows("not a page list")


class TextLineDecode(unittest.TestCase):
	def test_the_three_number_tail_is_read_as_debit_and_credit(self):
		lines = ["4110-01 รายได้จากการขาย (1,000.00)",
			"01/06/2569 IV001 ขายสินค้า 0.00 1,070.00 2,070.00",
			"02/06/2569 IV002 รับคืน 107.00 0.00 1,963.00"]
		result = parse.text_rows(lines)
		rows = result["rows"]
		self.assertEqual([(row.doc_no, row.date, row.debit, row.credit) for row in rows],
			[("IV001", "01/06/2569", 0.0, 1070.0), ("IV002", "02/06/2569", 107.0, 0.0)])
		# The description runs from the voucher number to the last occurrence of the
		# first tail number; the deployed reader searches the whole rest of the line,
		# so a balance ending in the same digits stretches it.
		self.assertEqual(rows[0].description, "ขายสินค้า 0.00 1,070.00 2,07")
		self.assertEqual(rows[1].description, "รับคืน")
		self.assertEqual(result["diag"],
			{"method": "text_lines", "accounts_seen": ["4110-01"], "candidate_data_lines": 2,
				"rows_kept": 2})

	def test_the_two_number_tail_follows_the_running_balance(self):
		lines = ["4110-01 รายได้จากการขาย (1,000.00)",
			"01/06/2569 IV001 ขายสินค้า 1,070.00 2,070.00",
			"02/06/2569 IV002 รับคืนสินค้า 107.00 1,963.00"]
		rows = parse.text_rows(lines)["rows"]
		self.assertEqual([(row.debit, row.credit) for row in rows], [(0.0, 1070.0), (107.0, 0.0)])

	def test_a_line_without_a_running_balance_is_a_credit(self):
		lines = ["4110-01 รายได้จากการขาย", "01/06/2569 IV001 ขายสินค้า 1,070.00 2,070.00"]
		self.assertEqual(parse.text_rows(lines)["rows"][0].credit, 1070.0)

	def test_a_line_without_a_date_inherits_the_previous_one(self):
		lines = ["4110-01 รายได้จากการขาย",
			"01/06/2569 IV001 ขายสินค้า 0.00 1,070.00 2,070.00",
			"IV002 ขายสินค้า 0.00 500.00 2,570.00"]
		rows = parse.text_rows(lines)["rows"]
		self.assertEqual([row.date for row in rows], ["01/06/2569", "01/06/2569"])

	def test_lines_before_a_heading_and_summary_lines_are_skipped(self):
		lines = ["01/06/2569 IV001 ขายสินค้า 0.00 1,070.00 2,070.00",
			"4110-01 รายได้จากการขาย",
			"รวมประจำเดือน 0.00 1,070.00 2,070.00",
			"รวมทั้งสิ้น 0.00 1,070.00 2,070.00",
			"02/06/2569 IV002 ขายสินค้า 0.00 500.00 2,570.00"]
		rows = parse.text_rows(lines)["rows"]
		self.assertEqual([row.doc_no for row in rows], ["IV002"])

	def test_a_lower_prefix_on_the_caller_side_still_selects_the_rows(self):
		lines = ["4110-01 รายได้", "01/06/2569 IV001 ขายสินค้า 0.00 1,070.00 2,070.00"]
		self.assertEqual(parse.text_rows(lines, "9")["rows"], [])
		self.assertEqual(parse.text_rows(lines, "41")["row_count"], 1)

	def test_a_non_list_source_is_refused(self):
		with self.assertRaises(ReconciliationContractError):
			parse.text_rows("4110-01 รายได้")

	def test_a_section_code_cut_across_cells_is_read_whole(self):
		# I02（2026-09-29）：打印文本导入 Excel，段首「4100-01」被切成「_x001B_E4100-0」「1」两格。
		sheet = [["วันที่", "สมุด", "ใบสำคัญ", "คำอธิบาย", "เดบิท", "เครดิต  สถานะ", "ยอดคงเหลือ"],
			["_x001B_E4100-0", "1", "รายไ", "ด้จากการขาย", None, "_x001B_F", "-131449711.36"],
			[datetime(1969, 5, 1), "ขาย", "IV6900773", "ขายเชื่อให้", None, "37000", "-131486711.36"],
			["4110-02", "5,000.00", None, None, None, None, None],
			[datetime(1969, 5, 2), "ขาย", "IV6900774", "ขาย", None, "100", "5,100.00"],
			["_x001B_E4200-1", 0, "รายไ", "ด้จากการขายเศษวัสดุ", None, "_x001B_F", -3330],
			[datetime(1969, 5, 29), "ขาย", "IC6900758", "ขายเชื่อ", None, 300, -3630]]
		rows = parse.excel_rows([sheet])["rows"]
		self.assertEqual([(row.account_code, row.date, row.credit) for row in rows],
			[("4100-01", "2026-05-01", 37000.0), ("4110-02", "2026-05-02", 100.0), ("4200-10", "2026-05-29", 300.0)])

	def test_an_express_ledger_with_dashed_account_codes_is_read(self):
		# I03（2026-09-29）：Express 打印的分类账，科目号「41-01-00-00」，每行只有一个金额加括号余额。
		lines = ["เลขที่บัญชี      41-01-00-00      ถึง   41-01-00-00       เลือกแผนก   *",
			"41-01-00-00     รายได้จากการขาย          (113,336,751.03)",
			" 01/04/69 ขาย    SV690401-001 บันทึกขายวันที่ 1/4/2569       228,594.49      (113,565,345.52)",
			" 02/04/69 ขาย    SV690402-001 บันทึกขายวันที่ 2/4/2569       228,719.49      (113,794,065.01)",
			"                         รวม                      0.00      457,313.98",
			"      รวมทั้งสิ้น         2 รายการ    1  บัญชี                      0.00      457,313.98"]
		rows = parse.text_rows(lines)["rows"]
		self.assertEqual([(row.doc_no, row.date, row.account_code, row.debit, row.credit) for row in rows],
			[("SV690401-001", "01/04/69", "41-01-00-00", 0.0, 228594.49),
				("SV690402-001", "02/04/69", "41-01-00-00", 0.0, 228719.49)])

	def test_a_dashed_document_number_or_date_is_not_an_account(self):
		for text in ("MB 69-04-002", "01-02-69", "30-04-2569", "SV690401-001"):
			with self.subTest(text=text):
				self.assertNotEqual(parse.extract_account_code(text), text)
		self.assertIsNone(parse.extract_account_code("MB 69-04-002"))

	def test_a_trial_balance_is_told_apart_from_a_ledger(self):
		trial = {"kind": "excel", "sheets": [[["บริษัท น้ำแข็ง จำกัด"], ["  งบทดลอง     ณ วันที่ 28 ก.พ."],
			["41-01-00-00", "รายได้จากการขาย", 0, 101255846.08, 0.1, 5629764.54, 0, 106885610.52]]]}
		self.assertTrue(parse.is_trial_balance(trial))
		self.assertEqual(parse.decode(trial)["rows"], [])
		self.assertTrue(parse.is_trial_balance({"kind": "pdf", "pages": [], "lines": ["Trial Balance"]}))
		self.assertFalse(parse.is_trial_balance({"kind": "pdf", "pages": [],
			"lines": ["รายงานแยกประเภททั่วไป", "41-01-00-00 รายได้"]}))
		self.assertFalse(parse.is_trial_balance({"kind": "pipeline", "decoded": {}}))


class SourceDispatch(unittest.TestCase):
	def test_tables_win_and_the_printed_lines_are_the_fallback(self):
		pages = [{"text": "4110-01 รายได้", "tables": [[
			["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต"],
			["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00"]]]}]
		framed = parse.pdf_rows(pages, [])
		self.assertEqual(framed["diag"]["method"], "tables")
		self.assertEqual(framed["row_count"], 1)
		unframed = parse.pdf_rows([{"text": "4110-01 รายได้\n01/06/2569 IV001 ขายสินค้า 0.00 1,070.00 2,070.00",
			"tables": []}], ["4110-01 รายได้",
			"01/06/2569 IV001 ขายสินค้า 0.00 1,070.00 2,070.00"])
		self.assertEqual(unframed["diag"]["method"], "text_lines")
		self.assertEqual(unframed["diag"]["primary_tried"], "tables")
		self.assertEqual(unframed["diag"]["primary_tables_seen"], 0)
		self.assertEqual(unframed["diag"]["primary_header_found"], False)
		self.assertEqual(unframed["row_count"], 1)

	def test_the_pipeline_entries_are_filtered_by_the_revenue_prefix(self):
		decoded = {"pages": [{"document": {"entries": [
			{"voucher_no": "IV001", "account_code": "4110-01", "description": "ขายสินค้า",
				"debit": 0, "credit": 1070.0, "transaction_date": "2026-06-01"},
			{"voucher_no": "GL-2", "account_code": "5010-01", "description": "cost",
				"debit": "500", "credit": "0", "transaction_date": "2026-06-02"},
			{"voucher_no": "", "account_code": "4110-01", "description": "",
				"debit": "abc", "credit": "250", "transaction_date": None},
			{"voucher_no": "IV004", "account_code": "4110-01", "description": "no movement",
				"debit": 0, "credit": 0, "transaction_date": "2026-06-04"},
		]}}]}
		rows = parse.pipeline_rows(decoded)
		self.assertEqual([(row.doc_no, row.norm_doc_no, row.date, row.credit) for row in rows],
			[("IV001", "iv001", "2026-06-01", 1070.0), ("", "", "", 250.0)])
		self.assertEqual(parse.pipeline_rows(decoded, "5")[0].account_code, "5010-01")
		self.assertEqual(parse.pipeline_rows(None), [])
		with self.assertRaises(ReconciliationContractError):
			parse.pipeline_rows(["not", "a", "document"])

	def test_the_dispatch_refuses_an_unknown_or_incomplete_source(self):
		with self.assertRaises(ReconciliationContractError):
			parse.decode({"kind": "scan"})
		with self.assertRaises(ReconciliationContractError):
			parse.decode({"kind": "pdf", "pages": []})
		with self.assertRaises(ReconciliationContractError):
			parse.decode(["not", "a", "source"])
		self.assertEqual(parse.decode({"kind": "text", "lines": []})["diag"]["method"],
			"text_lines")
		self.assertEqual(parse.decode({"kind": "pipeline", "decoded": {}})["diag"]["method"],
			"pipeline")

	def test_decoded_rows_land_exactly_on_the_stored_row_contract(self):
		rows = parse.text_rows(["4110-01 รายได้",
			"01/06/2569 IV001 ขายสินค้า 0.00 1,070.00 2,070.00"])["rows"]
		converted = parse.decoded_rows(rows)
		self.assertEqual(set(converted[0]), set(contracts.GL_KEYS))
		stored = contracts.decode_rows(converted, [])[0]
		self.assertEqual((stored[0]["doc_no"], stored[0]["norm_doc_no"], stored[0]["credit"]),
			("IV001", "iv001", contracts.Decimal("1070.0")))


if __name__ == "__main__":
	unittest.main()
