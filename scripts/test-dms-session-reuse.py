"""ADR-013 regression tests in the pinned Frappe runtime; no Site, Redis, browser or network.

Session reuse, lazy admin locking, the system account pool, daytime change sync
deferral and the busy error. Run: docker run ... <image> /candidate/scripts/test-dms-session-reuse.py
"""
from contextlib import contextmanager
import datetime
import unittest
from unittest.mock import MagicMock, Mock, patch

import frappe

from pearnly_core.session_lock import SessionLockUnavailable
from pearnly_dms.adapter import admin_pool, live_sync, session, session_cache

BASE = "https://dms.example.test/dms/"
URL = BASE + "index.php"


class FakeCache:
    def __init__(self):
        self.data, self.counter = {}, 0

    def get_value(self, key):
        return self.data.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.data[key] = value

    def delete_value(self, key):
        self.data.pop(key, None)

    def incr(self, key):
        self.counter += 1
        return self.counter

    def make_key(self, key):
        return key

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def body(self):
        return self._body.encode()


class FakeContext:
    def __init__(self, alive=True):
        self.cookies_added, self.cleared, self.alive = [], 0, alive
        self.request = Mock()
        self.request.post.side_effect = lambda *a, **k: FakeResponse(200, "dt::12" if self.alive else "<html>login")
        self._cookies = [{"name": "PHPSESSID", "value": "abc", "domain": "dms.example.test", "path": "/"},
                         {"name": "tracker", "value": "x", "domain": "other.example", "path": "/"}]

    def add_cookies(self, cookies):
        self.cookies_added.extend(cookies)

    def clear_cookies(self):
        self.cleared += 1

    def cookies(self):
        return list(self._cookies)


def _patch_env(test, conf=None):
    cache = FakeCache()
    test.enterContext(patch.object(frappe, "cache", return_value=cache))
    test.enterContext(patch.object(frappe, "conf", frappe._dict(conf or {})))
    test.enterContext(patch("frappe.utils.password.encrypt", side_effect=lambda text: "enc:" + text))
    test.enterContext(patch("frappe.utils.password.decrypt", side_effect=lambda text: text[4:]))
    return cache


class SessionCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = _patch_env(self)

    def test_save_keeps_only_this_dms_cookies_and_round_trips(self):
        self.assertTrue(session_cache.save("k", FakeContext().cookies(), BASE))
        self.assertEqual([c["name"] for c in session_cache.load("k")], ["PHPSESSID"])
        self.assertTrue(all(v.startswith("enc:") for v in self.cache.data.values()))
        self.assertFalse(any("k" == key for key in self.cache.data))  # account never in the key

    def test_idle_expiry_and_disable(self):
        session_cache.save("k", FakeContext().cookies(), BASE)
        with patch.object(session_cache.time, "time", return_value=10**12):
            self.assertIsNone(session_cache.load("k"))
        with patch.object(frappe, "conf", frappe._dict(pearnly_dms_session_idle_seconds=0)):
            self.assertFalse(session_cache.enabled())
            self.assertFalse(session_cache.save("k", FakeContext().cookies(), BASE))

    def test_sales_sessions_are_kept_for_a_shorter_time(self):
        now = session_cache.time.time()
        session_cache.save("sales", FakeContext().cookies(), BASE, idle=session_cache.sales_idle_seconds())
        session_cache.save("admin", FakeContext().cookies(), BASE, idle=session_cache.idle_seconds())
        with patch.object(session_cache.time, "time", return_value=now + 300):
            self.assertIsNone(session_cache.load("sales"))
            self.assertIsNotNone(session_cache.load("admin"))

    def test_restore_alive_reuses_and_dead_marks_kicked(self):
        session_cache.save("k", FakeContext().cookies(), BASE)
        live = FakeContext(alive=True)
        self.assertTrue(session_cache.restore(live, "k", BASE))
        self.assertEqual(live.cookies_added[0]["name"], "PHPSESSID")
        dead = FakeContext(alive=False)
        self.assertFalse(session_cache.restore(dead, "k", BASE))
        self.assertEqual(dead.cleared, 1)
        self.assertIsNone(session_cache.load("k"))
        self.assertTrue(session_cache.recently_kicked("k"))
        self.assertFalse(session_cache.recently_kicked("other"))


class Locks:
    """Records account lock order; keys in `busy` refuse the lock."""

    def __init__(self, busy=()):
        self.taken, self.busy = [], set(busy)

    @contextmanager
    def __call__(self, key, timeout_sec=180.0):
        if key in self.busy:
            raise SessionLockUnavailable("busy")
        self.taken.append(key)
        yield True


