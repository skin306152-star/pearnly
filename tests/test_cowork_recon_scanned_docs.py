"""对账三类文档的扫描件与照片（2026-09-29）：企业档 Document AI 读字 → 本地拼行 / 3.8 契约抽取 → 同一套行。

Document AI 与模型都是假的，不连服务商、不付费；页面来源用假对象，不渲染真 PDF。
"""
import json
import unittest
from datetime import date
from unittest import mock

import frappe

from pearnly_cowork.ocr.engine.contracts import Frame, ModelResponse
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.enterprise import contract, pages as enterprise_pages, pipeline
from pearnly_cowork.recon_center import document_ocr, sources


def payload(*texts):
	"""A Document AI answer for one read: one page per text, anchored into the document text."""
	full, page_list, start = "", [], 0
	for number, text in enumerate(texts, 1):
		full += text
		page_list.append({"pageNumber": number, "layout": {"textAnchor": {"textSegments": [
			{"startIndex": str(start), "endIndex": str(start + len(text))}]}}})
		start += len(text)
	return {"document": {"text": full, "pages": page_list}}


class FakeDocumentAi:
	def __init__(self, *answers):
		self.answers, self.calls = list(answers), []

	def __call__(self, content, mime, *, expected_pages, page_number=1):
		self.calls.append((mime, expected_pages))
		return self.answers[len(self.calls) - 1], 1, "key"


class FakeModel:
	def __init__(self, *payloads):
		self.payloads, self.requests = list(payloads), []

	def __call__(self, request):
		self.requests.append(request)
		data = self.payloads[min(len(self.requests) - 1, len(self.payloads) - 1)]
		return ModelResponse(text=json.dumps(data), raw={}, input_tokens=10, output_tokens=5, model=request.model)


VAT_PAGE = {"seller_name": "ACME", "seller_tax": "", "period_year": "2026", "period_month": "06", "total_subtotal": "",
	"total_vat": "", "total_total": "", "entries": [
		{"seq_no": "1", "transaction_date": "2026-06-01", "invoice_no": "IV001", "customer_name": "B Co",
		 "customer_tax": "0105556012345", "customer_branch": "00000", "subtotal": "1000.00", "vat": "70.00",
		 "total": "1070.00"}]}


class Rendered:
	def __init__(self):
		self.pages = []

	def __call__(self, number):
		self.pages.append(number)
		return b"png-%d" % number, "image/png"


