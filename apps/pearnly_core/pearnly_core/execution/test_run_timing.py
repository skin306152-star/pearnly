"""每次识别运行一行计时（2026-10-02 压测窗口：100 人时发票识别以外多出约 30 秒，要分清是排队还是处理）。

离线跑：数据库、处理函数都用替身。
"""
import contextlib
import io
import json
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

import frappe

from pearnly_core.execution import work
from pearnly_core.execution.errors import Deferred


class RunTiming(unittest.TestCase):
    def run_work(self, handler):
        now = work.utcnow()
        doc = frappe._dict(name="W" * 64, kind="cowork.invoice", status="queued", lease_until=None,
                           next_run=now - timedelta(seconds=7), attempts=1, expires_at=now + timedelta(hours=1),
                           user="u@example.com", arguments="{}", checkpoints="{}")
        db = MagicMock()
        db.sql.return_value = [doc]
        out = io.StringIO()
        with patch.object(work.frappe, "db", db), patch.object(work.frappe, "conf", frappe._dict()), \
                patch.object(work.frappe, "get_hooks", return_value={}), \
                patch.object(work, "_finish") as finish, patch.object(work, "handler", return_value="x.run"), \
                patch.object(work.frappe, "get_attr", return_value=handler), patch.object(work, "job_language"), \
                patch.object(work.frappe, "flags", frappe._dict()), patch.object(work.frappe, "local", frappe._dict(site="s1")), \
                patch.object(work, "stored_checkpoints", return_value={}), \
                patch("pearnly_core.execution.resources.check_memory"), contextlib.redirect_stdout(out):
            work.run("W1")
        return finish, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_a_run_logs_its_wait_duration_and_the_product_steps(self):
        def handler():
            with work.step("persist"):
                pass
            with work.step("persist"):
                pass
            return {}

        finish, line = self.run_work(handler)
        finish.assert_called_once_with("W1", "succeeded")
        self.assertEqual(line["event"], "ocr_run")
        self.assertEqual((line["site"], line["kind"], line["attempt"], line["outcome"]), ("s1", "cowork.invoice", 2, "succeeded"))
        self.assertGreaterEqual(line["ready"], 7)
        self.assertGreaterEqual(line["wall"], 0)
        self.assertEqual(sorted(line["steps"]), ["persist"])
        self.assertIn(len(line["steps"]["persist"]), (2, 3))  # 第三项：等 CPU 排队秒（Linux 才有）
        self.assertIn("rq", line)

    def test_a_deferred_run_names_its_reason(self):
        def handler():
            raise Deferred("remote_dispatched", 300)

        finish, line = self.run_work(handler)
        finish.assert_called_once_with("W1", "deferred", "remote_dispatched", 300)
        self.assertEqual(line["outcome"], "deferred:remote_dispatched")

    def test_a_step_outside_a_run_does_nothing(self):
        with patch.object(work.frappe, "flags", frappe._dict()):
            with work.step("x"):
                pass
            self.assertIsNone(work.frappe.flags.get("pearnly_ocr_timing"))


if __name__ == "__main__":
    unittest.main()
