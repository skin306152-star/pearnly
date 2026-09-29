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


# 合成账单（按表头定列）：表头、承前余额、一笔支出、一笔存入、余额归零（“0.00”旧读法当成时间）、再一笔存入。
STATEMENT = [
	[("Date", .05, .10), ("Withdrawal", .36, .42), ("Deposit", .48, .53), ("Balance", .63, .68)],
	[("Balance", .40, .45), ("Forward", .46, .52), ("1,000.00", .61, .67)],
	[("01/06/2026", .05, .12), ("200.00", .39, .44), ("800.00", .62, .67)],
	[("02/06/2026", .05, .12), ("500.00", .50, .55), ("1,300.00", .61, .67)],
	[("03/06/2026", .05, .12), ("1,300.00", .38, .44), ("0.00", .64, .67)],
	[("05/06/2026", .05, .12), ("50.00", .51, .55), ("50.00", .63, .67)],
]


def statement_payload(rows, top=.20, pitch=.03):
	"""A one-page Document AI answer: each row one line of tokens at its own height."""
	text, lines, tokens = "", [], []
	for number, row in enumerate(rows):
		y0 = top + number * pitch
		line_start = len(text)
		for word, x0, x1 in row:
			start = len(text)
			text += word + " "
			tokens.append({"layout": {"textAnchor": {"textSegments": [{"startIndex": str(start), "endIndex": str(start + len(word))}]},
				"boundingPoly": {"normalizedVertices": [{"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y0 + .01},
					{"x": x0, "y": y0 + .01}]}}})
		lines.append({"layout": {"textAnchor": {"textSegments": [{"startIndex": str(line_start), "endIndex": str(len(text) - 1)}]}}})
		text = text[:-1] + "\n"
	return {"document": {"text": text, "pages": [{"pageNumber": 1, "layout": {"textAnchor": {"textSegments": [
		{"startIndex": "0", "endIndex": str(len(text))}]}}, "lines": lines, "tokens": tokens}]}}


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

	def test_pdf_chunks_are_at_most_five_pages(self):
		# 旧线上 15 页一块；密的扫描页连全部字段约 3MB 一页，超过共用层 8MB 回复上限就成“结果不明”（2026-09-29）。
		import pymupdf

		with pymupdf.open() as document:
			for _ in range(12):
				document.new_page()
			content = document.tobytes()
		self.assertEqual([pages for _c, _m, pages in pipeline.pdf_chunks(content, 12)], [5, 5, 2])

	def test_a_local_reconstruction_that_raises_goes_on_to_the_extraction(self):
		# 旧线上一页没有日期行时 statistics 取不到中位数，报成页数不符、整份失败（BBL 网银账单，2026-09-29）。
		import statistics

		bank_page = {"opening_balance": "1000", "closing_balance": "900", "entries": [
			{"transaction_date": "2026-06-01", "description": "fee", "withdrawal": "100", "balance": "900"}]}
		model = FakeModel(bank_page)
		with mock.patch.object(pipeline, "reconstruct", side_effect=statistics.StatisticsError("no median")):
			result = pipeline.run("bank", page_count=1, reads=[(b"img", "image/jpeg", 1)], render=Rendered(),
				document_ai_call=FakeDocumentAi(payload("x")), model_call=model)
		self.assertEqual(len(model.requests), 1)
		self.assertEqual(result.pages[0].document.entries[0].withdrawal, "100")
		with self.assertRaises(ContentError):  # a real page-count mismatch still fails the file
			pipeline.run("bank", page_count=2, reads=[(b"img", "image/jpeg", 1)], render=Rendered(),
				document_ai_call=FakeDocumentAi(payload("x")), model_call=model)

	def test_a_statement_the_header_parser_balances_needs_no_model(self):
		model = FakeModel({})
		result = pipeline.run("bank", page_count=1, reads=[(b"img", "image/jpeg", 1)], render=Rendered(),
			document_ai_call=FakeDocumentAi(statement_payload(STATEMENT)), model_call=model)
		self.assertEqual(model.requests, [])
		self.assertEqual([entry.balance for entry in result.pages[0].document.entries], ["800.00", "1300.00", "0.00", "50.00"])
		self.assertEqual(result.pages[0].layer_chain, ["enterprise", "local_schema"])

	def test_document_ai_asks_only_for_the_fields_it_reads(self):
		from pearnly_cowork.ocr.providers.client import ProviderClient

		sent = []

		class Response:
			status_code = 200

			def json(self):
				return payload("page")

		def post(**values):
			sent.append(values)
			return Response()

		client = ProviderClient({}, project="synthetic-project", post=post, checkpoint_key=lambda *_a: "k",
			reject_checkpoint=None, terminal_read=lambda _k: None, terminal_write=None, feedback=None,
			scope=lambda *args: args, access_token=lambda: "FAKE_TOKEN_NOT_A_CREDENTIAL", require_context=lambda: None)
		client.document_ai_call(b"img", "image/jpeg", expected_pages=1)
		self.assertEqual(sent[0]["payload"]["fieldMask"], enterprise_pages.FIELD_MASK)
		self.assertTrue(sent[0]["payload"]["imagelessMode"])  # the deployed body is otherwise unchanged
		with self.assertRaises(Exception):
			client.document_ai_call(b"%PDF", "application/pdf", expected_pages=enterprise_pages.MAX_PAGES_PER_READ + 1)

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


class HeaderAnchoredStatement(unittest.TestCase):
	def read(self, rows):
		from pearnly_cowork.ocr.enterprise import header_bank

		trace = []
		return header_bank.from_payloads([statement_payload(rows)], 1, reference_year=2026, trace=trace), trace

	def test_columns_come_from_the_printed_header(self):
		result, _trace = self.read(STATEMENT)
		self.assertTrue(result.arithmetic_passed, result.issues)
		document = result.document
		self.assertEqual(document["opening_balance"], "1000.00")
		self.assertEqual([(entry["transaction_date"], entry["direction"], entry["amount"]) for entry in document["entries"]], [
			("2026-06-01", "withdrawal", "200.00"), ("2026-06-02", "deposit", "500.00"),
			("2026-06-03", "withdrawal", "1300.00"), ("2026-06-05", "deposit", "50.00")])
		self.assertEqual(document["closing_balance"], "50.00")

	def test_a_row_whose_balance_was_not_read_does_not_pass(self):
		rows = [list(row) for row in STATEMENT]
		rows[3] = [cell for cell in rows[3] if cell[0] != "1,300.00"]  # 02/06 row: date and amount, no balance
		result, _trace = self.read(rows)
		self.assertFalse(result is not None and result.arithmetic_passed)

	def test_a_newest_first_statement_is_left_to_the_extraction(self):
		rows = [STATEMENT[0], [("05/06/2026", .05, .12), ("50.00", .51, .55), ("1,050.00", .61, .67)],
			[("01/06/2026", .05, .12), ("200.00", .39, .44), ("1,000.00", .61, .67)]]
		result, trace = self.read(rows)
		self.assertIsNone(result)
		self.assertEqual(trace[-1]["reason"], "date_missing_or_out_of_order")

	def test_no_header_no_local_result(self):
		result, trace = self.read(STATEMENT[1:])
		self.assertIsNone(result)
		self.assertEqual(trace[-1]["reason"], "no_header")

	def test_printed_dates(self):
		from pearnly_cowork.ocr.enterprise.header_bank import parse_date, resolve_year

		self.assertEqual([resolve_year(year, 2026) for year in (25, 68, 69, 2569, 2026)], [2025, 2025, 2026, 2026, 2026])
		self.assertEqual(parse_date("30 เม.ย. 2569", 2026), "2026-04-30")
		self.assertEqual(parse_date("15 ม.ค. 69", 2026), "2026-01-15")
		self.assertEqual(parse_date("15 มี.ค. 69", 2026), "2026-03-15")
		self.assertEqual(parse_date("8 05/07/25 CMN", 2026), "2025-07-05")  # row 8, not 8 May 2007
		self.assertEqual(parse_date("705 / 07 / 25TRN", 2026), "2025-07-05")  # row number printed on the date
		self.assertEqual(parse_date("31/02/2026", 2026), "")


class ReconJobCleanup(unittest.TestCase):
	def test_a_cleanup_failure_does_not_fail_the_finished_job(self):
		from pearnly_cowork.recon_center import jobs

		states, db = {}, mock.MagicMock()
		db.exists.return_value = True
		with mock.patch.object(jobs, "_state", side_effect=lambda job, **values: states.update(values)), \
				mock.patch.object(jobs, "_read", return_value=[("a.xlsx", b"x")]), \
				mock.patch.object(jobs.frappe, "session", mock.Mock(user="u@example.com")), \
				mock.patch.object(jobs.frappe, "db", db), \
				mock.patch.object(jobs.frappe, "delete_doc", side_effect=[frappe.QueryTimeoutError("lock"), None]) as delete, \
				mock.patch.object(jobs.frappe, "log_error") as log_error, \
				mock.patch("pearnly_cowork.recon_center.sources.ledger_source", return_value={}), \
				mock.patch("pearnly_cowork.recon_center.sources.report_source", return_value={}), \
				mock.patch("pearnly_cowork.recon_center.document_ocr.reader"), \
				mock.patch.object(jobs.income_recon_service, "create_from_parsed", return_value={"name": "T1"}):
			jobs.run("job", "income", "ws", "t", {"left": ["F1"], "right": ["F2"]})
		self.assertEqual((states["state"], states["task"]), ("done", "T1"))
		self.assertEqual(delete.call_count, 2)  # the second file is still removed after the first one failed
		log_error.assert_called_once()


if __name__ == "__main__":
	unittest.main()
