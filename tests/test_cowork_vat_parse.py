"""Offline regressions for the migrated output-VAT report parsing conversions."""

import unittest

from pearnly_cowork.reconciliation import income_recon_contracts as contracts
from pearnly_cowork.reconciliation import vat_parse as parse

THAI_HEADER = ["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "สาขา", "มูลค่า",
	"ภาษี", "ยอดรวม"]
ERP_HEADER = ["ref", "txtsino", "txtamt", "txtvatamt", "vat_date"]


class LeafHelpers(unittest.TestCase):
	def test_an_unreadable_amount_stays_unknown(self):
		for value, expected in [("1,070.00", 1070.0), ("(250.00)", -250.0), ("", None), ("-", None),
				("–", None), ("abc", None), (None, None), ("1.005", 1.0), (" 12 ", 12.0)]:
			with self.subTest(value=value):
				self.assertEqual(parse.to_float(value), expected)

	def test_the_reference_column_wins_over_the_invoice_column(self):
		self.assertEqual(parse.map_columns(THAI_HEADER),
			{"date": 0, "invoice_no": 1, "buyer_name": 2, "buyer_tax_id": 3, "buyer_branch": 4,
				"amount_pre_vat": 5, "vat_amount": 6, "total_amount": 7})
		self.assertEqual(parse.map_columns(["วันที่", "อ้างอิง", "เลขที่เอกสาร"]),
			{"date": 0, "ref_no": 1, "invoice_no": 2})
		self.assertEqual(parse.map_columns(["Date", "Invoice No", "Customer", "Tax ID", "Branch",
			"Net", "VAT", "Total"]),
			{"date": 0, "invoice_no": 1, "buyer_name": 2, "buyer_tax_id": 3, "buyer_branch": 4,
				"amount_pre_vat": 5, "vat_amount": 6, "total_amount": 7})
		self.assertEqual(parse.map_columns(["日期", "发票号", "买方名称", "税号", "分支", "税前金额",
			"税额", "含税金额"]),
			{"date": 0, "invoice_no": 1, "buyer_name": 2, "buyer_tax_id": 3, "buyer_branch": 4,
				"amount_pre_vat": 5, "vat_amount": 6, "total_amount": 7})
		self.assertEqual(parse.map_columns(["", "", ""]), {})

	def test_one_row_is_read_into_the_deployed_field_names(self):
		row = parse.build_row(1, ["01/06/2569", "IV001", "ACME", "0-1234-56789-01-2", "00001",
			"1,000.00", "70.00", "1,070.00"], parse.map_columns(THAI_HEADER))
		self.assertEqual(row["row_no"], 1)
		self.assertEqual(row["report_date"], "2026-06-01")
		self.assertEqual(row["report_invoice_no"], "IV001")
		self.assertEqual(row["report_buyer_name"], "ACME")
		self.assertEqual(row["report_buyer_tax_id"], "0123456789012")
		self.assertEqual(row["report_buyer_branch"], "00001")
		self.assertEqual((row["report_amount_pre_vat"], row["report_vat_amount"],
			row["report_amount"]), (1000.0, 70.0, 1070.0))
		self.assertFalse(row["is_individual"])

	def test_a_row_without_a_tax_id_is_an_individual(self):
		row = parse.build_row(1, ["01/06/2569", "IV001", "ACME", "", "", "1,000.00", "70.00",
			"1,070.00"], parse.map_columns(THAI_HEADER))
		self.assertEqual((row["report_buyer_tax_id"], row["report_buyer_branch"],
			row["is_individual"]), ("", "00000", True))

	def test_cells_missing_from_the_row_stay_empty(self):
		row = parse.build_row(2, ["01/06/2569", "IV001"], {"date": 0, "invoice_no": 1,
			"amount_pre_vat": 5})
		self.assertEqual((row["report_date"], row["report_invoice_no"]),
			("2026-06-01", "IV001"))
		# The key is present but unknown: the deployed reader never guesses a zero.
		self.assertIsNone(row["report_amount_pre_vat"])
		self.assertTrue(row["is_individual"])

	def test_a_date_the_calendar_refuses_keeps_its_printed_text(self):
		row = parse.build_row(1, ["01/06/2026", "IV001"], {"date": 0, "invoice_no": 1})
		self.assertEqual(row["report_date"], "2026-06-01")
		unparsed = parse.build_row(1, ["xx/06/2026", "IV001"], {"date": 0, "invoice_no": 1})
		self.assertEqual(unparsed["report_date"], "xx/06/2026")

	def test_page_headers_separators_and_captions_are_filtered_out(self):
		kept = parse.filter_garbage_rows([
			{"report_ref_no": "IV001", "report_invoice_no": "IV001"},
			{"report_ref_no": "", "report_invoice_no": "1"},
			{"report_ref_no": "AB"},
			{"report_ref_no": "-----"},
			{"report_ref_no": "ใบกำกับภาษี เลขที่"},
			{"report_ref_no": "IV 001 extra word"},
			{"report_ref_no": "x" * 31},
			{"report_invoice_no": "OHC06/2025"},
		])
		self.assertEqual([row.get("report_ref_no") or row.get("report_invoice_no") for row in kept],
			["IV001", "OHC06/2025"])


