"""Offline regressions for the migrated file intake (batch 41).

Runs in the lab bench (openpyxl, pdfminer and the reconciliation package need
the bench environment). Every fixture is built in memory and the model path is a
fake, so nothing is read from disk and no model is called.
"""

import io
import unittest

import openpyxl

from pearnly_cowork.reconciliation import bank_intake, bank_parse

STATEMENT_HEADER = ["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"]
GL_HEADER = ["วันที่", "เลขที่เอกสาร", "คำอธิบาย", "เดบิต", "เครดิต", "คงเหลือ", "รหัสบัญชี"]


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


def pdf_with_lines(lines, leading=700):
	"""A one-page PDF whose text layer carries these lines in order."""
	parts = ["BT /F1 10 Tf 20 %d Td 14 TL" % leading]
	for index, line in enumerate(lines):
		if index:
			parts.append("T*")
		parts.append("(%s) Tj" % line)
	parts.append("ET")
	stream = "\n".join(parts).encode("latin-1", "replace")
	objects = [
		b"<< /Type /Catalog /Pages 2 0 R >>",
		b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
		b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
		b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
		b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
	]
	out = bytearray(b"%PDF-1.4\n")
	offsets = []
	for number, body in enumerate(objects, 1):
		offsets.append(len(out))
		out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
	xref = len(out)
	out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
	for offset in offsets:
		out += ("%010d 00000 n \n" % offset).encode()
	out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
		b"startxref\n" + str(xref).encode() + b"\n%%EOF\n")
	return bytes(out)


class FakeModel:
	"""Stands in for the injected model path and records how it was called."""

	def __init__(self, payload, document_type="bank_statement"):
		self.payload = payload
		self.document_type = document_type
		self.calls = []

	def __call__(self, document_type, file_bytes, filename):
		self.calls.append((document_type, filename, len(file_bytes)))
		if document_type != self.document_type:
			return None
		return self.payload


def statement_row(**overrides):
	fields = {"date": None, "description": "", "withdrawal": 0.0, "deposit": 0.0, "balance": 0.0}
	fields.update(overrides)
	from pearnly_cowork.reconciliation.bank_statement import StatementRow
	return StatementRow(**fields)


