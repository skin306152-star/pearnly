"""结果入库就推「有结论」，收尾成功时不再推第二次（2026-10-02 m7：结果入库到扣费完约 2 秒，等候页白等）。离线：框架用替身。"""
import unittest
from unittest.mock import MagicMock, patch

import frappe

from pearnly_core.execution import work


class NotifyResult(unittest.TestCase):
    def finish(self, status, pushed=None):
        db = MagicMock()
        db.sql.return_value = [("running",)]
        db.get_value.side_effect = lambda doctype, name, fields, as_dict=False: (
            frappe._dict(user="u@example.com", kind="cowork.invoice", reference="B1") if isinstance(fields, list) else None)
        flags = frappe._dict(pearnly_ocr_pushed=pushed)
        with patch.object(work.frappe, "db", db), patch.object(work.frappe, "flags", flags), \
                patch.object(work.frappe, "publish_realtime") as publish, patch.object(work, "_finished"), \
                patch("pearnly_core.execution.inputs.remove"), patch.object(work.budget, "redis"), \
                patch.object(work.budget, "tenant", return_value="t1"):
            work._finish("W1", status, notify=False)
        return publish

    def test_a_result_already_pushed_is_not_pushed_again_on_success(self):
        self.assertFalse(self.finish("succeeded", pushed="W1").called)
        self.assertTrue(self.finish("succeeded", pushed=None).called)
        self.assertTrue(self.finish("failed", pushed="W1").called)       # 收尾不是成功：照常再推
        self.assertTrue(self.finish("succeeded", pushed="W2").called)    # 推过的是别的任务

    def test_notify_result_pushes_now_and_marks_the_run(self):
        db = MagicMock()
        db.get_value.return_value = frappe._dict(user="u@example.com", kind="cowork.invoice", reference="B1")
        flags = frappe._dict(pearnly_ocr_work="W1")
        with patch.object(work.frappe, "db", db), patch.object(work.frappe, "flags", flags), \
                patch.object(work.frappe, "publish_realtime") as publish:
            work.notify_result()
        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[1], {"kind": "cowork.invoice", "reference": "B1", "status": "succeeded"})
        self.assertEqual(flags.pearnly_ocr_pushed, "W1")


if __name__ == "__main__":
    unittest.main()
