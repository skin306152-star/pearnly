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

    def pooled(self, rows):
        """登记表：`rows` = [(name, username, purpose)]；按用途过滤，与 frappe.get_all 同形。"""
        def get_all(doctype, filters=None, fields=None, order_by=None, **kwargs):
            purpose = (filters or {}).get("purpose")
            return [frappe._dict(name=n, username=u) for n, u, p in rows if p == purpose]
        return patch.object(frappe, "get_all", side_effect=get_all)

    def test_write_accounts_replace_the_connection_admin_and_skip_the_sales_name(self):
        self.locks.busy = {self.key("sys1")}
        rows = [("A1", "sys1", "write"), ("A2", "sale01", "write"), ("A3", "sys2", "write"),
                ("R1", "reader", "read")]
        adapter = self.adapter()
        with self.pooled(rows), \
             patch("pearnly_core.endpoints.read_password", return_value="pw"), \
             patch.object(admin_pool, "rotated", side_effect=lambda accounts: list(accounts)):
            with adapter, patch.object(adapter, "_perform_login") as login:
                adapter.ensure_admin_session()
        self.assertEqual([a.username for a in adapter._admin_pool], ["sys1", "sys2"],
                         "the connection's own admin and the read account are not used")
        self.assertEqual(login.call_args[0][1], "sys2", "busy sys1 is skipped for a free one")
        self.assertNotIn(self.key("boss"), self.locks.taken)

    def test_without_write_accounts_the_connection_admin_is_used(self):
        adapter = self.adapter()
        with self.pooled([("R1", "reader", "read")]), \
             patch("pearnly_core.endpoints.read_password", return_value="pw"):
            with adapter, patch.object(adapter, "_perform_login") as login:
                adapter.ensure_admin_session()
        self.assertEqual(login.call_args[0][1], "boss")

    def test_use_account_runs_everything_on_that_one_account(self):
        adapter = self.adapter()
        adapter.skip_sales_session = True
        adapter.use_account("reader", "pr")
        with adapter, patch.object(adapter, "_perform_login") as login:
            adapter.login()
        self.assertEqual(self.locks.taken, [self.key("reader")])
        self.assertEqual(login.call_args[0][1], "reader")
        self.assertFalse(adapter.has_admin_creds)
        with adapter:
            with self.assertRaises(session.DmsTechnicalError):
                adapter.use_account("other", "x")

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

    def test_password_check_never_reuses_a_cached_session(self):
        first = self.adapter(admin=False)
        with first, patch.object(first, "_perform_login"):
            first.login()
        self.assertIsNotNone(session_cache.load(self.key("sale01")))
        check = self.adapter(admin=False)
        check.verify_password = True
        with patch.object(check, "_logout_page") as logout:
            with check, patch.object(check, "_perform_login", side_effect=session.DmsAuthError("bad")) as login:
                with self.assertRaises(session.DmsAuthError):
                    check.login()
        login.assert_called_once()
        logout.assert_called_once()  # the live cached session is ended first, not left to collide
        self.assertFalse(check.session_reused)

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
        self.enterContext(patch.object(admin_pool, "read_account",
                                       return_value=admin_pool.Account("reader", "pr", True)))
        noon = datetime.datetime(2026, 9, 27, 12, 0)
        night = datetime.datetime(2026, 9, 27, 3, 0)
        with patch.object(frappe.utils, "now_datetime", return_value=noon):
            self.assertTrue(live_sync.defer_heavy("customers"))
            self.assertTrue(live_sync.defer_heavy("geo"))
            self.assertFalse(live_sync.defer_heavy("vehicles"))
        with patch.object(frappe.utils, "now_datetime", return_value=night):
            self.assertFalse(live_sync.defer_heavy("customers"))

    def test_disabled_by_interval_by_session_reuse_or_without_a_read_account(self):
        reader = admin_pool.Account("reader", "pr", True)
        with patch.object(admin_pool, "read_account", return_value=reader):
            with patch.object(frappe, "conf", frappe._dict(pearnly_dms_live_sync_seconds=0)):
                self.assertFalse(live_sync.enabled())
            with patch.object(frappe, "conf", frappe._dict(pearnly_dms_session_idle_seconds=0)):
                self.assertFalse(live_sync.enabled())
            self.assertTrue(live_sync.enabled())
        with patch.object(admin_pool, "read_account", return_value=None):
            self.assertFalse(live_sync.enabled(), "no read account: no daytime sync on the connection account")

    def test_window_is_6_to_22(self):
        for hour, inside in ((5, False), (6, True), (21, True), (22, False)):
            with patch.object(frappe.utils, "now_datetime",
                              return_value=datetime.datetime(2026, 9, 27, hour, 30)):
                self.assertEqual(live_sync.in_business_hours(), inside, hour)

    def test_bad_hours_fall_back_to_the_default_window(self):
        with patch.object(frappe, "conf", frappe._dict(pearnly_dms_live_sync_hours="22-3")):
            self.assertEqual(live_sync._hours(), (6, 22))

    def test_tick_throttles_and_deduplicates(self):
        self.enterContext(patch.object(admin_pool, "read_account",
                                       return_value=admin_pool.Account("reader", "pr", True)))
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