class ExcelDecode(unittest.TestCase):
	def test_the_header_is_found_inside_the_first_thirty_rows(self):
		cells = [["รายงานภาษีขาย"]] + [["", "", "", "", "", "", "", ""] for _ in range(5)]
		cells.append(THAI_HEADER)
		cells.append(["01/06/2569", "IV001", "ACME", "1234567890123", "00000", "1,000.00", "70.00",
			"1,070.00"])
		result = parse.excel_rows(cells)
		self.assertTrue(result["ok"])
		self.assertEqual(result["method"], "excel")
		self.assertEqual(result["parser_version"], parse.PARSER_VERSION)
		self.assertEqual([row["report_invoice_no"] for row in result["rows"]], ["IV001"])

	def test_a_total_row_fills_the_meta_totals_instead_of_a_row(self):
		cells = [THAI_HEADER,
			["01/06/2569", "IV001", "ACME", "1234567890123", "00000", "1,000.00", "70.00",
				"1,070.00"],
			["รวมทั้งสิ้น", "", "", "", "", "1,000.00", "70.00", "1,070.00"]]
		result = parse.excel_rows(cells)
		self.assertEqual(result["row_count"], 1)
		self.assertEqual(result["meta"], {"total_amount_pre_vat": 1000.0, "total_vat": 70.0})

	def test_a_sheet_without_a_header_is_refused_the_deployed_way(self):
		result = parse.excel_rows([["01/06/2569", "IV001", "ACME"]])
		self.assertEqual(result, {"ok": False, "error": "找不到表头行", "rows": []})

	def test_empty_rows_are_skipped_without_consuming_a_row_number(self):
		cells = [THAI_HEADER, ["", "", "", "", "", "", "", ""],
			["01/06/2569", "IV001", "ACME", "", "", "1,000.00", "70.00", "1,070.00"],
			["", "", "", "", "", "", "", ""],
			["02/06/2569", "IV002", "ACME", "", "", "500.00", "35.00", "535.00"]]
		result = parse.excel_rows(cells)
		self.assertEqual([row["row_no"] for row in result["rows"]], [1, 2])
		self.assertEqual(result["row_count"], 2)

	def test_a_non_list_source_is_refused(self):
		with self.assertRaises(ValueError):
			parse.excel_rows("not a sheet")


class PrintedTextLines(unittest.TestCase):
	def test_a_glued_invoice_tail_is_split_and_the_name_is_cleaned(self):
		rows = parse.text_lines([
			"1 01/06/2569 DXOHC25-006OHANAHAN REF-1 บริษัท ทดสอบ จำกัด 1234567890123 00000 "
			"1,000.00 70.00"])
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row["report_invoice_no"], "DXOHC25-006")
		self.assertEqual(row["report_ref_no"], "REF-1")
		self.assertEqual(row["report_buyer_tax_id"], "1234567890123")
		self.assertEqual(row["report_buyer_branch"], "00000")
		self.assertEqual(row["report_date"], "2026-06-01")
		self.assertEqual((row["report_amount_pre_vat"], row["report_vat_amount"],
			row["report_amount"]), (1000.0, 70.0, 1070.0))
		self.assertIn("บริษัท ทดสอบ จำกัด", row["report_buyer_name"])
		self.assertNotIn("1234567890123", row["report_buyer_name"])
		self.assertFalse(row["is_individual"])

	def test_a_reference_number_without_a_digit_is_not_a_reference(self):
		row = parse.text_lines(["01/06/2569 IV001 OHANAHAN 1,000.00 70.00"])[0]
		self.assertEqual((row["report_invoice_no"], row["report_ref_no"]), ("IV001", ""))

	def test_a_line_without_a_date_or_a_two_amount_tail_is_not_a_row(self):
		self.assertEqual(parse.text_lines(["01/06/2569 IV001 ACME 1,000.00"]), [])
		self.assertEqual(parse.text_lines(["IV001 ACME 1,000.00 70.00"]), [])
		self.assertEqual(parse.text_lines(["รวมทั้งสิ้น 3,000.00 210.00"]), [])
		self.assertEqual(parse.text_lines(["เลขที่ใบกำกับภาษี 1,000.00 70.00",
			"01/06/2569 เลขที่ใบกำกับภาษี 1,000.00 70.00"]), [])

	def test_the_row_number_counts_kept_lines_only(self):
		rows = parse.text_lines(["", "not a row",
			"01/06/2569 IV001 ACME 1,000.00 70.00",
			"02/06/2569 IV002 ACME 2,000.00 140.00"])
		self.assertEqual([row["row_no"] for row in rows], [1, 2])

	def test_a_non_list_source_is_refused(self):
		with self.assertRaises(ValueError):
			parse.text_lines("01/06/2569 IV001 ACME 1,000.00 70.00")


