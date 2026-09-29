"""对账三类文档的扫描件与照片（2026-09-29）：企业档 Document AI 读字 → 本地拼行 / 3.8 契约抽取 → 同一套行。

Document AI 与模型都是假的，不连服务商、不付费；页面来源用假对象，不渲染真 PDF。
"""
import json
from decimal import Decimal
import unittest
from datetime import date
from unittest import mock

import frappe

from pearnly_cowork.ocr.engine.contracts import Frame, ModelResponse
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.enterprise import contract, pages as enterprise_pages, pipeline
from pearnly_cowork.recon_center import document_ocr, sources
from pearnly_cowork.reconciliation.bank_statement import StatementRow


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
		self.assertEqual(first.timeout_s, 180)  # 旧线上的抽取时限（120 秒时密的账单页等回复超时）
		self.assertEqual(first.images, ((b"png-1", "image/png"),))
		self.assertIn("page one text", first.prompt)
		self.assertIn("page two text", model.requests[1].prompt)
		legacy = pipeline.to_legacy(result)
		self.assertEqual(legacy["engine"], "enterprise-2026-09-03-v1")
		self.assertEqual([page["document_type"] for page in legacy["pages"]], ["vat_report", "vat_report"])
		self.assertEqual(legacy["pages"][0]["document"]["entries"][0]["invoice_no"], "IV001")
		# Document AI 按页计（0.0015 美元 × 35）加每页抽取的 token 费（3.8：输入 0.75、输出 3.75 美元 / 百万）
		self.assertAlmostEqual(result.estimated_cost_thb, 2 * 0.0015 * 35 + 2 * (10 * 0.75 + 5 * 3.75) / 1e6 * 35, places=5)

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

	def test_pdf_chunks_are_at_most_two_pages(self):
		# 旧线上 15 页一块；密的扫描页连全部字段约 3MB 一页，超过共用层 8MB 回复上限就成“结果不明”（2026-09-29）。
		import pymupdf

		with pymupdf.open() as document:
			for _ in range(12):
				document.new_page()
			content = document.tobytes()
		self.assertEqual([pages for _c, _m, pages in pipeline.pdf_chunks(content, 12)], [2] * 6)

	def test_pdf_chunks_are_the_same_bytes_every_time(self):
		# 检查点键按请求字节算：续跑再拆一次必须逐字节相同，否则已读完的块重读、重复付费（BAY-CA-03，2026-09-29）。
		import time

		import pymupdf

		with pymupdf.open() as document:
			for _ in range(4):
				document.new_page()
			content = document.tobytes()
		first = pipeline.pdf_chunks(content, 4)
		time.sleep(1.1)
		self.assertEqual(pipeline.pdf_chunks(content, 4), first)

	def test_an_unknown_outcome_is_sent_once_more_only_when_asked(self):
		from pearnly_core.execution.errors import Uncertain
		from pearnly_cowork.ocr.providers.client import ProviderClient

		sent, rejected = [], []

		class Response:
			status_code = 200

			def json(self):
				return payload("page")

		def post(**values):
			sent.append(json.loads(values["model"])[-1])  # the attempt in the checkpoint identity
			if len(sent) in (1, 3, 4, 5, 6):
				raise Uncertain("Provider transport outcome unknown")
			return Response()

		client = ProviderClient({}, project="synthetic-project", post=post, checkpoint_key=lambda payload, identity: identity,
			reject_checkpoint=lambda key, body: rejected.append(body), terminal_read=lambda _k: None, terminal_write=None,
			feedback=None, scope=lambda *args: args, access_token=lambda: "FAKE_TOKEN_NOT_A_CREDENTIAL",
			require_context=lambda: None)
		with mock.patch("pearnly_cowork.ocr.providers.client._record_unknown") as record:
			client.document_ai_call(b"img", "image/jpeg", expected_pages=1, retry_unknown=True)
			self.assertEqual(sent, [0, 1])  # a fresh attempt, a fresh checkpoint
			self.assertEqual(rejected, [{"outcome_unknown": True, "retried": True, "deferred": True}])
			with self.assertRaises(Uncertain):  # without the opt-in (the invoice path) nothing is resent
				client.document_ai_call(b"img", "image/jpeg", expected_pages=1)
			self.assertEqual(sent, [0, 1, 0])
			with self.assertRaises(Uncertain):  # a reading is resent at most twice, then it stays unknown
				client.document_ai_call(b"img2", "image/jpeg", expected_pages=1, retry_unknown=True)
		self.assertEqual(sent, [0, 1, 0, 0, 1, 2])
		self.assertEqual(record.call_count, 5)

	def test_a_resumed_read_takes_the_resent_call_instead_of_paying_again(self):
		# 2026-09-29 压测：续跑时第一次调用的「已拒」被当成终局拒绝，整份失败（IMG_2487 / IMG_2500 / BAY）。
		from pearnly_core.execution.errors import Uncertain
		from pearnly_cowork.ocr.providers.client import ProviderClient

		checkpoints, sent = {}, []

		class Response:
			status_code = 200

			def json(self):
				return payload("page")

		def post(**values):
			attempt = json.loads(values["model"])[-1]
			sent.append(attempt)
			if attempt == 0:
				checkpoints[values["model"]] = {"state": "started"}
				raise Uncertain("Provider transport outcome unknown")
			checkpoints[values["model"]] = {"state": "done", "body": payload("page")}
			return Response()

		def terminal_read(key):
			saved = checkpoints.get(key) or {}
			body = saved.get("body") or {}
			if saved.get("state") == "rejected" and not body.get("deferred"):
				return {"refusal": {"code": "ocr.provider_http_error", "http_status": None, "provider_code": None}}
			return None

		client = ProviderClient({}, project="synthetic-project", post=post, checkpoint_key=lambda payload, identity: identity,
			reject_checkpoint=lambda key, body: checkpoints.__setitem__(key, {"state": "rejected", "body": body}),
			terminal_read=terminal_read, terminal_write=None, feedback=None, scope=lambda *args: args,
			access_token=lambda: "FAKE_TOKEN_NOT_A_CREDENTIAL", require_context=lambda: None,
			checkpoint_read=checkpoints.get)
		with mock.patch("pearnly_cowork.ocr.providers.client._record_unknown"):
			client.document_ai_call(b"img", "image/jpeg", expected_pages=1, retry_unknown=True)
			self.assertEqual(sent, [0, 1])
			# 续跑（让出后从头再调）：不被当成拒绝，也不再发第一次，直接走重发那次
			client.document_ai_call(b"img", "image/jpeg", expected_pages=1, retry_unknown=True)
		self.assertEqual(sent, [0, 1, 1])

	def test_a_token_exchange_that_times_out_is_retried_later(self):
		from pearnly_core.execution.errors import Deferred
		from pearnly_cowork.ocr.providers.client import ProviderClient

		class TransportError(Exception):
			pass

		def token():
			try:
				raise TransportError("oauth2.googleapis.com read timed out")
			except TransportError as cause:
				raise RuntimeError("The service account could not exchange its token") from cause

		sent = []
		client = ProviderClient({}, project="synthetic-project", post=lambda **values: sent.append(values),
			checkpoint_key=lambda payload, identity: identity, reject_checkpoint=None, terminal_read=lambda _k: None,
			terminal_write=None, feedback=None, scope=lambda *args: args, access_token=token, require_context=lambda: None)
		with self.assertRaises(Deferred):
			client.document_ai_call(b"img", "image/jpeg", expected_pages=1)
		self.assertEqual(sent, [])  # nothing paid was sent

		def missing():
			try:
				raise FileNotFoundError("ocr.json")
			except FileNotFoundError as cause:
				raise RuntimeError("no service account") from cause

		client._token = missing
		with self.assertRaises(RuntimeError):  # a configuration problem still fails
			client.document_ai_call(b"img", "image/jpeg", expected_pages=1)

	def test_a_server_error_on_a_reconciliation_read_is_retried_later(self):
		from pearnly_core.execution.errors import Deferred
		from pearnly_cowork.ocr.providers.client import ProviderClient, ProviderError

		class Response:
			status_code = 504

			def json(self):
				return {"error": {"status": "DEADLINE_EXCEEDED", "message": "timeout"}}

		rejected = []
		client = ProviderClient({}, project="synthetic-project", post=lambda **values: Response(),
			checkpoint_key=lambda payload, identity: identity, reject_checkpoint=lambda key, body: rejected.append(body),
			terminal_read=lambda _k: None, terminal_write=lambda *_a: None, feedback=lambda *_a: None,
			scope=lambda *args: args, access_token=lambda: "FAKE_TOKEN_NOT_A_CREDENTIAL", require_context=lambda: None)
		with self.assertRaises(Deferred):
			client.document_ai_call(b"img", "image/jpeg", expected_pages=1, retry_unknown=True)
		self.assertEqual(rejected, [{"http": 504, "deferred": True}])
		with self.assertRaises(ProviderError):  # the invoice path keeps a 5xx as a completed refusal
			client.document_ai_call(b"img", "image/jpeg", expected_pages=1)

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

	def test_a_vat_report_pdf_is_read_line_by_line_with_pymupdf(self):
		# pypdf 读 Express 打印的税报整页只给一行（2026-09-29）；税报与总账都走 _pdf_text。
		read, calls = self.reader({"pages": []})
		lines = "\n".join(f"0{day}/06/2569 IV00{day} OHANAHAN 1,000.00 70.00" for day in range(1, 5))
		with mock.patch.object(sources, "_pdf_text", return_value=[lines]) as text:
			source = sources.report_source(b"%PDF", "vat.pdf", read)
		text.assert_called_once()

	def test_a_sales_listing_is_refused_with_its_own_explanation(self):
		# V05（2026-09-29）：POS 销售明细不是销项税报告，不硬读出错行，也不转去付费识别。
		read, calls = self.reader({"pages": []})
		listing = "\n".join(["หน้า 1 / 12", "รายงานแสดงข้อมูลการขายสินค้าและบริการ", "ราคาขายทั้งบิลรวมภาษีแล้ว",
			"01/03/2026 (11:49) เซียร์รังสิต DocNo. 48473 ลูกค้า-เงินสดหน้าร้าน 0.00 3,850.00"])
		with mock.patch.object(sources, "_pdf_text", return_value=[listing]), \
				mock.patch.object(sources.document_tier, "text_pages", return_value=[listing]), \
				mock.patch.object(sources.frappe, "_", side_effect=lambda text: text), \
				mock.patch.object(sources.frappe, "throw", side_effect=frappe.ValidationError) as refused:
			with self.assertRaises(frappe.ValidationError):
				sources.report_source(b"%PDF", "sales.pdf", read)
		self.assertIn("vat_report_is_sales_listing", str(refused.call_args))
		self.assertEqual(calls, [])
		self.assertFalse(sources.vat_parse.is_sales_listing(["รายงานภาษีขาย", "รายงานแสดงข้อมูลการขาย"]))

	def test_a_ledger_pdf_that_reads_free_is_not_sent_to_the_reader(self):
		# I03（2026-09-30）：Express 总账有文字层，免费解析 30 行全对；原来先送识别，识别超时读空整套失败。
		read, calls = self.reader({"pages": []})
		lines = "\n".join(["41-01-00-00     รายได้จากการขาย          (113,336,751.03)"] + [
			f" 0{day}/04/69 ขาย    SV69040{day}-001 บันทึกขาย       1,000.00      (113,33{day},751.03)" for day in range(1, 5)])
		with mock.patch.object(sources, "_pdf_text", return_value=[lines]):
			source = sources.ledger_source(b"%PDF", "gl.pdf", read)
		self.assertEqual((source["kind"], calls), ("pdf", []))

	def test_the_ledger_is_cut_to_the_report_months_and_the_page_says_so(self):
		# I01（2026-09-30）：1–12 月的总账配 5 月的税报。
		from pearnly_cowork.reconciliation import income_recon_service, revision
		from pearnly_cowork.reconciliation.income_vat import GlRow

		def row(day):
			return GlRow(doc_no="x", norm_doc_no="x", date=day, account_code="4110", description="", debit=0.0, credit=1.0)

		kept, trimmed = income_recon_service.within_report_period(
			[row("2026-01-10"), row("05/05/69"), row("2026-05-31"), row(""), row("2026-06-01")],
			[{"report_date": "2026-05-02"}, {"report_date": "2026-05-20"}])
		self.assertEqual([item.date for item in kept], ["05/05/69", "2026-05-31", ""])
		self.assertEqual(trimmed, (2, "2026-05-01", "2026-05-31"))
		doc = mock.Mock(parse_info_json=json.dumps({"gl": [], "vat": [{"file": "r.jpg", "warnings": ["cut 2"]}]}))
		self.assertEqual(revision.income_notes(doc), ["cut 2"])

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

	def test_enterprise_rows_go_through_the_deployed_finalisation_too(self):
		# 2026-09-29 有意变化：旧线上企业档的行不走定案；3.8 抽取的行多读的金额没被纠正（B08 BBL0555）。
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
			enterprise_rows,
		)
		from pearnly_cowork.reconciliation import bank_intake

		legacy = {"engine": "enterprise-2026-09-03-v1", "pages": [{"document": {"opening_balance": "1000",
			"closing_balance": "900", "entries": [{"transaction_date": "2026-06-01", "description": "x",
			"withdrawal": "100", "deposit": "", "balance": "900"}]}}]}
		model = enterprise_rows(legacy, "bank_statement", "stmt.jpg")
		with mock.patch.object(bank_intake.bank_statement, "finalize_rows", wraps=bank_intake.bank_statement.finalize_rows) as final:
			result = bank_intake.read_statement_file(b"img", "stmt.jpg", model_rows=lambda *_a: model)
		final.assert_called_once()
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

	def test_an_unread_photo_is_named_and_the_rest_are_reconciled(self):
		from pearnly_cowork.reconciliation import bank_recon_service

		good = self.statement([(date(2026, 6, 1), "a", 100.0, 0.0, 900.0)], opening=1000.0, closing=900.0)
		ledger = {"ok": True, "rows": [], "row_count": 0, "accounts": [], "opening": 1000.0, "closing": 1000.0,
			"source": "workbook"}
		reads = iter([good, {"ok": False, "rows": [], "error": "outcome unknown"}])
		with mock.patch.object(bank_recon_service.bank_recon_access, "require"), \
				mock.patch.object(bank_recon_service.bank_intake, "read_statement_file", side_effect=lambda *a, **k: next(reads)), \
				mock.patch.object(bank_recon_service.bank_intake, "read_ledger_file", return_value=ledger), \
				mock.patch.object(bank_recon_service, "_", side_effect=lambda text, **_kw: text + " {0}") as translate, \
				mock.patch.object(bank_recon_service, "create", return_value={"name": "T1"}) as create:
			bank_recon_service.create_from_documents("ws", "t", "r", b"", "", b"", "",
				statement_files=[(b"1", "1.jpg"), (b"2", "2.jpg")], gl_files=[(b"g", "gl.xlsx")], lang="zh")
		# 提示按提交时选的语言写（后台任务的默认语言是站点语言；2026-09-29 压测 lang=zh 出了泰文）
		self.assertIn(mock.call("reconciliation.files_unread", lang="zh"), translate.call_args_list)
		self.assertIn("2.jpg", " ".join(create.call_args.kwargs["warnings"]))
		self.assertNotIn("1.jpg", " ".join(create.call_args.kwargs["warnings"]))

	def test_a_statement_with_a_balance_and_no_transactions_is_reconciled_as_zero_rows(self):
		# GIZE BBL-SA-04 / KBANK-SA-01（2026-09-30）：当月没有流水的储蓄户，读出了余额，原来整套报“读不出账单”。
		from pearnly_cowork.reconciliation import bank_intake

		quiet = bank_intake.read_statement_file(b"img", "sa.jpg", model_rows=lambda *a: {
			"ok": False, "rows": [], "enterprise": True, "balances_read": True, "opening": 0.0, "closing": 5010180.78,
			"parser_version": "enterprise-2026-09-03-v1", "identity": {"bank": "BBL", "last4": "3111"}})
		self.assertEqual((quiet["ok"], quiet["rows"], quiet["opening"], quiet["closing"], quiet["needs_review"]),
			(True, [], 5010180.78, 5010180.78, False))
		moved = bank_intake.read_statement_file(b"img", "sa.jpg", model_rows=lambda *a: {
			"ok": False, "rows": [], "enterprise": True, "balances_read": True, "opening": 100.0, "closing": 90.0})
		self.assertFalse(moved["ok"])  # 期初期末不等：少了流水明细，不按 0 行对

	def test_a_balance_only_page_beside_pages_with_transactions_is_skipped(self):
		from pearnly_cowork.reconciliation import bank_recon_service

		good = self.statement([(date(2026, 6, 1), "a", 100.0, 0.0, 900.0)], opening=1000.0, closing=900.0)
		quiet = {"ok": True, "rows": [], "no_movement": True, "opening": 5.0, "closing": 5.0}
		kept = bank_recon_service._quiet_pages_skipped([("a.jpg", good), ("b.jpg", quiet)])
		self.assertEqual([(name, result["ok"]) for name, result in kept], [("a.jpg", True), ("b.jpg", False)])
		self.assertEqual(bank_recon_service._quiet_pages_skipped([("b.jpg", quiet), ("c.jpg", quiet)])[0][1]["ok"], True)

	def test_pages_with_neither_transactions_nor_a_balance_say_what_is_missing(self):
		from pearnly_cowork.reconciliation import bank_intake, bank_recon_service

		summary = bank_intake.read_statement_file(b"img", "p2.pdf", model_rows=lambda *a: {
			"ok": False, "rows": [], "enterprise": True, "balances_read": False})
		with mock.patch.object(bank_recon_service.bank_recon_access, "require"), \
				mock.patch.object(bank_recon_service.bank_intake, "read_statement_file", return_value=summary), \
				mock.patch.object(bank_recon_service, "_", side_effect=lambda text, **_kw: text), \
				mock.patch.object(bank_recon_service.frappe, "throw", side_effect=frappe.ValidationError) as refused:
			with self.assertRaises(frappe.ValidationError):
				bank_recon_service.create_from_documents("ws", "t", "r", b"", "", b"", "",
					statement_files=[(b"1", "p2.pdf")], gl_files=[(b"g", "gl.xlsx")])
		self.assertEqual(refused.call_args.args[0], "reconciliation.statement_no_movement")

	def test_a_statement_without_running_balances_keeps_them_blank_and_takes_the_ledger_opening(self):
		# GIZE BBL-SA-01 / 03（2026-09-30）：网银查询截图没有逐笔余额、倒序；读出的「期初」其实是期末。
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
			enterprise_rows,
		)
		from pearnly_cowork.reconciliation import bank_intake, bank_recon_service, bank_review

		legacy = {"engine": "enterprise-2026-09-03-v1", "pages": [{"document": {
			"opening_balance": "4984593.78", "closing_balance": "4987068.78", "account_number": "1274983111",
			"entries": [
				{"transaction_date": "2026-03-19", "description": "TRF", "deposit": "11556.00", "withdrawal": "", "balance": ""},
				{"transaction_date": "2026-03-12", "description": "TRF", "deposit": "11556.00", "withdrawal": "", "balance": None}]}}]}
		read = bank_intake.read_statement_file(b"img", "sa.jpg",
			model_rows=lambda *a: enterprise_rows(legacy, "bank_statement", "sa.jpg"))
		self.assertTrue(read["ok"])
		self.assertEqual([(row.date.isoformat(), row.balance) for row in read["rows"]],
			[("2026-03-12", None), ("2026-03-19", None)])
		self.assertTrue(read["balances_missing"])
		statement, notes = bank_recon_service.without_running_balance(read, {"opening": 4961455.78})
		self.assertEqual((statement["opening"], statement["closing"]), (4961455.78, 4984567.78))
		self.assertEqual(notes, ["reconciliation.statement_no_running_balance",
			"reconciliation.statement_balances_from_ledger"])
		self.assertFalse(statement["needs_review"])
		rows = [{"deposit": Decimal("11556.00"), "withdrawal": Decimal(0), "balance": None}] * 2
		self.assertTrue(all(check["ok"] for check in bank_review.balance_chain(rows, Decimal(0))))

	def test_balances_that_add_up_are_kept_for_a_statement_without_running_balances(self):
		from pearnly_cowork.reconciliation import bank_recon_service
		from pearnly_cowork.reconciliation.bank_statement import StatementRow

		row = StatementRow(date=date(2026, 1, 30), description="TRF", withdrawal=0.0, deposit=3226.0, balance=0.0)
		row.balance = None
		statement = {"rows": [row], "opening": 4981367.78, "closing": 4984593.78}
		kept, notes = bank_recon_service.without_running_balance(statement, {"opening": 1.0})
		self.assertIs(kept, statement)
		self.assertEqual(notes, ["reconciliation.statement_no_running_balance"])

	def test_a_page_printing_no_data_for_a_known_account_is_a_quiet_month(self):
		# SCB 网银「No Data Available」（2026-09-30）：没有流水、也没印余额。
		from pearnly_cowork.reconciliation import bank_intake, bank_recon_service

		quiet = bank_intake.read_statement_file(b"img", "scb.pdf", model_rows=lambda *a: {
			"ok": False, "rows": [], "enterprise": True, "balances_read": False, "no_data_printed": True,
			"recognised": True})
		self.assertEqual((quiet["ok"], quiet["rows"], quiet["balances_missing"]), (True, [], True))
		statement, notes = bank_recon_service.without_running_balance(quiet, {"opening": 1023552.37})
		self.assertEqual((statement["opening"], statement["closing"]), (1023552.37, 1023552.37))
		self.assertEqual(notes, ["reconciliation.statement_balances_from_ledger"])
		unknown = bank_intake.read_statement_file(b"img", "x.pdf", model_rows=lambda *a: {
			"ok": False, "rows": [], "enterprise": True, "balances_read": False, "no_data_printed": True,
			"recognised": False})
		self.assertEqual(unknown["error_code"], bank_intake.NO_MOVEMENT)

	def test_a_summary_page_whose_balances_differ_is_not_a_quiet_month(self):
		# BBL-CA-01（2026-09-30）：只传了第 2/2 页汇总，期初期末不等说明有流水没读到。
		from pearnly_cowork.reconciliation import bank_intake

		summary = bank_intake.read_statement_file(b"img", "p2.pdf", model_rows=lambda *a: {
			"ok": False, "rows": [], "enterprise": True, "balances_read": True, "opening": 100.0, "closing": 90.0})
		self.assertEqual(summary["error_code"], bank_intake.NO_MOVEMENT)

	def test_no_data_printed_is_recognised_in_the_transcript(self):
		for text in ("No Data Available", "ไม่พบรายการในช่วงเวลาที่ค้นหา", "no transactions found"):
			self.assertTrue(pipeline.NO_DATA_RE.search(text), text)
		self.assertFalse(pipeline.NO_DATA_RE.search("Total No. of Credits 35 852,386.58"))

	def test_free_text_rows_that_are_clearly_wrong_go_to_the_reader(self):
		# B03 bank17.pdf（2026-09-30）：KBank 往来户电子对账单的文字层乱序分块，拼出 6 行表头 / 地址、金额余额全 0。
		from pearnly_cowork.reconciliation import bank_intake
		from pearnly_cowork.reconciliation.bank_statement import StatementRow

		junk = [StatementRow(date=date(2025, 6, day), description="รับโอนเงิน / ฝากเงิน", withdrawal=0.0, deposit=0.0,
			balance=0.0) for day in (9, 10, 15, 24, 25)] + [StatementRow(date=date(2025, 6, 24), description="x",
			withdrawal=133058.06, deposit=0.0, balance=0.0)]
		self.assertTrue(bank_intake.implausible_free_rows(junk, ""))
		good = [StatementRow(date=date(2025, 6, 2), description="a", withdrawal=0.0, deposit=136458.0, balance=323186.06),
			StatementRow(date=date(2025, 6, 5), description="b", withdrawal=300000.0, deposit=0.0, balance=23186.06)]
		self.assertFalse(bank_intake.implausible_free_rows(good, "รวมฝากเงิน 1 รายการ\nรวมถอนเงิน 1 รายการ"))
		self.assertTrue(bank_intake.implausible_free_rows(good, "รวมฝากเงิน 8 รายการ\nรวมถอนเงิน 3 รายการ"))
		self.assertFalse(bank_intake.implausible_free_rows(good, "Total No. of Credits 1\nTotal No. of Debits 1"))
		model = self.statement([(date(2025, 6, 2), "a", 0.0, 136458.0, 323186.06)], opening=186728.06, closing=323186.06)
		model["enterprise"] = True
		with mock.patch.object(bank_intake.table_io, "extract_pdf_text_pages", return_value=["KBank"]), \
				mock.patch.object(bank_intake.bank_parse, "detect_bank", return_value="kbank"), \
				mock.patch.object(bank_intake, "_statement_text_rows", return_value=(junk, 0.0, 0.0, "kbank_columns")):
			read = bank_intake.read_statement_file(b"%PDF", "bank17.pdf", model_rows=lambda *a: model)
			self.assertEqual((read["source"], read["opening"]), ("enterprise", 186728.06))
			alone = bank_intake.read_statement_file(b"%PDF", "bank17.pdf")
		self.assertTrue(all(row.confidence == "low" for row in alone["rows"]))
		self.assertTrue(alone["needs_review"])

	def test_another_account_s_statements_are_left_out_when_the_rest_reads(self):
		# B05-02（2026-09-30）：KBank 存折 2 月没有流水，两张 BBL 照片成了整条账单。
		from pearnly_cowork.reconciliation import bank_recon_service

		kbank = self.statement([(date(2025, 8, 5), "a", 0.0, 10.0, 110.0)], opening=100.0, closing=110.0)
		kbank["identity"] = {"bank": "KASIKORNBANK", "last4": "8229"}
		bbl = self.statement([(date(2026, 2, 2), "b", 0.0, 5.0, 905.0)], opening=900.0, closing=905.0)
		bbl["identity"] = {"bank": "ธนาคารกรุงเทพ", "last4": "0555"}
		ledger = {"ok": True, "rows": [], "row_count": 0, "accounts": [], "opening": 110.0, "closing": 110.0,
			"source": "workbook"}
		reads = iter([kbank, dict(kbank), bbl])
		with mock.patch.object(bank_recon_service.bank_recon_access, "require"), \
				mock.patch.object(bank_recon_service.bank_intake, "read_statement_file", side_effect=lambda *a, **k: next(reads)), \
				mock.patch.object(bank_recon_service.bank_intake, "read_ledger_file", return_value=ledger), \
				mock.patch.object(bank_recon_service, "_", side_effect=lambda text, **_kw: text + " {0}"), \
				mock.patch.object(bank_recon_service, "create", return_value={"name": "T1"}) as create:
			bank_recon_service.create_from_documents("ws", "t", "r", b"", "", b"", "",
				statement_files=[(b"1", "1.jpg"), (b"2", "2.jpg"), (b"3", "148266.jpg")], gl_files=[(b"g", "gl.xlsx")])
		warnings = create.call_args.kwargs["warnings"]
		self.assertIn("reconciliation.statement_other_account_left_out 148266.jpg", warnings)
		self.assertFalse(any(row["description"] == "b" for row in create.call_args.args[3]))

	def test_a_cover_page_that_was_read_is_skipped_not_called_unread(self):
		from pearnly_cowork.reconciliation import bank_intake, bank_recon_service

		cover = bank_intake.read_statement_file(b"img", "cover.jpg", model_rows=lambda *a: {
			"ok": False, "rows": [], "enterprise": True, "identity": {"bank": "KBANK", "last4": "2298"}})
		self.assertEqual(cover["error_code"], bank_intake.NO_MOVEMENT)
		failed = bank_intake.read_statement_file(b"img", "lost.jpg", model_rows=lambda *a: None)
		self.assertNotEqual(failed["error_code"], bank_intake.NO_MOVEMENT)
		good = self.statement([(date(2026, 6, 1), "a", 100.0, 0.0, 900.0)], opening=1000.0, closing=900.0)
		ledger = {"ok": True, "rows": [], "row_count": 0, "accounts": [], "opening": 1000.0, "closing": 1000.0,
			"source": "workbook"}
		reads = iter([good, cover])
		with mock.patch.object(bank_recon_service.bank_recon_access, "require"), \
				mock.patch.object(bank_recon_service.bank_intake, "read_statement_file", side_effect=lambda *a, **k: next(reads)), \
				mock.patch.object(bank_recon_service.bank_intake, "read_ledger_file", return_value=ledger), \
				mock.patch.object(bank_recon_service, "_", side_effect=lambda text, **_kw: text + " {0}"), \
				mock.patch.object(bank_recon_service, "create", return_value={"name": "T1"}) as create:
			bank_recon_service.create_from_documents("ws", "t", "r", b"", "", b"", "",
				statement_files=[(b"1", "1.jpg"), (b"2", "cover.jpg")], gl_files=[(b"g", "gl.xlsx")], lang="zh")
		warnings = create.call_args.kwargs["warnings"]
		self.assertIn("reconciliation.files_no_movement cover.jpg", warnings)
		self.assertFalse(any("files_unread" in warning for warning in warnings))

	def test_the_same_line_on_two_overlapping_photos_counts_once(self):
		from pearnly_cowork.reconciliation import bank_recon_service

		first = self.statement([(date(2026, 5, 1), "TRF x1", 0.0, 450.0, 1450.0), (date(2026, 5, 2), "fee", 10.0, 0.0, 1440.0)],
			opening=1000.0, closing=1440.0)
		second = self.statement([(date(2026, 5, 2), "FEE", 10.0, 0.0, 1440.0), (date(2026, 5, 3), "in", 0.0, 60.0, 1500.0)],
			opening=1450.0, closing=1500.0)
		merged = bank_recon_service._merged_statement([first, second])
		self.assertEqual([row.balance for row in merged["rows"]], [1450.0, 1440.0, 1500.0])
		self.assertEqual((merged["opening"], merged["closing"]), (1000.0, 1500.0))

	def test_photos_are_joined_by_the_balance_chain_not_just_the_date(self):
		# B01：同一天的行分在两张照片上，按日期排序会打乱；按「下一张倒推的期初 = 上一张最后余额」接。
		from pearnly_cowork.reconciliation import bank_recon_service

		early = self.statement([(date(2026, 5, 6), "a", 0.0, 100.0, 1100.0), (date(2026, 5, 7), "b", 50.0, 0.0, 1050.0)])
		late = self.statement([(date(2026, 5, 7), "c", 0.0, 200.0, 1250.0), (date(2026, 5, 7), "d", 25.0, 0.0, 1225.0)])
		middle = self.statement([(date(2026, 5, 6), "x", 0.0, 1.0, 1226.0), (date(2026, 5, 7), "y", 1.0, 0.0, 1225.0)])
		# 故意把接在中间的那张放最后、日期还更早：只看日期会排错
		merged = bank_recon_service._merged_statement([early, late, middle])
		self.assertEqual([row.description for row in merged["rows"]], ["a", "b", "c", "d", "x", "y"])
		fixed = bank_recon_service.chain_files([early["rows"], late["rows"]])
		self.assertEqual([row.balance for row in fixed], [1100.0, 1050.0, 1250.0, 1225.0])
		self.assertEqual(merged["opening"], 1000.0)

	def test_a_misread_balance_is_repaired_only_when_both_neighbours_agree(self):
		from pearnly_cowork.reconciliation import bank_statement
		from pearnly_cowork.reconciliation.bank_intake import repair_balance_from_neighbours
		from pearnly_cowork.reconciliation.bank_statement import StatementRow

		rows = [StatementRow(date=date(2026, 5, 6), description="", withdrawal=0.0, deposit=450.0, balance=451134.99),
			StatementRow(date=date(2026, 5, 6), description="", withdrawal=0.0, deposit=450.0, balance=451584.86)]
		bank_statement.verify_row_balances(rows, 450684.86)
		repair_balance_from_neighbours(rows, 450684.86)
		self.assertEqual(rows[0].balance, 451134.86)
		self.assertTrue(rows[0].amount_autocorrected)
		self.assertTrue(all(row.balance_ok for row in rows))

	def test_an_enterprise_amount_is_repaired_from_the_balances_as_the_text_path_is(self):
		# B08 BBL0555：存入 3,646,861.49 应是 3,646,561.49（余额前后都对，只有金额冲突）。
		from pearnly_cowork.reconciliation import bank_intake
		from pearnly_cowork.reconciliation.bank_statement import StatementRow

		rows = [StatementRow(date=date(2026, 4, 4), description="", withdrawal=0.0, deposit=100.0, balance=2818893.63),
			StatementRow(date=date(2026, 4, 5), description="", withdrawal=0.0, deposit=3646861.49, balance=6465455.12),
			StatementRow(date=date(2026, 4, 6), description="", withdrawal=300.0, deposit=0.0, balance=6465155.12),
			StatementRow(date=date(2026, 4, 7), description="", withdrawal=0.0, deposit=10.0, balance=6465165.12)]
		model = {"ok": True, "rows": rows, "enterprise": True, "opening": 2818793.63, "closing": 6465165.12}
		result = bank_intake.read_statement_file(b"img", "p.jpg", model_rows=lambda *_a: model)
		self.assertEqual(result["rows"][1].deposit, 3646561.49)
		self.assertTrue(result["rows"][1].amount_autocorrected)

	def test_a_photo_whose_first_line_is_read_the_wrong_way_still_joins_and_is_corrected(self):
		# B05 KBank8229-01：照片第一行手续费 685 读成存入；余额本身都对。
		from pearnly_cowork.reconciliation import bank_recon_service

		first = self.statement([(date(2026, 1, 5), "TRN", 743728.0, 0.0, 2851314.24)], opening=3595042.24)
		second = self.statement([(date(2026, 1, 5), "CMN", 0.0, 685.0, 2850629.24), (date(2026, 1, 9), "x", 0.0, 10.0, 2850639.24)])
		merged = bank_recon_service._merged_statement([second, first])
		self.assertEqual([row.description for row in merged["rows"]], ["TRN", "CMN", "x"])
		cmn = merged["rows"][1]
		self.assertEqual((cmn.withdrawal, cmn.deposit, cmn.direction_autocorrected), (685.0, 0.0, True))
		self.assertEqual(merged["opening"], 3595042.24)

	def test_a_line_with_nothing_on_it_is_dropped(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import enterprise_rows

		entries = [{"transaction_date_raw": "01/02/26", "description": "TRN", "deposit": "0", "withdrawal": "", "balance": "0"},
			{"transaction_date_raw": "02/02/26", "deposit": "5", "balance": "105"}]
		read = enterprise_rows({"pages": [{"document": {"entries": entries}}]}, "bank_statement", "p.jpg")
		self.assertEqual(read["row_count"], 1)

	def test_a_misread_last_balance_is_mended_from_the_next_photo(self):
		# B01 IMG_2485：最后一行印 362,264.99 读成 562,264.36，读取时按它把存入「补」成 200,449.37。
		from pearnly_cowork.reconciliation import bank_recon_service

		first = self.statement([(date(2026, 5, 8), "a", 0.0, 450.0, 361814.99), (date(2026, 5, 8), "b", 0.0, 200449.37, 562264.36)])
		first["rows"][1].amount_autocorrected = True
		second = self.statement([(date(2026, 5, 8), "c", 0.0, 840.0, 363104.99), (date(2026, 5, 9), "d", 10.0, 0.0, 363094.99)])
		merged = bank_recon_service._merged_statement([first, second])
		mended = merged["rows"][1]
		self.assertEqual((mended.balance, mended.deposit, mended.withdrawal), (362264.99, 450.0, 0.0))
		self.assertEqual([row.description for row in merged["rows"]], ["a", "b", "c", "d"])
		# 金额是印出来的（没标自动更正）就不动
		plain = self.statement([(date(2026, 5, 8), "a", 0.0, 450.0, 361814.99), (date(2026, 5, 8), "b", 0.0, 200449.37, 562264.36)])
		kept = bank_recon_service._merged_statement([plain, second])
		self.assertEqual(kept["rows"][1].balance, 562264.36)

	def test_a_photo_first_line_is_corrected_even_when_merged_by_date(self):
		# B05-02：一张照片末行没有余额，接链退回按日期合并；CMN 685 仍要按前一行余额纠正成支出。
		from pearnly_cowork.reconciliation import bank_recon_service

		first = self.statement([(date(2026, 1, 5), "TRN", 743728.0, 0.0, 2851314.24)], opening=3595042.24)
		second = self.statement([(date(2026, 1, 5), "CMN", 0.0, 685.0, 2850629.24), (date(2026, 3, 5), "TRN", 727299.0, 0.0, 0.0)])
		self.assertEqual(bank_recon_service.chain_files([first["rows"], second["rows"]]), [])
		merged = bank_recon_service._merged_statement([first, second])
		cmn = next(row for row in merged["rows"] if row.description == "CMN")
		self.assertEqual((cmn.withdrawal, cmn.deposit, cmn.direction_autocorrected), (685.0, 0.0, True))

	def test_statements_of_another_account_are_named(self):
		# B05-02：KBank 存折里混进两张 BBL 存折。
		from pearnly_cowork.reconciliation.bank_recon_service import other_accounts

		def read(bank, last4=""):
			return {"ok": True, "identity": {"bank": bank, "last4": last4}}

		statements = [("1.jpg", read("KASIKORNBANK")), ("2.jpg", read("ธนาคารกสิกรไทย")), ("3.jpg", read("KASIKORNBANK")),
			("148266.jpg", read("ธนาคารกรุงเทพ")), ("148267.jpg", read("ธนาคารกรุงเทพ")), ("x.jpg", {"ok": False})]
		self.assertEqual(other_accounts(statements), ["148266.jpg", "148267.jpg"])
		self.assertEqual(other_accounts([("a.jpg", read("", "8229")), ("b.jpg", read("", "8229")), ("c.jpg", read("", "0555"))]),
			["c.jpg"])
		self.assertEqual(other_accounts(statements[:3]), [])
		# B09-03（2026-09-30）：尾号只差一位是读错；与别的文件有相同的行就是同一账户。
		self.assertEqual(other_accounts([("a.jpg", read("", "8229")), ("b.pdf", read("", "8228"))]), [])
		line = StatementRow(date=date(2026, 3, 2), description="TRANSFER", withdrawal=0.0, deposit=10.0, balance=110.0)
		self.assertEqual(other_accounts([("a.jpg", {**read("", "8229"), "rows": [line]}),
			("b.jpg", {**read("", "8229"), "rows": [line]}), ("c.pdf", {**read("", "0555"), "rows": [line]})]), [])

	def test_overlapping_files_merge_on_the_best_chain(self):
		# B08-01（2026-09-30）：整月 PDF 漏了一行；覆盖几个月的存折有这一行，但把别的行读错（3,472.15 → 30,472.15）。
		from pearnly_cowork.reconciliation import bank_recon_service

		def line(day, deposit, balance, source):
			return StatementRow(date=date(2026, 1, day), description="x", withdrawal=0.0, deposit=deposit, balance=balance,
				source_file=source)

		pdf = [line(2, 100.0, 1100.0, "01.pdf"), line(5, 200.0, 1300.0, "01.pdf"), line(6, 50.0, 1400.0, "01.pdf"),
			line(7, 10.0, 1410.0, "01.pdf")]
		book = [StatementRow(date=date(2025, 12, 30), description="old", withdrawal=0.0, deposit=5.0, balance=900.0,
			source_file="book.pdf"), line(5, 50.0, 1350.0, "book.pdf"), line(6, 500.0, 1850.0, "book.pdf")]
		merged = bank_recon_service.primary_merge([book, pdf], (date(2026, 1, 1), date(2026, 1, 31)))
		inside = [(row.date.day, row.deposit) for row in merged if row.date.year == 2026]
		self.assertEqual(inside, [(2, 100.0), (5, 200.0), (5, 50.0), (6, 50.0), (7, 10.0)])
		self.assertEqual(bank_recon_service.chain_breaks(merged, (date(2026, 1, 1), date(2026, 1, 31))), 0)

	def test_a_misread_line_inside_a_whole_chain_is_dropped_and_other_accounts_never_lead(self):
		# B08-01 6.jpg（2026-09-30）：01-06 那笔读成 01-02、金额余额都不对；主链首尾之间一笔不漏，这行只能是读错的重复。
		from pearnly_cowork.reconciliation import bank_recon_service

		def line(day, deposit, balance, source):
			return StatementRow(date=date(2026, 1, day), description="x", withdrawal=0.0, deposit=deposit, balance=balance,
				source_file=source)

		pdf = [line(2, 100.0, 1100.0, "01.pdf"), line(3, 100.0, 1200.0, "01.pdf"), line(7, 10.0, 1210.0, "01.pdf")]
		photo = [line(5, 21588.5, 4613308.36, "6.jpg"), line(9, 5.0, 1215.0, "6.jpg")]
		merged = bank_recon_service.primary_merge([pdf, photo])
		self.assertEqual([(row.date.day, row.deposit) for row in merged], [(2, 100.0), (3, 100.0), (7, 10.0), (9, 5.0)])
		# 去掉的行在结果里逐条列出（不悄悄丢数据）
		self.assertEqual(bank_recon_service.dropped_note(merged.dropped), "6.jpg 2026-01-05 +21,588.50")
		self.assertLessEqual(len(bank_recon_service.dropped_note(merged.dropped * 40)), 361)
		merged_statement = bank_recon_service._merged_statement([
			{"ok": True, "rows": pdf, "completeness": {"ok": True}}, {"ok": True, "rows": photo, "completeness": {"ok": True}}])
		self.assertEqual(len(merged_statement["dropped"]), 1)
		# 判成别的账户的那份不当主份
		other = [line(2, 1.0, 50.0, "bbl.jpg"), line(3, 1.0, 51.0, "bbl.jpg"), line(4, 1.0, 52.0, "bbl.jpg"),
			line(5, 1.0, 53.0, "bbl.jpg")]
		led = bank_recon_service.primary_merge([pdf, other], excluded=("bbl.jpg",))
		self.assertEqual(led[0].source_file, "01.pdf")

	def test_a_page_read_twice_in_one_file_keeps_the_copy_that_chains(self):
		# B08-01 第 15 轮（2026-09-30）：同一份 PDF 把 01-12 两笔读了两遍，一遍在最前面、一遍在正确位置。
		from pearnly_cowork.reconciliation import bank_recon_service

		def line(day, deposit, withdrawal, balance):
			return StatementRow(date=date(2026, 1, day), description="x", withdrawal=withdrawal, deposit=deposit,
				balance=balance, source_file="book.pdf")

		rows = [line(12, 7200.0, 0.0, 4641277.86), line(12, 0.0, 2000000.0, 2641277.86),
			line(10, 100.0, 0.0, 4634077.86), line(12, 7200.0, 0.0, 4641277.86), line(12, 0.0, 2000000.0, 2641277.86),
			line(13, 192.6, 0.0, 2641470.46)]
		self.assertEqual(sorted(bank_recon_service._chained_copy(rows)), [2, 3, 4, 5])
		# 另一个账户的那份不当链头
		kbank = [line(2, 10.0, 0.0, 110.0), line(3, 10.0, 0.0, 120.0)]
		for row in kbank:
			row.source_file = "k.jpg"
		bbl = [line(1, 5.0, 0.0, 905.0), line(2, 5.0, 0.0, 910.0)]
		for row in bbl:
			row.source_file = "b.jpg"
		chained = bank_recon_service.chain_files([bbl, kbank], excluded=("b.jpg",))
		self.assertEqual(chained[0].source_file, "k.jpg")

	def test_a_line_read_with_a_neighbouring_date_is_the_same_line(self):
		# B09-03：同一页，一份读成 03-27、一份读成 03-26，存取与余额都一样。
		from pearnly_cowork.reconciliation.bank_recon_service import same_lines_once

		first = StatementRow(date=date(2026, 3, 26), description="a", withdrawal=37700.0, deposit=0.0, balance=1420163.16,
			source_file="03.pdf")
		second = StatementRow(date=date(2026, 3, 27), description="b", withdrawal=37700.0, deposit=0.0, balance=1420163.16,
			source_file="03.jpg")
		far = StatementRow(date=date(2026, 3, 5), description="c", withdrawal=37700.0, deposit=0.0, balance=1420163.16,
			source_file="03.jpg")
		self.assertEqual([row.description for row in same_lines_once([first, second, far])], ["a", "c"])

	def test_a_misdated_line_that_chains_takes_its_place_in_the_chain(self):
		# B08-02「04/03/25」夹在 26 年的行之间；B09-01 一笔读成「30/12」夹在 01-03 与 01-06 之间。
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
			_neighbour_years,
		)

		rows = [StatementRow(date=date(2026, 3, 3), description="a", withdrawal=0.0, deposit=0.0, balance=100.0),
			StatementRow(date=date(2025, 3, 4), description="b", withdrawal=0.0, deposit=10.0, balance=110.0),
			StatementRow(date=date(2026, 3, 5), description="c", withdrawal=0.0, deposit=10.0, balance=120.0)]
		_neighbour_years(rows)
		self.assertEqual(rows[1].date, date(2026, 3, 4))
		rows = [StatementRow(date=date(2026, 1, 3), description="a", withdrawal=0.0, deposit=0.0, balance=1000000.0),
			StatementRow(date=date(2025, 12, 30), description="b", withdrawal=48065.0, deposit=0.0, balance=951935.0),
			StatementRow(date=date(2026, 1, 6), description="c", withdrawal=15600.0, deposit=0.0, balance=936335.0)]
		_neighbour_years(rows)
		self.assertEqual((rows[1].date, rows[1].confidence), (date(2026, 1, 3), "low"))

	def test_the_opening_comes_from_the_first_line_of_the_period(self):
		# B08-01：期间前混着 2016 年的旧存折页，不能拿「期间前最后一行」当期初。
		from pearnly_cowork.reconciliation.bank_recon_service import within_ledger_period
		from pearnly_cowork.reconciliation.bank_matching import GlRow

		old = StatementRow(date=date(2016, 11, 14), description="old", withdrawal=0.0, deposit=0.0, balance=7113353.69)
		first = StatementRow(date=date(2026, 1, 2), description="a", withdrawal=0.0, deposit=882.75, balance=3051849.57)
		ledger = {"rows": [GlRow(date=date(2026, 1, 2), doc_no="x", account_code="", description="", debit=882.75,
			credit=0.0, balance=0.0)]}
		trimmed, _hint = within_ledger_period({"rows": [first, old], "opening": 0.0, "closing": 0.0}, ledger)
		self.assertEqual(trimmed["opening"], 3050966.82)

	def test_the_same_line_read_from_a_photo_and_a_pdf_is_kept_once(self):
		# B09-01（2026-09-30）：jpg 与 pdf 是同一页，描述一份「TRANSFER Telfax BBL X0…」一份「TRANSFER」。
		from pearnly_cowork.reconciliation.bank_recon_service import same_lines_once

		def line(text, source, balance=110.0):
			return StatementRow(date=date(2026, 1, 5), description=text, withdrawal=0.0, deposit=10.0, balance=balance,
				source_file=source)

		rows = [line("TRANSFER Telfax BBL X0", "01.jpg"), line("TRANSFER", "01.pdf"), line("TRANSFER", "01.jpg"),
			line("a", "01.jpg"), line("b", "01.pdf")]
		rows[3].balance = rows[4].balance = None
		self.assertEqual([(row.description, row.source_file) for row in same_lines_once(rows)],
			[("TRANSFER Telfax BBL X0", "01.jpg"), ("TRANSFER", "01.jpg"), ("a", "01.jpg"), ("b", "01.pdf")])

	def test_an_unjoined_photo_keeps_its_printed_first_amount(self):
		# 接不上的交界行不按跨照片的余额差补金额（B05-02 把 BBL 首行补成了 2,678,117.31）。
		from pearnly_cowork.reconciliation import bank_recon_service

		kbank = self.statement([(date(2026, 2, 1), "a", 0.0, 10.0, 2122630.24), (date(2026, 2, 2), "b", 5.0, 0.0, 2122625.24)])
		bbl = self.statement([(date(2026, 2, 3), "c", 0.0, 2821224.30, 4800747.55), (date(2026, 2, 4), "d", 0.0, 11652.30, 4812399.85)])
		merged = bank_recon_service._merged_statement([kbank, bbl])
		self.assertEqual(merged["rows"][2].deposit, 2821224.30)
		self.assertFalse(merged["rows"][2].amount_autocorrected)

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


