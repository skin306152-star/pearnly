"""对账识别看板的真实进度（用户 2026-09-30：「不想看这个大白屏，起码要知道他在干嘛」）。离线：缓存、数据库用替身。"""
import json
import unittest
from unittest import mock

from pearnly_cowork.recon_center import document_ocr, progress


class FakeCache:
	def __init__(self):
		self.values, self.hashes = {}, {}

	def get_value(self, key):
		return self.values.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.values[key] = json.loads(json.dumps(value))

	def hset(self, name, key, value):
		self.hashes.setdefault(name, {})[key.encode()] = json.loads(json.dumps(value))

	def hgetall(self, name):
		return dict(self.hashes.get(name, {}))

	def make_key(self, name):
		return name

	def expire(self, name, seconds):
		pass


class Progress(unittest.TestCase):
	def setUp(self):
		self.cache = FakeCache()
		for patcher in (mock.patch.object(progress.frappe, "cache", self.cache),
				mock.patch("pearnly_core.execution.state.store", return_value=self.cache)):
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
		with mock.patch("datetime.date", wraps=__import__("datetime").date) as clock:
			clock.today.return_value = __import__("datetime").date(2026, 9, 30)
			self.assertEqual(progress.page_summary(document, "stmt.pdf", 5), {"name": "stmt.pdf", "page": 5, "rows": 2,
				"date": "2026-05-17", "kind": "deposit", "amount": "11,556.00"})
			# 3.8 逐页抽取的页还没经余额链校正：只说读出几笔，不给金额（压测第 25 轮 B01：200,449.37 其实是 450）
			self.assertEqual(progress.page_summary(document, "stmt.pdf", 5, amounts=False)["amount"], "")
			# 两位年没换算（1983）的日期不显示
			self.assertEqual(progress.page_summary({"entries": [{"transaction_date": "1983-03-06", "deposit": "5.00"}]},
				"s.pdf", 1)["date"], "")
		self.assertIsNone(progress.page_summary({"entries": []}, "stmt.pdf", 6))

	def test_a_page_with_imputed_lines_shows_no_amount(self):
		# 压测第 26 轮 B01 IMG_2485：本地拼行「算平」了，但最后一笔是为接上读错的余额补出来的
		document = {"entries": [{"transaction_date": "2026-05-06", "deposit": "200449.37", "chain_amount_imputed": True},
			{"transaction_date": "2026-05-06", "deposit": "450.00"}]}
		item = progress.page_summary(document, "IMG_2485.JPG", 1)
		self.assertEqual((item["rows"], item["amount"], item["date"]), (2, "", ""))

	def test_a_read_that_reached_reading_never_shows_queued_again(self):
		# 压测第 26 轮 B01 IMG_2492：还没读到字就让出，read/done 都是 0，原来退回「排队中」
		progress.update("F1", name="IMG_2492.JPG", pages=1, state="waiting", work="W1")
		db = mock.MagicMock()
		with mock.patch.object(progress.frappe, "db", db):
			db.get_value.return_value = mock.Mock(status="running", creation="2026-09-30 06:00:00")
			first = progress.job_view("job", {"stage": "statement", "files": [{"file": "F1", "side": "left"}]})
			db.get_value.return_value = mock.Mock(status="queued", creation="2026-09-30 06:00:00")
			second = progress.job_view("job", {"stage": "statement", "files": [{"file": "F1", "side": "left"}]})
		self.assertEqual((first["files"][0]["state"], second["files"][0]["state"]), ("reading", "reading"))

	def test_progress_never_goes_backwards(self):
		# 压测第 25 轮 B01：两个进程同时写，(done,1,1) 被旧的 (waiting,0,0) 盖回去
		progress.update("F1", name="a.jpg", pages=1, state="waiting")
		progress.advance("F1", state="done", read=1, done=1)
		progress.advance("F1", state="reading", read=0, work="W1")
		current = progress.get("F1")
		self.assertEqual((current["state"], current["read"], current["done"], current["work"]), ("done", 1, 1, "W1"))

	def test_a_started_read_is_not_shown_as_queued_while_it_yields(self):
		progress.update("F1", name="s.pdf", pages=12, state="waiting", work="W1")
		progress.advance("F1", state="reading", read=12, done=5)
		db = mock.MagicMock()
		db.get_value.return_value = mock.Mock(status="queued", creation="2026-09-30 06:00:00")
		with mock.patch.object(progress.frappe, "db", db):
			view = progress.job_view("job", {"stage": "statement", "files": [{"file": "F1", "side": "left"}]})
		self.assertEqual(view["files"][0]["state"], "reading")

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

	def test_an_active_reconciliation_keeps_when_it_started(self):
		# 「进行中」一页的开始时间列（2026-09-30）：同一份再登记只留一条、最新的在前。
		with mock.patch.object(progress, "now", return_value="2026-09-30 15:04:05"):
			progress.active_add("u@x", "job-1", "bank", "B01")
			progress.active_add("u@x", "job-2", "bank", "B02")
			progress.active_add("u@x", "job-1", "bank", "B01")
		self.assertEqual([(item["job"], item["started"]) for item in progress.active("u@x", "bank")],
			[("job-1", "2026-09-30 15:04:05"), ("job-2", "2026-09-30 15:04:05")])
		progress.active_remove("u@x", "job-1")
		self.assertEqual([item["job"] for item in progress.active("u@x", "bank")], ["job-2"])

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

		def read_file(source, content, is_pdf, mime, kind, docai, model, map_pages=None, environment=None):
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
						mock.patch.object(progress.frappe, "cache", cache), mock.patch("pearnly_core.execution.state.store", return_value=cache), \
						mock.patch.object(document_ocr.frappe, "cache", cache), mock.patch("pearnly_core.execution.state.store", return_value=cache), \
						mock.patch.object(document_ocr.frappe, "db", mock.MagicMock()), \
						mock.patch.object(document_ocr, "PageSource", mock.MagicMock()), \
						mock.patch.object(document_ocr, "read_file", side_effect=read_file):
					document_ocr._read(b"%PDF", "s.pdf", 3, {"environment": {}, "target": dict.fromkeys(document_ocr.IDENTITY)},
						"bank_statement", "job", "F1")
			finally:
				client.model_call.side_effect = model_call

		run_once({2})
		run_once(set())  # 续跑：已完成的调用从检查点取回、照样经过；数字取较大的，不翻倍
		with mock.patch.object(progress.frappe, "cache", cache), mock.patch("pearnly_core.execution.state.store", return_value=cache):
			current = progress.get("F1")
			self.assertEqual((current["read"], current["done"], current["unread"]), (3, 3, [2]))
			self.assertEqual((progress.latest_items("job")[0]["rows"], progress.latest_items("job")[0]["amount"]), (1, ""))


class FinishedNotice(unittest.TestCase):
	def test_the_starter_is_told_when_it_finishes_and_a_failed_push_changes_nothing(self):
		from pearnly_cowork.recon_center import jobs

		with mock.patch.object(jobs.frappe, "session", mock.Mock(user="u@example.com")), \
				mock.patch.object(jobs.frappe, "publish_realtime") as push:
			jobs._announce("job", "bank", "BAY / GL", task="CBR-1")
		push.assert_called_once()
		self.assertEqual(push.call_args.args[0], "cowork_recon_finished")
		self.assertEqual((push.call_args.args[1]["task"], push.call_args.kwargs["user"]), ("CBR-1", "u@example.com"))
		with mock.patch.object(jobs.frappe, "session", mock.Mock(user="u@example.com")), \
				mock.patch.object(jobs.frappe, "publish_realtime", side_effect=RuntimeError("socket down")):
			jobs._announce("job", "bank", "BAY / GL", error="failed")  # 不抛


if __name__ == "__main__":
	unittest.main()
