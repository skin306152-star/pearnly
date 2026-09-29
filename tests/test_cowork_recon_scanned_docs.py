"""对账三类文档的扫描件与照片（2026-09-29）：逐页取字 → 文档识别 → 同一套行与校验。

模型与 Vision 都是假的，不连服务商、不付费；页面来源用假对象，不渲染真 PDF。
"""
import json
import unittest
from unittest import mock

import frappe

from pearnly_cowork.ocr.engine import document_tier
from pearnly_cowork.ocr.engine.contracts import Frame, ModelResponse, VisionResponse
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.schema.evidence import Page
from pearnly_cowork.recon_center import document_ocr, sources


def bank_page(opening, entries, closing=""):
	return {"document_type": "bank_statement", "bank_name": "Bangkok Bank", "opening_balance": opening,
		"closing_balance": closing, "entries": [
			{"transaction_date": date, "description": text, "deposit": deposit, "withdrawal": withdrawal,
			 "balance": balance} for date, text, deposit, withdrawal, balance in entries]}


VAT_PAGE = {"document_type": "vat_report", "seller_name": "ACME", "entries": [
	{"seq_no": "1", "transaction_date": "2026-06-01", "invoice_no": "IV001", "customer_name": "B Co",
	 "customer_tax": "0105556012345", "customer_branch": "00000", "subtotal": "1000.00", "vat": "70.00",
	 "total": "1070.00"}]}
GL_PAGE = {"document_type": "general_ledger", "entries": [
	{"transaction_date": "2026-06-01", "voucher_no": "IV001", "account_code": "4110", "description": "sale",
	 "credit": "1000.00", "balance": "1000.00"}]}


class FakeModel:
	def __init__(self, *payloads):
		self.payloads, self.requests = list(payloads), []

	def __call__(self, request):
		self.requests.append(request)
		payload = self.payloads[min(len(self.requests) - 1, len(self.payloads) - 1)]
		return ModelResponse(text=json.dumps(payload), raw={}, input_tokens=1, output_tokens=1, model=request.model)


class FakeSource:
	"""页面来源：``texts`` 为 None 表示没有文字层（整份扫描件）。"""

	def __init__(self, count, texts=None):
		self.page_count, self.rendered = count, []
		self.text_pages = None if texts is None else tuple(
			Page(page_number=index, width=0, height=0, full_text=text, avg_confidence=1.0, blocks=[])
			for index, text in enumerate(texts, 1))

	def frame(self, number):
		self.rendered.append(number)
		return Frame(image_bytes=b"png-%d" % number, mime_type="image/png")


def vision(frame, *, stage, page_number):
	return VisionResponse(page=Page(page_number=page_number, width=1, height=1,
		full_text=f"vision text of page {page_number}", avg_confidence=0.9, blocks=[]), raw={})


class ReadPages(unittest.TestCase):
	def test_a_multi_page_statement_is_merged_into_one_document(self):
		model = FakeModel(bank_page("1000.00", [("2026-06-01", "fee", "", "100.00", "900.00")]),
			bank_page("", [("2026-06-02", "deposit", "500.00", "", "1400.00")], closing="1400.00"))
		legacy, warnings = document_tier.read_pages("bank_statement",
			[(1, "page one", ()), (2, "page two", ())], model, model="strong")
		document = legacy["pages"][0]["document"]
		self.assertEqual(len(legacy["pages"]), 1)
		self.assertEqual([entry["balance"] for entry in document["entries"]], ["900.00", "1400.00"])
		self.assertEqual((document["opening_balance"], document["closing_balance"]), ("1000.00", "1400.00"))
		# 续页没印期初：接上一页最后余额，余额链不断，不付钱升级重读
		self.assertEqual(len(model.requests), 2)
		self.assertEqual({request.model for request in model.requests}, {"strong"})

	def test_ledger_and_vat_pages_stay_one_per_page(self):
		legacy, _ = document_tier.read_pages("vat_report", [(1, "a", ()), (2, "b", ())], FakeModel(VAT_PAGE))
		self.assertEqual([page["page_number"] for page in legacy["pages"]], [1, 2])
		self.assertEqual(legacy["pages"][0]["document"]["entries"][0]["invoice_no"], "IV001")

	def test_a_blank_page_is_skipped_and_an_all_blank_file_is_unreadable(self):
		model = FakeModel(GL_PAGE)
		legacy, _ = document_tier.read_pages("general_ledger", [(1, " ", ()), (2, "text", ())], model)
		self.assertEqual([page["page_number"] for page in legacy["pages"]], [2])
		self.assertEqual(len(model.requests), 1)
		with self.assertRaises(ContentError):
			document_tier.read_pages("general_ledger", [(1, "", ())], FakeModel(GL_PAGE))

	def test_the_page_picture_goes_with_the_text_when_given(self):
		model = FakeModel(VAT_PAGE)
		document_tier.read_pages("vat_report", [(1, "text", ((b"img", "image/png"),))], model)
		self.assertEqual(model.requests[0].images, ((b"img", "image/png"),))