class StatementOrderAndDates(unittest.TestCase):
	def test_a_newest_first_statement_is_turned_around(self):
		# BBL 网银（B04 BBL6764）：第一行是月底，透支户余额为负。
		from pearnly_cowork.reconciliation.bank_intake import chronological
		from pearnly_cowork.reconciliation.bank_statement import StatementRow

		rows = [StatementRow(date=date(2026, 4, 30), description="fee", withdrawal=1019.69, deposit=0.0, balance=-423347.30),
			StatementRow(date=date(2026, 4, 30), description="fee", withdrawal=120.0, deposit=0.0, balance=-422327.61),
			StatementRow(date=date(2026, 4, 2), description="in", withdrawal=0.0, deposit=500.0, balance=-422207.61)]
		ordered, opening, closing = chronological(rows, -422327.61, 0.0)
		self.assertEqual([row.balance for row in ordered], [-422207.61, -422327.61, -423347.30])
		self.assertEqual((opening, closing), (-422707.61, -423347.30))
		same = [rows[2], rows[1]]
		self.assertIs(chronological(same, 0.0, 0.0)[0], same)  # oldest first already: untouched

	def test_the_printed_two_digit_year_wins_over_the_model(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import _entry_date

		self.assertEqual(_entry_date({"transaction_date": "1983-03-23", "transaction_date_raw": "23/03/26"}), date(2026, 3, 23))
		self.assertEqual(_entry_date({"transaction_date": "", "transaction_date_raw": "23/03/26"}), date(2026, 3, 23))
		self.assertEqual(_entry_date({"transaction_date": "1982-05-19", "transaction_date_raw": ""}), date(2025, 5, 19))
		self.assertEqual(_entry_date({"transaction_date": "2026-04-05", "transaction_date_raw": "05/04/69"}), date(2026, 4, 5))

	def test_an_offsetting_pair_of_imputed_amounts_is_undone_for_review(self):
		# B01 行 357 / 358：余额 534,708.53 读成 708.53，两行金额都是补的（支出 533,940 / 存入 534,390）。
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import enterprise_rows

		entries = [{"transaction_date_raw": "10/05/26", "deposit": "100", "balance": "534648.53"},
			{"transaction_date_raw": "10/05/26", "withdrawal": "533940.00", "balance": "708.53", "chain_amount_imputed": True},
			{"transaction_date_raw": "10/05/26", "deposit": "534390.00", "balance": "535098.53", "chain_amount_imputed": True},
			{"transaction_date_raw": "10/05/26", "deposit": "50", "balance": "535148.53", "chain_amount_imputed": True}]
		rows = enterprise_rows({"pages": [{"document": {"entries": entries}}]}, "bank_statement", "p.jpg")["rows"]
		self.assertEqual([(row.deposit, row.withdrawal) for row in rows[1:3]], [(0.0, 0.0), (0.0, 0.0)])
		self.assertEqual([row.confidence for row in rows[1:3]], ["low", "low"])
		self.assertEqual(rows[3].deposit, 50.0)  # an imputed amount without an offsetting partner stays

	def test_a_misread_year_in_the_statement_month_takes_the_statement_year(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import enterprise_rows

		entries = [{"transaction_date_raw": raw, "deposit": "1", "balance": "1"} for raw in
			("01/05/26", "02/05/26", "03/05/20", "04/05/25", "28/04/26")]
		read = enterprise_rows({"pages": [{"document": {"entries": entries}}]}, "bank_statement", "p.jpg")
		self.assertEqual([row.date for row in read["rows"]], [date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3),
			date(2026, 5, 4), date(2026, 4, 28)])


class NoInvoiceChargeAfterTheCheckClosed(unittest.TestCase):
	def test_invoices_read_after_the_vat_check_failed_are_not_charged(self):
		# V01（2026-09-30）：核查失败后读完的小票照样扣费。
		from pearnly_cowork.recon_center import document_ocr, invoice_ocr

		saved = {"body": {"invoices": [{"invoice_no": "1"}]}}
		cache = mock.MagicMock()
		cache.get_value.return_value = {"state": "failed"}
		with mock.patch.object(invoice_ocr.frappe, "flags", mock.MagicMock(get=lambda key: "W1")), \
				mock.patch.object(invoice_ocr.frappe, "db", mock.MagicMock(get_value=lambda *a, **k: frappe._dict(
					kind=invoice_ocr.KIND, user="u@example.com", status="running"))), \
				mock.patch.object(invoice_ocr.frappe, "session", mock.Mock(user="Administrator")), \
				mock.patch.object(invoice_ocr.frappe, "set_user"), mock.patch.object(document_ocr.frappe, "cache", cache), \
				mock.patch.object(invoice_ocr.core_work, "checkpoint",
					side_effect=lambda key, *a: {invoice_ocr.RESULT: saved}.get(key)), \
				mock.patch.object(invoice_ocr, "_charge") as charge:
			outcome = invoice_ocr.run("job", "F1", "p.jpg", 1, "sha", {"billing_tier": "t"})
		self.assertEqual(outcome, {"cancelled": "recon_job_closed"})
		charge.assert_not_called()


class InvoiceReadsInTheVatCheck(unittest.TestCase):
	"""第 17 轮 V03（2026-09-30）。"""

	def test_a_twelve_digit_tax_id_gets_its_leading_zero_back(self):
		from pearnly_cowork.recon_center import invoice_ocr

		self.assertEqual(invoice_ocr._tax("105541034263"), "0105541034263")
		self.assertEqual(invoice_ocr._tax("0105541034263"), "0105541034263")
		self.assertEqual(invoice_ocr._tax("0-1055-41034-26-3"), "0-1055-41034-26-3")  # 印了分隔符的原样
		self.assertEqual(invoice_ocr._tax(""), "")

	def test_a_day_and_month_read_the_wrong_way_round_are_swapped_into_the_report_month(self):
		from pearnly_cowork.recon_center import vat_check

		rows = [{"report_date": "2026-07-01"}, {"report_date": "2026-07-31"}]
		records = [{"invoice_date": "2026-01-07"}, {"invoice_date": "2026-07-15"}, {"invoice_date": "2026-09-20"},
			{"invoice_date": "2026-09-07"}]
		self.assertEqual([record["invoice_date"] for record in vat_check.swapped_dates(records, rows)],
			["2026-07-01", "2026-07-15", "2026-09-20", "2026-07-09"])

	def test_an_unknown_page_becomes_a_page_failure_not_a_lost_file(self):
		from pearnly_core.execution.errors import Uncertain
		from pearnly_cowork.ocr.engine.contracts import ModelRequest, ProviderFailure
		from pearnly_cowork.recon_center import invoice_ocr

		client = mock.Mock()
		client.model_call.side_effect = Uncertain("read timeout")
		captured = {}

		def run_invoice(source, model_call, vision_call, **kwargs):
			with self.assertRaises(ProviderFailure):
				model_call(ModelRequest(stage="direct", page_number=3, attempt=0, model="m", prompt="p"))
			captured["timeout"] = client.model_call.call_args.args[0].timeout_s
			return mock.Mock(pipeline=mock.Mock(pages=[]))

		frozen = {"environment": {}, "policy": {}, "target": dict.fromkeys(("backend", "project", "location",
			"credentials_path")), "anchor_date": "2026-07-01"}
		with mock.patch.object(invoice_ocr, "build_client", return_value=client), \
				mock.patch.object(invoice_ocr, "invoice_policy", return_value=mock.Mock(text_fast_path=False)), \
				mock.patch.object(invoice_ocr, "config_from_environment"), \
				mock.patch.object(invoice_ocr, "PageSource", mock.MagicMock()), \
				mock.patch.object(invoice_ocr, "run_invoice", side_effect=run_invoice), \
				mock.patch.object(invoice_ocr, "records", return_value=[]), \
				mock.patch.object(invoice_ocr.frappe, "db", mock.MagicMock()):
			invoice_ocr._recognize(b"x", "a.pdf", 25, frozen)
		self.assertEqual(captured["timeout"], invoice_ocr.MODEL_TIMEOUT_SECONDS)


class ReportPhotosAreSubmittedTogether(unittest.TestCase):
	def test_vat_report_photos_are_submitted_before_reading_in_order(self):
		# 第 17 轮 V01（2026-09-30）：21 张税报照片一张读完才提交下一张。
		from pearnly_cowork.recon_center import document_ocr, vat_check

		with mock.patch.object(document_ocr, "submit") as submit, mock.patch.object(document_ocr, "reader"), \
				mock.patch.object(vat_check, "_report", side_effect=RuntimeError("stop")), \
				mock.patch.object(vat_check.frappe, "db", mock.MagicMock()), \
				mock.patch.object(vat_check.frappe, "session", mock.Mock(user="u@example.com")):
			with self.assertRaises(RuntimeError):
				vat_check.run("ws", "t", "r", [("1.JPG", b"a"), ("r.pdf", b"b"), ("2.jpg", b"c")], [], "zh",
					recon_job="job", report_files=["F1", "F2", "F3"])
		self.assertEqual([call.args for call in submit.call_args_list], [
			("job", "report0", "F1", "1.JPG", b"a", "vat_report", "u@example.com"),
			("job", "report2", "F3", "2.jpg", b"c", "vat_report", "u@example.com")])


class SeveralIncomeReportFiles(unittest.TestCase):
	"""用户 2026-09-30 定：收入对账的税报可多份（I01 的 21 张税报照片）。"""

	def test_only_the_report_side_of_an_income_reconciliation_takes_several_files(self):
		from pearnly_cowork.recon_center import jobs

		with mock.patch.object(jobs.scope, "default_workspace", return_value="ws"), \
				mock.patch.object(jobs.KINDS["income"], "require"), mock.patch.object(jobs, "_save", return_value="F"), \
				mock.patch.object(jobs, "_state"), mock.patch.object(jobs.frappe, "enqueue"), \
				mock.patch.object(jobs.frappe, "session", mock.Mock(user="u@example.com")), \
				mock.patch.object(jobs, "_", side_effect=lambda text: text), \
				mock.patch.object(jobs.frappe, "throw", side_effect=frappe.ValidationError):
			jobs.start("income", "t", {"left": [("gl.xlsx", b"g")], "right": [("1.jpg", b"a"), ("2.jpg", b"b")]})
			with self.assertRaises(frappe.ValidationError):
				jobs.start("income", "t", {"left": [("a.xlsx", b"g"), ("b.xlsx", b"h")], "right": [("1.jpg", b"a")]})

	def test_several_report_files_merge_in_order_counting_a_repeated_page_once(self):
		from pearnly_cowork.recon_center import document_ocr, vat_check

		def row(no, day, net):
			return {"report_invoice_no": no, "report_date": day, "report_amount_pre_vat": net,
				"report_vat_amount": round(net * 0.07, 2), "report_buyer_name": "A"}

		reads = {"1.jpg": {"ok": True, "rows": [row("IV1", "2026-05-01", 100.0), row("IV2", "2026-05-02", 200.0)],
			"meta": {}}, "2.jpg": {"ok": True, "rows": [row("IV2", "2026-05-02", 200.0), row("IV3", "2026-05-03", 300.0)],
			"meta": {}}}

		def report(content, name, read=None):
			if name == "3.jpg":
				raise frappe.ValidationError("unread")
			return reads[name]

		with mock.patch.object(document_ocr, "submit") as submit, mock.patch.object(document_ocr, "reader"), \
				mock.patch.object(vat_check, "_report", side_effect=report), \
				mock.patch.object(vat_check.frappe, "db", mock.MagicMock()), \
				mock.patch.object(vat_check.frappe, "clear_last_message"), \
				mock.patch.object(vat_check.frappe, "session", mock.Mock(user="u@example.com")), \
				mock.patch.object(vat_check, "_", side_effect=lambda text: text + " {0}"):
			rows, warnings = vat_check.merged_report_rows("job", [("1.jpg", b"a"), ("2.jpg", b"b"), ("3.jpg", b"c")],
				["F1", "F2", "F3"])
		self.assertEqual([row["report_invoice_no"] for row in rows], ["IV1", "IV2", "IV3"])
		self.assertEqual(warnings, ["reconciliation.files_unread 3.jpg", "reconciliation.report_rows_repeated 1"])
		self.assertEqual(submit.call_count, 3)


class PurchaseInvoicesInTheSalesCheck(unittest.TestCase):
	def test_invoices_bought_by_the_reporting_company_are_named(self):
		# V04（2026-09-30）：放进来的全是 GIZE 自己收到的进项发票。
		from pearnly_cowork.recon_center import sheets

		inputs = {"seller_tax_id": "0105559093717", "invoices": [
			{"filename": "RN1.pdf", "buyer_tax_id": "0-1055-59093-71-7"}, {"filename": "RN1.pdf", "buyer_tax_id": "0105559093717"},
			{"filename": "IV2.pdf", "buyer_tax_id": "0105536000633"}]}
		with mock.patch.object(sheets, "_", side_effect=lambda text: text + " {0}"):
			self.assertEqual(sheets.purchase_invoice_notes(inputs), ["vat_check.note.purchase_invoices RN1.pdf"])
			self.assertEqual(sheets.purchase_invoice_notes({**inputs, "seller_tax_id": ""}), [])


class TenfoldAtABreak(unittest.TestCase):
	def test_an_amount_read_with_one_zero_too_many_is_suggested_not_changed(self):
		# BAY-CA-03（2026-09-30）：存入 1,500 读成 15,000；前后余额都对。
		from pearnly_cowork.reconciliation import revision

		def row(deposit, withdrawal, balance):
			return {"deposit": Decimal(deposit), "withdrawal": Decimal(withdrawal),
				"balance": None if balance is None else Decimal(balance)}

		statement = [row("0", "0", "1000.00"), row("15000.00", "0", "2500.00"), row("100.00", "0", "2600.00")]
		self.assertEqual(revision._tenfold(statement, 1, Decimal("1000.00")), "1,500.00")
		self.assertEqual(revision._tenfold([row("150.00", "0", "2500.00")], 0, Decimal("1000.00")), "1,500.00")
		# 下一行接不上（余额本身可疑）、倍数不是 10、方向相反：都不提示
		self.assertEqual(revision._tenfold([row("0", "0", "1000.00"), row("15000.00", "0", "2500.00"),
			row("100.00", "0", "9999.00")], 1, Decimal("1000.00")), "")
		self.assertEqual(revision._tenfold([row("3000.00", "0", "2500.00")], 0, Decimal("1000.00")), "")
		self.assertEqual(revision._tenfold([row("0", "15000.00", "2500.00")], 0, Decimal("1000.00")), "")


class WaitingForRoomInTheQueue(unittest.TestCase):
	"""V01（2026-09-30）：72 张小票一次提交，超出每租户待处理上限，整单报「识别繁忙」。"""

	def test_a_full_queue_is_waited_out_not_failed(self):
		from pearnly_core.execution.errors import QueueFull
		from pearnly_cowork.recon_center import admission

		tries = iter([QueueFull("full"), QueueFull("full"), "W1"])

		def create():
			value = next(tries)
			if isinstance(value, Exception):
				raise value
			return value

		with mock.patch.object(admission.frappe, "db", mock.MagicMock()), mock.patch.object(admission.time, "sleep") as sleep:
			self.assertEqual(admission.when_room(create), "W1")
		self.assertEqual(sleep.call_count, 2)

	def test_a_queue_still_full_at_the_deadline_is_reported(self):
		from pearnly_core.execution.errors import QueueFull
		from pearnly_cowork.recon_center import admission

		def create():
			raise QueueFull("full")

		with mock.patch.object(admission.frappe, "db", mock.MagicMock()), mock.patch.object(admission.time, "sleep"):
			with self.assertRaises(QueueFull):
				admission.when_room(create, deadline=0)


class SameBytesAsAnOriginal(unittest.TestCase):
	"""V02（2026-09-30）：销项核查上传的 PDF 与发票录入时的原件内容相同，原件保护按哈希把它当成改原件拒绝。"""

	def test_a_copy_of_an_original_is_stored_apart_and_read_back_unchanged(self):
		from pearnly_cowork.recon_center import storage

		inserted = {}

		def get_doc(value, name=None):
			if isinstance(value, dict):
				inserted.update(value)
				return mock.Mock(insert=mock.Mock(return_value=mock.Mock(name="F1")))
			return mock.Mock(file_name=inserted["file_name"], get_content=lambda: inserted["content"])

		with mock.patch.object(storage.access, "protected_sources", return_value=["S1"]), \
				mock.patch.object(storage.frappe, "get_doc", side_effect=get_doc):
			storage.save(b"%PDF-1.4 invoice", "Big C Jun.pdf")
			self.assertNotEqual(inserted["content"], b"%PDF-1.4 invoice")
			self.assertTrue(inserted["file_name"].endswith(".bin"))  # 不按 PDF 处理加了前缀的字节
			self.assertEqual(storage.read("F1"), ("Big C Jun.pdf", b"%PDF-1.4 invoice"))
		# 守住两点（用户 2026-09-30 定保持另存）：副本内容哈希与原件不同 —— 原件改公开 / 私有时 Frappe 按同哈希连带改，
		# 改不到副本；副本名字固定 .bin、存自己的路径 —— 删副本不会删到原件的文件。
		from frappe.core.doctype.file.utils import get_content_hash

		self.assertNotEqual(get_content_hash(inserted["content"]), get_content_hash(b"%PDF-1.4 invoice"))
		self.assertEqual(inserted["file_name"], storage.COPY_NAME)
		with mock.patch.object(storage.access, "protected_sources", return_value=[]), \
				mock.patch.object(storage.frappe, "get_doc", side_effect=get_doc):
			storage.save(b"%PDF-1.4 other", "other.pdf")
			self.assertEqual((inserted["file_name"], inserted["content"]), ("other.pdf", b"%PDF-1.4 other"))
			self.assertEqual(storage.read("F2"), ("other.pdf", b"%PDF-1.4 other"))


class NoChargeAfterTheReconciliationClosed(unittest.TestCase):
	def run_work(self, job_state):
		saved = {"body": {"legacy": {"pages": [{"document": {"entries": [{"deposit": "1"}]}}]}}}
		cache = mock.MagicMock()
		cache.get_value.return_value = {"state": job_state} if job_state else None
		checkpoints = {document_ocr.RESULT: saved}
		with mock.patch.object(document_ocr.frappe, "flags", mock.MagicMock(get=lambda key: "W1")), \
				mock.patch.object(document_ocr.frappe, "db", mock.MagicMock(get_value=lambda *a, **k: frappe._dict(
					kind=document_ocr.KIND, user="u@example.com", status="running"))), \
				mock.patch.object(document_ocr.frappe, "session", mock.Mock(user="Administrator")), \
				mock.patch.object(document_ocr.frappe, "set_user"), mock.patch.object(document_ocr.frappe, "cache", cache), \
				mock.patch.object(document_ocr.core_work, "checkpoint", side_effect=lambda key, *a: checkpoints.get(key)), \
				mock.patch.object(document_ocr, "_charge") as charge:
			outcome = document_ocr.run("job", "F1", "p.jpg", 1, "sha", "bank_statement", {"billing_tier": "t"})
		return outcome, charge

	def test_a_read_that_finishes_after_the_reconciliation_failed_is_not_charged(self):
		outcome, charge = self.run_work("failed")
		self.assertEqual(outcome, {"cancelled": "recon_job_closed"})
		charge.assert_not_called()

	def test_a_read_the_reconciliation_is_waiting_for_is_charged(self):
		outcome, charge = self.run_work("running")
		self.assertEqual(outcome, {})
		charge.assert_called_once()

	def test_a_read_whose_reconciliation_state_was_cleared_but_whose_file_is_gone_is_not_charged(self):
		# 部署 / 清缓存会清掉对账状态；对账结束时一定删掉它存的源文件。
		with mock.patch.object(document_ocr, "_recon_closed", wraps=document_ocr._recon_closed):
			db = mock.MagicMock(get_value=lambda *a, **k: frappe._dict(kind=document_ocr.KIND, user="u@example.com",
				status="running"), exists=lambda *a, **k: False)
			saved = {"body": {"legacy": {"pages": [{"document": {"entries": [{"deposit": "1"}]}}]}}}
			cache = mock.MagicMock()
			cache.get_value.return_value = None
			with mock.patch.object(document_ocr.frappe, "flags", mock.MagicMock(get=lambda key: "W1")), \
					mock.patch.object(document_ocr.frappe, "db", db), \
					mock.patch.object(document_ocr.frappe, "session", mock.Mock(user="Administrator")), \
					mock.patch.object(document_ocr.frappe, "set_user"), mock.patch.object(document_ocr.frappe, "cache", cache), \
					mock.patch.object(document_ocr.core_work, "checkpoint",
						side_effect=lambda key, *a: {document_ocr.RESULT: saved}.get(key)), \
					mock.patch.object(document_ocr, "_charge") as charge:
				outcome = document_ocr.run("job", "F1", "p.jpg", 1, "sha", "bank_statement", {"billing_tier": "t"})
		self.assertEqual(outcome, {"cancelled": "recon_job_closed"})
		charge.assert_not_called()


class WaitingForARead(unittest.TestCase):
	def run_wait(self, rows, checkpoints):
		clock = iter(range(0, 100000, 100))
		rows, checkpoints = iter(rows), iter(checkpoints)

		def get_value(doctype, name, fields, as_dict=False):
			return next(rows) if isinstance(fields, list) else next(checkpoints)

		with mock.patch.object(document_ocr.frappe, "db", mock.MagicMock(get_value=get_value)), \
				mock.patch.object(document_ocr.time, "monotonic", side_effect=lambda: next(clock)), \
				mock.patch.object(document_ocr.time, "sleep"), mock.patch.object(document_ocr, "_", lambda text: text), \
				mock.patch.object(document_ocr.frappe, "throw", side_effect=RuntimeError):
			return document_ocr.wait("W1")

	def test_a_read_that_finished_reading_at_the_deadline_is_waited_for(self):
		running = frappe._dict(status="running", last_code="")
		result = json.dumps({document_ocr.RESULT: {"state": "done", "body": {"legacy": {"pages": []}}}})
		legacy = self.run_wait([running] * 17 + [frappe._dict(status="succeeded", last_code="")], [result, result])
		self.assertEqual(legacy, {"pages": []})

	def test_a_read_still_reading_at_the_deadline_times_out(self):
		running = frappe._dict(status="running", last_code="")
		with self.assertRaises(RuntimeError):
			self.run_wait([running] * 40, ["{}"])


class NoCallAfterTheReconciliationClosed(unittest.TestCase):
	"""对账判失败后，识别任务下一个付费调用前就停（BAY-CA-03，2026-09-29：对账失败了识别还在读、占名额）。"""

	def test_the_next_paid_call_is_not_made_once_the_reconciliation_failed(self):
		states = iter([{"state": "running"}, {"state": "failed"}])
		cache = mock.MagicMock()
		cache.get_value.side_effect = lambda key: next(states)
		calls = []

		client = mock.Mock()
		client.document_ai_call.side_effect = lambda *a, **k: calls.append("docai") or {}
		with mock.patch.object(document_ocr, "build_client", return_value=client), \
				mock.patch.object(document_ocr.frappe, "cache", cache), \
				mock.patch.object(document_ocr.frappe, "db", mock.MagicMock()), \
				mock.patch.object(document_ocr, "PageSource", mock.MagicMock()), \
				mock.patch.object(document_ocr, "_mime", return_value="image/png"), \
				mock.patch.object(document_ocr, "read_file", side_effect=lambda source, content, is_pdf, mime, kind,
					docai, model: (docai("a"), docai("b"))):
			with self.assertRaises(document_ocr.ReconClosed):
				document_ocr._read(b"img", "p.png", 1, {"environment": {}, "target": dict.fromkeys(document_ocr.IDENTITY)},
					"bank_statement", "job")
		self.assertEqual(calls, ["docai"])


class ReviewWithBalanceBreaks(unittest.TestCase):
	"""用户 2026-09-29 选 B：需复核的银行对账有断点时，逐个确认成「此处缺页 / 已知差异」才可保存。"""

	def save(self, acknowledged):
		from decimal import Decimal

		from pearnly_cowork.reconciliation import revision

		statement = [{"date": date(2026, 5, 1), "deposit": Decimal("100"), "withdrawal": Decimal("0"), "balance": Decimal("1100")},
			{"date": date(2026, 5, 3), "deposit": Decimal("50"), "withdrawal": Decimal("0"), "balance": Decimal("1300")}]
		doc = mock.MagicMock(status="needs_review", run_info_json="{}")
		doc.name = "CBR-1"
		with mock.patch.object(revision, "bank_compute", return_value=((statement, []), {"stmt_opening": Decimal("1000")}, [], {})), \
				mock.patch.object(revision, "_", side_effect=lambda text: text + " {0} {1} {2} {3}"), \
				mock.patch.object(revision.frappe, "db", mock.MagicMock()), mock.patch.object(revision, "_record"), \
				mock.patch.object(revision.frappe, "throw", side_effect=lambda message, *a, **k: self.refuse(message)), \
				mock.patch.object(revision.bank_recon_service, "persist"), \
				mock.patch.object(revision.bank_recon_service, "_persist_rows"):
			self.assertEqual([item["idx"] for item in revision.bank_breaks(doc, {})], [1])
			revision.bank_save(doc, {}, acknowledged)
		return doc

	def test_a_file_left_unread_is_named_beside_the_title(self):
		from pearnly_cowork.reconciliation import revision

		doc = mock.MagicMock(status="completed", summary_json="{}", parse_info_json=json.dumps({"stmt_files": [
			{"file": "IMG_2500.JPG", "rows": 44, "ok": True}, {"file": "IMG_2501.JPG", "rows": 0, "ok": False},
			{"file": "cover.jpg", "rows": 0, "ok": False, "error_code": "no_movement"}],
			"gl_files": [{"file": "gl.xlsx", "rows": 9, "ok": True}]}))
		with mock.patch.object(revision, "bank_inputs", return_value={"statement": [], "gl": []}), \
				mock.patch.object(revision, "_contract", return_value=([], [])), \
				mock.patch.object(revision.bank_warnings, "detect_recon_mismatch", return_value=[]), \
				mock.patch.object(revision, "_", side_effect=lambda text: text + " {0}"), \
				mock.patch.object(revision.frappe, "local", mock.Mock(lang="zh")):
			notes = revision.bank_notes(doc)
		# 读成功、没有流水的封面只说已跳过（B05，2026-09-29）。
		self.assertEqual(notes, ["reconciliation.files_unread IMG_2501.JPG", "reconciliation.files_no_movement cover.jpg"])

	@staticmethod
	def refuse(message):
		raise frappe.ValidationError(message)

	def test_a_break_left_unmarked_is_not_saved(self):
		with self.assertRaises(frappe.ValidationError):
			self.save(None)
		with self.assertRaises(frappe.ValidationError):
			self.save({"1": "whatever"})

	def test_marked_breaks_are_saved_and_listed_in_the_result(self):
		doc = self.save({"1": "missing_page"})
		warnings = json.loads(doc.run_info_json)["warnings"]
		self.assertEqual(len(warnings), 1)
		self.assertIn("cowork.recon.break.missing_page", warnings[0])
		self.assertIn("2026-05-03", warnings[0])


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

	def run_job(self, kind, left, right, create_error=None):
		from pearnly_cowork.recon_center import jobs

		states, db, loaded = {}, mock.MagicMock(), {"left": left, "right": right}
		db.exists.return_value = False
		side_of = lambda names: "left" if names == ["L%d" % i for i in range(len(left))] else "right"  # noqa: E731
		with mock.patch.object(jobs, "_state", side_effect=lambda job, **values: states.update(values)), \
				mock.patch.object(jobs, "_read", side_effect=lambda names: loaded[side_of(names)]), \
				mock.patch.object(jobs.frappe, "session", mock.Mock(user="u@example.com")), \
				mock.patch.object(jobs.frappe, "db", db), \
				mock.patch.object(jobs.frappe, "log_error") as log_error, \
				mock.patch.object(jobs.frappe, "_", side_effect=lambda text: text), \
				mock.patch("pearnly_cowork.reconciliation.messages._", side_effect=lambda text: text), \
				mock.patch("pearnly_cowork.recon_center.document_ocr.submit") as submit, \
				mock.patch.object(jobs.bank_recon_service, "create_from_documents",
								  side_effect=create_error, return_value={"name": "T1"}):
			jobs.run("job", kind, "ws", "t", {"left": ["L%d" % i for i in range(len(left))],
											  "right": ["R%d" % i for i in range(len(right))]})
		return states, submit, log_error

	def test_passbook_photos_are_all_submitted_before_the_first_is_awaited(self):
		states, submit, _log = self.run_job("bank", [("1.jpg", b"a"), ("2.JPG", b"b"), ("stmt.pdf", b"%PDF")],
			[("gl.xlsx", b"x"), ("gl.png", b"y")])
		self.assertEqual(states["state"], "done")
		self.assertEqual([(call.args[1], call.args[3], call.args[5]) for call in submit.call_args_list], [
			("left0", "1.jpg", "bank_statement"), ("left1", "2.JPG", "bank_statement"),
			("right4", "gl.png", "general_ledger")])  # the PDF may still read free, the workbook needs no OCR

	def test_the_job_speaks_the_submitted_language(self):
		from pearnly_cowork.recon_center import jobs

		# Frappe 16 在任务里第一次翻译时按用户语言重设（lang_resolved 没标就重设）：要标成已定（BBL-CA-01，2026-09-30）。
		local = mock.Mock(lang="th", job=mock.Mock(lang_resolved=False))
		with mock.patch.object(jobs.frappe, "local", local), mock.patch.object(jobs, "_state"), \
				mock.patch.object(jobs, "_read", side_effect=RuntimeError("stop")), \
				mock.patch.object(jobs.frappe, "db", mock.MagicMock()), mock.patch.object(jobs.frappe, "log_error"), \
				mock.patch.object(jobs.frappe, "_", side_effect=lambda text, **_kw: text):
			jobs.run("job", "income", "ws", "t", {"left": [], "right": []}, json.dumps({"lang": "zh"}))
		self.assertEqual(local.lang, "zh")
		self.assertTrue(local.job.lang_resolved)

	def test_a_database_lock_is_not_reported_as_a_bad_file(self):
		states, _submit, log_error = self.run_job("bank", [("s.xlsx", b"a")], [("g.xlsx", b"b")],
			create_error=frappe.QueryTimeoutError("lock"))
		self.assertEqual(states["state"], "failed")
		self.assertEqual(states["error"], "reconciliation.system_busy")
		log_error.assert_called_once()

	def test_a_contract_error_is_logged_and_keeps_its_own_text(self):
		from pearnly_cowork.reconciliation.recon_contracts import ReconciliationContractError

		states, _submit, log_error = self.run_job("bank", [("s.xlsx", b"a")], [("g.xlsx", b"b")],
			create_error=ReconciliationContractError("reconciliation.parse_info_invalid:stmt_files"))
		self.assertNotEqual(states["error"], "reconciliation.system_busy")  # the contract text, as before
		log_error.assert_called_once()

	def status_after_cache_loss(self, stored=None, job=None, user="u@example.com"):
		import hashlib

		from pearnly_cowork.recon_center import jobs

		db, cache = mock.MagicMock(), mock.MagicMock()
		cache.get_value.return_value = None  # the deploy's clear-cache dropped the state
		key = hashlib.sha256(f"site.test|{user}|abc123".encode()).hexdigest()
		db.exists.side_effect = lambda doctype, name: (doctype, name) == stored(key) if stored else False
		with mock.patch.object(jobs.frappe, "cache", cache), mock.patch.object(jobs.frappe, "db", db), \
				mock.patch.object(jobs.frappe, "local", mock.Mock(site="site.test")), \
				mock.patch.object(jobs.frappe, "session", mock.Mock(user=user)), \
				mock.patch.object(jobs, "_", side_effect=lambda text: text), \
				mock.patch("frappe.utils.background_jobs.get_job", return_value=job):
			return jobs._recovered("abc123")

	def test_a_finished_job_is_found_by_its_task_after_the_cache_is_cleared(self):
		found = self.status_after_cache_loss(stored=lambda key: ("Cowork Income Reconciliation", "CIR-" + key[:40]))
		self.assertEqual((found["kind"], found["state"]), ("income", "done"))
		self.assertTrue(found["task"].startswith("CIR-"))

	def test_a_queued_job_is_found_in_the_queue_only_for_its_owner(self):
		job = mock.Mock(kwargs={"user": "u@example.com", "kwargs": {"kind": "bank"}})
		job.get_status.return_value = "queued"
		self.assertEqual(self.status_after_cache_loss(job=job)["state"], "queued")
		job.get_status.return_value = "started"
		self.assertEqual(self.status_after_cache_loss(job=job)["state"], "running")
		job.get_status.return_value = "finished"  # ended without a stored result: it failed
		self.assertEqual(self.status_after_cache_loss(job=job)["state"], "failed")
		self.assertIsNone(self.status_after_cache_loss(job=job, user="other@example.com"))
		self.assertIsNone(self.status_after_cache_loss())

	def test_a_job_abandoned_by_a_restart_stops_showing_as_running(self):
		# 测试机同步重启后，后台任务被判弃置移进失败队列，缓存还停在「进行中」，页面一直转圈（2026-09-29）。
		from pearnly_cowork.recon_center import jobs

		def status(job_state):
			cache, db = mock.MagicMock(), mock.MagicMock()
			cache.get_value.return_value = {"user": "u@example.com", "kind": "bank", "state": "running", "stage": "reading"}
			db.exists.return_value = False
			job = mock.Mock(kwargs={"user": "u@example.com", "kwargs": {"kind": "bank"}})
			job.get_status.return_value = job_state
			with mock.patch.object(jobs.frappe, "cache", cache), mock.patch.object(jobs.frappe, "db", db), \
					mock.patch.object(jobs.frappe, "local", mock.Mock(site="site.test")), \
					mock.patch.object(jobs.frappe, "session", mock.Mock(user="u@example.com")), \
					mock.patch.object(jobs, "_", side_effect=lambda text: text), \
					mock.patch("frappe.utils.background_jobs.get_job", return_value=job):
				return jobs.status("abc123")

		self.assertEqual(status("failed")["state"], "failed")
		self.assertEqual(status("started")["state"], "running")

	def test_uploads_are_saved_as_native_files_under_the_site_limit(self):
		from pearnly_cowork.recon_center import jobs

		from pearnly_cowork.recon_center import storage

		with mock.patch.object(jobs.frappe, "get_doc") as get_doc, \
				mock.patch.object(storage.access, "protected_sources", return_value=[]):
			get_doc.return_value.insert.return_value.name = "F1"
			self.assertEqual(jobs._save(b"x" * 11 * 1024 * 1024, "big.pdf"), "F1")
		self.assertEqual(get_doc.call_args.args[0]["is_private"], 1)
		get_doc.return_value.insert.assert_called_once_with(ignore_permissions=True)


if __name__ == "__main__":
	unittest.main()