@contextmanager
def _noop(*args, **kwargs):
    yield


class FakeBrowser:
    def __init__(self, **kwargs):
        self.page = Mock()
        self.page.url = URL
        self.page.context = FakeContext()
        self.dialogs = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def new_isolated_page(self, extra_secrets=None):
        ctx = FakeContext()
        page = Mock()
        page.context = ctx
        return ctx, page


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.cache = _patch_env(self)
        self.locks = Locks()
        self.enterContext(patch.object(session, "account_lock", self.locks))
        self.enterContext(patch.object(session, "foreground", _noop))
        self.enterContext(patch.object(session, "BrowserSession", FakeBrowser))
        self.enterContext(patch.object(frappe, "get_all", return_value=[]))

    def adapter(self, admin=True):
        return session.MrerpDmsAdapter(system_url=URL, username="sale01", password="p1",
                                       admin_username="boss" if admin else None,
                                       admin_password="p2" if admin else None)

    def key(self, user):
        return session.dms_login_key(URL, user)

    def test_single_credentials_lock_only_the_sales_account(self):
        with self.adapter(admin=False):
            pass
        self.assertEqual(self.locks.taken, [self.key("sale01")])

    def test_admin_lock_is_taken_only_when_the_admin_session_is_used(self):
        adapter = self.adapter()
        with adapter, patch.object(adapter, "_perform_login"):
            self.assertEqual(self.locks.taken, [self.key("sale01")])
            adapter.ensure_admin_session()
            self.assertEqual(self.locks.taken, [self.key("sale01"), self.key("boss")])

    def test_admin_only_reads_do_not_lock_the_sales_account(self):
        adapter = self.adapter()
        adapter.skip_sales_session = True
        with adapter:
            pass
        self.assertEqual(self.locks.taken, [self.key("boss")])

    def test_pool_picks_a_free_account_and_skips_the_sales_name(self):
        self.locks.busy = {self.key("boss")}
        rows = [frappe._dict(name="A1", username="sys1"), frappe._dict(name="A2", username="sale01")]
        adapter = self.adapter()
        with patch.object(frappe, "get_all", return_value=rows), \
             patch("pearnly_core.endpoints.read_password", return_value="pw"), \
             patch.object(admin_pool, "rotated", side_effect=lambda accounts: list(accounts)):
            with adapter, patch.object(adapter, "_perform_login") as login:
                adapter.ensure_admin_session()
        self.assertIn(self.key("sys1"), self.locks.taken)
        self.assertEqual(login.call_args[0][1], "sys1")
        self.assertEqual([a.username for a in adapter._admin_pool], ["boss", "sys1"])

    def test_clean_exit_keeps_the_session_and_skips_logout(self):
        adapter = self.adapter(admin=False)
        with patch.object(adapter, "_logout_page") as logout:
            with adapter, patch.object(adapter, "_perform_login") as login:
                adapter.login()
            login.assert_called_once()
            logout.assert_not_called()
        self.assertIsNotNone(session_cache.load(self.key("sale01")))
        second = self.adapter(admin=False)
        with second, patch.object(second, "_perform_login") as login:
            second.login()
            login.assert_not_called()
        self.assertTrue(second.session_reused)

    def test_technical_failure_logs_out_and_drops_the_cache(self):
        adapter = self.adapter(admin=False)
        with patch.object(adapter, "_logout_page") as logout:
            with self.assertRaises(session.DmsTechnicalError):
                with adapter, patch.object(adapter, "_perform_login"):
                    adapter.login()
                    raise session.DmsTechnicalError("socket hang up")
            logout.assert_called_once()
        self.assertIsNone(session_cache.load(self.key("sale01")))

    def test_lock_wait_calls_the_heartbeat_then_times_out(self):
        self.locks.busy = {self.key("sale01")}
        beats = []
        with patch.object(frappe.local, "dms_lock_wait_hook", lambda: beats.append(1), create=True), \
             patch.object(session, "LOCK_HEARTBEAT_SECONDS", 0.05), \
             patch.object(session, "FOREGROUND_LOCK_WAIT_SECONDS", 0.2):
            with self.assertRaises(SessionLockUnavailable):
                with self.adapter(admin=False):
                    pass
        self.assertTrue(beats)