class WorkbookStatement(unittest.TestCase):
	def test_a_workbook_is_read_without_asking_the_model(self):
		model = FakeModel({"ok": True, "rows": [statement_row(description="wrong")]})
		result = bank_intake.read_statement_file(workbook([("8258", [
			STATEMENT_HEADER,
			["01/06/2569", "ยกมา", "", "", 1000.0],
			["02/06/2569", "โอนเงิน", "", 100.0, 1100.0],
			["03/06/2569", "ค่าอาหาร", 50.0, "", 1050.0],
			["04/06/2569", "โอนออก", 25.0, "", 1025.0],
		])]), "stm.xlsx", model_rows=model)
		self.assertTrue(result["ok"])
		self.assertEqual(result["source"], "workbook")
		self.assertEqual(model.calls, [])
		self.assertEqual(result["row_count"], 3)
		self.assertEqual(result["bank_code"], "generic")
		self.assertEqual([row.balance for row in result["rows"]], [1100.0, 1050.0, 1025.0])
		self.assertTrue(all(row.source_file == "stm.xlsx" for row in result["rows"]))

	def test_the_filename_bank_code_wins(self):
		result = bank_intake.read_statement_file(workbook([("s", [
			STATEMENT_HEADER,
			["01/06/2569", "โอนเงิน", "", 100.0, 1100.0],
			["02/06/2569", "โอนเงิน", "", 100.0, 1200.0],
			["03/06/2569", "โอนเงิน", "", 100.0, 1300.0],
		])]), "STM TTB 2569-05.xlsx")
		self.assertEqual(result["bank_code"], "ttb")
		self.assertEqual(result["source"], "workbook")

	def test_a_statement_with_few_rows_gives_the_model_a_turn(self):
		model = FakeModel({"ok": True, "rows": [
			statement_row(description="a", deposit=100.0, balance=100.0),
			statement_row(description="b", deposit=100.0, balance=200.0),
			statement_row(description="c", deposit=100.0, balance=300.0),
		], "opening": 0.0, "closing": 300.0})
		result = bank_intake.read_statement_file(workbook([("s", [
			STATEMENT_HEADER,
			["01/06/2569", "โอนเงิน", "", 100.0, 1100.0],
		])]), "stm.xlsx", model_rows=model)
		self.assertEqual(model.calls[0][0], "bank_statement")
		self.assertEqual(result["source"], "model")
		self.assertEqual(result["row_count"], 3)

	def test_a_free_read_that_is_no_worse_keeps_its_rows(self):
		# Both chains are equally untrustworthy, so the model is not adopted; the
		# two sides disagree on the closing, which is recorded instead of hidden.
		model = FakeModel({"ok": True, "opening": 1000.0, "closing": 9999.0, "rows": [
			statement_row(description="a", deposit=100.0, balance=100.0),
			statement_row(description="b", deposit=100.0, balance=100.0),
			statement_row(description="c", deposit=100.0, balance=100.0),
		]})
		result = bank_intake.read_statement_file(workbook([("s", [
			STATEMENT_HEADER,
			["01/06/2569", "ยกมา", "", "", 1000.0],
			["02/06/2569", "โอนเงิน", "", 100.0, 5000.0],
			["03/06/2569", "โอนเงิน", "", 100.0, 5000.0],
			["04/06/2569", "โอนเงิน", "", 100.0, 5000.0],
		])]), "stm.xlsx", model_rows=model)
		self.assertEqual(result["source"], "workbook")
		self.assertEqual(result["opening"], 1000.0)
		self.assertEqual(result["method_divergence"],
			["期末两法分歧:免费解析 5000.0 vs Gemini 9999.0 — 需人工核票面"])
		self.assertFalse(result["completeness"]["ok"])

	def test_an_unreadable_file_reports_the_reader_error(self):
		# The statement reader has no "read it as CSV whatever it says" fallback, so
		# bytes that are not a workbook are unreadable rather than headerless.
		result = bank_intake.read_statement_file(b"not a workbook at all", "stm.xlsx")
		self.assertFalse(result["ok"])
		self.assertEqual(result["error_code"], "file_unreadable")
		self.assertEqual(result["rows"], [])
		headerless = bank_intake.read_statement_file(workbook([("s", [["hello", "world"]])]),
			"stm.xlsx")
		self.assertFalse(headerless["ok"])
		self.assertEqual(headerless["error_code"], "stmt_headers_not_found")

	def test_the_model_takes_over_when_the_readers_refuse(self):
		model = FakeModel({"ok": True, "opening": 0.0, "closing": 200.0, "rows": [
			statement_row(description="a", deposit=100.0, balance=100.0),
			statement_row(description="b", deposit=100.0, balance=200.0),
			statement_row(description="c", deposit=0.0, balance=200.0),
		]})
		result = bank_intake.read_statement_file(b"not a workbook at all", "stm.xlsx",
			model_rows=model)
		self.assertTrue(result["ok"])
		self.assertEqual(result["source"], "model")
		self.assertEqual(model.calls, [("bank_statement", "stm.xlsx", 21)])

	def test_a_csv_statement_is_read_by_the_workbook_reader(self):
		content = ("วันที่;รายการ;ถอนเงิน;ฝากเงิน;ยอดคงเหลือ\n"
			"01/06/2569;โอนเงิน;;100;1100\n"
			"02/06/2569;ค่าอาหาร;50;;1050\n"
			"03/06/2569;โอนออก;25;;1025\n").encode("cp874")
		result = bank_intake.read_statement_file(content, "stm.csv")
		self.assertEqual(result["source"], "workbook")
		self.assertEqual([row.balance for row in result["rows"]], [1100.0, 1050.0, 1025.0])

	def test_an_unsupported_format_needs_the_model(self):
		result = bank_intake.read_statement_file(b"whatever", "stm.docx")
		self.assertFalse(result["ok"])
		self.assertEqual(result["error_code"], "unsupported_format")


