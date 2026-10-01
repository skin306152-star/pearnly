"""续跑时遇到上一个进程留下的「started」：默认整份判结果不明；声明了能自己处理的类别照常续跑（2026-09-30 Cowork 压测）。

离线跑：数据库、Redis、处理函数都用替身。
"""
import json
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

import frappe

from pearnly_core.execution import work


def _doc(kind):
    now = work.utcnow()
    return frappe._dict(name="W1", kind=kind, status="deferred", lease_until=None, next_run=None, attempts=1,
                        expires_at=now + timedelta(hours=1), user="u@example.com", arguments="{}",
                        checkpoints=json.dumps({"k1": {"state": "done", "body": {}}, "k2": {"state": "started"}}))


class ResumeUnknown(unittest.TestCase):
    def run_work(self, kind, resumable):
        db = MagicMock()
        db.sql.return_value = [_doc(kind)]
        handler = MagicMock(return_value={})
        with patch.object(work.frappe, "db", db), patch.object(work.frappe, "conf", frappe._dict()), \
                patch.object(work.frappe, "get_hooks", side_effect=lambda key: resumable if key ==
                             "pearnly_ocr_resume_unknown" else {}), \
                patch.object(work, "_finish") as finish, patch.object(work, "handler", return_value="x.run"), \
                patch.object(work.frappe, "get_attr", return_value=handler), patch.object(work, "job_language"), \
                patch.object(work.frappe, "flags", frappe._dict()), patch.object(work.frappe, "log_error"), \
                patch.object(work, "stored_checkpoints", return_value=json.loads(_doc(kind).checkpoints)), \
                patch("pearnly_core.execution.resources.check_memory"):
            work.run("W1")
        return finish, handler

    def test_an_undeclared_kind_is_finished_as_unknown(self):
        finish, handler = self.run_work("erp.purchase", ["cowork.vat_check"])
        finish.assert_called_once_with("W1", "uncertain", "provider_outcome_unknown")
        handler.assert_not_called()

    def test_a_declared_kind_resumes_and_decides_itself(self):
        finish, handler = self.run_work("cowork.vat_check", ["cowork.vat_check"])
        handler.assert_called_once()
        finish.assert_called_once_with("W1", "succeeded")


if __name__ == "__main__":
    unittest.main()