class LiveSyncTests(unittest.TestCase):
    def setUp(self):
        _patch_env(self)

    def test_heavy_groups_wait_for_the_night_inside_business_hours(self):
        noon = datetime.datetime(2026, 9, 27, 12, 0)
        night = datetime.datetime(2026, 9, 27, 3, 0)
        with patch.object(frappe.utils, "now_datetime", return_value=noon):
            self.assertTrue(live_sync.defer_heavy("customers"))
            self.assertTrue(live_sync.defer_heavy("geo"))
            self.assertFalse(live_sync.defer_heavy("vehicles"))
        with patch.object(frappe.utils, "now_datetime", return_value=night):
            self.assertFalse(live_sync.defer_heavy("customers"))

    def test_disabled_by_interval_or_by_session_reuse(self):
        with patch.object(frappe, "conf", frappe._dict(pearnly_dms_live_sync_seconds=0)):
            self.assertFalse(live_sync.enabled())
        with patch.object(frappe, "conf", frappe._dict(pearnly_dms_session_idle_seconds=0)):
            self.assertFalse(live_sync.enabled())
        self.assertTrue(live_sync.enabled())

    def test_bad_hours_fall_back_to_the_default_window(self):
        with patch.object(frappe, "conf", frappe._dict(pearnly_dms_live_sync_hours="22-3")):
            self.assertEqual(live_sync._hours(), (7, 21))

    def test_tick_throttles_and_deduplicates(self):
        noon = datetime.datetime(2026, 9, 27, 12, 0)
        with patch.object(frappe.utils, "now_datetime", return_value=noon), \
             patch.object(live_sync, "_primary_endpoint", return_value="EP-1"), \
             patch.object(frappe, "enqueue") as enqueue:
            live_sync.tick()
            live_sync.tick()
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["queue"], "dms_sync")
        self.assertTrue(enqueue.call_args.kwargs["deduplicate"])


class LocalPreflightTests(unittest.TestCase):
    """ADR-013: the pre-submit master check reads the local snapshot only."""

    def directory(self, cars, paints=None):
        from pearnly_dms.contracts.client_base import DmsClientError

        directory = Mock()
        directory.masters.return_value = {"cars": cars, "place_books": [["place", "P", "Place"]],
                                          "term_sales": [["term", "T", "Term"]],
                                          "regis_behalfs": [["regis", "R", "Regis"]],
                                          "advisors": [["a", "A", "Advisor"]], "branches": [],
                                          **{key: [] for key in ("company_banks", "source_banks", "cheque_banks",
                                                                 "cashier_banks", "card_banks")}}
        if paints is None:
            directory.paints.side_effect = DmsClientError("absent", "ERR_DMS_MASTER_CHANGED")
        else:
            directory.paints.return_value = paints
        return directory

    def qa(self):
        return {"answers": {"car": {"id": "car"}, "paint": {"id": "paint"}, "place": {"id": "place"},
                            "term": {"id": "term"}, "regis": {"id": "regis"}}}

    def test_removed_car_is_not_ok_and_reads_no_paints(self):
        from pearnly_dms.booking.booking_preflight import local_preflight

        directory = self.directory([["other", "O", "Other"]])
        result = local_preflight(self.qa(), directory)
        self.assertNotEqual(result["status"], "ok")
        directory.paints.assert_not_called()

    def test_missing_paint_catalogue_is_reported_not_treated_as_empty(self):
        from pearnly_dms.booking.booking_preflight import local_preflight
        from pearnly_dms.contracts.client_base import DmsClientError

        with self.assertRaises(DmsClientError) as raised:
            local_preflight(self.qa(), self.directory([["car", "C", "Car"]], paints=None))
        self.assertEqual(raised.exception.error_code, "ERR_DMS_MASTER_UNAVAILABLE")


class BusyErrorTests(unittest.TestCase):
    def test_lock_timeout_is_reported_as_busy_and_retryable(self):
        from pearnly_dms.adapter import operations
        from pearnly_dms.config import pacing

        adapter = MagicMock()
        adapter.__enter__.side_effect = SessionLockUnavailable("queue")
        with patch.object(operations, "build_adapter", return_value=(adapter, None)), \
             patch.object(pacing, "cooldown_left", return_value=0):
            result = operations.run_logged_in("EP-1", lambda client, adapter: {"ok": True})
        self.assertEqual(result["error_code"], "ERR_DMS_BUSY")
        self.assertTrue(result["busy"])
        self.assertTrue(pacing.is_retryable("ERR_DMS_BUSY"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