class PdfDecode(unittest.TestCase):
	def page(self, text="x" * 250, tables=None):
		return {"text": text, "tables": tables or []}

	def test_a_framed_page_is_read_as_a_table(self):
		result = parse.pdf_rows([self.page(tables=[[
			THAI_HEADER,
			["01/06/2569", "IV001", "ACME", "1234567890123", "00000", "1,000.00", "70.00",
				"1,070.00"],
			["รวมทั้งสิ้น", "", "", "", "", "1,000.00", "70.00", "1,070.00"],
		]])], [])
		self.assertEqual(result["method"], "pdf_text")
		self.assertEqual(result["row_count"], 1)
		self.assertEqual(result["meta"], {"total_amount_pre_vat": 1000.0, "total_vat": 70.0})

	def test_a_page_without_frames_is_retried_through_the_printed_lines(self):
		lines = ["01/06/2569 IV001 ACME 1,000.00 70.00"]
		result = parse.pdf_rows([self.page(text="\n".join(lines + ["x" * 250]))], lines)
		self.assertEqual(result["method"], "pdf_text_regex")
		self.assertEqual(result["row_count"], 1)

	def test_a_thin_text_layer_without_rows_needs_ocr(self):
		self.assertIsNone(parse.pdf_rows([self.page(text="scan")], ["scan"]))
		self.assertIsNone(parse.pdf_rows([self.page(text="")
			, self.page(text="x" * 250)], []))
		self.assertIsNone(parse.pdf_rows([], []))

	def test_a_page_that_is_not_a_mapping_is_refused(self):
		with self.assertRaises(ValueError):
			parse.pdf_rows(["not a page"], [])


class DelimitedDecode(unittest.TestCase):
	def test_a_keyword_header_is_read_in_full(self):
		result = parse.csv_rows("\n".join([
			",".join(THAI_HEADER),
			"01/06/2569,IV001,ACME,1234567890123,00000,\"1,000.00\",70.00,\"1,070.00\"",
			"รวมทั้งสิ้น,,,,,\"1,000.00\",70.00,\"1,070.00\"",
		]))
		self.assertEqual(result["method"], "csv_direct_v1")
		self.assertEqual(result["row_count"], 1)
		self.assertEqual(result["warnings"], [])
		row = result["rows"][0]
		self.assertEqual((row["report_invoice_no"], row["report_amount_pre_vat"],
			row["report_vat_amount"], row["report_amount"]), ("IV001", 1000.0, 70.0, 1070.0))
		self.assertEqual(row["report_date"], "2026-06-01")

	def test_the_erp_export_columns_are_matched_exactly(self):
		result = parse.csv_rows("\n".join([
			",".join(ERP_HEADER),
			"REF-1,IV001,\"1,000.00\",70.00,01/06/2569",
		]))
		row = result["rows"][0]
		self.assertEqual((row["report_ref_no"], row["report_invoice_no"],
			row["report_amount_pre_vat"], row["report_vat_amount"]),
			("REF-1", "IV001", 1000.0, 70.0))
		self.assertEqual(row["report_buyer_branch"], "00000")
		self.assertEqual(row["report_date"], "2026-06-01")

	def test_a_missing_total_is_filled_from_net_plus_vat(self):
		result = parse.csv_rows("\n".join([
			"วันที่,เลขที่ใบกำกับ,มูลค่า,ภาษี",
			"01/06/2569,IV001,\"1,000.00\",70.00",
		]))
		self.assertEqual(result["rows"][0]["report_amount"], 1070.0)

	def test_a_row_without_a_document_number_is_counted_in_a_warning(self):
		result = parse.csv_rows("\n".join([
			",".join(THAI_HEADER),
			",,,,,1,000.00,70.00,1,070.00",
		]))
		self.assertEqual(result["rows"], [])
		self.assertEqual(result["warnings"], ["跳过 1 行(无单据号)"])

	def test_a_file_whose_columns_are_unknown_is_left_to_the_caller(self):
		self.assertIsNone(parse.csv_rows("a,b,c\n1,2,3"))
		self.assertIsNone(parse.csv_rows(""))
		self.assertIsNone(parse.csv_rows("วันที่,เลขที่ใบกำกับ\n01/06/2569,IV001"))

	def test_a_tab_delimited_file_uses_its_own_delimiter(self):
		result = parse.csv_rows("\n".join([
			"\t".join(["วันที่", "เลขที่ใบกำกับ", "มูลค่า", "ภาษี"]),
			"01/06/2569\tIV001\t1,000.00\t70.00",
		]), "\t")
		self.assertEqual(result["rows"][0]["report_invoice_no"], "IV001")


