"""Pinned Frappe consumer regressions, with no Site or external network.

Real LINE framing, QA/session paths, full customer client/writeback, scopes and
scheduler run unchanged. DB documents, authentication, queue delivery and outer
HTTP are in-memory substitutes. This does not claim database concurrency or
external business acceptance; the separate native lab owns those checks.
"""
from contextlib import ExitStack, nullcontext
import copy
import importlib.util
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import frappe
import requests


def fixture(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


queue_fixture = fixture("snapshot_consumer_memory", "test-dms-intake-queue.py")
identity_fixture = fixture("snapshot_consumer_identity", "test-dms-customer-identity.py")
reader_fixture = fixture("snapshot_consumer_reader", "test-dms-snapshot-reader.py")
booking_fixture = fixture("snapshot_consumer_booking", "test-dms-native-flow.py")
USER = queue_fixture.USER
UID = queue_fixture.LINE_USER
TOKEN = "synthetic-reply-token"
EP = "snapshot-endpoint-one"


class SnapshotConsumers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        translate = patch.object(frappe, "_", lambda text, *a, **kw: text)
        translate.start(); cls.addClassCleanup(translate.stop)
        from pearnly_core import line, line_session
        from pearnly_dms import hooks, i18n
        from pearnly_dms.line import out, cards
        from pearnly_dms.intake import service, flow
        from pearnly_dms.booking.conversation import booking_qa
        from pearnly_dms.menu import menu_flow
        from pearnly_dms.adapter import operations, snapshot_writeback, geo_sync
        from pearnly_dms.snapshots import store, schedule
        from pearnly_dms.config import pacing
        cls.line, cls.sessions, cls.hooks, cls.i18n, cls.out, cls.cards = line, line_session, hooks, i18n, out, cards
        cls.service, cls.flow, cls.qa, cls.menu = service, flow, booking_qa, menu_flow
        cls.operations, cls.writeback, cls.geo_sync = operations, snapshot_writeback, geo_sync
        cls.store, cls.schedule, cls.pacing = store, schedule, pacing

    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.db = queue_fixture.MemoryDatabase()
        self.http, self.jobs = [], []
        self.binding = {"user_id": USER, "tenant_id": "offline", "binding_revision": "revision-1"}
        frappe.local.site = "snapshot-consumers.invalid"
        frappe.local.lang = "th"
        frappe.local.flags = frappe._dict(in_uninstall=False, in_install=False)
        frappe.local.conf = frappe._dict(pearnly_line_channel_access_token="unused-synthetic-token")
        self.replace(frappe, "db", new=self.db)
        self.replace(frappe, "get_doc", side_effect=self.db.get_doc)
        self.replace(frappe, "new_doc", side_effect=lambda kind: self.db.get_doc({"doctype": kind}))
        self.replace(frappe, "get_installed_apps", return_value=["frappe", "pearnly_core", "pearnly_dms"])
        self.replace(frappe.utils, "now", return_value="2026-09-24 10:00:00")
        clock = frappe.utils.get_datetime
        self.replace(frappe.utils, "get_datetime", side_effect=lambda value=None: clock(value or "2026-09-24 10:00:00"))
        self.replace(frappe, "log_error")
        self.cache = Mock(); self.cache.make_key.side_effect = lambda key: key
        self.replace(frappe, "cache", return_value=self.cache)
        self.replace(frappe, "enqueue", side_effect=lambda method, **kw: self.jobs.append((method, copy.deepcopy(kw))))
        self.replace(frappe, "get_hooks", side_effect=lambda name: self.hooks.pearnly_dms_snapshot_collectors
                     if name == "pearnly_dms_snapshot_collectors" else [])
        self.replace(requests.sessions.Session, "request", side_effect=AssertionError("Unexpected HTTP"))
        self.replace(requests, "post", side_effect=self.http_post)
        self.replace(socket.socket, "connect", side_effect=AssertionError("Network forbidden"))
        self.replace(socket, "create_connection", side_effect=AssertionError("Network forbidden"))
        self.add_endpoint(EP, USER)
        self.add_endpoint("snapshot-endpoint-two", "other-user@example.invalid")
        self.passwords = {(ep, field): "synthetic-admin-secret" if field == "admin_password" else "synthetic-user-secret"
                          for ep in (EP, "snapshot-endpoint-two") for field in ("password", "admin_password")}
        self.replace(self.store, "read_password", side_effect=lambda kind, name, field: self.passwords.get((name, field), ""))

    def replace(self, owner, name, *args, **kwargs):
        return self.stack.enter_context(patch.object(owner, name, *args, **kwargs))

    def add_endpoint(self, name, owner):
        self.db.rows["Pearnly ERP Endpoint", name] = {
            "name": name, "enabled": 1, "adapter": "mrerp_dms", "system_url": "https://identity.invalid/dms/index.php",
            "username": "same-synthetic-sales", "admin_username": "shared-authority", "owner_user": owner}
        self.db.rows["User", owner] = {"name": owner, "enabled": 1}
        operator = "operator" if owner == USER else "operator-two"
        self.db.rows["DMS Operator", operator] = {"name": operator, "user": owner, "status": "active",
                                                 "can_query_dms": 1, "modified": "revision-1"}

    def http_post(self, url, **kwargs):
        self.assertIn(url, {"https://api.line.me/v2/bot/message/reply", "https://api.line.me/v2/bot/message/push",
                            "https://api.line.me/v2/bot/chat/loading/start"})
        self.http.append((url, copy.deepcopy(kwargs["json"])))
        return SimpleNamespace(status_code=200, text="{}", headers={})

    def flags(self):
        frappe.flags.dms_snapshot_status = {"as_of": 1790209380, "stale": False}
        frappe.flags.dms_snapshot_geo_status = {"as_of": 1790122980, "stale": True}

    def test_notice_uses_both_source_times_without_mutating_input(self):
        self.flags()
        msg = {"type": "text", "text": "Original", "quickReply": {"items": []}}
        before = copy.deepcopy(msg)
        result = self.out._snapshot_notice(msg)
        self.assertEqual(msg, before)
        self.assertIsNot(result, msg)
        self.assertIn("24/09/2026 07:23", result["text"])
        self.assertIn("23/09/2026 07:23", result["text"])
        self.assertNotIn("dms_snapshot_status", frappe.flags)
        self.assertNotIn("dms_snapshot_geo_status", frappe.flags)
        self.assertEqual(self.out._snapshot_notice(msg), msg)

    def test_notice_one_flex_reply_not_extra_push_and_no_next_card_pollution(self):
        bubble = {"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": []}}
        msg = {"type": "flex", "altText": "test", "contents": {"type": "carousel", "contents": [bubble]}}
        before = copy.deepcopy(msg); self.flags()
        self.assertTrue(self.out.send(UID, msg, TOKEN))
        self.assertEqual(msg, before)
        self.assertEqual(len(self.http), 1)
        self.assertTrue(self.http[0][0].endswith("/reply"))
        self.assertEqual(len(self.http[0][1]["messages"]), 1)
        rendered = self.http[0][1]["messages"][0]
        self.assertEqual(len(rendered["contents"]["contents"][0]["body"]["contents"]), 1)
        self.assertTrue(self.out.push_or_reply_text(UID, "Next card", TOKEN))
        self.assertEqual(self.http[1][1]["messages"], [{"type": "text", "text": "Next card"}])

    def test_pending_qa_preserves_customer_and_continue_recovers_without_ocr_or_write(self):
        self.replace(self.qa, "resolve_dms_endpoint", return_value={"name": EP})
        self.replace(self.qa, "resolve_operator_advisor", return_value=({"id": "AD1", "name": "Advisor"}, "sales"))
        masters = {key: [["1", "CODE", "Synthetic"]] for key in ("cars", "place_books", "term_sales", "regis_behalfs")}
        masters["company_banks"] = []
        snapshot = {"data": {"masters": masters, "paints": {}, "customers": {}},
                    "as_of": 1790209380, "generation": "synthetic-generation", "stale": False}
        reads = self.replace(self.store, "read", side_effect=self.store.SnapshotUnavailable())
        forbidden = self.replace(self.service, "push_idcard_fields", side_effect=AssertionError("No replay of saved customer"))
        self.replace(self.flow, "process_image", side_effect=AssertionError("No OCR replay"))
        draft = {"name": "Synthetic", "phone": "00000000"}; summary = {"title": "Saved"}
        self.qa.start("offline", UID, EP, "saved-id", "Synthetic", "synthetic-image", TOKEN,
                      draft=draft, summary=summary, user_id=USER)
        saved = self.sessions.get_session(UID)
        self.assertEqual(saved["state"], "customer_saved")
        self.assertEqual(saved["payload"]["customer_id"], "saved-id")
        self.assertEqual(saved["payload"]["draft"], draft)
        self.assertEqual(saved["payload"]["summary"], summary)
        action = self.http[0][1]["messages"][0]["quickReply"]["items"][0]["action"]
        parsed = {key: value[0] for key, value in parse_qs(action["data"]).items()}
        self.assertEqual(parsed, {"action": "continue_booking", "cid": "saved-id"})
        reads.side_effect = None; reads.return_value = snapshot
        self.menu.handle_postback(self.binding, UID, TOKEN, parsed["action"], parsed, saved)
        resumed = self.sessions.get_session(UID)
        self.assertEqual(resumed["state"], "booking_qa")
        self.assertEqual(resumed["payload"]["qa"]["customer"]["id"], "saved-id")
        self.assertEqual(resumed["payload"]["qa"]["draft"], draft)
        self.assertEqual(resumed["payload"]["qa"]["files"]["id_card_mid"], "synthetic-image")
        self.assertTrue(self.http[-1][0].endswith("/reply"), "continue must preserve this event's free reply token")
        forbidden.assert_not_called(); self.assertEqual(self.jobs, [])

    def test_continue_rejects_another_customer_without_restarting(self):
        start = self.replace(self.qa, "start")
        self.menu.handle_postback(self.binding, UID, TOKEN, self.cards.ACT_CONTINUE_BOOKING,
            {"cid": "different"}, {"payload": {"customer_id": "saved-id"}})
        start.assert_not_called()
        self.assertEqual(self.http[-1][1]["messages"][0]["text"], self.cards.TXT_EXPIRED)

    def test_writeback_rejects_empty_or_truncated_form_without_store_update(self):
        transport = reader_fixture.Transport()
        client = reader_fixture.DmsClient(transport, "https://snapshot.invalid/dms/")
        publish = self.replace(self.store, "verified_customer")
        for body in ("", "<form></form>", reader_fixture.customer_form("C1").replace('<input name="txttel" value="0800000000">', '')):
            with self.subTest(length=len(body)):
                transport.overrides["cus/form.php"] = body
                self.assertFalse(self.writeback.customer(EP, client, "C1", expected_scope="expected"))
        publish.assert_not_called()

    def test_writeback_complete_read_keeps_source_id_and_scope(self):
        transport = reader_fixture.Transport()
        client = reader_fixture.DmsClient(transport, "https://snapshot.invalid/dms/")
        publish = self.replace(self.store, "verified_customer")
        self.assertTrue(self.writeback.customer(EP, client, "SOURCE", expected_scope="expected"))
        args, kwargs = publish.call_args
        self.assertEqual(args[:2], (EP, "SOURCE"))
        self.assertEqual(args[2]["customer_id"], "SOURCE")
        self.assertEqual(kwargs, {"expected_scope": "expected"})

    def live_writer(self):
        data = identity_fixture.record()
        for field in reader_fixture._IDENTITY_MAP.values(): data.setdefault(field, "")
        data.update(branchcusval="B", txtbranchcus="Branch")
        transport = identity_fixture.Transport({"own": data})
        client = identity_fixture.DmsClient(transport, "https://identity.invalid/dms/")
        events = []
        class Adapter:
            has_distinct_admin_creds = False
            concurrent_login_detected = False
            def __enter__(self): events.append("enter"); return self
            def __exit__(self, *args): events.append("exit")
            def login(self): events.append("login")
            def client(self): events.append("client"); return client
        self.replace(self.operations, "build_adapter", return_value=(Adapter(), None))
        self.replace(self.pacing, "cooldown_left", return_value=0)
        self.replace(self.pacing, "note_success")
        self.replace(self.store, "read", side_effect=AssertionError("Final write cannot use snapshot as authority"))
        return client, transport, events

    def test_final_write_remains_live_and_success_survives_cache_failure(self):
        client, transport, events = self.live_writer()
        publish = self.replace(self.store, "verified_customer", side_effect=RuntimeError("synthetic cache unavailable"))
        saved = self.replace(client, "save_customer", wraps=client.save_customer)
        result = self.service.push_idcard_fields(EP, fields=identity_fixture.FIELDS,
                                                mode="overwrite", customer_id="own")
        self.assertTrue(result["success"]); self.assertFalse(result["cache_updated"])
        self.assertEqual(result["customer_id"], "own")
        self.assertEqual(events, ["enter", "login", "client", "exit"])
        self.assertEqual(len(transport.writes()), 1); saved.assert_called_once(); publish.assert_called_once()
        self.assertGreater(sum(path == "cus/form.php" for path, _ in transport.calls), 1)

    def test_final_live_write_rejection_cannot_be_overridden_by_cache(self):
        _, transport, events = self.live_writer()
        transport.statuses["cus/edit.php"] = 503
        publish = self.replace(self.store, "verified_customer")
        result = self.service.push_idcard_fields(EP, fields=identity_fixture.FIELDS,
                                                mode="overwrite", customer_id="own")
        self.assertFalse(result["success"]); publish.assert_not_called()
        self.assertEqual(len(transport.writes()), 1)
        self.assertEqual(events[-1], "exit")

    def test_sales_refresh_is_requested_not_completed_and_missing_collector_is_false(self):
        hooks = self.replace(self.schedule, "collectors", return_value=[])
        enqueue = self.replace(frappe, "enqueue", return_value=None)
        self.assertFalse(self.writeback.sales(EP))
        enqueue.assert_not_called()
        hooks.return_value = self.hooks.pearnly_dms_snapshot_collectors
        self.assertTrue(self.writeback.sales(EP))
        enqueue.assert_called_once()
        self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
        self.assertEqual(enqueue.call_args.kwargs["queue"], "dms_sync")
        self.assertEqual(enqueue.call_args.args, ("pearnly_dms.query.snapshot_sync.refresh",))
        enqueue.side_effect = RuntimeError("synthetic dispatch failure")
        self.assertFalse(self.writeback.sales(EP))

    def test_booking_success_is_not_cache_completion_and_survives_refresh_failure(self):
        from pearnly_dms.booking import service as booking_service
        client = booking_fixture.DmsFixture("SNAPSHOT-ONLY")
        self.replace(self.operations, "run_logged_in", side_effect=client.session)
        self.replace(booking_service, "booking_lock", side_effect=lambda _: nullcontext(True))
        self.replace(self.store, "read", side_effect=AssertionError("Booking must use live preflight"))
        enqueue = self.replace(frappe, "enqueue", return_value=None)
        customer_read = self.replace(client, "read_customer", wraps=client.read_customer)
        master_read = self.replace(client, "fetch_masters", wraps=client.fetch_masters)
        payload = {"qa": {"customer": {"id": "customer", "name": "Local Fixture"},
            "advisor": {"id": "advisor", "name": "Advisor"},
            "draft": {"people_id": "1101700203451", "phone": "0812345678"},
            "answers": {**{key: {"id": key, "name": key.title()} for key in ("place", "paint", "term", "regis")},
                        "car": {"id": "car", "label": "CAR Car"},
                        "delivery_date_be": "30/09/2569", "regis_name": "Synthetic"},
            "payments": [{"channel": "cash", "amount": "10.25", "extra": {}}]}}
        for expected in (True, False):
            with self.subTest(refresh_requested=expected):
                enqueue.side_effect = None if expected else RuntimeError("synthetic dispatch failure")
                result = booking_service.book_in_session(EP, copy.deepcopy(payload), on_attempt=Mock())
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["booking_id"], str(len(client.orders)))
                self.assertFalse(result["cache_updated"])
                self.assertEqual(result["cache_refresh_requested"], expected)
        self.assertEqual(len(client.orders), 2)
        self.assertEqual(customer_read.call_count, 2)
        self.assertEqual(master_read.call_count, 2)

    def test_scope_dedupes_authority_but_isolates_sales_owner_revision_and_site(self):
        other = "snapshot-endpoint-two"
        self.assertEqual(self.store.scope(EP), self.store.scope(other))
        sales = self.store.scope(EP, reader="sales")
        self.assertNotEqual(sales, self.store.scope(other, reader="sales"))
        self.db.rows["DMS Operator", "operator"]["modified"] = "new-revision"
        self.assertNotEqual(sales, self.store.scope(EP, reader="sales"))
        before = self.store.scope(EP); frappe.local.site = "other-site.invalid"
        self.assertNotEqual(before, self.store.scope(EP))

    def test_scope_fresh_permissions_fail_closed(self):
        self.db.rows["DMS Operator", "operator"]["can_query_dms"] = 0
        with self.assertRaises(self.store.SnapshotUnavailable): self.store.scope(EP, reader="sales")
        self.assertTrue(self.store.scope(EP))
        self.db.rows["User", USER]["enabled"] = 0
        with self.assertRaises(self.store.SnapshotUnavailable): self.store.scope(EP)

    def test_scheduler_hook_scope_coalescing_daily_geo_and_queue_contract(self):
        self.assertIn("pearnly_dms.snapshots.schedule.sweep", self.hooks.scheduler_events["cron"]["*/5 * * * *"])
        self.replace(frappe, "get_all", return_value=[EP, "snapshot-endpoint-two"])
        stage = self.replace(self.store, "stage", return_value=None)
        read = self.replace(self.store, "read", return_value={"stale": False})
        result = self.schedule.sweep()
        self.assertEqual(result["queued"], 3)  # one shared authority, two employee scopes; fresh geo skipped
        self.assertEqual(len({job[1]["job_id"] for job in self.jobs}), 3)
        for _, options in self.jobs:
            self.assertEqual(options["queue"], "dms_sync")
            self.assertTrue(options["deduplicate"]); self.assertTrue(options["enqueue_after_commit"])
        read.assert_called_once_with(EP, "geo", max_age=3 * 86400, refresh_interval=86400)
        self.jobs.clear(); stage.return_value = {"tree": {"todo": [["districts", "P"]]}}
        read.reset_mock(); result = self.schedule.sweep()
        self.assertEqual(result["queued"], 4)
        read.assert_not_called()
        self.assertEqual(sum(method == "pearnly_dms.adapter.geo_sync.refresh" for method, _ in self.jobs), 1)

    def test_geo_fresh_direct_refresh_does_not_login_and_releases_lease(self):
        self.replace(self.store, "stage", return_value=None)
        self.replace(self.store, "read", return_value={"stale": False})
        login = self.replace(self.geo_sync, "run_logged_in", side_effect=AssertionError("Fresh geo must not login"))
        result = self.geo_sync.refresh(EP)
        self.assertEqual(result, {"ok": True, "fresh": True}); login.assert_not_called()
        self.cache.lock.return_value.release.assert_called_once()

    def snapshot_directory(self, customers):
        from pearnly_dms.snapshots.directory import Directory
        self.replace(self.store, "read", return_value={"data": {
            "masters": {}, "paints": {}, "customers": customers},
            "as_of": 1790209380, "generation": "synthetic-directory", "stale": False})
        return Directory(EP)

    def test_ledger_hint_missing_from_generation_is_pending_not_a_new_customer(self):
        directory = self.snapshot_directory({})
        with self.assertRaises(self.store.SnapshotUnavailable) as raised:
            directory.lookup_customer("1000000000123", fallback_customer_ids=["just-created"])
        self.assertEqual(raised.exception.error_code, "ERR_DMS_SNAPSHOT_PENDING")
        self.assertEqual(self.http, []); self.assertEqual(self.jobs, [])

    def test_known_ledger_hint_with_same_tail_but_other_full_id_is_not_exact(self):
        directory = self.snapshot_directory({"older": {
            "customer_id": "older", "name": "Synthetic Other", "people_id": "2000000000123"}})
        self.assertEqual(directory.lookup_customer("1000000000123", fallback_customer_ids=["older"]),
                         {"found": False, "customer_id": None, "fields": {}})
        self.assertEqual(self.http, []); self.assertEqual(self.jobs, [])

    def test_known_ledger_hint_with_exact_full_id_returns_copy_of_customer(self):
        fields = {"customer_id": "known", "name": "Synthetic Exact", "people_id": "1000000000123"}
        directory = self.snapshot_directory({"known": fields})
        result = directory.lookup_customer("1000000000123", fallback_customer_ids=["known", "known"])
        self.assertEqual(result, {"found": True, "customer_id": "known", "fields": fields})
        result["fields"]["name"] = "Edited response only"
        self.assertEqual(fields["name"], "Synthetic Exact")
        self.assertEqual(self.http, []); self.assertEqual(self.jobs, [])

    def test_dedup_cache_path_runs_guard_inline_and_rejects_binding_change(self):
        from pearnly_dms.line import identity
        lookup = self.replace(self.flow.dms_intake, "recognize_lookup", return_value=copy.deepcopy(queue_fixture.SUCCESS))
        self.replace(self.flow._id_ocr, "recent_dms_customer_ids_by_tail", return_value=[])
        self.sessions.set_session(UID, "collecting", {"id_card": queue_fixture.CARD, "phone": queue_fixture.PHONE,
            "endpoint_id": EP, "mode": "customer"})
        self.out.enqueue("dedup", self.flow.run_dedup, self.binding, UID, queue_fixture.CARD,
                         queue_fixture.PHONE, EP, 1, TOKEN)
        lookup.assert_called_once(); self.assertEqual(self.jobs, [])
        self.assertEqual(self.sessions.get_session(UID)["state"], "reviewing")
        # Real run_dedup + out/LINE wrapper: only the result reply is sent.
        # Do not patch start_loading: the outer HTTP list detects its regression.
        self.assertEqual([url for url, _ in self.http], ["https://api.line.me/v2/bot/message/reply"])
        lookup.reset_mock()
        self.replace(identity, "matches", return_value=False)
        result = self.out.enqueue("dedup", self.flow.run_dedup, self.binding, UID, queue_fixture.CARD,
                                  queue_fixture.PHONE, EP, 1, TOKEN)
        self.assertEqual(result, {"cancelled": "binding_changed"}); lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