class PdfStatement(unittest.TestCase):
	LINES = ["01/06/69 TRANSFER IN 100.00 1,100.00",
		"02/06/69 SERVICE FEE 50.00 1,050.00"]

	def test_the_text_layer_is_read_without_a_model(self):
		result = bank_intake.read_statement_file(pdf_with_lines(self.LINES), "stm.pdf")
		self.assertTrue(result["ok"])
		self.assertEqual(result["source"], "text_lines")
		self.assertEqual(result["row_count"], 2)
		self.assertEqual(result["closing"], 1050.0)

	def test_a_kbank_pdf_uses_the_column_reader(self):
		# The columns are flattened onto their own lines. The text itself is fed
		# through the real seam (a PDF cannot carry this Thai layout when it is
		# hand-built), and the filename carries the bank, which is what routes the
		# flattened layout to the column reader.
		from unittest.mock import patch

		text = ("K BANK\n"
			"01/06/69\n"
			"\u0e23\u0e32\u0e22\u0e25\u0e30\u0e40\u0e2d\u0e35\u0e22\u0e14\n"
			"12,345.67\n"
			"02/06/69\n"
			"10:00\n"
			"\u0e23\u0e31\u0e1a\u0e42\u0e2d\u0e19\u0e40\u0e07\u0e34\u0e19\n"
			"100.00\n"
			"1,100.00 K PLUS\n"
			"03/06/69\n"
			"11:00\n"
			"\u0e42\u0e2d\u0e19\u0e40\u0e07\u0e34\u0e19\n"
			"50.00\n"
			"1,050.00 K PLUS\n")
		with patch.object(bank_intake.pdf_text, "extract_pdf_text_pages", return_value=[text]):
			result = bank_intake.read_statement_file(b"%PDF-1.4 fake", "stm_kbank.pdf")
		self.assertTrue(result["ok"])
		self.assertEqual(result["source"], "kbank_columns")
		self.assertEqual((result["opening"], result["closing"]), (12345.67, 1050.0))
		self.assertEqual(result["row_count"], 2)

	def test_a_pdf_with_no_text_says_so(self):
		result = bank_intake.read_statement_file(pdf_with_lines([]), "stm.pdf")
		self.assertFalse(result["ok"])
		self.assertEqual(result["error_code"], "no_rows")
		self.assertIn("no extractable text", result["error"])


class RereadHook(unittest.TestCase):
	# The other eye only exists for PDFs: a workbook was read cell by cell.
	LINES = ["01/06/69 TRANSFER IN 100.00 1,100.00",
		"02/06/69 SERVICE FEE 50.00 9,999.00"]

	def test_a_broken_chain_is_reread_when_a_page_reader_is_supplied(self):
		pages = {1: [statement_row(description="TRANSFER IN", deposit=100.0, balance=1100.0),
			statement_row(description="SERVICE FEE", withdrawal=50.0, balance=1050.0)]}
		result = bank_intake.read_statement_file(pdf_with_lines(self.LINES), "stm.pdf",
			read_page=lambda page: pages[page], page_count=1)
		self.assertEqual(result["source"], "text_lines")
		self.assertEqual([row.balance for row in result["rows"]], [1100.0, 1050.0])
		self.assertEqual(len(result["escalations"]), 1)
		self.assertTrue(result["escalations"][0]["kept"])
		self.assertEqual(result["escalations"][0]["breaks_before"], 1)

	def test_without_a_page_reader_the_rows_stay_as_read(self):
		result = bank_intake.read_statement_file(pdf_with_lines(self.LINES), "stm.pdf")
		self.assertEqual([row.balance for row in result["rows"]], [1100.0, 9999.0])
		self.assertNotIn("escalations", result)
		self.assertEqual(result["balance_warn_count"], 1)

	def test_a_workbook_is_never_reread(self):
		result = bank_intake.read_statement_file(workbook([("s", [
			STATEMENT_HEADER,
			["01/06/2569", "ยกมา", "", "", 1000.0],
			["02/06/2569", "โอนเงิน", "", 100.0, 1100.0],
			["03/06/2569", "ค่าอาหาร", 50.0, "", 9999.0],
		])]), "stm.xlsx", read_page=lambda page: [], page_count=1)
		self.assertNotIn("escalations", result)
		self.assertEqual(result["balance_warn_count"], 1)


class OpeningBackfill(unittest.TestCase):
	def test_an_explicit_brought_forward_row_fills_the_opening(self):
		model = FakeModel({"ok": True, "rows": [
			statement_row(description="brought forward", balance=500.0),
			statement_row(description="a", deposit=100.0, balance=600.0),
		]})
		result = bank_intake.read_statement_file(b"x", "stm.docx", model_rows=model)
		self.assertEqual(result["opening"], 500.0)

	def test_the_first_movement_back_derives_the_opening(self):
		model = FakeModel({"ok": True, "closing": 600.0, "rows": [
			statement_row(description="a", deposit=100.0, balance=600.0),
		]})
		result = bank_intake.read_statement_file(b"x", "stm.docx", model_rows=model)
		self.assertEqual(result["opening"], 500.0)

	def test_a_failed_model_leaves_the_reader_error_in_place(self):
		model = FakeModel({"ok": False, "error": "gemini statement parse failed (all models)"})
		result = bank_intake.read_statement_file(b"not a workbook", "stm.xlsx", model_rows=model)
		self.assertFalse(result["ok"])
		self.assertEqual(result["error_code"], "file_unreadable")


