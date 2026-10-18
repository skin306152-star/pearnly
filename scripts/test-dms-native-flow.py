"""DMS flow integration on an explicitly disposable Site, with no external IO.

Keeps signed webhook dispatch, native session/identity/nonce/ledger/delivery,
real Frappe enqueue, Redis serialization, RQ Job.perform -> execute_job ->
run_guarded, booking service/preflight and durable recovery. Only LINE HTTP and
the authenticated DMS client boundary are replaced. Does not claim external
DMS HTML/browser or actual LINE acceptance. No scheduler/worker daemon is run.

Run in bench/sites: ../env/bin/python /workspace/scripts/test-dms-native-flow.py
Identity fixtures are disabled; ledger/delivery audit records remain only in
this disposable Site for failure inspection. UUID-owned jobs/snapshots/sessions
are cleared, never pre-existing native queue jobs.
Any failed assertion or unexpected queue target fails the process.
"""
import base64
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import runpy
import time
import unittest
from unittest.mock import patch
import uuid

import frappe
import requests
from werkzeug.wrappers import Request
from frappe.utils.background_jobs import get_queue
from pearnly_core import line, line_session as store
from pearnly_dms.adapter import operations
from pearnly_dms.booking import delivery, recovery
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.intake import service as intake_service
from pearnly_dms.line import cards

SNAPSHOT_LAB = os.environ.get("PEARNLY_DMS_SNAPSHOT_LAB") == "1"
SITE = "dms-snapshot-lab.localhost" if SNAPSHOT_LAB else "dms-flow-lab.localhost"
SECRET = "dms-snapshot-lab-only" if SNAPSHOT_LAB else "dms-flow-lab-only"
MARKER = "DMS-NATIVE-FLOW-"


def connect():
    if SNAPSHOT_LAB:
        # Reuse the strict Site/volume marker, DB, fake credentials, Redis and
        # paused-scheduler guard. An environment flag alone grants no access.
        lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))
        lab["connect"]()
        if frappe.conf.get("pearnly_line_channel_secret") != SECRET:
            raise RuntimeError("Refusing foreign snapshot-lab signing credentials")
        return
    frappe.init(site=SITE, force=True)
    frappe.connect()
    if not frappe.conf.get("pearnly_dms_flow_lab"):
        raise RuntimeError("Refusing any Site without the dedicated flow-lab marker")
    if frappe.conf.get("db_host") != "db" or frappe.conf.get("pearnly_line_channel_access_token") != "lab:" + SITE:
        raise RuntimeError("Refusing foreign DB/LINE credentials")
    frappe.set_user("Administrator")


