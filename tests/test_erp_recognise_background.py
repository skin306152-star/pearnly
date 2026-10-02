"""ERP 网页识别放后台跑、页面按号轮询（2026-10-02：同步请求读多页 PDF 会撞 gunicorn 120 秒）。在生产镜像里跑（用镜像里的 frappe），不连站点。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_erp_portal"):
    sys.path.insert(0, str(ROOT / "apps" / app))
for name in [n for n in sys.modules if n.split(".")[0] in ("pearnly_core", "pearnly_erp_portal")]:
    del sys.modules[name]

import frappe  # noqa: E402

from pearnly_erp_portal.recognition import background, progress  # noqa: E402

assert Path(background.__file__).is_relative_to(ROOT), background.__file__


class Store:
    def __init__(self):
        self.data = {}

    def set_value(self, key, value, expires_in_sec=None):
        self.data[key] = value

    def get_value(self, key):
        return self.data.get(key)


def _throw(message, exc=None, *args, **kwargs):
    raise (exc or frappe.ValidationError)(message)


class BackgroundRecognition(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        frappe.local.site = "erp.example.invalid"
        frappe.local.lang = "th"
        frappe.local.session = frappe._dict(user="clerk@example.invalid")
        for target, kwargs in (("pearnly_core.execution.state.store", {"return_value": self.store}),
                               ("frappe.db", {"new": MagicMock()}),
                               ("frappe._", {"new": lambda text, *a, **kw: text}),  # 没有站点，不加载翻译
                               ("frappe.throw", {"new": _throw}),  # 没有请求上下文，不记消息
                               ("pearnly_erp_portal.recognition.background._", {"new": lambda text, *a, **kw: text})):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def test_start_queues_on_the_recognition_workers_and_returns_at_once(self):
        with patch("frappe.enqueue") as enqueue:
            self.assertEqual(background.start("sales", "token-abcdefgh", attachments=[{"file": "a"}], document_name="S-1"),
                             {"queued": "token-abcdefgh"})
        kwargs = enqueue.call_args.kwargs
        self.assertEqual(enqueue.call_args.args[0], "pearnly_erp_portal.recognition.background.run")
        self.assertEqual((kwargs["queue"], kwargs["user"], kwargs["lang"], kwargs["kind"]), ("ocr", "clerk@example.invalid", "th", "sales"))
        self.assertTrue(kwargs["deduplicate"])
        self.assertEqual(kwargs["arguments"], {"attachments": [{"file": "a"}], "document_name": "S-1"})
        with patch.object(progress, "_job_alive", return_value=True):
            self.assertEqual(progress.read("token-abcdefgh")["state"], "queued")

    def test_bad_token_is_refused_before_queueing(self):
        with patch("frappe.enqueue") as enqueue, self.assertRaises(frappe.ValidationError):
            background.start("sales", "x", attachments=[])
        enqueue.assert_not_called()

    def run_job(self, recognise):
        with patch("frappe.get_attr", return_value=recognise), patch("frappe.set_user") as set_user, \
             patch("frappe.log_error") as log_error:
            background.run("purchase", "token-abcdefgh", "clerk@example.invalid", "th", {"attachments": [], "company": "C"})
        set_user.assert_called_once_with("clerk@example.invalid")
        with patch.object(progress, "_job_alive", return_value=False):
            return progress.read("token-abcdefgh"), log_error

    def test_job_reports_pages_and_stores_the_result(self):
        def recognise(attachments, company, progress):
            progress(1, 2)
            progress(2, 2)
            return {"doc_no": "INV-1"}
        state, _ = self.run_job(recognise)
        self.assertEqual(state, {"state": "done", "done": 2, "total": 2, "result": {"doc_no": "INV-1"}})

    def test_reasons_for_people_reach_the_page_unchanged(self):
        def recognise(**_):
            raise frappe.ValidationError("This is not a purchase or sales document")
        state, log_error = self.run_job(recognise)
        self.assertEqual((state["state"], state["message"]), ("failed", "This is not a purchase or sales document"))
        log_error.assert_not_called()

    def test_unexpected_errors_are_logged_and_shown_generically(self):
        def recognise(**_):
            raise RuntimeError("boom")
        state, log_error = self.run_job(recognise)
        self.assertEqual(state["state"], "failed")
        self.assertNotIn("boom", state["message"])
        log_error.assert_called_once()

    def test_a_job_that_died_without_an_answer_reads_as_failed(self):
        progress.queued("token-abcdefgh", "erp-recognise:clerk@example.invalid:token-abcdefgh")
        with patch.object(progress, "_job_alive", return_value=False):
            self.assertEqual(progress.read("token-abcdefgh")["state"], "failed")
        with patch.object(progress, "_job_alive", return_value=True):
            self.assertEqual(progress.read("token-abcdefgh")["state"], "queued")

    def test_each_user_only_reads_their_own(self):
        progress.queued("token-abcdefgh", "job")
        frappe.local.session = frappe._dict(user="other@example.invalid")
        self.assertEqual(progress.read("token-abcdefgh"), {})


if __name__ == "__main__":
    unittest.main()
