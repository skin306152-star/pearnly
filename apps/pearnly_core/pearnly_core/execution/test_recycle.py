"""不 fork 的识别进程按内存高水位温和重启。离线：不连站点、不连 Redis。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pearnly_core.execution import recycle


class Recycle(unittest.TestCase):
    def setUp(self):
        recycle._requested = False

    def run_hook(self, *, env, origin="home-frappe-frappe-bench:ocr", rss=400):
        with patch.dict(recycle.os.environ, env, clear=True), \
                patch("rq.get_current_job", return_value=SimpleNamespace(origin=origin) if origin else None), \
                patch.object(recycle, "_rss_mb", return_value=rss), \
                patch.object(recycle.os, "kill") as kill, \
                patch("frappe.logger", create=True):
            recycle.after_job(method="m", kwargs={}, result=None)
        return kill

    def test_a_no_fork_ocr_worker_over_the_limit_asks_itself_to_stop_once(self):
        kill = self.run_hook(env={"FRAPPE_BACKGROUND_WORKERS_NOFORK": "1"})
        kill.assert_called_once_with(recycle.os.getpid(), recycle.signal.SIGTERM)
        kill = self.run_hook(env={"FRAPPE_BACKGROUND_WORKERS_NOFORK": "1"})  # 已经要求过：不再发第二次（第二次是强停）
        kill.assert_not_called()

    def test_under_the_limit_or_other_workers_are_left_alone(self):
        for case in ({"env": {"FRAPPE_BACKGROUND_WORKERS_NOFORK": "1"}, "rss": 120},
                     {"env": {}},  # fork 模式：子进程本来就会退出
                     {"env": {"FRAPPE_BACKGROUND_WORKERS_NOFORK": "1"}, "origin": "home-frappe-frappe-bench:long"},
                     {"env": {"FRAPPE_BACKGROUND_WORKERS_NOFORK": "1"}, "origin": None}):
            recycle._requested = False
            self.run_hook(**case).assert_not_called()

    def test_the_limit_can_be_set_per_container(self):
        kill = self.run_hook(env={"FRAPPE_BACKGROUND_WORKERS_NOFORK": "1", "PEARNLY_OCR_WORKER_RECYCLE_MB": "500"}, rss=450)
        kill.assert_not_called()
        kill = self.run_hook(env={"FRAPPE_BACKGROUND_WORKERS_NOFORK": "1", "PEARNLY_OCR_WORKER_RECYCLE_MB": "bad"}, rss=301)
        kill.assert_called_once()

    def test_reading_its_own_memory_works_here(self):
        self.assertGreater(recycle._rss_mb(), 1)


if __name__ == "__main__":
    unittest.main()
