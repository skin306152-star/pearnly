"""Account lock renews itself while held and frees within one TTL once the holder dies.

Pinned Frappe, no Redis: a fake lock records extend/release. Run: docker run ... <image>
/candidate/scripts/test-session-lock.py
"""
import os
import threading
import time
import unittest
from unittest.mock import patch

import frappe

from pearnly_core import session_lock


class FakeLock:
    def __init__(self, *, acquire=True, extend=True):
        self.acquired, self.extend_ok = acquire, extend
        self.extends, self.released, self.kwargs = [], False, {}
        self.redis = self
        self.name = "k"

    def acquire(self, **kwargs):
        return self.acquired

    def extend(self, additional_time, replace_ttl=False):
        self.extends.append((additional_time, replace_ttl, threading.current_thread().name))
        return self.extend_ok

    def release(self):
        self.released = True

    def ttl(self, name):
        return 30


class FakeCache:
    def __init__(self, lock):
        self.fake = lock

    def lock(self, name, **kwargs):
        self.fake.kwargs = kwargs
        return self.fake


class AccountLockTests(unittest.TestCase):
    def use(self, lock, ttl="4"):
        self.enterContext(patch.dict(os.environ, {"PEARNLY_SESSION_LOCK_TTL": ttl}))
        self.enterContext(patch.object(frappe, "cache", lambda: FakeCache(lock), create=True))
        self.enterContext(patch("pearnly_core.execution.state.store", lambda: FakeCache(lock)))  # 锁在不淘汰的状态存储
        self.enterContext(patch.object(session_lock, "_", side_effect=lambda text: text))

    def test_short_ttl_and_a_token_the_heartbeat_can_use(self):
        self.assertEqual(session_lock.DEFAULT_LOCK_TTL_SEC, 120)
        lock = FakeLock()
        self.use(lock)
        with session_lock.account_lock("url|user", timeout_sec=1) as got:
            self.assertTrue(got)
        self.assertEqual(lock.kwargs["timeout"], 4)
        self.assertIs(lock.kwargs["thread_local"], False)
        self.assertTrue(lock.released)

    def test_heartbeat_renews_to_the_full_ttl_while_held_and_stops_after(self):
        lock = FakeLock()
        self.use(lock)
        with session_lock.account_lock("url|user", timeout_sec=1):
            time.sleep(2.3)
        renewed = len(lock.extends)
        self.assertGreaterEqual(renewed, 2)
        self.assertTrue(all(ttl == 4 and replace for ttl, replace, _ in lock.extends))
        self.assertTrue(all(name == "pearnly-session-lock-heartbeat" for _, _, name in lock.extends))
        time.sleep(1.5)
        self.assertEqual(len(lock.extends), renewed)  # no renewal after release
        self.assertFalse(any(t.name == "pearnly-session-lock-heartbeat" for t in threading.enumerate()))

    def test_heartbeat_gives_up_when_the_lock_is_no_longer_ours(self):
        lock = FakeLock(extend=False)
        self.use(lock)
        with session_lock.account_lock("url|user", timeout_sec=1):
            time.sleep(2.3)
        self.assertEqual(len(lock.extends), 1)

    def test_heartbeat_stops_at_the_hold_cap(self):
        lock = FakeLock()
        beat = session_lock._Heartbeat(lock, ttl=4, max_hold=0.5)
        beat.start()
        beat.join(timeout=3)
        self.assertFalse(beat.is_alive())
        self.assertEqual(lock.extends, [])

    def test_no_heartbeat_without_the_lock(self):
        lock = FakeLock(acquire=False)
        self.use(lock)
        with self.assertRaises(session_lock.SessionLockUnavailable):
            with session_lock.account_lock("url|user", timeout_sec=1):
                pass
        self.assertEqual(lock.extends, [])
        self.assertFalse(lock.released)


if __name__ == "__main__":
    unittest.main()