class PipelineDecode(unittest.TestCase):
	def decoded(self):
		return {"pages": [{"document": {"entries": [
			{"seq_no": "3", "invoice_no": "IV001", "transaction_date": "2026-06-01",
				"customer_name": "ACME", "customer_tax": "1234567890123",
				"customer_branch": "00000", "subtotal": "1,000.00", "vat": "70.00",
				"total": "1,070.00"},
			{"seq_no": "", "invoice_no": "1", "transaction_date": "", "customer_name": "",
				"customer_tax": "", "customer_branch": "", "subtotal": "0", "vat": "0",
				"total": "0"},
		]}}]}

	def test_a_row_without_a_real_invoice_number_is_dropped(self):
		rows = parse.pipeline_rows(self.decoded())
		self.assertEqual([row["report_invoice_no"] for row in rows], ["IV001"])
		self.assertEqual((rows[0]["row_no"], rows[0]["report_date"], rows[0]["report_ref_no"]),
			(3, "2026-06-01", "IV001"))
		self.assertEqual((rows[0]["report_amount_pre_vat"], rows[0]["report_vat_amount"],
			rows[0]["report_amount"]), (1000.0, 70.0, 1070.0))
		self.assertEqual((rows[0]["report_buyer_tax_id"], rows[0]["is_individual"]),
			("1234567890123", False))

	def test_an_enterprise_engine_keeps_the_row(self):
		rows = parse.pipeline_rows(self.decoded(), "enterprise-2025-01")
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[1]["row_no"], 2)

	def test_empty_decoded_input_yields_no_row(self):
		self.assertEqual(parse.pipeline_rows(None), [])
		self.assertEqual(parse.pipeline_rows({}), [])
		with self.assertRaises(ValueError):
			parse.pipeline_rows(["not", "a", "document"])


