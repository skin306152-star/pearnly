"""对账识别看板的真实进度（用户 2026-09-30：「不想看这个大白屏，起码要知道他在干嘛」）。离线：缓存、数据库用替身。"""
import json
import unittest
from unittest import mock

from pearnly_cowork.recon_center import document_ocr, progress


class FakeCache:
	def __init__(self):
		self.values = {}

	def get_value(self, key):
		return self.values.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.values[key] = json.loads(json.dumps(value))


class Progress(unittest.TestCase):
	def setUp(self):
		self.cache = FakeCache()
		patcher = mock.patch.object(progress.frappe, "cache", self.cache)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_pages_are_counted_from_the_upload(self):
		import pymupdf

		document = pymupdf.open()
		for _ in range(3):
			document.new_page()
		self.assertEqual(progress.page_count("s.pdf", document.tobytes()), 3)
		self.assertEqual(progress.page_count("p.jpg", b"\xff\xd8"), 1)
		self.assertEqual(progress.page_count("gl.xlsx", b"PK"), 0)

	def test_a_page_summary_is_the_last_line_actually_read(self):
		document = {"entries": [{"transaction_date": "2026-05-16", "deposit": "", "withdrawal": "100.00"},
			{"transaction_date": "2026-05-17T09:47:29", "deposit": "11,556.00", "withdrawal": ""}]}
		self.assertEqual(progress.page_summary(document, "stmt.pdf", 5), {"name": "stmt.pdf", "page": 5, "rows": 2,
			"date": "2026-05-17", "kind": "deposit", "amount": "11,556.00"})
		self.assertIsNone(progress.page_summary({"entries": []}, "stmt.pdf", 6))

	def test_the_latest_lines_keep_the_newest_first_and_no_repeats(self):
		for page in range(1, 9):
			progress.latest("job", {"name": "s.pdf", "page": page})
		progress.latest("job", {"name": "s.pdf", "page": 8})
		self.assertEqual([item["page"] for item in progress.latest_items("job")], [8, 7, 6, 5, 4, 3])

	def test_files_not_read_by_recognition_are_done_once_matching_starts(self):
		progress.update("F1", name="gl.xlsx", pages=0, state="waiting")
		progress.update("F2", name="s.pdf", pages=3, state="done", read=3, done=2, unread=[2])
		state = {"stage": "matching", "files": [{"file": "F1", "side": "right"}, {"file": "F2", "side": "left"}]}
		with mock.patch.object(progress.frappe, "db", mock.MagicMock()):
			view = progress.job_view("job", state)
		self.assertEqual([(item["name"], item["state"]) for item in view["files"]], [("gl.xlsx", "done"), ("s.pdf", "done")])
		self.assertEqual(view["files"][1]["unread"], [2])

	def test_a_queued_read_says_how_many_are_ahead(self):
		progress.update("F1", name="s.pdf", pages=12, state="waiting", work="W1")
		db = mock.MagicMock()
		db.get_value.return_value = mock.Mock(status="queued", creation="2026-09-30 06:00:00")
		db.count.return_value = 2
		with mock.patch.object(progress.frappe, "db", db):
			view = progress.job_view("job", {"stage": "statement", "files": [{"file": "F1", "side": "left"}]})
		self.assertEqual((view["files"][0]["state"], view["files"][0]["ahead"]), ("queued", 2))

	def test_an_unavailable_cache_never_breaks_the_reconciliation(self):
		with mock.patch.object(progress.frappe, "cache", None):
			self.assertEqual(progress.get("F1"), {})
			progress.update("F1", read=3)
			progress.latest("job", {"page": 1})
			self.assertEqual(progress.latest_items("job"), [])


class RecognitionCountsItsPages(unittest.TestCase):
	def test_reads_and_extracted_pages_are_counted_and_a_resume_does_not_double_count(self):
		from pearnly_cowork.ocr.engine.contracts import ModelResponse
		from pearnly_cowork.ocr.engine.errors import ContentError

		cache = FakeCache()
		response = ModelResponse(text=json.dumps({"entries": [{"transaction_date": "2026-05-02", "deposit": "50.00"}]}),
			raw={}, input_tokens=1, output_tokens=1, model="m")
		client = mock.Mock()
		client.document_ai_call.return_value = ({}, 1, "key")
		client.model_call.side_effect = lambda request, **_kw: response

		def read_file(source, content, is_pdf, mime, kind, docai, model):
			docai(b"a", "application/pdf", expected_pages=2, page_number=1)
			docai(b"b", "application/pdf", expected_pages=1, page_number=2)
			for number in (1, 2, 3):
				request = mock.Mock(stage="enterprise_schema", page_number=number)
				try:
					model(request)
				except ContentError:
					pass
			return {"pages": []}

		def run_once(lost):
			model_call = client.model_call.side_effect

			def call(request, **kwargs):
				if request.page_number in lost:
					from pearnly_core.execution.errors import Uncertain
					raise Uncertain("read timeout")
				return model_call(request, **kwargs)
			client.model_call.side_effect = call
			try:
				with mock.patch.object(document_ocr, "build_client", return_value=client), \
						mock.patch.object(progress.frappe, "cache", cache), \
						mock.patch.object(document_ocr.frappe, "cache", cache), \
						mock.patch.object(document_ocr.frappe, "db", mock.MagicMock()), \
						mock.patch.object(document_ocr, "PageSource", mock.MagicMock()), \
						mock.patch.object(document_ocr, "read_file", side_effect=read_file):
					document_ocr._read(b"%PDF", "s.pdf", 3, {"environment": {}, "target": dict.fromkeys(document_ocr.IDENTITY)},
						"bank_statement", "job", "F1")
			finally:
				client.model_call.side_effect = model_call

		run_once({2})
		run_once(set())  # 续跑：已完成的调用从检查点取回、照样经过；数字取较大的，不翻倍
		with mock.patch.object(progress.frappe, "cache", cache):
			current = progress.get("F1")
			self.assertEqual((current["read"], current["done"], current["unread"]), (3, 3, [2]))
			self.assertEqual(progress.latest_items("job")[0]["amount"], "50.00")


if __name__ == "__main__":
	unittest.main()
