"""外部结果到了就唤醒等着的识别任务（Cowork 交给 Cloud Tasks 的识别做完回调，2026-10-01）。

离线跑：数据库、缓存、队列都用替身。重点是「回调比派发它的那次运行收尾还快」时不漏掉唤醒。
"""
import unittest
from unittest.mock import MagicMock, patch

import frappe

from pearnly_core.execution import work


class Cache:
    def __init__(self):
        self.values = {}

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value

    def get_value(self, key):
        return self.values.get(key)

    def delete_value(self, key):
        self.values.pop(key, None)


class Wake(unittest.TestCase):
    def setUp(self):
        self.cache, self.db = Cache(), MagicMock()
        patchers = (patch.object(work.frappe, "cache", self.cache), patch.object(work.frappe, "db", self.db),
                    patch.object(work, "kick"))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.kick = work.kick

    def test_a_deferred_work_is_put_back_at_once(self):
        self.db.sql.return_value = [("deferred",)]
        self.assertTrue(work.wake("W1"))
        self.db.set_value.assert_called_once()
        self.assertEqual(self.db.set_value.call_args.args[:2], (work.DT, "W1"))
        self.kick.assert_called_once_with("W1")

    def test_a_running_work_is_remembered_and_finishes_without_its_long_delay(self):
        self.db.sql.return_value = [("running",)]
        self.assertFalse(work.wake("W1"))
        self.kick.assert_not_called()
        self.db.reset_mock()
        self.db.sql.return_value = [("running",)]
        with patch.object(work.frappe, "publish_realtime"), patch("pearnly_core.execution.dispatch.later") as later, \
                patch.object(work, "_finished"):
            work._finish("W1", "deferred", "remote_dispatched", 900)
        values = self.db.set_value.call_args.args[2]
        self.assertLess((values["next_run"] - work.utcnow()).total_seconds(), 5)
        later.assert_called_once_with("W1", 0)
        self.assertIsNone(self.cache.get_value(work.WAKE + "W1"))   # 只用一次

    def test_a_wake_does_not_clear_a_delay_the_result_itself_asked_for(self):
        self.db.sql.return_value = [("running",)]
        work.wake("W1")
        with patch("pearnly_core.execution.dispatch.later") as later, patch.object(work, "_finished"):
            work._finish("W1", "deferred", "provider_429", 60)
        later.assert_called_once_with("W1", 60)

    def test_a_wake_handled_by_requeueing_leaves_no_mark_behind(self):
        self.db.sql.return_value = [("deferred",)]
        work.wake("W1")
        self.assertIsNone(self.cache.get_value(work.WAKE + "W1"))

    def test_without_a_wake_the_delay_is_kept(self):
        self.db.sql.return_value = [("running",)]
        with patch("pearnly_core.execution.dispatch.later") as later, patch.object(work, "_finished"):
            work._finish("W1", "deferred", "remote_dispatched", 900)
        later.assert_called_once_with("W1", 900)

    def test_a_finished_work_is_left_alone(self):
        self.db.sql.return_value = [("succeeded",)]
        self.assertFalse(work.wake("W1"))
        self.db.set_value.assert_not_called()
        self.kick.assert_not_called()


class ExternalCountsAsPaid(unittest.TestCase):
    """交给外部执行的（Cowork 交给 Cloud Tasks）：过了未付费的排队时限也照常续跑（云上可能已付费），只受付费宽限上限。"""

    def run_work(self, state, expired_for):
        import json
        from datetime import timedelta

        now = work.utcnow()
        doc = frappe._dict(name="W1", kind="cowork.recon_document", status="deferred", lease_until=None, next_run=None,
                           attempts=1, expires_at=now - expired_for, user="u@example.com", arguments="{}")
        db = MagicMock()
        db.sql.return_value = [doc]
        handler = MagicMock(return_value={})
        with patch.object(work.frappe, "db", db), patch.object(work.frappe, "conf", frappe._dict()), \
                patch.object(work.frappe, "get_hooks", return_value=[]), patch.object(work, "_finish") as finish, \
                patch.object(work, "handler", return_value="x.run"), patch.object(work.frappe, "get_attr", return_value=handler), \
                patch.object(work, "job_language"), patch.object(work.frappe, "flags", frappe._dict()), \
                patch.object(work, "stored_checkpoints", return_value={"k": {"state": state, "body": {}}}), \
                patch("pearnly_core.execution.resources.check_memory"):
            work.run("W1")
        return finish

    def test_an_external_work_past_the_unpaid_limit_still_runs(self):
        from datetime import timedelta

        self.run_work(work.EXTERNAL, timedelta(minutes=10)).assert_called_once_with("W1", "succeeded")
        self.run_work("reply", timedelta(minutes=10)).assert_called_once_with("W1", "failed", "queue_expired")
        self.run_work(work.EXTERNAL, work.PAID_GRACE + timedelta(minutes=1)).assert_called_once_with(
            "W1", "failed", "queue_expired")


if __name__ == "__main__":
    unittest.main()