class EnterprisePipeline(unittest.TestCase):
	def test_a_vat_report_goes_page_by_page_through_the_frozen_contract(self):
		docai, model, render = FakeDocumentAi(payload("page one text", "page two text")), FakeModel(VAT_PAGE), Rendered()
		result = pipeline.run("vat", page_count=2, reads=[(b"%PDF", "application/pdf", 2)], render=render,
			document_ai_call=docai, model_call=model)
		self.assertEqual(docai.calls, [("application/pdf", 2)])
		self.assertEqual(render.pages, [1, 2])
		first = model.requests[0]
		self.assertEqual((first.model, first.thinking, first.max_output_tokens),
			(contract.MODEL, contract.THINKING_LEVEL, contract.MAX_OUTPUT_TOKENS))
		self.assertEqual(first.response_schema, contract.SCHEMAS["vat"])
		self.assertEqual(first.images, ((b"png-1", "image/png"),))
		self.assertIn("page one text", first.prompt)
		self.assertIn("page two text", model.requests[1].prompt)
		legacy = pipeline.to_legacy(result)
		self.assertEqual(legacy["engine"], "enterprise-2026-09-03-v1")
		self.assertEqual([page["document_type"] for page in legacy["pages"]], ["vat_report", "vat_report"])
		self.assertEqual(legacy["pages"][0]["document"]["entries"][0]["invoice_no"], "IV001")
		# Document AI 按页计：0.0015 美元 × 35
		self.assertAlmostEqual(result.estimated_cost_thb, 2 * 0.0015 * 35)

	def test_a_statement_the_geometry_cannot_balance_is_extracted_on_every_page(self):
		bank_page = {"bank_name": "", "account_number": "", "period_start": "", "period_end": "", "opening_balance": "1000",
			"closing_balance": "900", "total_deposit": "", "total_withdrawal": "", "entries": [
				{"transaction_date": "2026-06-01", "description": "fee", "reference": "", "deposit": "", "withdrawal": "100",
				 "balance": "900"}]}
		model, render = FakeModel(bank_page), Rendered()
		result = pipeline.run("bank", page_count=1, reads=[(b"img", "image/jpeg", 1)], render=render,
			document_ai_call=FakeDocumentAi(payload("no geometry here")), model_call=model)
		self.assertEqual(render.pages, [1])
		self.assertEqual(len(model.requests), 1)
		self.assertEqual(result.pages[0].document.entries[0].withdrawal, "100")

	def test_an_unreadable_extraction_fails_the_file(self):
		with self.assertRaises(ContentError):
			pipeline.run("vat", page_count=1, reads=[(b"img", "image/png", 1)], render=Rendered(),
				document_ai_call=FakeDocumentAi(payload("x")), model_call=FakeModel([]))

	def test_pdf_chunks_are_at_most_fifteen_pages(self):
		import pymupdf

		with pymupdf.open() as document:
			for _ in range(32):
				document.new_page()
			content = document.tobytes()
		self.assertEqual([pages for _c, _m, pages in pipeline.pdf_chunks(content, 32)], [15, 15, 2])

	def test_page_texts_follow_the_anchors(self):
		self.assertEqual([page.text for page in enterprise_pages.page_texts(payload("aa", "bbb"))], ["aa", "bbb"])
		with self.assertRaises(ContentError):
			enterprise_pages.page_texts({"document": {"text": "", "pages": []}})


class DocumentOcrReadFile(unittest.TestCase):
	def test_an_image_is_read_once_and_rendered_only_for_extraction(self):
		class Source:
			page_count = 1

			def frame(self, number):
				return Frame(image_bytes=b"oriented", mime_type="image/jpeg")

		docai, model = FakeDocumentAi(payload("vat text")), FakeModel(VAT_PAGE)
		legacy = document_ocr.read_file(Source(), b"raw", False, "image/jpeg", "vat_report", docai, model)
		self.assertEqual(docai.calls, [("image/jpeg", 1)])
		self.assertEqual(model.requests[0].images, ((b"oriented", "image/jpeg"),))
		self.assertTrue(document_ocr._rows_found(legacy))
		self.assertFalse(document_ocr._rows_found({"pages": [{"document": {"entries": []}}]}))