class AccountProbeTests(unittest.TestCase):
    """登记前按用途试：缺哪项回哪项；登录失败走统一错误码。"""

    def probe(self, purpose, *, fail=(), login_error=None):
        from pearnly_dms.adapter import account_probe

        adapter = MagicMock()
        adapter.__enter__.return_value = adapter
        adapter.concurrent_login_detected = False
        if login_error:
            adapter.login.side_effect = login_error
        checks = {purpose: [(name, (lambda client, n=name: n not in fail)) for name, _check
                            in account_probe.CHECKS[purpose]]}
        with patch.object(session, "MrerpDmsAdapter", return_value=adapter), \
             patch.object(account_probe, "CHECKS", checks), \
             patch("pearnly_dms.line.errors.friendly", side_effect=lambda code: "text:" + code):
            return account_probe.probe(URL, "u", "p", purpose)

    def test_missing_rights_are_named(self):
        self.assertEqual(self.probe("read"), {"ok": True, "missing": []})
        self.assertEqual(self.probe("read", fail={"events"}), {"ok": False, "missing": ["events"]})
        self.assertEqual(self.probe("write", fail={"customer_form"})["missing"], ["customer_form"])

    def test_bad_password_is_an_auth_error(self):
        result = self.probe("read", login_error=session.DmsAuthError("no"))
        self.assertEqual((result["ok"], result["error_code"]), (False, "ERR_DMS_AUTH"))


class ReadAccountRoutingTests(unittest.TestCase):
    """用户 2026-09-27：后台读取（同步、读全档、凌晨回读）登记了读档账号就只用它；前台操作照旧。"""

    def setUp(self):
        _patch_env(self)

    def run_with(self, *, hour, reader, **kwargs):
        from pearnly_dms.adapter import operations
        from pearnly_dms.config import pacing

        adapter = MagicMock()
        adapter.__enter__.side_effect = SessionLockUnavailable("stop here")
        with patch.object(operations, "build_adapter", return_value=(adapter, None)), \
             patch.object(pacing, "cooldown_left", return_value=0), \
             patch.object(admin_pool, "read_account", return_value=reader), \
             patch.object(frappe.utils, "now_datetime",
                          return_value=datetime.datetime(2026, 9, 27, hour, 0)):
            operations.run_logged_in("EP-1", lambda client, a: {"ok": True}, **kwargs)
        return adapter

    def test_background_reads_use_the_read_account_day_and_night(self):
        reader = admin_pool.Account("reader", "pr", True)
        background = dict(authoritative_read=True, background=True, read_only_batch=True)
        self.run_with(hour=10, reader=reader, **background).use_account.assert_called_once_with("reader", "pr")
        self.run_with(hour=3, reader=reader, **background).use_account.assert_called_once_with("reader", "pr")
        self.run_with(hour=10, reader=None, **background).use_account.assert_not_called()
        self.run_with(hour=10, reader=reader, authoritative_read=True).use_account.assert_not_called()
        self.run_with(hour=10, reader=reader, background=True, read_only_batch=True).use_account.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