class LedgerIntake(unittest.TestCase):
	def test_a_ledger_workbook_is_read_without_a_model(self):
		model = FakeModel({"ok": True, "rows": []}, "general_ledger")
		result = bank_intake.read_ledger_file(workbook([("gl", [
			GL_HEADER,
			["01/06/2569", "PV-1", "ค่าอาหาร 5301", 100.0, "", 1100.0, "5301"],
			["02/06/2569", "PV-2", "โอนเงิน", "", 50.0, 1050.0, "5301"],
		])]), "gl.xlsx", model_rows=model)
		self.assertTrue(result["ok"])
		self.assertEqual(result["source"], "workbook")
		self.assertEqual(model.calls, [])
		self.assertEqual(result["accounts"], ["5301"])
		self.assertEqual([row.source_file for row in result["rows"]], ["gl.xlsx", "gl.xlsx"])

	def test_the_account_filter_reaches_the_reader(self):
		result = bank_intake.read_ledger_file(workbook([("gl", [
			GL_HEADER,
			["01/06/2569", "PV-1", "ค่าเช่า", 100.0, "", 1100.0, "5301-01"],
			["02/06/2569", "PV-2", "ค่าอาหาร", 200.0, "", 1300.0, "6201-01"],
		])]), "gl.xlsx", account_code="5301")
		self.assertEqual([row.account_code for row in result["rows"]], ["5301-01"])

	def test_the_model_takes_over_when_the_ledger_has_no_header(self):
		model = FakeModel({"ok": True, "opening": 1000.0, "closing": 900.0, "rows": [
			bank_intake.GlRow(date=bank_parse.parse_date("2026-06-01"), doc_no="PV-1",
				account_code="5301", description="a", debit=0.0, credit=100.0),
		], "accounts": ["5301"]}, "general_ledger")
		result = bank_intake.read_ledger_file(workbook([("gl", [
			["Date", "Description", "Amount", "Balance"],
			["01/06/2569", "net only", 100.0, 100.0],
		])]), "gl.xlsx", model_rows=model)
		self.assertTrue(result["ok"])
		self.assertEqual(result["source"], "model")
		self.assertEqual(model.calls[0][0], "general_ledger")
		self.assertEqual(result["accounts"], ["5301"])

	def test_a_pdf_ledger_needs_the_model(self):
		lines = ["01/06/69 PV-1 fuel 100.00", "02/06/69 PV-2 fee 50.00"]
		result = bank_intake.read_ledger_file(pdf_with_lines(lines), "gl.pdf")
		self.assertFalse(result["ok"])
		self.assertEqual(result["error_code"], "model_unavailable")
		model = FakeModel({"ok": True, "opening": 0.0, "closing": 50.0, "rows": [
			bank_intake.GlRow(date=bank_parse.parse_date("2026-06-01"), doc_no="PV-1",
				account_code="", description="fuel", debit=100.0, credit=0.0),
		], "accounts": []}, "general_ledger")
		result = bank_intake.read_ledger_file(pdf_with_lines(lines), "gl.pdf", model_rows=model)
		self.assertTrue(result["ok"])
		self.assertEqual(result["source"], "model")

	def test_a_model_failure_is_reported(self):
		model = FakeModel({"ok": False, "error": "gemini returned no usable rows"},
			"general_ledger")
		result = bank_intake.read_ledger_file(pdf_with_lines(["x"]), "gl.pdf", model_rows=model)
		self.assertFalse(result["ok"])
		self.assertEqual(result["error"], "gemini returned no usable rows")

	def test_an_unsupported_ledger_format_needs_the_model(self):
		result = bank_intake.read_ledger_file(b"x", "gl.docx")
		self.assertFalse(result["ok"])
		self.assertEqual(result["error_code"], "unsupported_format")
		self.assertEqual(result["accounts"], [])

	def test_a_reader_that_finds_no_header_reports_its_code(self):
		result = bank_intake.read_ledger_file(workbook([("gl", [
			["Date", "Description", "Amount", "Balance"],
			["01/06/2569", "net only", 100.0, 100.0],
		])]), "gl.xlsx")
		self.assertFalse(result["ok"])
		self.assertEqual(result["error_code"], "gl_headers_not_found")


if __name__ == "__main__":
	unittest.main()
