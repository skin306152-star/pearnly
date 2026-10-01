"""识别任务的名字与调用进度（2026-10-01，Cowork 对账等识别时不占进程要用）。离线：数据库用替身。

真实 MariaDB 上的时间差（TIMESTAMPDIFF 与检查点行同一时钟）在测试环境核验，见 docs/work/2026-10-01-recon-wait-no-worker.md。
"""
import hashlib
import unittest
from unittest.mock import MagicMock, patch

from pearnly_core.execution import work


class Names(unittest.TestCase):
    def test_the_name_is_what_submit_stores(self):
        self.assertEqual(work.name_for("cowork.vat_check", "J:0"),
                         hashlib.sha256("cowork.vat_check\0J:0".encode()).hexdigest())


class CallProgress(unittest.TestCase):
    def test_counts_and_ages_per_work_and_none_before_any_call(self):
        db = MagicMock()
        db.sql.side_effect = lambda query, values=None, **kw: [("W1", 120, 30)] if "TIMESTAMPDIFF" in query else []
        with patch.object(work.frappe, "db", db), patch.object(work, "done_calls", return_value={"W1": 3, "W2": 0}), \
                patch.object(work.frappe.utils, "now", return_value="2026-10-01 10:00:00"):
            out = work.call_progress(["W1", "W2", "W1"])
        self.assertEqual(out, {"W1": {"done": 3, "since_first": 120, "since_done": 30},
                               "W2": {"done": 0, "since_first": None, "since_done": None}})
        query, values = next(call.args for call in db.sql.call_args_list if "TIMESTAMPDIFF" in call.args[0])
        self.assertEqual(values["names"], ("W1", "W2"))  # 去重，一次查完
        self.assertEqual(values["now"], "2026-10-01 10:00:00")  # 与检查点行写入同一个时钟

    def test_nothing_to_look_up(self):
        with patch.object(work.frappe, "db", MagicMock()) as db:
            self.assertEqual(work.call_progress([]), {})
        db.sql.assert_not_called()


if __name__ == "__main__":
    unittest.main()