class DmsFixture:
    """The external authenticated-client boundary, not a booking-service mock."""
    def __init__(self, label):
        self.label = label
        self.orders = []
        self.fail_before_submit = False
        self.unknown_after_submit = False
        self.reject_submit = False
        self.posts = 0
        self.attach_available = True
        self.attach_calls = 0
        self.form = {}
        self.callback_results = []
        self.masters = {key: [[rid, rid.upper(), rid.title()]] for key, rid in (
            ("cars", "car"), ("advisors", "advisor"), ("place_books", "place"),
            ("term_sales", "term"), ("regis_behalfs", "regis"))}

    def session(self, endpoint, fn, **kwargs):
        if self.fail_before_submit:
            return {"ok": False, "error_code": "ERR_DMS_MASTER_UNAVAILABLE"}
        try:
            result = fn(self, self)
            self.callback_results.append(copy.deepcopy(result))
            return result
        except DmsClientError as exc:
            # Same as operations.run_logged_in: the error keeps its submission evidence.
            return intake_service.err(exc.error_code, response_body=getattr(exc, "response_body", None))
        except requests.RequestException:
            return intake_service.err("ERR_DMS_TECHNICAL")

    def fetch_masters(self, **kwargs):
        return copy.deepcopy(self.masters)

    def _bshsd_all(self, elem, **kwargs):
        return [["paint", "PAINT", "Paint"]] if elem == "txtcarpaint" else []

    def read_customer(self, customer_id):
        return {"name": "Local Fixture", "people_id": "1101700203451", "birthday_be": "01/01/2540",
                "prefix_id": "17", "prefix_name": "นาย", "phone": "0812345678", "house_no": "1",
                **{key: "1" for key in ("province_id", "province_name", "district_id", "district_name",
                    "subdistrict_id", "subdistrict_name", "zipcode_id", "zipcode_name")}}

    def _post_text(self, path, data):
        if path == "drfcbc/component/detailbooksell.php":
            return json.dumps(["branch", "Branch", "team", "Team", "", "", "", "", "", "", "", ""])
        if path == "bank/view.php":
            return ""
        if path == "drfcbc/form.php":
            return json.dumps(self.form)
        raise AssertionError("Unexpected external DMS path: " + path)

    def _parse_form_defaults(self, body):
        return json.loads(body)

    def create_booking_via_form(self, *, customer_id, booking, card, on_attempt, readback_out=None):
        number = self.label + "-" + str(len(self.orders) + 1)
        fields = {"txtdocno": number, "cusval": customer_id, "txttel": card.phone}
        on_attempt.capture_submission(number, fields)
        self.posts += 1
        if self.reject_submit:
            # DMS answered err:: and a read-only readback found no such order.
            exc = DmsClientError("fixture rejected", "ERR_DMS_IMPORT")
            exc.response_body = {"submitted": False, "rejected": True, "booking_no": number}
            raise exc
        self.orders.append({"number": number, "customer": customer_id, "booking": booking})
        self.form = {"stsel": "e", "idsel": str(len(self.orders)), **fields}
        if self.unknown_after_submit:
            # The transport/client reports uncertainty, never fabricates success.
            raise DmsClientError("fixture lost response", "ERR_DMS_BOOKING_OUTCOME_UNKNOWN")
        if readback_out is not None:
            # The verified readback hands its form to the receipt review, as the real client does.
            readback_out.update({"form": dict(self.form), "submitted": dict(fields)})
        return str(len(self.orders)), number

    def attach_booking_files(self, *, booking_id, files):
        self.attach_calls += 1
        if not self.attach_available:
            raise requests.Timeout("fixture attachment unavailable")
        for index, value in enumerate(files, 1):
            self.form["fulcurrname" + str(index)] = value["display_name"]
        return {"ok": True, "attached": len(files), "failed": []}


