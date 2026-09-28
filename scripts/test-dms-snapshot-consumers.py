"""Pinned Frappe consumer regressions, with no Site or external network.

Real LINE framing, QA/session paths, full customer client/writeback, scopes and
retired scheduler run unchanged. DB documents, authentication, queue delivery and outer
HTTP are in-memory substitutes. This does not claim database concurrency or
external business acceptance; the separate native lab owns those checks.
"""
from contextlib import ExitStack, nullcontext
import copy
import importlib.util
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
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
        from pearnly_dms.adapter import operations, snapshot_writeback, geo_sync, snapshot_sync
        from pearnly_dms.snapshots import store, schedule
        from pearnly_dms.config import pacing
        cls.line, cls.sessions, cls.hooks, cls.i18n, cls.out, cls.cards = line, line_session, hooks, i18n, out, cards
        cls.service, cls.flow, cls.qa, cls.menu = service, flow, booking_qa, menu_flow
        cls.operations, cls.writeback, cls.geo_sync = operations, snapshot_writeback, geo_sync
        cls.snapshot_sync = snapshot_sync
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

    def test_notice_never_injects_source_times_into_line_messages(self):
        """操作员 2026-09-25：每条 LINE 回话都带「ข้อมูล ณ …」太啰嗦 —— 不再注入。

        曾经这里断言"两段时间都出现在正文里"。契约变了，但**严格性不变**：
        消息必须原样返回（不夹任何时间），两个 flag 仍然按"本消息消费一次"清掉。
        """
        self.flags()
        msg = {"type": "text", "text": "Original", "quickReply": {"items": []}}
        before = copy.deepcopy(msg)
        result = self.out._snapshot_notice(msg)
        self.assertEqual(msg, before)
        self.assertEqual(result, msg)
        self.assertEqual(result["text"], "Original")
        self.assertNotIn("dms_snapshot_status", frappe.flags)
        self.assertNotIn("dms_snapshot_geo_status", frappe.flags)
        self.assertEqual(self.out._snapshot_notice(msg), msg)

    def test_notice_leaves_a_flex_bubble_untouched_and_keeps_one_reply(self):
        bubble = {"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": []}}
        msg = {"type": "flex", "altText": "test", "contents": {"type": "carousel", "contents": [bubble]}}
        before = copy.deepcopy(msg); self.flags()
        self.assertTrue(self.out.send(UID, msg, TOKEN))
        self.assertEqual(msg, before, "卡片正文不再被追加时间行")
        self.assertEqual(len(self.http), 1)
        self.assertTrue(self.http[0][0].endswith("/reply"))
        self.assertEqual(len(self.http[0][1]["messages"]), 1)
        rendered = self.http[0][1]["messages"][0]
        self.assertEqual(len(rendered["contents"]["contents"][0]["body"]["contents"]), 0,
                         "卡片正文不再被追加时间行，也不额外多发一条消息")
        self.assertTrue(self.out.push_or_reply_text(UID, "Next card", TOKEN))
        self.assertEqual(self.http[1][1]["messages"], [{"type": "text", "text": "Next card"}])

    def test_pending_qa_preserves_customer_and_continue_recovers_without_ocr_or_write(self):
        self.replace(self.qa, "resolve_dms_endpoint", return_value={"name": EP})
        self.replace(self.qa, "resolve_operator_advisor", return_value=({"id": "AD1", "name": "Advisor"}, "sales"))
        masters = {key: [["1", "CODE", "Synthetic"]] for key in ("cars", "place_books", "term_sales", "regis_behalfs")}
        masters["company_banks"] = []
        data = {
            "vehicles": {"masters": {"cars": masters["cars"]}, "paints": {}, "catalogues": {"fixture": {}}},
            "company": {"masters": {key: value for key, value in masters.items() if key != "cars"},
                        "catalogues": {"fixture": {}}},
            "customers": {"customers": {}, "prefixes": [["p", "Prefix"]]},
        }
        snapshots = {dataset: {"data": value, "as_of": 1790209380, "completed_at": 1790209400,
                               "generation": "synthetic-" + dataset, "stale": False}
                     for dataset, value in data.items()}
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
        def master_groups_only(endpoint, dataset, **kwargs):
            name = endpoint.get("name") if isinstance(endpoint, dict) else endpoint
            self.assertEqual(name, EP)
            self.assertIn(dataset, snapshots)
            return copy.deepcopy(snapshots[dataset])
        reads.side_effect = master_groups_only
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
        # Native dispatch protocol remains real; only document/Redis IO is local
        # memory here. The dedicated native test owns actual DB concurrency.
        self.db.set_value = lambda kind, name, values: self.db.rows[kind, name].update(values)
        self.replace(frappe, "get_all", side_effect=lambda kind, **kwargs: [
            frappe._dict(row) for (dt, name), row in self.db.rows.items()
            if dt == kind and name.startswith("CW-") and row.get("status") == "manual"])
        document = queue_fixture.MemoryDocument
        def insert(doc, **kwargs):
            doc.name = kwargs.get("set_name") or doc.get("name")
            if doc.name and (doc.doctype, doc.name) in self.db.rows:
                raise AssertionError("Duplicate customer dispatch reservation")
            return document.save(doc)
        self.replace(document, "insert", insert)
        self.cache.lock.side_effect = lambda *args, **kwargs: MagicMock()
        data = identity_fixture.record()
        for field in reader_fixture._IDENTITY_MAP.values(): data.setdefault(field, "")
        data.update(branchcusval="B", txtbranchcus="Branch", stsel="e", idsel="own")
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
        baseline = client.read_customer("own")
        result = self.service.push_idcard_fields(EP, fields={**identity_fixture.FIELDS, "phone": "0800000999"},
            mode="overwrite", customer_id="own", baseline=baseline, request_id="server-nonce-success", actor=USER)
        self.assertTrue(result["success"]); self.assertFalse(result["cache_updated"])
        self.assertEqual(result["customer_id"], "own")
        self.assertEqual(events, ["enter", "login", "client", "exit"])
        self.assertEqual(len(transport.writes()), 1); saved.assert_called_once(); publish.assert_called_once()
        self.assertGreater(sum(path == "cus/form.php" for path, _ in transport.calls), 1)
        self.assertEqual(self.db.rows["Pearnly Push Log", result["customer_write_log"]]["status"], "success")

    def test_final_live_write_rejection_cannot_be_overridden_by_cache(self):
        client, transport, events = self.live_writer()
        transport.statuses["cus/edit.php"] = 503
        publish = self.replace(self.store, "verified_customer")
        baseline = client.read_customer("own")
        result = self.service.push_idcard_fields(EP, fields={**identity_fixture.FIELDS, "phone": "0800000999"},
            mode="overwrite", customer_id="own", baseline=baseline, request_id="server-nonce-failure", actor=USER)
        self.assertFalse(result["success"]); publish.assert_not_called()
        self.assertEqual(len(transport.writes()), 1)
        self.assertEqual(events[-1], "exit")
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(self.db.rows["Pearnly Push Log", result["customer_write_log"]]["status"], "manual")

    def test_booking_writeback_never_starts_an_employee_scan(self):
        hooks = self.replace(self.schedule, "collectors")
        enqueue = self.replace(frappe, "enqueue", return_value=None)
        self.assertFalse(self.writeback.sales(EP))
        enqueue.assert_not_called()
        enqueue.side_effect = RuntimeError("synthetic dispatch failure")
        self.assertFalse(self.writeback.sales(EP))
        enqueue.assert_not_called(); hooks.assert_not_called()

    def test_booking_checks_masters_against_local_snapshot_without_sales_refresh(self):
        """ADR-014：提交前主档复核读本地完整快照，不再借管理员去 DMS 实时读主档。"""
        from pearnly_dms.booking import service as booking_service
        client = booking_fixture.DmsFixture("SNAPSHOT-ONLY")
        self.replace(self.operations, "run_logged_in", side_effect=client.session)
        self.replace(booking_service, "booking_lock", side_effect=lambda _: nullcontext(True))
        banks = {key: [] for key in ("company_banks", "source_banks", "cheque_banks", "cashier_banks", "card_banks")}
        local = {
            "vehicles": {"masters": {"cars": client.masters["cars"]},
                         "paints": {"car": [["paint", "PAINT", "Paint"]]}, "catalogues": {"fixture": {}}},
            "company": {"masters": {**{k: v for k, v in client.masters.items() if k != "cars"}, **banks},
                        "catalogues": {"fixture": {}}},
            "customers": {"customers": {}, "prefixes": [["17", "Prefix"]]},
        }
        reads = self.replace(self.store, "read", side_effect=lambda endpoint, dataset, **kw: {
            "data": copy.deepcopy(local[dataset]), "as_of": 1790209380, "completed_at": 1790209400,
            "generation": "synthetic-" + dataset, "stale": False})
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
        for queue_available in (True, False):
            with self.subTest(queue_available=queue_available):
                enqueue.side_effect = None if queue_available else RuntimeError("synthetic dispatch failure")
                result = booking_service.book_in_session(EP, copy.deepcopy(payload), on_attempt=Mock())
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["booking_id"], str(len(client.orders)))
                self.assertFalse(result["cache_updated"])
                self.assertFalse(result["cache_refresh_requested"])
        self.assertEqual(len(client.orders), 2)
        self.assertEqual(customer_read.call_count, 2)
        self.assertEqual(master_read.call_count, 0, "主档不再去 DMS 实时读")
        self.assertTrue({"vehicles", "company"} <= {call.args[1] for call in reads.call_args_list})
        enqueue.assert_not_called()

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

    def test_periodic_reads_retired_but_business_recovery_hooks_preserved(self):
        methods = {method for values in self.hooks.scheduler_events["cron"].values() for method in values}
        self.assertFalse({"pearnly_dms.snapshots.schedule.sweep",
            "pearnly_dms.adapter.snapshot_sync.refresh", "pearnly_dms.adapter.geo_sync.refresh",
            "pearnly_dms.query.snapshot_sync.refresh"} & methods)
        self.assertTrue({"pearnly_dms.booking.recovery.reconcile_due",
            "pearnly_dms.booking.delivery.schedule_due", "pearnly_dms.booking.claim_recovery.schedule_due"} <= methods)
        endpoints = self.replace(frappe, "get_all", side_effect=AssertionError("Retired sweep cannot discover accounts"))
        read = self.replace(self.store, "read", side_effect=AssertionError("Retired sweep cannot read data"))
        self.assertEqual(self.schedule.collectors(), [])
        self.assertEqual(self.schedule.sweep(), {"queued": 0, "skipped": 0, "retired": True})
        self.assertIsNone(self.schedule.enqueue(EP, {"method": "pearnly_dms.adapter.geo_sync.refresh"}))
        endpoints.assert_not_called(); read.assert_not_called(); self.assertEqual(self.jobs, [])
        registry = self.hooks.pearnly_dms_snapshot_collectors
        self.assertEqual({item["dataset"] for item in registry if item["reader"] == "authority"},
                         {"vehicles", "customers", "geo", "company"})
        self.assertEqual([item.get("dataset_prefix") for item in registry if item["reader"] == "sales"], ["query."])

    def test_old_geo_and_directory_jobs_without_request_do_not_login_or_acquire_lease(self):
        stage = self.replace(self.store, "stage", side_effect=AssertionError("No implicit checkpoint access"))
        read = self.replace(self.store, "read", side_effect=AssertionError("No implicit snapshot access"))
        for module in (self.geo_sync, self.snapshot_sync):
            with self.subTest(module=module.__name__):
                login = self.replace(module, "run_logged_in", side_effect=AssertionError("Old jobs must not login"))
                self.assertEqual(module.refresh(EP), {"ok": False, "retired": True})
                login.assert_not_called()
        self.assertEqual(self.snapshot_sync.refresh(EP, dataset="masters", request_id="obsolete"),
                         {"ok": False, "retired": True})
        stage.assert_not_called(); read.assert_not_called()
        self.cache.lock.assert_not_called(); self.assertEqual(self.jobs, [])

    def snapshot_directory(self, customers):
        from pearnly_dms.snapshots.directory import Directory
        def customer_only(endpoint, dataset, **kwargs):
            self.assertEqual((endpoint, dataset), (EP, "customers"))
            return {"data": {"customers": customers}, "as_of": 1790209380,
                    "completed_at": 1790209400, "generation": "synthetic-customers", "stale": False}
        self.replace(self.store, "read", side_effect=customer_only)
        return Directory(EP)

    def test_independent_dataset_reads_keep_real_times_and_never_trigger_refresh(self):
        from pearnly_dms.snapshots.directory import Directory
        snapshots = {
            "vehicles": {"data": {"masters": {"cars": [["car", "Car"]]}, "paints": {"car": [["c", "Color"]]},
                                   "catalogues": {"fixture": {}}},
                         "as_of": 200, "completed_at": 205, "generation": "v1", "stale": False},
            "company": {"data": {"masters": {"branches": [["branch", "Branch"]]}, "catalogues": {"fixture": {}}},
                        "as_of": 150, "completed_at": 155, "generation": "o1", "stale": False},
            "customers": {"data": {"customers": {"c1": {"customer_id": "c1", "name": "Synthetic"}},
                                    "prefixes": [["p", "Prefix"]]},
                          "as_of": 100, "completed_at": 110, "generation": "c1", "stale": True},
            "geo": {"data": {"provinces": {"": [["p1", "Province"]]}},
                    "as_of": 50, "completed_at": 90, "generation": "g1", "stale": True},
        }
        reads = self.replace(self.store, "read", side_effect=lambda ep, ds, **kw: copy.deepcopy(snapshots[ds]))
        directory = Directory(EP)
        self.assertEqual(directory.paints("car"), [["c", "Color"]])
        self.assertEqual([call.args[1] for call in reads.call_args_list], ["vehicles"])
        self.assertEqual(directory.metadata()["as_of"], 200)
        masters = directory.masters()
        self.assertEqual(masters["cars"], [["car", "Car"]])
        self.assertEqual(masters["branches"], [["branch", "Branch"]])
        self.assertEqual(masters["prefixes"], [["p", "Prefix"]])
        self.assertEqual(directory.read_customer("c1")["customer_id"], "c1")
        self.assertEqual([call.args[1] for call in reads.call_args_list], ["vehicles", "company", "customers"])
        self.assertEqual(directory.metadata()["as_of"], 100)
        self.assertEqual(directory.metadata()["completed_at"], 110)
        self.assertEqual(directory.metadata()["generation"], "o1:c1:v1")
        self.assertTrue(directory.metadata()["stale"])
        directory.list_geo("provinces")
        self.assertEqual(directory.metadata()["as_of"], 100, "Address age stays independent of business data")
        self.assertEqual(directory.metadata()["geo_as_of"], 50)
        self.assertTrue(directory.metadata()["geo_stale"])
        self.assertEqual([call.args[1] for call in reads.call_args_list], ["vehicles", "company", "customers", "geo"])
        self.assertEqual(self.http, []); self.assertEqual(self.jobs, [])

    def test_prefixes_only_read_customer_group_without_vehicle_or_company_dependency(self):
        directory = self.snapshot_directory({})
        result = {"data": {"customers": {}, "prefixes": [["p", "Prefix"]]}, "as_of": 100,
                  "completed_at": 110, "generation": "customer-only", "stale": False}
        self.store.read.side_effect = lambda ep, dataset, **kwargs: copy.deepcopy(result) if dataset == "customers" else self.fail(dataset)
        self.assertEqual(directory.list_prefixes(), [["p", "Prefix"]])
        self.store.read.assert_called_once_with(EP, "customers", refresh_interval=1800)
        self.assertEqual(directory.metadata()["generation"], "customer-only")
        self.assertEqual(self.http, []); self.assertEqual(self.jobs, [])

    def customer_editor_fixture(self):
        from pearnly_core import endpoints
        from pearnly_dms.editor import booking_edit, customer_edit
        self.replace(frappe, "get_roles", return_value=[])
        self.replace(endpoints, "read_password", side_effect=lambda kind, name, field: self.passwords.get((name, field), ""))
        self.replace(self.operations, "run_logged_in", side_effect=AssertionError("Editor must not login"))
        self.replace(self.service, "run_logged_in", side_effect=AssertionError("Editor must not login"))
        self.replace(reader_fixture.DmsClient, "_post_text", side_effect=AssertionError("Editor must not read DMS"))
        client = reader_fixture.DmsClient(None, "https://snapshot.invalid/dms/")
        html = reader_fixture.customer_form("C1")
        customer = {**client._extract_customer_fields(client._parse_form_defaults(html), html),
                    "customer_id": "C1", "birthday_be": "01/01/2540", "house_no": "1", "zipcode": "TEST"}
        snapshots = {
            "customers": {"customers": {"C1": customer}, "prefixes": [["17", "TEST"]]},
            "geo": {"provinces": {"": [["P1", "TEST"]]}, "districts": {"P1": [["D1", "TEST"]]},
                    "subdistricts": {"D1": [["S1", "TEST"]]}, "zipcodes": {"S1": [["Z1", "TEST"]]}},
        }
        scope = self.store.scope(EP)
        for dataset, data in snapshots.items():
            name = self.store._name(scope, dataset)
            self.db.rows[self.store.DT, name] = {"name": name, "payload": json.dumps(data),
                "source_started_at": 1790209380, "completed_at": 1790209400,
                "generation": "synthetic-" + dataset, "last_error": ""}
        payload = {"nonce": "editor-before", "endpoint_id": EP, "customer_id": "C1",
                   "mode": "customer", "draft": copy.deepcopy(customer)}
        self.sessions.set_session(UID, "reviewing", payload)
        reads = self.replace(self.store, "read", wraps=self.store.read)
        return customer_edit, booking_edit, customer, reads

    def test_customer_editor_load_save_only_reads_customer_and_address_groups(self):
        customer_edit, _, customer, reads = self.customer_editor_fixture()
        loaded = customer_edit.load(UID, "editor-before")
        self.assertEqual(loaded["form"]["customer"]["people_id"], customer["people_id"])
        self.assertEqual(set(loaded["masters"]), {"prefixes"})
        self.assertEqual(loaded["snapshot"]["generation"], "synthetic-customers")
        nonce = customer_edit.save(UID, "editor-before", {"customer": customer})
        self.assertNotEqual(nonce, "editor-before")
        self.assertEqual(self.sessions.get_session(UID)["payload"]["nonce"], nonce)
        self.assertEqual({call.args[1] for call in reads.call_args_list}, {"customers", "geo"})
        self.assertEqual([url for url, _ in self.http], ["https://api.line.me/v2/bot/message/push"])
        self.assertEqual(self.jobs, [])

    def test_customer_editor_missing_required_cache_or_auth_rejection_keeps_draft(self):
        customer_edit, booking_edit, customer, reads = self.customer_editor_fixture()
        scope = self.store.scope(EP)
        before = self.sessions.get_session(UID)
        for dataset, error in (("customers", ""), ("geo", ""), ("customers", "ERR_DMS_ADMIN_AUTH")):
            with self.subTest(dataset=dataset, error=error):
                key = self.store.DT, self.store._name(scope, dataset)
                saved = copy.deepcopy(self.db.rows[key])
                if error:
                    self.db.rows[key]["last_error"] = error
                else:
                    del self.db.rows[key]
                try:
                    for method, args in ((customer_edit.load, (UID, "editor-before")),
                                         (customer_edit.save, (UID, "editor-before", {"customer": customer}))):
                        with self.assertRaises(booking_edit.DmsBookingEditError) as raised:
                            method(*args)
                        self.assertEqual(raised.exception.code, "dms.err." + (error or "ERR_DMS_SNAPSHOT_PENDING"))
                        self.assertEqual(self.sessions.get_session(UID), before)
                finally:
                    self.db.rows[key] = saved
        self.assertEqual(self.http, []); self.assertEqual(self.jobs, [])

    def test_customer_editor_still_rejects_expired_nonce_revocation_and_disabled_endpoint(self):
        customer_edit, booking_edit, customer, reads = self.customer_editor_fixture()
        before = self.sessions.get_session(UID)
        for key, field, value in ((None, None, None), (("Pearnly LINE Binding", "binding"), "status", "unbound"),
                                  (("User", USER), "enabled", 0), (("Pearnly ERP Endpoint", EP), "enabled", 0)):
            with self.subTest(target=key):
                old = self.db.rows[key][field] if key else None
                if key:
                    self.db.rows[key][field] = value
                try:
                    nonce = "editor-before" if key else "expired"
                    for method, args in ((customer_edit.load, (UID, nonce)),
                                         (customer_edit.save, (UID, nonce, {"customer": customer}))):
                        with self.assertRaises(booking_edit.DmsBookingEditError):
                            method(*args)
                    self.assertEqual(self.sessions.get_session(UID), before)
                finally:
                    if key:
                        self.db.rows[key][field] = old
        reads.assert_not_called()
        self.assertEqual(self.http, []); self.assertEqual(self.jobs, [])

    def test_customer_editor_failed_line_delivery_restores_original_nonce_and_payload(self):
        customer_edit, booking_edit, customer, reads = self.customer_editor_fixture()
        before = self.sessions.get_session(UID)
        def reject(url, **kwargs):
            response = self.http_post(url, **kwargs)
            response.status_code = 400
            return response
        self.replace(requests, "post", side_effect=reject)
        with self.assertRaises(booking_edit.DmsBookingEditError) as raised:
            customer_edit.save(UID, "editor-before", {"customer": customer})
        self.assertEqual(raised.exception.code, "dms_booking.preview_send_failed")
        self.assertEqual(self.sessions.get_session(UID), before)
        self.assertEqual(len(self.http), 1); self.assertEqual(self.jobs, [])
        self.assertEqual({call.args[1] for call in reads.call_args_list}, {"customers", "geo"})

    def test_booking_editor_keeps_vehicle_and_company_cache_dependency(self):
        _, booking_edit, customer, reads = self.customer_editor_fixture()
        for has_vehicle in (False, True):
            if has_vehicle:
                name = self.store._name(self.store.scope(EP), "vehicles")
                self.db.rows[self.store.DT, name] = {"name": name,
                    "payload": json.dumps({"masters": {"cars": [["car", "Car"]]}, "paints": {}}),
                    "source_started_at": 1790209380, "completed_at": 1790209400,
                    "generation": "synthetic-vehicles", "last_error": ""}
            reads.reset_mock()
            with self.assertRaises(booking_edit.DmsBookingEditError) as raised:
                booking_edit._snapshot(EP, customer=customer)
            self.assertEqual(raised.exception.code, "dms.err.ERR_DMS_SNAPSHOT_PENDING")
            self.assertIn("company" if has_vehicle else "vehicles", [call.args[1] for call in reads.call_args_list])
        self.assertEqual(self.http, []); self.assertEqual(self.jobs, [])

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
