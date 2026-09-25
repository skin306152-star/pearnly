"""A background read must wait for the operator instead of giving up instantly.

2026-09-25 lesson: the background read yielded to an interactive session with a
single check and zero waiting, so it always came back `busy`. Nothing re-queues
a busy read, so the cache stopped updating. Waiting a bounded time is what lets
it pick the account up as soon as the operator's own operation finishes.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))


class FakeCache:
    """Only the two sorted-set calls the priority marker uses."""

    def __init__(self):
        self.sets = {}

    def zremrangebyscore(self, key, low, high):
        marks = self.sets.get(key) or {}
        self.sets[key] = {token: score for token, score in marks.items() if score > high}

    def zcard(self, key):
        return len(self.sets.get(key) or {})

    def zadd(self, key, mapping):
        self.sets.setdefault(key, {}).update(mapping)

    def zrem(self, key, token):
        (self.sets.get(key) or {}).pop(token, None)

    def expire(self, key, seconds):
        return True


class ReadPriorityWaitTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.cache = lambda: self.cache
        self.frappe.local = types.SimpleNamespace(site="testdms.pearnly.com")
        self.cache = FakeCache()
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sys.modules.pop("pearnly_dms.adapter.read_priority", None)
        self.priority = importlib.import_module("pearnly_dms.adapter.read_priority")

    def _mark(self, account, seconds_left=900):
        self.cache.zadd(self.priority._key(account), {"token": time.time() + seconds_left})

    def test_no_interactive_session_returns_at_once(self):
        started = time.monotonic()
        self.priority.wait_for_foreground(["dmstest"], timeout_seconds=30)
        self.assertLess(time.monotonic() - started, 0.2)

    def test_it_waits_until_the_operator_finishes(self):
        # 交互会话先占着，0.15 秒后结束 —— 后台读取应该接着进去，而不是当场放弃。
        marker = self.priority._key("dmstest")
        self._mark("dmstest")

        def release():
            time.sleep(0.15)
            self.cache.zrem(marker, "token")

        import threading

        thread = threading.Thread(target=release)
        thread.start()
        self.addCleanup(thread.join)

        started = time.monotonic()
        self.priority.wait_for_foreground(["dmstest"], timeout_seconds=5, poll_seconds=0.05)
        self.assertGreaterEqual(time.monotonic() - started, 0.1)

    def test_a_session_that_never_ends_still_gives_up(self):
        self._mark("dmstest")
        with self.assertRaises(self.priority.ReadDeferred):
            self.priority.wait_for_foreground(["dmstest"], timeout_seconds=0.2, poll_seconds=0.05)


if __name__ == "__main__":
    unittest.main()
