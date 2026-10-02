"""录入工作台按页的进度（用户 2026-09-30：「录入工作台按页分进度，不要按份」）。离线，不连站点。"""
import unittest
from types import SimpleNamespace
from unittest import mock



from pearnly_cowork.ocr.execution import store, workflow


class PagesRead(unittest.TestCase):
	def setUp(self):
		self.values = {}
		self.cache = mock.Mock(get_value=self.values.get,
			set_value=lambda key, value, expires_in_sec=None: self.values.__setitem__(key, value))

	def test_reaching_page_n_means_the_pages_before_it_are_read(self):
		doc = SimpleNamespace(name="B1", page_count=5)
		with mock.patch.object(store.frappe, "cache", self.cache, create=True):
			for page in (1, 1, 2, 4, 3):  # 同一页复核、续跑重放、先读后页的旧回报都不倒退
				store.pages_reached("B1", page)
			self.assertEqual(store.pages_done(doc, "running"), 3)
			self.assertEqual(store.pages_done(doc, "saved"), 5)
			self.assertEqual(store.pages_done(doc, "not_invoice"), 5)
			self.assertEqual(store.pages_done(SimpleNamespace(name="B2", page_count=5), "queued"), 0)

	def test_a_cache_that_fails_never_stops_recognition(self):
		broken = mock.Mock(get_value=mock.Mock(side_effect=RuntimeError), set_value=mock.Mock(side_effect=RuntimeError))
		with mock.patch.object(store.frappe, "cache", broken, create=True):
			store.pages_reached("B1", 3)
			self.assertEqual(store.pages_done(SimpleNamespace(name="B1", page_count=5), "running"), 0)

	def test_starting_to_read_is_pushed_to_the_submitter(self):
		with mock.patch.object(store.frappe, "publish_realtime", create=True) as publish:
			store.reading_started("B1", user="someone@example.com")
		publish.assert_called_once_with("pearnly_ocr_work", {"kind": "cowork.invoice", "reference": "B1", "reading": True},
			user="someone@example.com", after_commit=False)
		with mock.patch.object(store.frappe, "publish_realtime", side_effect=RuntimeError, create=True):
			store.reading_started("B1", user="someone@example.com")  # 推不了不影响识别

	def test_the_waiting_page_tells_queued_from_reading_by_the_batch_phase(self):
		def view(phase, work_status):
			doc = SimpleNamespace(name="B1", source_file="F1", page_count=1, phase=phase, work_name="W1",
				billing_state="", billing_code="", requires_review=0, history_records="[]", get=lambda key, default=None: None,
				billing_units=1)
			work = SimpleNamespace(status=work_status, last_code="", attempts=1)
			with mock.patch.object(store.access, "require_batch"), \
					mock.patch.object(store.frappe, "db", SimpleNamespace(get_value=lambda *a, **k: work), create=True), \
					mock.patch.object(store.frappe, "cache", self.cache, create=True):
				return store.project(doc)["reading"]
		self.assertFalse(view("accepted", "queued"))          # 还在队列里
		self.assertTrue(view("recognizing", "deferred"))      # 交给云端读着
		self.assertTrue(view("recognized", "queued"))         # 读完等入库
		self.assertFalse(view("saved", "succeeded"))          # 已有结论

	def test_the_page_comes_from_the_model_request_or_the_vision_call(self):
		self.assertEqual(workflow._call_page((SimpleNamespace(page_number=4),), {}), 4)
		self.assertEqual(workflow._call_page((object(),), {"stage": "layer1", "page_number": 2}), 2)
		self.assertIsNone(workflow._call_page((), {}))
		self.assertIsNone(workflow._call_page((SimpleNamespace(page_number=True),), {}))


if __name__ == "__main__":
	unittest.main()