class DocumentOcrPages(unittest.TestCase):
	def test_a_scan_is_rendered_page_by_page_and_read_from_vision_text(self):
		source, model = FakeSource(3), FakeModel(GL_PAGE)
		legacy = document_ocr.read_pages(source, "general_ledger", model, vision, model="m", escalated_model="m")
		self.assertEqual(source.rendered, [1, 2, 3])
		self.assertEqual(len(legacy["pages"]), 3)
		self.assertIn("vision text of page 2", model.requests[1].prompt)
		# 多页扫描件只送文字（旧线上 Vision 路），不把每页图都塞给模型
		self.assertTrue(all(request.images == () for request in model.requests))

	def test_a_single_photo_is_read_with_its_picture(self):
		source, model = FakeSource(1), FakeModel(VAT_PAGE)
		document_ocr.read_pages(source, "vat_report", model, vision, model="m", escalated_model="m")
		self.assertEqual(model.requests[0].images, ((b"png-1", "image/png"),))

	def test_an_electronic_pdf_uses_its_text_layer_without_vision(self):
		source, model = FakeSource(2, texts=["page one text", "page two text"]), FakeModel(GL_PAGE)
		document_ocr.read_pages(source, "general_ledger", model, vision, model="m", escalated_model="m")
		self.assertEqual(source.rendered, [])
		self.assertIn("page one text", model.requests[0].prompt)

	def test_rows_found_decides_billing(self):
		self.assertTrue(document_ocr._rows_found({"pages": [{"document": {"entries": [{}]}}]}))
		self.assertFalse(document_ocr._rows_found({"pages": [{"document": {"entries": []}}]}))
		self.assertFalse(document_ocr._rows_found(None))


class SourcesRouteToTheReader(unittest.TestCase):
	def reader(self, legacy):
		calls = []

		def read(document_type, content, filename):
			calls.append((document_type, filename))
			return legacy
		return read, calls

	def test_a_photo_of_a_ledger_and_of_a_vat_report_go_to_the_reader(self):
		read, calls = self.reader({"pages": [{"document": GL_PAGE}]})
		self.assertEqual(sources.ledger_source(b"img", "gl.jpg", read)["kind"], "pipeline")
		read, calls2 = self.reader({"pages": [{"document": VAT_PAGE}]})
		self.assertEqual(sources.report_source(b"img", "vat.png", read)["kind"], "pipeline")
		self.assertEqual((calls[0][0], calls2[0][0]), ("general_ledger", "vat_report"))

	def test_a_scanned_pdf_goes_to_the_reader(self):
		read, calls = self.reader({"pages": [{"document": VAT_PAGE}]})
		with mock.patch.object(sources.document_tier, "text_pages", return_value=["", ""]):
			self.assertEqual(sources.report_source(b"%PDF", "vat.pdf", read)["kind"], "pipeline")
		self.assertEqual(calls, [("vat_report", "vat.pdf")])

	def test_an_electronic_vat_report_with_enough_rows_is_read_for_free(self):
		read, calls = self.reader({"pages": [{"document": VAT_PAGE}]})
		lines = "รายงานภาษีขาย บริษัท ทดสอบ จำกัด เดือน มิถุนายน 2569 " * 8 + "\n" + "\n".join(
			f"0{day}/06/2569 IV00{day} OHANAHAN 1,000.00 70.00" for day in range(1, 5))
		with mock.patch.object(sources.document_tier, "text_pages", return_value=[lines]):
			source = sources.report_source(b"%PDF", "vat.pdf", read)
		self.assertEqual((source["kind"], calls), ("pdf", []))

	def test_without_a_reader_a_photo_is_still_refused(self):
		with mock.patch.object(sources.frappe, "_", side_effect=lambda text: text), \
				mock.patch.object(sources.frappe, "throw", side_effect=frappe.ValidationError) as refused:
			with self.assertRaises(frappe.ValidationError):
				sources.report_source(b"img", "vat.png")
		self.assertIn("file_type_unsupported", str(refused.call_args))


class BankSeam(unittest.TestCase):
	def test_the_bank_seam_uses_the_durable_reader(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
			document_model_rows,
		)
		legacy = document_tier.document_to_legacy(document_tier.decode_document("bank_statement", bank_page(
			"1000.00", [("2026-06-01", "fee", "", "100.00", "900.00"), ("2026-06-02", "in", "500.00", "", "1400.00"),
			("2026-06-03", "out", "", "400.00", "1000.00")], closing="1000.00")), "bank_statement")
		seen = []
		rows = document_model_rows(read=lambda document_type, content, filename: seen.append(document_type) or legacy)
		result = rows("bank_statement", b"img", "stmt.jpg")
		self.assertEqual(seen, ["bank_statement"])
		self.assertTrue(result["ok"])
		self.assertEqual(len(result["rows"]), 3)
		self.assertIsNone(document_model_rows()("bank_statement", b"img", "stmt.jpg"))


if __name__ == "__main__":
	unittest.main()
