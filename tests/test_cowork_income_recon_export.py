"""Offline XLSX checks for the migrated income-reconciliation review workbook.

The workbook is pure presentation: the caller supplies the detail rows and the
summary, so these cases need no Site, database, model or file.
"""
import unittest
from io import BytesIO

import openpyxl

from pearnly_cowork.reconciliation.export import income_workbook
from pearnly_cowork.reconciliation.income_vat import GlVatSummary, ReconRow


def detail_row(doc_no="GL-1", **values):
	return ReconRow(
		doc_no=doc_no, date=values.get("date", "01/06/2569"),
		customer_name=values.get("customer_name", "ACME"),
		vat_amount=values.get("vat_amount", 1070.0),
		gl_amount=values.get("gl_amount", 1070.0),
		diff=values.get("diff", 0.0),
		account_codes=values.get("account_codes", "4110-01"))


def summary(**values):
	return GlVatSummary(
		gl_total=values.get("gl_total", 1493.0),
		gl_only_credit=values.get("gl_only_credit", 500.0),
		gl_only_debit=values.get("gl_only_debit", 0.0),
		vat_only_positive=values.get("vat_only_positive", 250.0),
		vat_only_negative=values.get("vat_only_negative", 0.0),
		vat_total=values.get("vat_total", 1243.0),
		gl_only_credit_items=values.get("gl_only_credit_items", [
			{"doc_no": "GL-3", "date": "03/06/2569", "name": "no vat row", "amount": 500.0}]),
		vat_only_positive_items=values.get("vat_only_positive_items", [
			{"doc_no": "INV-9", "date": "05/06/2569", "name": "BETA", "amount": 250.0}]))


def sheets(content):
	return openpyxl.load_workbook(BytesIO(content), data_only=False)


