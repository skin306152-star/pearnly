"""一个识别进程同时跑几份（pool.py）。离线：不连站点、不连 Redis、不调 API；Frappe 原生执行用替身。"""
import contextvars
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from pearnly_core.execution import pool, recycle

NOFORK = {"FRAPPE_BACKGROUND_WORKERS_NOFORK": "1"}
OCR_JOB = SimpleNamespace(origin="home-frappe-frappe-bench:ocr")


class Pool(unittest.TestCase):
    def setUp(self):
        pool._slots = None
        pool._running = 0
        frappe.local.site = "tenant.example"
        frappe.local.job = frappe._dict(job_name="ocr-1", user="someone@example.com")
        self.addCleanup(lambda: [delattr(frappe.local, key) for key in ("site", "job")
                                 if key in dict(iter(frappe.local))])

    def env(self, **extra):
        return patch.dict(pool.os.environ, {**NOFORK, **extra}, clear=True)

    def test_off_by_default_and_outside_the_ocr_queue(self):
        cases = [({}, OCR_JOB),                                   # 没开线程：照原样就地跑
                 ({"PEARNLY_OCR_THREADS": "1"}, OCR_JOB),
                 ({"PEARNLY_OCR_THREADS": "bad"}, OCR_JOB),
                 ({"PEARNLY_OCR_THREADS": "4"}, SimpleNamespace(origin="home-frappe-frappe-bench:long")),
                 ({"PEARNLY_OCR_THREADS": "4"}, None)]             # 内联调用（脚本、测试）
        for extra, job in cases:
            with self.env(**extra), patch("rq.get_current_job", return_value=job), \
                    patch("frappe.utils.background_jobs.execute_job") as execute:
                self.assertFalse(pool.handoff("m", {"name": "x"}))
            execute.assert_not_called()
        with patch.dict(pool.os.environ, {"PEARNLY_OCR_THREADS": "4"}, clear=True), \
                patch("rq.get_current_job", return_value=OCR_JOB):
            self.assertFalse(pool.handoff("m", {"name": "x"}))      # fork 模式：子进程本来就一份一个

    def test_hands_the_job_to_a_thread_running_native_frappe_execution(self):
        seen = []

        def execute(site, method, event, job_name, kwargs, user=None, is_async=True):
            seen.append((site, method, job_name, kwargs, user, is_async, pool.in_thread(),
                         "site" in dict(iter(frappe.local))))

        with self.env(PEARNLY_OCR_THREADS="4"), patch("rq.get_current_job", return_value=OCR_JOB), \
                patch("frappe.utils.background_jobs.execute_job", side_effect=execute):
            self.assertTrue(pool.handoff("pearnly_core.execution.work.run", {"name": "w1"}))
            self.wait_idle()
        # 线程从全新的上下文起跑（看不到 RQ 线程的 frappe.local），由原生 execute_job 自己 init / connect。
        self.assertEqual(seen, [("tenant.example", "pearnly_core.execution.work.run", "ocr-1", {"name": "w1"},
                                 "someone@example.com", True, True, False)])
        self.assertFalse(pool.in_thread())

    def test_at_most_n_run_at_once_and_the_queue_side_waits_for_a_free_thread(self):
        release, running, peak, lock = threading.Event(), [], [0], threading.Lock()

        def execute(*args, **kwargs):
            with lock:
                running.append(1)
                peak[0] = max(peak[0], len(running))
            release.wait(5)
            with lock:
                running.pop()

        with self.env(PEARNLY_OCR_THREADS="3"), patch("rq.get_current_job", return_value=OCR_JOB), \
                patch("frappe.utils.background_jobs.execute_job", side_effect=execute):
            for index in range(3):
                self.assertTrue(pool.handoff("m", {"name": index}))
            self.assertEqual(pool.busy(), 3)
            fourth = []
            # 模拟 RQ 线程：带着当前的 frappe.local（站点、任务）去交第 4 份
            waiter = threading.Thread(target=contextvars.copy_context().run,
                                      args=(lambda: fourth.append(pool.handoff("m", {"name": 3})),))
            waiter.start()
            time.sleep(0.2)
            self.assertEqual(fourth, [])              # 第 4 份在 RQ 这边等，不多取
            release.set()
            waiter.join(5)
            self.assertEqual(fourth, [True])
            self.wait_idle()
        self.assertEqual(peak[0], 3)

    def test_no_free_thread_in_time_runs_it_in_place(self):
        with self.env(PEARNLY_OCR_THREADS="2"), patch("rq.get_current_job", return_value=OCR_JOB), \
                patch.object(pool, "SLOT_WAIT_SECONDS", 0.05), \
                patch("frappe.utils.background_jobs.execute_job", side_effect=lambda *a, **k: time.sleep(0.5)):
            self.assertTrue(pool.handoff("m", {"name": 1}))
            self.assertTrue(pool.handoff("m", {"name": 2}))
            self.assertFalse(pool.handoff("m", {"name": 3}))   # 调用方就地跑，任务不因等位失败
            self.wait_idle()

    def test_a_failing_job_frees_its_thread(self):
        with self.env(PEARNLY_OCR_THREADS="2"), patch("rq.get_current_job", return_value=OCR_JOB), \
                patch("frappe.utils.background_jobs.execute_job", side_effect=RuntimeError("boom")):
            self.assertTrue(pool.handoff("m", {"name": 1}))
            self.wait_idle()
        self.assertEqual(pool._slots._value, 2)

    def test_threads_are_waited_for_when_the_process_exits(self):
        with self.env(PEARNLY_OCR_THREADS="2"), patch("rq.get_current_job", return_value=OCR_JOB), \
                patch("frappe.utils.background_jobs.execute_job", side_effect=lambda *a, **k: time.sleep(0.2)):
            pool.handoff("m", {"name": 1})
            workers = [t for t in threading.enumerate() if t.name == "pearnly-ocr"]
            self.assertTrue(workers and not any(t.daemon for t in workers))
            self.wait_idle()

    def test_recycling_allows_room_for_each_thread(self):
        recycle._requested = False
        with self.env(PEARNLY_OCR_THREADS="4"), patch("rq.get_current_job", return_value=OCR_JOB), \
                patch.object(recycle, "_rss_mb", return_value=600), patch.object(recycle.os, "kill") as kill:
            recycle.after_job()
        kill.assert_not_called()                      # 300 + 150 × 3 = 750
        with self.env(PEARNLY_OCR_THREADS="4"), patch("rq.get_current_job", return_value=OCR_JOB), \
                patch.object(recycle, "_rss_mb", return_value=800), patch.object(recycle.os, "kill") as kill, \
                patch("frappe.logger", create=True):
            recycle.after_job()
        kill.assert_called_once()
        recycle._requested = False

    def wait_idle(self):
        deadline = time.monotonic() + 5
        while pool.busy() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(pool.busy(), 0)


