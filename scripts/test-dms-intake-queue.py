"""DMS text/menu -> real queue guard -> real consumer regression; pinned Frappe.

Run with the candidate Apps installed/mounted in the pinned Frappe runtime:
    env/bin/python /workspace/scripts/test-dms-intake-queue.py

No Site is connected. Database documents, Redis, RQ delivery, DMS service IO and
HTTP are in-memory substitutes. Routing, session/nonce logic, identity checks,
out.enqueue/run_guarded, get_attr resolution, dedup/write/retry workflows, cards
and reply/push accounting execute unchanged. Unexpected SQL or HTTP fails closed.
No real LINE messages, OCR, DMS writes or filesystem writes; nonzero on failure.
This is not a database-concurrency test or external-business acceptance.
"""
from collections import Counter
from contextlib import ExitStack
import copy
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

import frappe
import requests


USER = "queue-regression@example.invalid"
LINE_USER = "U-offline-intake-queue"
PHONE = "000000000"
CARD = {"people_id": "0000000000000", "name": "Offline Fixture", "address": {}}
SUCCESS = {"ok": True, "scenario": "none", "geo": {}, "prefixes": []}
UNREACHABLE = {"ok": False, "error_code": "ERR_DMS_UNREACHABLE"}


class MemoryDocument(frappe._dict):
    def save(self, **kwargs):
        self.name = self.get("name") or self.get("line_user_id") or f"doc-{len(self._db.rows)}"
        self._db.rows[self.doctype, self.name] = copy.deepcopy(
            {key: value for key, value in self.items() if key not in {"_db", "flags"}}
        )
        return self

    insert = save


class MemoryDatabase:
    """Native document IO boundary, not a replacement for session/identity rules."""
    def __init__(self):
        self.rows = {
            ("User", USER): {"name": USER, "enabled": 1},
            ("DMS Operator", "operator"): {"name": "operator", "user": USER, "status": "active"},
            ("Pearnly LINE Binding", "binding"): {
                "name": "binding", "line_user_id": LINE_USER, "user": USER,
                "status": "active", "modified": "revision-1",
            },
        }

    def matching(self, doctype, filters):
        if isinstance(filters, str):
            filters = {"name": filters}
        return [row for (kind, _), row in self.rows.items() if kind == doctype
                and all(row.get(key) == value for key, value in filters.items())]

    def get_value(self, doctype, filters, fields="name", as_dict=False):
        rows = self.matching(doctype, filters)
        if not rows:
            return None
        row = rows[0]
        if isinstance(fields, str):
            return row.get(fields)
        result = {key: row.get(key) for key in fields}
        return frappe._dict(result) if as_dict else tuple(result.values())

    def exists(self, doctype, filters):
        return bool(self.matching(doctype, filters))

    def sql(self, query, values, as_dict=False):
        if query != ("select state, payload, expires_at from `tabPearnly LINE Session` "
                     "where line_user_id=%s for update"):
            raise AssertionError(f"Unexpected SQL: {query}")
        return [frappe._dict(copy.deepcopy(row)) for row in
                self.matching("Pearnly LINE Session", {"line_user_id": values[0]})]

    def delete(self, doctype, filters):
        for row in self.matching(doctype, filters):
            del self.rows[doctype, row["name"]]

    def get_doc(self, doctype, name=None):
        data = copy.deepcopy(doctype if isinstance(doctype, dict) else self.rows[doctype, name])
        return MemoryDocument(**data, flags=frappe._dict(), _db=self)

    def commit(self):
        pass


class IntakeQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Frappe translation DB/cache is IO. Product Thai CSV/card rendering stays real.
        translate = patch.object(frappe, "_", lambda text, *args, **kwargs: text)
        translate.start()
        cls.addClassCleanup(translate.stop)
        from pearnly_core import line, line_session
        from pearnly_dms.line import handlers, out, cards
        from pearnly_dms.intake import flow
        cls.line, cls.store, cls.handlers, cls.out, cls.cards, cls.flow = (
            line, line_session, handlers, out, cards, flow)

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db, self.jobs, self.http, self.counts = MemoryDatabase(), [], [], Counter()
        self.reply_status = 200
        frappe.local.site = "offline-queue.test"
        frappe.local.lang = "th"
        frappe.local.flags = frappe._dict(in_uninstall=False, in_install=False)
        frappe.local.conf = frappe._dict(
            host_name="https://offline.invalid", pearnly_line_channel_access_token="unused-offline")

        def replace(owner, name, *args, **kwargs):
            return self.stack.enter_context(patch.object(owner, name, *args, **kwargs))

        replace(frappe, "db", new=self.db)
        replace(frappe, "get_doc", side_effect=lambda *a, **kw: self.db.get_doc(*a, **kw))
        replace(frappe, "new_doc", side_effect=lambda kind: self.db.get_doc({"doctype": kind}))
        replace(frappe, "get_installed_apps", return_value=["frappe", "pearnly_core", "pearnly_dms"])
        replace(frappe.utils, "now", return_value="2026-09-24 10:00:00")
        # Session expiry comparisons use the same deterministic clock as writes.
        datetime_reader = frappe.utils.get_datetime
        replace(frappe.utils, "get_datetime", side_effect=lambda value=None:
                datetime_reader(value or "2026-09-24 10:00:00"))
        replace(frappe, "log_error")
        cache = Mock()
        cache.make_key.side_effect = lambda key: key
        cache.hincrby.side_effect = lambda key, field, value: self.counts.update({field.partition("|")[2]: value})
        replace(frappe, "cache", return_value=cache)
        replace(frappe, "enqueue", side_effect=lambda method, **kwargs: self.jobs.append((method, copy.deepcopy(kwargs))))
        replace(requests.sessions.Session, "request", side_effect=AssertionError("Unexpected external HTTP"))
        replace(requests, "post", side_effect=self.http_post)
        self.lookup = replace(self.flow.dms_intake, "recognize_lookup", return_value=copy.deepcopy(SUCCESS))
        self.push_fields = replace(self.flow.dms_intake, "push_idcard_fields", return_value={
            "success": True, "customer_id": "offline-customer", "mode": "create"})
        replace(self.flow._id_ocr, "recent_dms_customer_ids_by_tail", return_value=[])
        replace(self.flow._id_ocr, "resolve_dms_endpoint", return_value={"name": "offline-endpoint"})

    def http_post(self, url, *, json, **kwargs):
        allowed = {"https://api.line.me/v2/bot/message/reply", "https://api.line.me/v2/bot/message/push",
                   "https://api.line.me/v2/bot/chat/loading/start"}
        if url not in allowed:
            raise AssertionError(f"Unexpected external URL: {url}")
        self.http.append((url, copy.deepcopy(json)))
        status = self.reply_status if url.endswith("/reply") else 200
        return Mock(status_code=status, text="offline transport fixture", headers={})

    def fixture(self, state="collecting", *, phone=False, mode="customer"):
        payload = {"id_card": CARD, "id_card_mid": "offline-image", "endpoint_id": "offline-endpoint",
                   "mode": mode}
        if phone:
            payload["phone"] = PHONE
        self.store.set_session(LINE_USER, state, payload)

    def event(self, *, text=None, action=None, token="reply-initial", **values):
        event = {"source": {"userId": LINE_USER}, "replyToken": token}
        if action is None:
            event.update(type="message", message={"type": "text", "text": text})
        else:
            event.update(type="postback", postback={"data": urlencode({"action": action, **values})})
        self.assertTrue(self.handlers.handle(event))

    def worker(self):
        self.assertEqual(len(self.jobs), 1)
        method, kwargs = self.jobs.pop(0)
        self.assertEqual(method, "pearnly_dms.line.out.run_guarded")
        self.assertEqual(kwargs["queue"], "long")
        self.assertEqual(kwargs["arguments"]["binding"]["binding_revision"], "revision-1")
        # Both dotted paths are resolved by native Frappe; no lambda consumer.
        return frappe.get_attr(method)(target=kwargs["target"], arguments=kwargs["arguments"])

    def messages(self):
        return [(url.rsplit("/", 1)[-1], body) for url, body in self.http if "/message/" in url]

    def assert_review(self, token, mode="customer"):
        sess = self.store.get_session(LINE_USER)
        self.assertEqual(sess["state"], "reviewing")
        self.assertEqual(sess["payload"]["mode"], mode)
        self.assertEqual(sess["payload"]["phone"], PHONE)
        self.assertTrue(sess["payload"]["nonce"])
        kind, body = self.messages()[-1]
        self.assertEqual((kind, body["replyToken"]), ("reply", token))
        self.assertEqual(body["messages"][0]["type"], "flex")

    def test_phone_entry_reaches_real_guard_consumer_and_free_reply(self):
        self.fixture()
        self.event(text=PHONE)
        self.assertEqual(self.jobs[0][1]["arguments"]["attempt"], 1)
        self.assertEqual(self.jobs[0][1]["arguments"]["reply_token"], "reply-initial")
        self.worker()
        self.assert_review("reply-initial")
        self.lookup.assert_called_once()
        self.assertEqual(self.lookup.call_args.kwargs["attempt"], 1)
        self.assertEqual(self.counts, {"reply_ok": 1})

    def test_duplicate_delivery_does_not_repeat_lookup_or_result(self):
        self.fixture()
        self.event(text=PHONE)
        delivered = copy.deepcopy(self.jobs[0])
        self.worker()
        self.jobs.append(delivered)
        self.worker()
        self.lookup.assert_called_once()
        self.assertEqual(len(self.messages()), 1)

    def test_menu_text_and_postback_with_complete_material_run_consumer(self):
        for choice, mode in (("1", "customer"), ("2", "booking")):
            for entry in ("text", "postback"):
                with self.subTest(choice=choice, entry=entry):
                    self.fixture("menu", phone=True)
                    token = f"reply-menu-{choice}-{entry}"
                    if entry == "text":
                        self.event(text=choice, token=token)
                    else:
                        action = self.cards.ACT_MENU_CUSTOMER if choice == "1" else self.cards.ACT_MENU_BOOKING
                        self.event(action=action, token=token)
                    self.worker()
                    self.assert_review(token, mode)
        self.assertEqual(self.lookup.call_count, 4)
        self.assertEqual(self.counts, {"reply_ok": 4})

    def test_rejected_reply_falls_back_to_push_and_both_are_counted(self):
        self.fixture()
        self.reply_status = 400
        self.event(text=PHONE)
        self.worker()
        self.assertEqual([kind for kind, _ in self.messages()], ["reply", "push"])
        self.assertEqual(self.counts, {"reply_rejected": 1, "push_ok": 1})

    def test_missing_reply_token_keeps_counted_push_fallback(self):
        self.fixture()
        self.event(text=PHONE, token="")
        self.worker()
        self.assertEqual([kind for kind, _ in self.messages()], ["push"])
        self.assertEqual(self.counts, {"push_ok": 1})

    def test_retry_button_queues_real_dedup_with_new_token_and_attempt(self):
        self.fixture()
        self.lookup.side_effect = [UNREACHABLE, SUCCESS]
        self.event(text=PHONE)
        self.worker()
        self.assertEqual(self.store.get_session(LINE_USER)["payload"]["dms_retry"]["attempt"], 1)
        self.assertIn("quickReply", self.messages()[-1][1]["messages"][0])
        self.event(action=self.cards.ACT_RETRY, token="reply-retry")
        self.worker()
        self.assert_review("reply-retry")
        self.assertEqual([call.kwargs["attempt"] for call in self.lookup.call_args_list], [1, 2])
        self.assertEqual(self.counts, {"reply_ok": 2})

    def test_three_failed_attempts_stop_retry_and_fourth_click_does_not_enqueue(self):
        self.fixture()
        self.lookup.return_value = UNREACHABLE
        self.event(text=PHONE)
        self.worker()
        for attempt in (2, 3):
            self.event(action=self.cards.ACT_RETRY, token=f"reply-retry-{attempt}")
            self.worker()
        self.assertNotIn("dms_retry", self.store.get_session(LINE_USER)["payload"])
        self.assertNotIn("quickReply", self.messages()[-1][1]["messages"][0])
        self.assertEqual(self.messages()[-1][1]["replyToken"], "reply-retry-3")
        self.event(action=self.cards.ACT_RETRY, token="reply-stale-retry")
        self.assertFalse(self.jobs)
        self.assertEqual([call.kwargs["attempt"] for call in self.lookup.call_args_list], [1, 2, 3])

    def test_nonretryable_lookup_failure_uses_original_reply_without_retry_button(self):
        self.fixture()
        self.lookup.return_value = {"ok": False, "error_code": "ERR_DMS_ADMIN_AUTH"}
        self.event(text=PHONE)
        self.worker()
        self.assertNotIn("dms_retry", self.store.get_session(LINE_USER)["payload"])
        self.assertNotIn("quickReply", self.messages()[-1][1]["messages"][0])
        self.assertEqual(self.messages()[-1][1]["replyToken"], "reply-initial")

    def test_revoked_or_rebound_identity_cancels_already_queued_work(self):
        for mutation in ("unbind", "rebind", "disable-user", "disable-operator"):
            with self.subTest(mutation=mutation):
                self.db = MemoryDatabase()
                frappe.db = self.db
                self.fixture()
                self.event(text=PHONE)
                if mutation == "unbind":
                    self.db.rows["Pearnly LINE Binding", "binding"]["status"] = "unbound"
                elif mutation == "rebind":
                    self.db.rows["Pearnly LINE Binding", "binding"]["modified"] = "revision-2"
                elif mutation == "disable-user":
                    self.db.rows["User", USER]["enabled"] = 0
                else:
                    self.db.rows["DMS Operator", "operator"]["status"] = "disabled"
                self.assertEqual(self.worker(), {"cancelled": "binding_changed"})
        self.lookup.assert_not_called()
        self.assertFalse(self.messages())

    def test_binding_revoked_during_lookup_cannot_publish_stale_result(self):
        self.fixture()
        def revoke(*args, **kwargs):
            self.db.rows["Pearnly LINE Binding", "binding"]["status"] = "unbound"
            return SUCCESS
        self.lookup.side_effect = revoke
        self.event(text=PHONE)
        self.worker()
        self.assertEqual(self.store.get_session(LINE_USER)["state"], "collecting")
        self.assertFalse(self.messages())

    def test_session_changed_during_lookup_cannot_publish_stale_result(self):
        self.fixture()
        def choose_another_flow(*args, **kwargs):
            self.store.set_session(LINE_USER, "menu", {})
            return SUCCESS
        self.lookup.side_effect = choose_another_flow
        self.event(text=PHONE)
        self.worker()
        self.assertEqual(self.store.get_session(LINE_USER), {"state": "menu", "payload": {}})
        self.assertFalse(self.messages())

    def review_for_write(self, kind):
        self.fixture()
        self.event(text=PHONE)
        self.worker()
        payload = self.store.get_session(LINE_USER)["payload"]
        if kind == "update":
            payload.update(customer_id="offline-customer", field_diffs=[
                {"field": "phone", "old": "0000000000", "new": PHONE}])
            self.store.set_session(LINE_USER, "reviewing", payload)
        self.event(action=self.cards.ACT_CREATE if kind == "create" else self.cards.ACT_UPDATE,
                   nonce=payload["nonce"], token="reply-confirm")
        self.assertEqual(self.jobs[0][1]["target"], f"pearnly_dms.intake.flow.write_{kind}")
        return payload["nonce"]

    def test_create_and_update_confirm_run_real_consumer_and_nonce_is_single_use(self):
        for kind in ("create", "update"):
            with self.subTest(kind=kind):
                self.push_fields.reset_mock()
                self.push_fields.return_value = {"success": True, "customer_id": "offline-customer",
                                                 "mode": "create" if kind == "create" else "overwrite"}
                logs_before = len(self.db.matching("Pearnly Push Log", {}))
                nonce = self.review_for_write(kind)
                self.worker()
                self.push_fields.assert_called_once()
                self.assertEqual(self.push_fields.call_args.kwargs["attempt"], 1)
                self.assertIsNone(self.store.get_session(LINE_USER))
                logs = self.db.matching("Pearnly Push Log", {})
                self.assertEqual(len(logs), logs_before + 1)
                log = logs[-1]
                self.assertEqual((log["actor"], log["endpoint"], log["status"], log["business_key"]),
                                 (USER, "offline-endpoint", "success", "offline-customer"))
                self.assertEqual(self.messages()[-2][1]["replyToken"], "reply-confirm")
                self.assertEqual(self.messages()[-1][0], "push")  # Existing separate completion notice.
                self.event(action=self.cards.ACT_CREATE if kind == "create" else self.cards.ACT_UPDATE,
                           nonce=nonce, token="reply-repeated-confirm")
                self.assertFalse(self.jobs)
                self.push_fields.assert_called_once()
                self.assertEqual(len(self.db.matching("Pearnly Push Log", {})), logs_before + 1)

    def test_create_and_update_retry_execute_real_writer_with_new_token(self):
        for kind in ("create", "update"):
            with self.subTest(kind=kind):
                self.push_fields.reset_mock()
                self.push_fields.side_effect = [
                    {"success": False, "error_code": "ERR_DMS_UNREACHABLE"},
                    {"success": True, "customer_id": "offline-customer"},
                ]
                self.review_for_write(kind)
                self.worker()
                marker = self.store.get_session(LINE_USER)["payload"]["dms_retry"]
                self.assertEqual((marker["kind"], marker["attempt"]), (kind, 1))
                self.assertEqual(self.messages()[-1][1]["replyToken"], "reply-confirm")
                self.event(action=self.cards.ACT_RETRY, token="reply-write-retry")
                self.worker()
                self.assertIsNone(self.store.get_session(LINE_USER))
                self.assertEqual([call.kwargs["attempt"] for call in self.push_fields.call_args_list], [1, 2])
                self.assertEqual(self.messages()[-2][1]["replyToken"], "reply-write-retry")

    def test_create_and_update_admin_auth_failure_keeps_free_reply_without_retry(self):
        for kind in ("create", "update"):
            with self.subTest(kind=kind):
                self.push_fields.reset_mock()
                self.push_fields.return_value = {"success": False, "error_code": "ERR_DMS_ADMIN_AUTH"}
                self.review_for_write(kind)
                before = len(self.messages())
                self.worker()
                self.push_fields.assert_called_once()
                messages = self.messages()[before:]
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0][0], "reply")
                self.assertEqual(messages[0][1]["replyToken"], "reply-confirm")
                self.assertEqual(messages[0][1]["messages"], [
                    {"type": "text", "text": self.cards.TXT_ADMIN_AUTH_FAIL}])
                session = self.store.get_session(LINE_USER)
                self.assertEqual(session["state"], "reviewing")
                self.assertNotIn("dms_retry", session["payload"])
                self.assertFalse(self.jobs)
                logs = self.db.matching("Pearnly Push Log", {})
                self.assertEqual(logs[-1]["status"], "failed")
                self.assertEqual(logs[-1]["error_msg"], "ERR_DMS_ADMIN_AUTH")
                self.assertEqual((logs[-1]["actor"], logs[-1]["endpoint"], logs[-1]["business_key"]),
                                 (USER, "offline-endpoint", ""))

    def test_revoked_binding_cancels_queued_write_retry_without_second_write(self):
        self.push_fields.return_value = {"success": False, "error_code": "ERR_DMS_UNREACHABLE"}
        self.review_for_write("create")
        self.worker()
        self.event(action=self.cards.ACT_RETRY, token="reply-write-retry")
        sent_before_revoke = len(self.messages())
        self.db.rows["Pearnly LINE Binding", "binding"]["status"] = "unbound"
        self.assertEqual(self.worker(), {"cancelled": "binding_changed"})
        self.push_fields.assert_called_once()
        self.assertEqual(len(self.messages()), sent_before_revoke)


if __name__ == "__main__":
    unittest.main(verbosity=2)