class SourceDispatch(unittest.TestCase):
	def test_each_kind_reaches_its_reader(self):
		excel = parse.decode({"kind": "excel", "cells": [THAI_HEADER, ["01/06/2569", "IV001",
			"ACME", "", "", "1,000.00", "70.00", "1,070.00"]]})
		self.assertEqual(excel["method"], "excel")
		self.assertEqual(excel["row_count"], 1)
		text = parse.decode({"kind": "text", "lines": ["01/06/2569 IV001 ACME 1,000.00 70.00"]})
		self.assertEqual(text["method"], "pdf_text_regex")
		csv_result = parse.decode({"kind": "csv", "text": "วันที่,เลขที่ใบกำกับ,มูลค่า,ภาษี\n"
			"01/06/2569,IV001,1,000.00,70.00", "delimiter": ","})
		self.assertEqual(csv_result["method"], "csv_direct_v1")
		pipeline = parse.decode({"kind": "pipeline", "decoded": {
			"pages": [{"document": {"entries": [{"invoice_no": "IV001", "subtotal": "1,000.00",
				"vat": "70.00"}]}}]}, "engine": "qwen3-32b"})
		self.assertEqual((pipeline["method"], pipeline["row_count"]), ("qwen3-32b", 1))
		self.assertIsNone(parse.decode({"kind": "pdf", "pages": [{"text": "scan", "tables": []}],
			"lines": ["scan"]}))

	def test_a_page_header_that_reads_as_a_row_is_filtered_on_the_way_out(self):
		result = parse.decode({"kind": "text", "lines": [
			"01/06/2569 ใบกำกับภาษี ACME 1,000.00 70.00",
			"01/06/2569 IV001 ACME 1,000.00 70.00"]})
		self.assertEqual([row["report_invoice_no"] for row in result["rows"]], ["IV001"])
		self.assertEqual(result["row_count"], 1)

	def test_a_sheet_whose_header_is_cut_up_is_read_by_the_shape_of_its_rows(self):
		# Express 的文字导出（2026-09-29 压测 I02）：表头切成两行、词拆进相邻格子，日期是 Excel 当成 1969 的「69」。
		import datetime

		cells = [["", "", "เดือ", "น/ปีภาษี พฤ"], ["_x001B_E_x001B_W_x0001_รายงานภ", "าษีขาย"], ["----", "------"],
			["<----ใบกำ", "กับภาษี----", "->  ชื่อผู้ซื้อ", "เลขประจำตัว", "สถานประกอบการ", "มูลค่าสินค้า", "จำนวนเงิน"],
			["ลำดั", "บ วัน/เดือน", "/ปี\xa0เลขที่", "ผู้เสียภาษี", "สนญ.   สาขาที่", "หรือบริการ", "ภาษีมูลค่าเพิ", "่ม"],
			[1, datetime.datetime(1969, 5, 1), "IV6900773", "บริษัท•ซีพีเอฟ\xa0จำกัด", "0107555000023•", 97, 37000, 2590],
			[2, datetime.datetime(1969, 5, 1), "IV6900774", "บริษัท•บางคล้า\xa0จำกัด", "0245550000332•", "X", 7740, 541.8],
			[3, datetime.datetime(1969, 5, 5), "C-6900879", "ร้าน•เพ็ทเตอร์", "3412000154396•", "X", 4205.61, 294.39],
			["", "", "", "รวม", "", "", 48945.61, 3426.19]]
		result = parse.decode({"kind": "excel", "cells": cells})
		self.assertEqual((result["method"], result["row_count"]), ("excel_shape", 3))
		first = result["rows"][0]
		self.assertEqual((first["report_date"], first["report_invoice_no"], first["report_buyer_tax_id"],
			first["report_buyer_branch"], first["report_amount_pre_vat"], first["report_vat_amount"]),
			("2026-05-01", "IV6900773", "0107555000023", "00097", 37000.0, 2590.0))
		self.assertEqual(first["report_buyer_name"], "บริษัท ซีพีเอฟ จำกัด")
		self.assertEqual(result["rows"][1]["report_buyer_branch"], "00000")

	def test_printed_lines_keep_daily_summaries_and_split_numbers(self):
		# Express 打印的税报（I03 / V03）：当日汇总行没有单号；「MB 69-04-002」被空格拆开；分支不从单号里取。
		result = parse.decode({"kind": "text", "lines": [
			"1  01/04/69               บันทึกขายวันที่ 1/4/2569        228,594.49      16,001.61",
			"2  01/04/69  MB 69-04-002 เอเซียมีนบุรี จํากัด               150,000.00      10,500.00",
			"3  01/07/69  IV69/00564 บิ๊กซี ซูเปอรเซ็นเตอร 0107536000633 00000      268.00          18.76",
			"   รวมทั้งสิ้น   งวด 01/04/69 ถึง  30/04/69                   378,862.49      26,520.37"]})
		rows = result["rows"]
		self.assertEqual([row["report_invoice_no"] for row in rows], ["", "MB 69-04-002", "IV69/00564"])
		self.assertEqual(rows[1]["report_buyer_name"], "เอเซียมีนบุรี จํากัด")
		self.assertEqual((rows[2]["report_buyer_branch"], rows[2]["report_buyer_name"]), ("00000", "บิ๊กซี ซูเปอรเซ็นเตอร"))

	def test_an_unknown_or_incomplete_source_is_refused(self):
		with self.assertRaises(ValueError):
			parse.decode({"kind": "scan"})
		with self.assertRaises(ValueError):
			parse.decode({"kind": "csv"})
		with self.assertRaises(ValueError):
			parse.decode(["not", "a", "source"])

	def test_decoded_rows_land_exactly_on_the_stored_row_contract(self):
		rows = parse.text_lines(["01/06/2569 IV001 ACME 1,000.00 70.00"])
		converted = parse.decoded_rows(rows)
		self.assertEqual(set(converted[0]), set(contracts.VAT_KEYS))
		stored = contracts.decode_rows([], converted)[1][0]
		self.assertEqual((stored["report_invoice_no"], stored["report_date"],
			stored["report_amount_pre_vat"]), ("IV001", "2026-06-01",
			contracts.Decimal("1000.0")))
		# An unreadable amount is the contract's zero, not a refusal.
		unknown = parse.decoded_rows([{"report_invoice_no": "IV002",
			"report_amount_pre_vat": None}])
		self.assertEqual(contracts.decode_rows([], unknown)[1][0]["report_amount_pre_vat"],
			contracts.Decimal(0))