if __name__ == "__main__":
    unittest.main()


class LocalSlot(unittest.TestCase):
    """识别交给 Cloud Run 后（ADR-015）：本机识别每进程同时最多 local_slots() 份；等名额前放开数据库连接。"""

    def setUp(self):
        pool._local = None

    def test_no_threads_means_no_limit(self):
        with patch.dict(pool.os.environ, {}, clear=True), patch.object(pool, "release_db") as release:
            with pool.local_slot(), pool.local_slot():
                pass
        release.assert_not_called()

    def test_at_most_local_slots_run_and_waiters_release_the_database(self):
        inside, peak, lock, started = [0], [0], threading.Lock(), threading.Event()

        def work():
            with pool.local_slot():
                with lock:
                    inside[0] += 1
                    peak[0] = max(peak[0], inside[0])
                started.set()
                time.sleep(0.05)
                with lock:
                    inside[0] -= 1

        with patch.dict(pool.os.environ, {"PEARNLY_OCR_THREADS": "8", "PEARNLY_OCR_LOCAL_SLOTS": "1"}, clear=True), \
                patch.object(pool, "release_db") as release:
            threads = [threading.Thread(target=work) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(peak[0], 1)
        self.assertGreaterEqual(release.call_count, 1)  # 拿不到名额的先放开连接再等

    def test_thread_memory_and_local_slots_are_bounded_settings(self):
        with patch.dict(pool.os.environ, {"PEARNLY_OCR_THREAD_MB": "30", "PEARNLY_OCR_LOCAL_SLOTS": "2"}, clear=True):
            self.assertEqual((pool.thread_mb(), pool.local_slots()), (30, 2))
        with patch.dict(pool.os.environ, {"PEARNLY_OCR_THREAD_MB": "bad", "PEARNLY_OCR_LOCAL_SLOTS": "0"}, clear=True):
            self.assertEqual((pool.thread_mb(), pool.local_slots()), (150, 1))

    def test_release_db_commits_then_closes(self):
        calls = []
        db = SimpleNamespace(commit=lambda: calls.append("commit"), close=lambda: calls.append("close"))
        with patch.object(frappe, "db", db, create=True):
            pool.release_db()
        self.assertEqual(calls, ["commit", "close"])