class NativeFlow(unittest.TestCase):
    def setUp(self):
        connect()
        self.label = MARKER + uuid.uuid4().hex[:12]
        self.uid = self.label
        self.user = self.label.lower() + "@example.invalid"
        self.http = []
        self.push_status = 200
        self.reply_status = 200
        self.jobs_run = []
        self.snapshot_template = None
        self.snapshot_keys = set()
        self.snapshot_jobs = set()
        self.dms = DmsFixture(self.label)
        self.enterContext(patch.object(frappe, "in_test", True))
        self.enterContext(patch.object(requests.sessions.Session, "request", self.transport))
        self.enterContext(patch.object(operations, "run_logged_in", self.dms.session))
        self.enterContext(patch.object(delivery, "run_logged_in", self.dms.session))
        self.enterContext(patch.object(recovery, "run_logged_in", self.dms.session))
        # Native import mode is scoped to synthetic fixture creation, avoiding
        # the hourly interactive sign-up throttle in a repeatedly reused lab.
        with patch.dict(frappe.flags, {"in_import": True}):
            frappe.get_doc({"doctype": "User", "email": self.user, "first_name": self.label,
                            "enabled": 1, "send_welcome_email": 0,
                            "roles": [{"role": "DMS Admin"}]}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Pearnly LINE Binding", "line_user_id": self.uid,
                        "user": self.user, "status": "active"}).insert(ignore_permissions=True)
        endpoint = frappe.get_doc({"doctype": "Pearnly ERP Endpoint", "label": self.label,
            "adapter": "mrerp_dms", "owner_user": self.user, "enabled": 1,
            "system_url": "https://dms.invalid/index.php", "username": self.label,
            "booking_defaults": "{}"}).insert(ignore_permissions=True)
        self.endpoint = endpoint.name
        frappe.db.commit()
        # Site installation legitimately queues Frappe's website index builder.
        # Keep pre-existing jobs intact; only consume jobs created by this test.
        self.queue_baseline = set(get_queue("long").get_job_ids())

    def tearDown(self):
        if getattr(frappe.local, "site", None):
            frappe.db.rollback()
            for row in self.deliveries():
                # Unverified records would also hold back later fixtures' same customer.
                if row.status in ("Pending", delivery.UNVERIFIED):
                    frappe.db.set_value(delivery.DT, row.name, {"status": "Review", "last_error": "lab_test_finished"})
            for job_id in self.snapshot_jobs:
                queue = get_queue("dms_sync")
                job = queue.fetch_job(job_id)
                if job:
                    self.assertEqual(job.kwargs.get("site"), SITE)
                    self.assertEqual(job.kwargs.get("method"), "pearnly_dms.query.snapshot_sync.refresh")
                    self.assertEqual(job.kwargs.get("kwargs"), {"endpoint": self.endpoint})
                    queue.remove(job_id)
                    job.delete()
            from pearnly_dms.snapshots import store as snapshots
            for scope_key in self.snapshot_keys:
                frappe.db.delete(snapshots.DT, {"scope_key": scope_key})
            # Keep ledger/delivery audit records, but revoke this UUID-owned
            # identity so later Site-wide schedulers cannot adopt its fixtures.
            if frappe.db.exists("DMS Operator", self.user):
                operator = frappe.get_doc("DMS Operator", self.user)
                operator.status = "inactive"
                operator.save(ignore_permissions=True)
            session = store.get_session(self.uid) or {}
            attempt = (session.get("payload") or {}).get("booking_attempt") or {}
            store.clear_session(self.uid, completed_booking_nonce=attempt.get("nonce", ""))
            frappe.db.set_value("Pearnly LINE Binding", {"line_user_id": self.uid}, "status", "unbound")
            frappe.db.set_value("Pearnly ERP Endpoint", self.endpoint, "enabled", 0)
            frappe.db.set_value("User", self.user, "enabled", 0)
            if self.snapshot_template:
                # execute_job reinitializes the Frappe globals; scope test mode
                # again so deleting this fixture does not enqueue cleanup work.
                with patch.object(frappe, "in_test", True):
                    frappe.delete_doc("Pearnly ERP Endpoint", self.snapshot_template, ignore_permissions=True)
            frappe.db.commit()
            frappe.destroy()

    def transport(self, method, url, **kwargs):
        if not url.startswith(("https://api.line.me/", "https://api-data.line.me/")):
            raise AssertionError("Unexpected external HTTP attempt: " + url)
        self.http.append((method.upper(), url, copy.deepcopy(kwargs)))
        response = requests.Response()
        response.status_code = (self.push_status if url.endswith("/push")
                                else self.reply_status if url.endswith("/reply") else 200)
        response._content = b"\xff\xd8local-fixture" if url.endswith("/content") else b"{}"
        response._content_consumed = True
        response.headers["Content-Type"] = "image/jpeg" if url.endswith("/content") else "application/json"
        return response

    def send(self, *, text=None, action=None, nonce=None, event_id=None):
        event_id = event_id or self.label + "-" + uuid.uuid4().hex
        event = {"webhookEventId": event_id, "source": {"userId": self.uid}, "replyToken": "reply-" + event_id}
        if action:
            event.update(type="postback", postback={"data": "action=" + action + ("&nonce=" + nonce if nonce else "")})
        else:
            event.update(type="message", message={"type": "text", "text": text})
        body = json.dumps({"events": [event]}).encode()
        signature = base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
        frappe.set_user("Guest")
        frappe.local.request = Request.from_values(method="POST", data=body,
            headers={"X-Line-Signature": signature}, content_type="application/json")
        self.assertEqual(line.webhook(), {"ok": True})
        self.assertEqual(frappe.db.get_value(line.EVENT, event_id, "status"), "done")
        frappe.set_user("Administrator")
        return event_id

    def drain(self):
        """Consume actual Redis-serialized RQ jobs via Frappe's native executor."""
        frappe.db.commit()
        queue = get_queue("long")
        jobs = [job for job in queue.get_jobs() if job.id not in self.queue_baseline]
        for queued in jobs:
            self.assertEqual(queued.func_name, "frappe.utils.background_jobs.execute_job")
            self.assertEqual(queued.kwargs["site"], SITE)
            method = queued.kwargs["method"]
            self.assertIn(method, {"pearnly_dms.line.out.run_guarded", "pearnly_dms.booking.delivery.deliver"})
            queue.remove(queued.id)
            # Job.perform deserializes the stored method and kwargs; no direct
            # function calls or enqueue recorders replace the consumer boundary.
            queued.perform()
            self.jobs_run.append(method)
            queued.delete()
            connect()
        return len(jobs)

    def local_snapshots(self):
        """Operator identity plus complete local snapshots; bookings check masters against them (ADR-014)."""
        from pearnly_dms.snapshots import store as snapshots
        if self.snapshot_template:
            return
        self.assertFalse(frappe.db.exists("Pearnly ERP Endpoint", {
            "adapter": "mrerp_dms", "owner_user": ["is", "not set"]}),
            "Refusing an existing shared connection from another fixture")
        template = frappe.get_doc({"doctype": "Pearnly ERP Endpoint", "label": self.label,
            "adapter": "mrerp_dms", "enabled": 1, "system_url": "https://dms.invalid/index.php",
            "booking_defaults": "{}"}).insert(ignore_permissions=True)
        self.snapshot_template = template.name
        frappe.get_doc({"doctype": "DMS Operator", "user": self.user, "display_name": self.label,
            "status": "active", "dms_role": "sales", "dms_username": self.label,
            "dms_password": "fake-only-" + self.label, "can_query_dms": 1}).insert(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("Pearnly ERP Endpoint", {"owner_user": self.user}, "name"), self.endpoint)
        authority = snapshots.scope(self.endpoint)
        self.snapshot_keys.update((authority, snapshots.scope(self.endpoint, reader="sales")))
        masters = {**self.dms.masters, "company_banks": [], "branches": [], "employees": [],
            "prefixes": [["17", "นาย"]], "source_banks": [], "cheque_banks": [],
            "cashier_banks": [], "card_banks": []}
        snapshots.publish(self.endpoint, "directory", {"masters": masters,
            "paints": {"car": [["paint", "PAINT", "Paint"]]},
            "customers": {"customer": {"customer_id": "customer", **self.dms.read_customer("customer")}}},
            time.time() - 1, expected_scope=authority)
        # ADR-014: the pre-submit master check reads the per-group local snapshot, not a live admin read.
        vehicle_keys = ("cars",)
        snapshots.publish(self.endpoint, "vehicles", {
            "masters": {key: masters[key] for key in vehicle_keys},
            "paints": {"car": [["paint", "PAINT", "Paint"]]}, "catalogues": {"fixture": {}}},
            time.time() - 1, expected_scope=authority)
        snapshots.publish(self.endpoint, "company", {
            "masters": {key: value for key, value in masters.items()
                        if key not in vehicle_keys and key != "prefixes"},
            "catalogues": {"fixture": {}}}, time.time() - 1, expected_scope=authority)
        snapshots.publish(self.endpoint, "customers", {
            "customers": {"customer": {"customer_id": "customer", **self.dms.read_customer("customer")}},
            "prefixes": masters["prefixes"]}, time.time() - 1, expected_scope=authority)
        frappe.db.commit()

    def republish_company(self):
        from pearnly_dms.snapshots import store as snapshots
        masters = {key: value for key, value in self.dms.masters.items() if key != "cars"}
        masters.update({"company_banks": [], "branches": [], "employees": [], "source_banks": [],
                        "cheque_banks": [], "cashier_banks": [], "card_banks": []})
        snapshots.publish(self.endpoint, "company", {"masters": masters, "catalogues": {"fixture": {}}},
                          time.time(), expected_scope=snapshots.scope(self.endpoint))
        frappe.db.commit()

    def review(self, *, attachments=False):
        self.local_snapshots()
        qa = {"endpoint_id": self.endpoint, "step": "pay_more",
            "customer": {"id": "customer", "name": "Local Fixture"},
            "advisor": {"id": "advisor", "name": "Advisor"},
            "draft": {"people_id": "1101700203451", "phone": "0812345678"},
            "answers": {**{key: {"id": key, "name": key.title()} for key in ("place", "paint", "term", "regis")},
                "car": {"id": "car", "label": "CAR Car"}, "delivery_date_be": "30/09/2569", "regis_name": self.label},
            "files": {"id_card_mid": "fixture-image"} if attachments else {},
            "payments": [{"channel": "cash", "amount": "10.25", "extra": {}}]}
        nonce = uuid.uuid4().hex
        store.set_session(self.uid, "booking_review", {"nonce": nonce, "qa": qa})
        frappe.db.commit()
        return nonce

    def deliveries(self):
        return frappe.get_all(delivery.DT, filters={"line_user_id": self.uid}, fields=["name", "status", "last_error", "attempt"])

    def due_delivery(self):
        rows = self.deliveries()
        self.assertEqual(len(rows), 1)
        frappe.db.set_value(delivery.DT, rows[0].name, "next_attempt_at", frappe.utils.add_to_date(minutes=-1))
        frappe.db.commit()
        delivery.schedule_due()
        self.assertEqual(self.drain(), 1)
        return self.deliveries()[0]

    def test_menu_1_and_2_are_distinct_signed_guest_flows(self):
        for action, mode in ((cards.ACT_MENU_CUSTOMER, "customer"), (cards.ACT_MENU_BOOKING, "booking")):
            self.send(text="เมนู")
            self.send(action=action)
            session = store.get_session(self.uid)
            self.assertEqual((session["state"], session["payload"]["mode"]), ("collecting", mode))
        self.assertTrue(any(url.endswith("/reply") for _, url, _ in self.http))

    def test_invalid_signature_never_creates_an_event_or_queues_work(self):
        event_id = self.label + "-bad-signature"
        body = json.dumps({"events": [{"webhookEventId": event_id, "type": "message",
            "source": {"userId": self.uid}, "message": {"type": "text", "text": "เมนู"}}]}).encode()
        frappe.set_user("Guest")
        frappe.local.request = Request.from_values(method="POST", data=body,
            headers={"X-Line-Signature": "not-a-valid-signature"}, content_type="application/json")
        self.assertEqual(line.webhook(), {"ok": False, "error": "bad_signature"})
        self.assertFalse(frappe.db.exists(line.EVENT, event_id))
        self.assertFalse(store.get_session(self.uid))
        frappe.set_user("Administrator")
        self.assertEqual(self.drain(), 0)
        self.assertFalse(self.http)

    def test_confirm_real_queue_service_ledger_receipt_and_duplicate(self):
        nonce = self.review()
        event = self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=nonce)
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=nonce, event_id=event)
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=nonce)
        self.assertEqual(self.drain(), 1)
        self.assertEqual(len(self.dms.orders), 1)
        self.assertIsNone(store.get_session(self.uid))
        self.assertEqual(self.deliveries()[0].status, "Delivered")
        self.assertEqual(frappe.db.count("Pearnly Push Log", {"actor": self.user, "status": "success"}), 1)
        # The receipt rides the confirm click's reply token: free, not a paid push.
        # Match message content only: reply tokens embed the label too ("reply-<label>-<hex>"),
        # so a token whose hex starts with the order suffix would count as a second receipt.
        receipts = [data["json"] for _, url, data in self.http
                    if self.dms.orders[0]["number"] in str((data.get("json") or {}).get("messages"))]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["replyToken"], "reply-" + event)
        self.assertFalse(any(url.endswith("/push") for _, url, _ in self.http))

    def test_verified_booking_does_not_automatically_refresh_sales_or_claim_fresh_data(self):
        from pearnly_dms.snapshots import store as snapshots
        from pearnly_dms.query import snapshot_sync
        self.local_snapshots()
        before = snapshots.read(self.endpoint)
        queue = get_queue("dms_sync")
        baseline = set(queue.get_job_ids())
        forbidden = self.enterContext(patch.object(snapshot_sync, "run_logged_in",
            side_effect=AssertionError("The interactive booking must not execute a sales capture")))
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=self.review())
        self.assertEqual(self.drain(), 1)
        created = set(queue.get_job_ids()) - baseline
        self.snapshot_jobs.update(created)
        self.assertEqual(created, set())
        forbidden.assert_not_called()
        # Durable delivery deliberately projects business fields only. Observe
        # the unchanged real service callback result, not a fabricated payload.
        results = [value for value in self.dms.callback_results if value.get("booking_id")]
        self.assertEqual(len(results), 1)
        self.assertIs(results[0]["cache_refresh_requested"], False)
        self.assertIs(results[0]["cache_updated"], False)
        self.assertEqual(snapshots.read(self.endpoint), before)
        with self.assertRaises(snapshots.SnapshotUnavailable):
            snapshots.read(self.endpoint, "sales_query", reader="sales")
        self.assertEqual(len(self.dms.orders), 1)
        self.assertEqual(self.deliveries()[0].status, "Delivered")
        self.assertTrue(self.queue_baseline.issubset(set(get_queue("long").get_job_ids())))

    def test_presubmit_failure_rotates_nonce_then_retry_uses_real_queue(self):
        self.dms.fail_before_submit = True
        nonce = self.review()
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=nonce)
        self.assertEqual(self.drain(), 1)
        payload = store.get_session(self.uid)["payload"]
        self.assertNotEqual(payload["nonce"], nonce)
        self.assertFalse(payload.get("booking_attempt"))
        self.assertFalse(self.dms.orders)
        self.dms.fail_before_submit = False
        self.send(action=cards.ACT_RETRY_BOOKING, nonce=payload["nonce"])
        self.assertEqual(self.drain(), 1)
        self.assertEqual(len(self.dms.orders), 1)
        self.assertEqual(self.deliveries()[0].status, "Delivered")

    def test_attachment_recovery_keeps_original_order_and_native_delivery_job(self):
        # Old bug: the receipt waited for the files, so a failed upload left the operator with
        # nothing on LINE. Now it rides the confirm click's free reply at once, word for word.
        self.dms.attach_available = False
        nonce = self.review(attachments=True)
        event = self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=nonce)
        self.assertEqual(self.drain(), 1)
        self.assertIsNone(store.get_session(self.uid))
        self.assertEqual((self.deliveries()[0].status, self.deliveries()[0].last_error), ("Pending", "attachments_pending"))
        self.assertEqual(self.dms.attach_calls, 1)  # the round that sends the receipt never repairs
        number = self.dms.orders[0]["number"]
        receipts = [data["json"] for _, url, data in self.http
                    if number in str((data.get("json") or {}).get("messages"))]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["replyToken"], "reply-" + event)
        stored = json.loads(self.deliveries()[0].attempt)["receipt"]["message"]["text"]
        self.assertIn(stored, [message.get("text") for message in receipts[0]["messages"]])
        self.assertFalse(any(url.endswith("/push") for _, url, _ in self.http))
        sent = len(self.http)
        self.dms.attach_available = True
        self.assertEqual(self.due_delivery().status, "Delivered")
        self.assertEqual(len(self.dms.orders), 1)
        self.assertTrue(self.dms.form.get("fulcurrname1"))
        # Adding the files sends nothing to the operator (no second receipt, no notice).
        self.assertFalse([url for _, url, _ in self.http[sent:] if url.endswith(("/push", "/reply"))])

    def test_receipt_failure_reuses_key_without_creating_another_order(self):
        # The free reply is refused (token expired), then the paid push fails too.
        self.reply_status = 400
        self.push_status = 500
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=self.review())
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.deliveries()[0].last_error, "receipt_pending")
        self.push_status = 200
        self.reply_status = 200
        self.assertEqual(self.due_delivery().status, "Delivered")
        keys = [data["headers"]["X-Line-Retry-Key"] for _, url, data in self.http if url.endswith("/push")]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(len(self.dms.orders), 1)

    def test_queued_booking_revoked_binding_does_not_call_dms(self):
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=self.review())
        frappe.db.set_value("Pearnly LINE Binding", {"line_user_id": self.uid}, "status", "unbound")
        frappe.db.commit()
        self.assertEqual(self.drain(), 1)
        self.assertFalse(self.dms.orders)
        self.assertFalse(self.deliveries())

    def messages(self):
        return [json.dumps(data.get("json") or {}, ensure_ascii=False) for _, url, data in self.http
                if url.endswith(("/push", "/reply"))]

    def test_submitted_unknown_result_retains_evidence_and_never_replays(self):
        self.dms.unknown_after_submit = True
        nonce = self.review()
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=nonce)
        self.assertEqual(self.drain(), 1)
        number = self.dms.orders[0]["number"]
        # The evidence moved to a durable record; the conversation (the whole LINE) is free at once.
        self.assertIsNone(store.get_session(self.uid))
        record, = self.deliveries()
        self.assertEqual(record.status, delivery.UNVERIFIED)
        marker = json.loads(record.attempt)
        self.assertEqual(marker["booking_no"], number)
        self.assertTrue(marker["context"]["submitted"])
        self.assertTrue(any(number in text for text in self.messages()))  # the notice carries the number
        self.assertEqual(frappe.db.get_value("Pearnly Push Log", {"actor": self.user, "business_key": number},
                                             "status"), "manual")
        self.send(action=cards.ACT_RETRY_BOOKING, nonce=nonce)
        self.assertEqual(self.drain(), 0)
        self.assertEqual(len(self.dms.orders), 1)
        self.send(text="เมนู")
        self.send(action=cards.ACT_MENU_CUSTOMER)
        self.assertEqual(store.get_session(self.uid)["state"], "collecting")
        # The same customer cannot be booked again while that order is being checked: no DMS call.
        self.dms.unknown_after_submit = False
        fresh = self.review()
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=fresh)
        self.assertEqual(self.drain(), 1)
        self.assertEqual((len(self.dms.orders), self.dms.posts), (1, 1))
        rearmed = store.get_session(self.uid)["payload"]
        self.assertNotIn(rearmed["nonce"], (None, fresh))
        self.assertIn(number, self.messages()[-1])
        self.assertEqual(len(self.deliveries()), 1)

    def test_rejected_booking_returns_the_card_then_a_new_confirmation_books_once(self):
        self.dms.reject_submit = True
        nonce = self.review()
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=nonce)
        self.assertEqual(self.drain(), 1)
        session = store.get_session(self.uid)
        payload = session["payload"]
        self.assertEqual(session["state"], "booking_review")
        self.assertFalse(payload.get("booking_attempt") or payload.get("booking_claim"))
        self.assertNotIn(payload["nonce"], (None, nonce))
        self.assertIn(payload["nonce"], self.messages()[-1])  # one card, its confirm button armed
        self.assertFalse(self.deliveries())
        self.assertEqual(frappe.db.get_value("Pearnly Push Log", {"actor": self.user}, "status"), "failed")
        self.dms.reject_submit = False
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=payload["nonce"])
        self.assertEqual(self.drain(), 1)
        self.assertEqual((len(self.dms.orders), self.dms.posts), (1, 2))  # one rejected, one created
        self.assertEqual(self.deliveries()[0].status, "Delivered")
        self.assertIsNone(store.get_session(self.uid))

    def test_master_rename_returns_one_updated_card_without_submitting(self):
        from pearnly_dms.booking.conversation import qa_cards
        nonce = self.review()
        # The daytime change sync brings the DMS rename into the local snapshot (ADR-014).
        self.dms.masters["place_books"][0][2] = "Renamed Place"
        self.republish_company()
        self.send(action=cards.ACT_CONFIRM_BOOKING, nonce=nonce)
        self.assertEqual(self.drain(), 1)
        payload = store.get_session(self.uid)["payload"]
        self.assertNotEqual(payload["nonce"], nonce)
        self.assertFalse(self.dms.orders)
        self.assertFalse(any(url.endswith("/push") for _, url, _ in self.http))
        replies = [data["json"]["messages"] for _, url, data in self.http
                   if url.endswith("/reply") and qa_cards.TXT_MASTER_CHANGED in str(data["json"])]
        self.assertEqual(len(replies), 1)
        self.assertEqual(len(replies[0]), 1)
        self.assertEqual(payload["qa"]["answers"]["place"]["name"], "Renamed Place")


if __name__ == "__main__":
    if not SNAPSHOT_LAB and os.environ.get("PEARNLY_DMS_FLOW_LAB") != "1":
        raise SystemExit("Run only in the dedicated offline DMS flow laboratory")
    if Path("sites").is_dir():
        os.chdir("sites")
    unittest.main(verbosity=2)
