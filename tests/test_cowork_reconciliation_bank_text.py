"""Offline regressions for the migrated text-line readers and the PDF text layer.

Runs in the lab bench (pdfminer/pypdf and the reconciliation package need the
bench environment). Every fixture is built in memory; nothing is read from disk
and no network or model call happens.
"""

import unittest

from pearnly_cowork.reconciliation import bank_text, table_io


def minimal_pdf(text):
	"""A one-page PDF carrying ``text`` in its text layer.

	Hand-built because the bench has no PDF writer: an uncompressed content stream
	with a single Helvetica line is enough for both extractors, and pdfminer
	tolerates the flat layout.
	"""
	objects = [
		b"<< /Type /Catalog /Pages 2 0 R >>",
		b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
		b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
	]
	stream = ("BT /F1 12 Tf 20 100 Td (%s) Tj ET" % text).encode("latin-1")
	objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
		+ stream + b"\nendstream")
	objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
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
		+ b"startxref\n" + str(xref).encode() + b"\n%%EOF\n")
	return bytes(out)


class PdfTextLayer(unittest.TestCase):
	def test_the_text_layer_of_a_pdf_is_read(self):
		pages = table_io.extract_pdf_text_pages(minimal_pdf("hello pearnly"))
		self.assertEqual(len(pages), 1)
		self.assertIn("hello pearnly", pages[0])

	def test_bytes_that_are_not_a_pdf_return_nothing(self):
		self.assertEqual(table_io.extract_pdf_text_pages(b"not a pdf at all"), [])
		self.assertEqual(table_io.extract_pdf_text_pages(b""), [])


class TextLines(unittest.TestCase):
	def test_a_line_per_transaction_with_balance_driven_direction(self):
		rows, opening, closing = bank_text.parse_statement_text_lines(
			"ยอดยกมา 1,000.00\n"
			"01/06/69 โอนเงิน 100.00 1,100.00\n"
			"02/06/69 ค่าอาหาร 50.00 1,050.00\n"
			"ยอดยกไป 1,050.00\n")
		self.assertEqual((opening, closing), (1000.0, 1050.0))
		self.assertEqual(len(rows), 2)
		self.assertEqual((rows[0].deposit, rows[0].withdrawal), (100.0, 0.0))
		self.assertEqual((rows[1].deposit, rows[1].withdrawal), (0.0, 50.0))
		self.assertEqual(rows[0].description, "โอนเงิน")
		self.assertEqual(rows[0].date.isoformat(), "2026-06-01")

	def test_a_description_hint_decides_when_the_balance_does_not_add_up(self):
		# The printed balance movement does not match the amount, so the deployed
		# reader falls back to the words in the description.
		rows, _opening, _closing = bank_text.parse_statement_text_lines(
			"ยอดยกมา 1,000.00\n"
			"01/06/69 ถอนเงินสด 100.00 999.00\n"
			"02/06/69 โอนเงิน 100.00 1,099.00\n")
		self.assertEqual((rows[0].withdrawal, rows[0].deposit), (100.0, 0.0))
		self.assertEqual((rows[1].withdrawal, rows[1].deposit), (0.0, 100.0))

	def test_the_first_row_without_a_previous_balance_counts_as_a_deposit(self):
		rows, opening, _closing = bank_text.parse_statement_text_lines(
			"01/06/69 โอนเงิน 100.00 1,100.00\n")
		self.assertEqual(rows[0].deposit, 100.0)
		# The opening is inferred from the first row: balance - deposit.
		self.assertEqual(opening, 1000.0)

	def test_a_thai_dot_as_thousands_separator_is_read(self):
		rows, _opening, _closing = bank_text.parse_statement_text_lines(
			"ยอดยกมา 115.586.50\n"
			"01/06/69 โอนเงิน 100.00 115.686.50\n")
		self.assertEqual(rows[0].balance, 115686.5)

	def test_lines_that_cannot_be_transactions_are_skipped(self):
		rows, opening, closing = bank_text.parse_statement_text_lines(
			"หัวข้อรายงาน\n"
			"01/06/69\n"
			"ไม่ใช่วันที่ โอนเงิน 100.00 1,100.00\n"
			"31/13/69 โอนเงิน 100.00 1,100.00\n"
			"01/06/69 ไม่มีตัวเลข\n")
		self.assertEqual((rows, opening, closing), ([], 0.0, 0.0))

	def test_a_zero_movement_line_is_not_a_transaction(self):
		rows, _opening, _closing = bank_text.parse_statement_text_lines(
			"ยอดยกมา 1,000.00\n"
			"01/06/69 โอนเงิน 0.00 1,000.00\n")
		self.assertEqual(rows, [])

	def test_the_closing_falls_back_to_the_last_row(self):
		_rows, _opening, closing = bank_text.parse_statement_text_lines(
			"ยอดยกมา 1,000.00\n"
			"01/06/69 โอนเงิน 100.00 1,100.00\n")
		self.assertEqual(closing, 1100.0)

	def test_empty_text_returns_nothing(self):
		self.assertEqual(bank_text.parse_statement_text_lines(""), ([], 0.0, 0.0))


class KbankStackedColumns(unittest.TestCase):
	COLUMN_STACKED = (
		"K BANK\n"
		"เลขที่บัญชี 123-4-56789-0\n"
		"01/06/69\n"
		"รายละเอียด\n"
		"12,345.67\n"
		"02/06/69\n"
		"10:00\n"
		"รับโอนเงิน\n"
		"100.00\n"
		"1,100.00 K PLUS\n"
		"03/06/69\n"
		"11:00\n"
		"โอนเงิน\n"
		"50.00\n"
		"1,050.00 K PLUS\n"
	)

	def test_the_transactions_are_paired_with_their_values_by_position(self):
		rows, opening, closing = bank_text.parse_kbank_text_columns(self.COLUMN_STACKED)
		self.assertEqual((opening, closing), (12345.67, 1050.0))
		self.assertEqual([row.date.isoformat() for row in rows],
			["2026-06-02", "2026-06-03"])
		self.assertEqual((rows[0].deposit, rows[0].withdrawal), (100.0, 0.0))
		self.assertEqual((rows[1].deposit, rows[1].withdrawal), (0.0, 50.0))

	def test_the_channel_joins_the_description(self):
		rows, _opening, _closing = bank_text.parse_kbank_text_columns(self.COLUMN_STACKED)
		self.assertEqual(rows[0].description, "รับโอนเงิน K PLUS")

	def test_text_without_a_transaction_returns_nothing(self):
		self.assertEqual(bank_text.parse_kbank_text_columns("K BANK\nno dates here\n"),
			([], 0.0, 0.0))
		self.assertEqual(bank_text.parse_kbank_text_columns(""), ([], 0.0, 0.0))


if __name__ == "__main__":
	unittest.main()