class SheetLayout(unittest.TestCase):
	def test_the_three_sheets_are_named_in_the_requested_language(self):
		for lang, names in [("th", ("รายละเอียดผลต่าง", "กระทบยอด", "วิธีใช้งาน")),
				("zh", ("差异明细", "对账汇总", "使用说明")),
				("en", ("Detail", "Reconciliation", "Instructions")),
				("ja", ("差異明細", "照合まとめ", "使い方"))]:
			with self.subTest(lang=lang):
				book = sheets(income_workbook.export_gl_vat_excel([detail_row()], summary(),
					lang=lang))
				self.assertEqual(book.sheetnames, list(names))

	def test_an_unknown_language_falls_back_to_thai(self):
		book = sheets(income_workbook.export_gl_vat_excel([], summary(), lang="bogus"))
		self.assertEqual(book.sheetnames[0], "รายละเอียดผลต่าง")
		self.assertEqual(book.sheetnames[2], "วิธีใช้งาน")

	def test_the_detail_sheet_keeps_the_deployed_geometry(self):
		content = income_workbook.export_gl_vat_excel([detail_row(), detail_row("GL-2")],
			summary(), lang="en")
		ws = sheets(content)["Detail"]
		self.assertEqual((ws.max_row, ws.max_column), (6, 8))
		self.assertEqual(ws.freeze_panes, "A5")
		self.assertEqual(ws.row_dimensions[2].height, 28)
		self.assertEqual(ws.row_dimensions[4].height, 22)
		self.assertEqual([ws.column_dimensions[letter].width for letter in "ABCDEFGH"],
			[14, 20, 14, 32, 18, 18, 14, 22])
		self.assertEqual([ws.cell(4, index).value for index in range(1, 9)],
			["Status", "Document No.", "Date", "Customer", "VAT Report Amount", "GL Amount",
				"Difference", "Revenue Account"])
		self.assertIsNone(ws.cell(3, 1).value)

	def test_the_kpi_band_reports_the_deployed_counters(self):
		rows = [detail_row("GL-1"), detail_row("GL-2", gl_amount=1100.0, diff=30.0),
			detail_row("INV-9", gl_amount=None, diff=None, account_codes="")]
		ws = sheets(income_workbook.export_gl_vat_excel(rows, summary(), lang="en"))["Detail"]
		self.assertEqual([ws.cell(1, index).value for index in range(1, 6)],
			["Total", "Matched", "Diff", "GL Missing", "Total Diff Amount"])
		self.assertEqual([ws.cell(2, index).value for index in range(1, 6)],
			[3, 1, 1, 1, 30.0])
		self.assertEqual(ws.cell(2, 5).number_format, "#,##0.00;[Red](#,##0.00);0.00")
		self.assertEqual(ws.cell(1, 1).font.color.rgb, "001D4ED8")
		self.assertEqual(ws.cell(1, 1).fill.fgColor.rgb, "00EFF6FF")
		self.assertEqual(ws.cell(2, 2).font.color.rgb, "0016A34A")
		self.assertEqual(ws.cell(2, 2).fill.fgColor.rgb, "00DCFCE7")
		self.assertEqual(ws.cell(2, 4).font.color.rgb, "00D97706")
		self.assertEqual(ws.cell(2, 4).fill.fgColor.rgb, "00FFEDD5")

	def test_a_row_carries_its_status_text_and_colour(self):
		rows = [detail_row("GL-1"), detail_row("GL-2", gl_amount=1100.0, diff=30.0),
			detail_row("INV-9", gl_amount=None, diff=None, account_codes="")]
		ws = sheets(income_workbook.export_gl_vat_excel(rows, summary(), lang="en"))["Detail"]
		self.assertEqual([ws.cell(row, 1).value for row in (5, 6, 7)],
			["✓ Matched", "⚠ Diff", "❗ GL Missing"])
		self.assertEqual(ws.cell(5, 1).fill.fgColor.rgb, "00D6F5E3")
		self.assertEqual(ws.cell(6, 1).fill.fgColor.rgb, "00FDE8E8")
		self.assertEqual(ws.cell(7, 1).fill.fgColor.rgb, "00FFF3CD")
		self.assertEqual(ws.cell(5, 7).fill.fgColor.rgb, "00D6F5E3")
		self.assertEqual(ws.cell(6, 7).fill.fgColor.rgb, "00FDE8E8")
		self.assertEqual(ws.cell(7, 6).fill.fgColor.rgb, "00FFF3CD")
		self.assertEqual(ws.cell(7, 7).fill.fgColor.rgb, "00FFF3CD")
		self.assertEqual(ws.cell(5, 1).alignment.horizontal, "left")
		for column in (5, 6, 7):
			self.assertEqual(ws.cell(5, column).alignment.horizontal, "right")
			self.assertEqual(ws.cell(5, column).number_format,
				"#,##0.00;[Red](#,##0.00);0.00")

	def test_an_absent_ledger_side_reads_as_the_not_found_label(self):
		rows = [detail_row("INV-9", gl_amount=None, diff=None, account_codes="")]
		for lang, label in [("th", "ไม่พบข้อมูล"), ("zh", "未找到数据"), ("en", "Not found"),
				("ja", "データなし")]:
			with self.subTest(lang=lang):
				ws = sheets(income_workbook.export_gl_vat_excel(rows, summary(), lang=lang))[
					income_workbook._t(lang, "sheet1")]
				self.assertEqual((ws.cell(5, 6).value, ws.cell(5, 7).value), (label, label))

	def test_an_empty_task_still_renders_the_band_and_the_header(self):
		ws = sheets(income_workbook.export_gl_vat_excel([], summary(), lang="en"))["Detail"]
		self.assertEqual([ws.cell(2, index).value for index in range(1, 6)], [0, 0, 0, 0, 0])
		self.assertEqual(ws.cell(4, 1).value, "Status")
		self.assertEqual(ws.max_row, 4)