class HeaderVariants(unittest.TestCase):
	"""2026-09-28 录制读代码发现：税务局标准表头与英文表头认错列。"""

	def fields(self, headers):
		return {field: headers[index] for field, index in parse.map_columns(headers).items()}

	def test_the_revenue_department_layout(self):
		headers = ["ลำดับ", "วัน เดือน ปี", "เลขที่", "ชื่อผู้ซื้อสินค้า/ผู้รับบริการ",
			"เลขประจำตัวผู้เสียภาษีอากรของผู้ซื้อ", "สถานประกอบการ", "มูลค่าสินค้าหรือบริการ", "จำนวนเงินภาษีมูลค่าเพิ่ม"]
		got = self.fields(headers)
		self.assertEqual((got["date"], got["invoice_no"], got["buyer_branch"], got["amount_pre_vat"], got["vat_amount"]),
			("วัน เดือน ปี", "เลขที่", "สถานประกอบการ", "มูลค่าสินค้าหรือบริการ", "จำนวนเงินภาษีมูลค่าเพิ่ม"))

	def test_english_headers_and_a_sequence_column(self):
		got = self.fields(["No.", "Customer Tax ID", "Customer Name", "Invoice No.", "Date", "Amount before VAT", "VAT"])
		self.assertEqual((got["invoice_no"], got["buyer_tax_id"], got["buyer_name"], got["amount_pre_vat"], got["vat_amount"]),
			("Invoice No.", "Customer Tax ID", "Customer Name", "Amount before VAT", "VAT"))
		# 没有别的发票号列时，序号列才当发票号
		self.assertEqual(self.fields(["No.", "Date", "VAT"])["invoice_no"], "No.")

	def test_vat_before_amount_does_not_take_the_net_column(self):
		got = self.fields(["วันที่", "เลขที่ใบกำกับ", "ภาษีมูลค่าเพิ่ม", "มูลค่า"])
		self.assertEqual((got["vat_amount"], got["amount_pre_vat"]), ("ภาษีมูลค่าเพิ่ม", "มูลค่า"))

	def test_spreadsheet_date_cells(self):
		from datetime import datetime
		row = parse.build_row(1, [datetime(2026, 1, 5), "IV-1"], {"date": 0, "invoice_no": 1})
		self.assertEqual(row["report_date"], "2026-01-05")
		row = parse.build_row(1, [46027.0, "IV-1"], {"date": 0, "invoice_no": 1})
		self.assertEqual(row["report_date"], "2026-01-05")


class TextLineTails(unittest.TestCase):
	def test_a_line_ending_in_net_vat_and_total_takes_the_first_two(self):
		# 2026-09-28 录制读代码发现：行尾三个数时取成了 VAT 与合计
		rows = parse.text_lines(["1 02/09/2569 IV6909-001 บริษัท เอ จำกัด 0105547051615 00000 1,000.00 70.00 1,070.00"])
		self.assertEqual((rows[0]["report_amount_pre_vat"], rows[0]["report_vat_amount"]), (1000.0, 70.0))
		self.assertEqual(rows[0]["report_invoice_no"], "IV6909-001")

	def test_two_trailing_amounts_read_as_before(self):
		rows = parse.text_lines(["02/09/2569 IV6909-002 บริษัท บี จำกัด 0105547051615 00000 2,000.00 140.00"])
		self.assertEqual((rows[0]["report_amount_pre_vat"], rows[0]["report_vat_amount"]), (2000.0, 140.0))

if __name__ == "__main__":
	unittest.main()
