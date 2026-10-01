"""多页文件各页同时读的执行方式（pages.py）。离线：Frappe 的站点初始化与数据库用替身。"""
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import frappe

from pearnly_core.execution import pages


class PageThreads(unittest.TestCase):
    def setUp(self):
        frappe.local.site = "tenant.example"
        frappe.local.flags = frappe._dict(pearnly_ocr_work="w1")
        frappe.local.lang = "zh"
        self.addCleanup(lambda: [delattr(frappe.local, key) for key in ("site", "flags", "lang")
                                 if key in dict(iter(frappe.local))])
        self.seen, self.lock = [], threading.Lock()

        @contextmanager
        def context(name):
            with self.lock:
                self.seen.append(("work", name))
            yield

        def init(site=None, **kwargs):
            frappe.local.site = site
            frappe.local.flags = frappe._dict()

        for item in (patch.object(pages.frappe, "init", side_effect=init), patch.object(pages.frappe, "connect"),
                     patch.object(pages.frappe, "set_user", side_effect=lambda user: self.note("user", user)),
                     patch.object(pages.frappe, "destroy"), patch.object(pages.frappe, "db", MagicMock()),
                     patch.object(pages.frappe, "session", frappe._dict(user="actor@example.com")),
                     patch("pearnly_core.execution.work.context", side_effect=context)):
            item.start()
            self.addCleanup(item.stop)

    def note(self, *value):
        with self.lock:
            self.seen.append(value)

    def test_off_by_default(self):
        with patch.dict(pages.os.environ, {}, clear=True):
            self.assertIsNone(pages.mapper())
        with patch.dict(pages.os.environ, {"PEARNLY_OCR_PAGE_THREADS": "bad"}, clear=True):
            self.assertIsNone(pages.mapper())

    def test_each_page_runs_in_its_own_site_context_and_results_keep_page_order(self):
        done = []
        with patch.dict(pages.os.environ, {"PEARNLY_OCR_PAGE_THREADS": "4"}, clear=True):
            run = pages.mapper(on_done=done.append)

            def read(number):
                assert frappe.local.site == "tenant.example" and frappe.local.lang == "zh"
                return (number, threading.current_thread().name)
            results = run(read, range(1, 7))
        self.assertEqual([number for number, _ in results], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(name.startswith("pearnly-ocr-page") for _, name in results))
        self.assertEqual(sorted(done), [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.seen.count(("work", "w1")), 6)
        self.assertEqual(self.seen.count(("user", "actor@example.com")), 6)

    def test_the_lowest_page_error_is_raised_after_every_page_finished(self):
        finished = []
        with patch.dict(pages.os.environ, {"PEARNLY_OCR_PAGE_THREADS": "3"}, clear=True):
            run = pages.mapper()

            def read(number):
                if number in (2, 4):
                    raise RuntimeError(f"page {number}")
                finished.append(number)
                return number
            with self.assertRaises(RuntimeError) as caught:
                run(read, range(1, 6))
        self.assertEqual(str(caught.exception), "page 2")
        self.assertEqual(sorted(finished), [1, 3, 5])
        pages.frappe.db.rollback.assert_called()


if __name__ == "__main__":
    unittest.main()
