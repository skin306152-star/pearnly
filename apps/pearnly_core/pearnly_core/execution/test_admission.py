"""受理不再用全站锁（2026-10-01）：名额原子预占、重复提交由主键兜底。离线：数据库、Redis 用替身。

真实 Redis / 数据库上的并发核验见 docs/work/2026-10-01-ocr-checkpoint-rows.md（测试环境 30 份并发 3.27 → 0.89 秒）。
"""
import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

from pearnly_core.execution import work
from pearnly_core.execution.errors import QueueFull

CFG = {"concurrency": 4, "tenant_concurrency": 2, "rpm": 30, "pending": 300, "tenant_pending": 50, "max_age": 1800}


class Admission(unittest.TestCase):
    def setUp(self):
        self.db, self.client = MagicMock(), MagicMock()
        self.db.get_value.return_value = None
        self.db.count.return_value = 0
        self.db.exists.return_value = False
        self.client.eval.return_value = 1
        self.doc = MagicMock()
        for item in (patch.object(work.frappe, "db", self.db), patch.object(work.frappe, "conf", frappe._dict()),
                     patch.object(work.frappe, "local", MagicMock(site="tenant.example")),
                     patch.object(work, "handler", return_value="x.run"),
                     patch.object(work.budget, "config", return_value=dict(CFG)),
                     patch.object(work.budget, "redis", return_value=self.client),
                     patch.object(work.budget, "tenant", return_value="t1"),
                     patch.object(work, "kick", return_value=True),
                     patch.object(work.frappe, "get_doc", return_value=self.doc)):
            item.start()
            self.addCleanup(item.stop)

    def submit(self):
        return work._submit("cowork.invoice", "ref-1", {"batch_name": "b1"}, "u@example.com")

    def test_no_global_lock_and_one_atomic_reservation(self):
        name = self.submit()
        self.client.lock.assert_not_called()
        self.assertEqual(self.client.eval.call_count, 1)
        self.assertIs(self.client.eval.call_args.args[0], work.RESERVE)
        self.db.savepoint.assert_called_once_with("pearnly_ocr_admission")
        self.db.commit.assert_called_once()
        self.assertEqual(len(name), 64)

    def test_a_full_queue_is_refused_before_any_write(self):
        self.client.eval.return_value = 0
        with self.assertRaises(QueueFull):
            self.submit()
        self.doc.insert.assert_not_called()
        self.db.count.return_value = CFG["tenant_pending"]
        self.client.eval.reset_mock()
        with self.assertRaises(QueueFull):
            self.submit()
        self.client.eval.assert_not_called()

    def test_a_concurrent_duplicate_keeps_the_callers_transaction_and_the_winners_slot(self):
        encoded = json.dumps({"batch_name": "b1"}, sort_keys=True, ensure_ascii=False)
        self.doc.insert.side_effect = frappe.DuplicateEntryError("Pearnly OCR Work", "x", None)
        self.db.get_value.side_effect = [None, frappe._dict(arguments=encoded, user="u@example.com", status="queued")]
        self.assertEqual(len(self.submit()), 64)
        self.db.rollback.assert_called_once_with(save_point="pearnly_ocr_admission")  # 只退回到插入之前
        self.client.zrem.assert_not_called()                                         # 名额归先落库的那份

    def test_a_reused_reference_with_other_content_is_still_refused(self):
        self.db.get_value.return_value = frappe._dict(arguments="{}", user="u@example.com", status="queued")
        with self.assertRaises(ValueError):
            self.submit()

    def test_a_failed_insert_gives_the_slot_back_unless_the_work_exists(self):
        self.doc.insert.side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.submit()
        self.assertEqual(self.client.zrem.call_count, 2)
        self.client.zrem.reset_mock()
        self.db.exists.return_value = True
        with self.assertRaises(RuntimeError):
            self.submit()
        self.client.zrem.assert_not_called()


if __name__ == "__main__":
    unittest.main()