class SummarySheet(unittest.TestCase):
	def test_the_ladder_reads_total_less_credits_plus_debits_then_the_report(self):
		ws = sheets(income_workbook.export_gl_vat_excel([], summary(), lang="en"))[
			"Reconciliation"]
		self.assertEqual(ws.max_column, 2)
		self.assertEqual(ws.cell(1, 1).value, "Description")
		self.assertEqual(ws.cell(1, 2).value, "Amount")
		# Only the two totals carry an amount; each adjustment heading leaves it to the
		# itemized rows the deployed report expands below it.
		self.assertEqual([(ws.cell(row, 1).value, ws.cell(row, 2).value)
			for row in (2, 5, 6, 8, 9)],
			[("Total per General Ledger", 1493.0),
				("Add: GL debits not in VAT Report", None),
				("Add: Sales in VAT Report not in GL", None),
				("Less: Credit notes in VAT Report not in GL", None),
				("Total per VAT Sales Report", 1243.0)])
		self.assertEqual([(ws.cell(row, 1).value, ws.cell(row, 2).value)
			for row in (3, 4, 7)],
			[("Less: GL credits not in VAT Report", None),
				("    · GL-3  ·  03/06/2569  ·  no vat row", -500.0),
				("    · INV-9  ·  05/06/2569  ·  BETA", 250.0)])

	def test_the_emphasized_rows_and_the_item_rows_keep_their_formatting(self):
		ws = sheets(income_workbook.export_gl_vat_excel([], summary(), lang="en"))[
			"Reconciliation"]
		self.assertEqual(ws.cell(2, 2).fill.fgColor.rgb, "001A3C5E")
		self.assertEqual(ws.cell(2, 2).number_format, "#,##0.00;[Red](#,##0.00);0.00")
		self.assertEqual(ws.cell(3, 1).fill.fgColor.rgb, "00EFF6FF")
		self.assertEqual(ws.cell(3, 1).border.top.style, "thin")
		self.assertEqual(ws.cell(4, 1).value, "    · GL-3  ·  03/06/2569  ·  no vat row")
		# A credit row is shown negated.
		self.assertEqual(ws.cell(4, 2).value, -500.0)
		self.assertEqual(ws.cell(4, 1).fill.fgColor.rgb, "00FFFFFF")
		self.assertEqual(ws.cell(4, 1).font.size, 9)
		self.assertEqual(ws.cell(4, 2).number_format, "#,##0.00;[Red](#,##0.00);0.00")
		self.assertEqual(ws.column_dimensions["A"].width, 90)
		self.assertEqual(ws.column_dimensions["B"].width, 22)

	def test_a_long_item_name_is_cut_with_an_ellipsis(self):
		value = summary(gl_only_credit_items=[{"doc_no": "GL-9", "date": "01/06/2569",
			"name": "x" * 80, "amount": 1.0}])
		ws = sheets(income_workbook.export_gl_vat_excel([], value, lang="en"))["Reconciliation"]
		self.assertEqual(ws.cell(4, 1).value, f"    · GL-9  ·  01/06/2569  ·  {'x' * 53}…")

	def test_a_positive_item_is_not_negated(self):
		ws = sheets(income_workbook.export_gl_vat_excel([], summary(), lang="en"))[
			"Reconciliation"]
		rows = {ws.cell(row, 1).value: ws.cell(row, 2).value for row in range(2, ws.max_row + 1)}
		self.assertEqual(rows["    · INV-9  ·  05/06/2569  ·  BETA"], 250.0)

	def test_the_group_separator_appears_from_the_second_section_on(self):
		ws = sheets(income_workbook.export_gl_vat_excel([], summary(), lang="en"))[
			"Reconciliation"]
		headings = [row for row in range(3, ws.max_row)
			if ws.cell(row, 2).value is None]
		self.assertEqual(headings, [3, 5, 6, 8])
		for row in headings:
			self.assertEqual(ws.cell(row, 1).border.top.style, "thin")
		# The totals and the item rows stay unruled.
		for row in (2, 4, 7, 9):
			self.assertIsNone(ws.cell(row, 1).border.top.style)


class InstructionsSheet(unittest.TestCase):
	def test_the_reference_card_lists_all_four_languages_whatever_the_language(self):
		ws = sheets(income_workbook.export_gl_vat_excel([], summary(), lang="en"))["Instructions"]
		values = [ws.cell(row, 1).value for row in range(1, ws.max_row + 1)]
		self.assertEqual(values[0], "GL vs VAT Reconciliation · Reference Card")
		for title in ("中文 · 使用说明", "ภาษาไทย · วิธีใช้งาน", "English · Instructions",
				"日本語 · 使い方"):
			self.assertIn(title, values)
		self.assertEqual(ws.column_dimensions["A"].width, 100)
		body = [value for value in values if value and value.startswith("1. ")]
		self.assertEqual(len(body), 4)


class LabelTable(unittest.TestCase):
	def test_every_label_covers_the_four_languages_and_unknown_keys_fall_back(self):
		self.assertEqual(set(income_workbook._I18N), {"th", "zh", "en", "ja"})
		keys = set(income_workbook._I18N["th"])
		for lang in ("th", "zh", "en", "ja"):
			self.assertEqual(set(income_workbook._I18N[lang]), keys)
		for lang in ("th", "zh", "en", "ja"):
			for key in keys:
				self.assertTrue(income_workbook._t(lang, key))
		self.assertEqual(income_workbook._t("en", "missing_key"), "missing_key")
		self.assertEqual(income_workbook._t("bogus", "sheet1"),
			income_workbook._I18N["th"]["sheet1"])


if __name__ == "__main__":
	unittest.main()