class SourcesRouteToTheReader(unittest.TestCase):
	def reader(self, legacy):
		calls = []

		def read(document_type, content, filename):
			calls.append((document_type, filename))
			return legacy
		return read, calls

	def test_photos_and_scans_go_to_the_reader_with_the_engine(self):
		legacy = {"pages": [{"document": VAT_PAGE}], "engine": "enterprise-2026-09-03-v1"}
		read, calls = self.reader(legacy)
		source = sources.report_source(b"img", "vat.png", read)
		self.assertEqual((source["kind"], source["engine"]), ("pipeline", "enterprise-2026-09-03-v1"))
		with mock.patch.object(sources.document_tier, "text_pages", return_value=["", ""]):
			self.assertEqual(sources.ledger_source(b"%PDF", "gl.pdf", read)["kind"], "pipeline")
		self.assertEqual([call[0] for call in calls], ["vat_report", "general_ledger"])

	def test_an_electronic_vat_report_with_enough_rows_is_read_for_free(self):
		read, calls = self.reader({"pages": []})
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
	def test_enterprise_rows_are_final_and_keep_every_page(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
			document_model_rows,
		)
		legacy = {"engine": "enterprise-2026-09-03-v1", "_needs_review": False, "pages": [
			{"needs_manual_review": False, "document": {"opening_balance": "1000", "closing_balance": "900", "entries": [
				{"transaction_date": "2026-06-01", "description": "fee", "withdrawal": "100", "deposit": "",
				 "balance": "900", "chain_amount_imputed": True}]}},
			{"needs_manual_review": True, "document": {"opening_balance": "900", "closing_balance": "1400", "entries": [
				{"transaction_date": "2026-06-02", "description": "in", "withdrawal": "", "deposit": "500",
				 "balance": "1400"}]}}]}
		result = document_model_rows(read=lambda *_args: legacy)("bank_statement", b"img", "stmt.jpg")
		self.assertTrue(result["enterprise"])
		self.assertEqual((result["opening"], result["closing"], result["row_count"]), (1000.0, 1400.0, 2))
		self.assertEqual([row.confidence for row in result["rows"]], ["medium", "low"])
		self.assertTrue(result["rows"][0].amount_autocorrected)
		self.assertIsNone(document_model_rows()("bank_statement", b"img", "stmt.jpg"))

	def test_the_intake_does_not_repair_enterprise_rows_again(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
			enterprise_rows,
		)
		from pearnly_cowork.reconciliation import bank_intake

		legacy = {"engine": "enterprise-2026-09-03-v1", "pages": [{"document": {"opening_balance": "1000",
			"closing_balance": "900", "entries": [{"transaction_date": "2026-06-01", "description": "x",
			"withdrawal": "100", "deposit": "", "balance": "900"}]}}]}
		model = enterprise_rows(legacy, "bank_statement", "stmt.jpg")
		with mock.patch.object(bank_intake.bank_statement, "finalize_rows", side_effect=AssertionError("repaired twice")):
			result = bank_intake.read_statement_file(b"img", "stmt.jpg", model_rows=lambda *_a: model)
		self.assertTrue(result["ok"])
		self.assertEqual(result["source"], "enterprise")


class SeveralFilesPerSide(unittest.TestCase):
	def statement(self, rows, ok=True, opening=0.0, closing=0.0):
		from pearnly_cowork.reconciliation.bank_statement import StatementRow

		return {"ok": ok, "rows": [StatementRow(date=date, description=text, withdrawal=out, deposit=into, balance=balance)
			for date, text, out, into, balance in rows], "opening": opening, "closing": closing, "bank_code": "kbank",
			"source": "enterprise", "parser_version": "enterprise-2026-09-03-v1", "balance_warn_count": 0,
			"amount_fixed_count": 0, "low_conf_count": len(rows), "completeness": {"ok": True}, "needs_review": False}

	def test_one_file_is_kept_exactly_as_read(self):
		from pearnly_cowork.reconciliation import bank_recon_service

		one = self.statement([(date(2026, 6, 1), "a", 100.0, 0.0, 900.0)], opening=1000.0, closing=900.0)
		self.assertIs(bank_recon_service._merged_statement([one]), one)

	def test_passbook_photos_are_merged_by_date_and_deduplicated(self):
		from pearnly_cowork.reconciliation import bank_recon_service

		first = self.statement([(date(2026, 6, 1), "a", 100.0, 0.0, 900.0), (date(2026, 6, 3), "b", 0.0, 500.0, 1400.0)],
			opening=1000.0, closing=1400.0)
		second = self.statement([(date(2026, 6, 3), "b", 0.0, 500.0, 1400.0), (date(2026, 6, 5), "c", 200.0, 0.0, 1200.0)],
			opening=1400.0, closing=1200.0)
		failed = {"ok": False, "rows": [], "error": "no rows"}
		merged = bank_recon_service._merged_statement([second, failed, first])
		self.assertEqual([row.description for row in merged["rows"]], ["a", "b", "c"])
		self.assertEqual((merged["opening"], merged["closing"]), (1000.0, 1200.0))
		self.assertTrue(merged["needs_review"])  # one of three photos did not read
		self.assertEqual(merged["low_conf_count"], 4)


if __name__ == "__main__":
	unittest.main()
