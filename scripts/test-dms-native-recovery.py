"""Native recovery regressions on the disposable dms-flow-lab Site only.

Real MariaDB sessions/bindings/ledger/Delivery, Redis leases and serialized RQ
jobs execute reconcile -> exact HTML readback -> durable completion. Only the
authenticated DMS transport and LINE HTTP are fake; no external order is made.
No scheduler/worker daemon runs. The script fails on unexpected IO or targets.

Run from bench/sites with the pinned runtime:
  ../env/bin/python /workspace/scripts/test-dms-native-recovery.py

Creates UUID-owned synthetic records. Cleanup clears only their sessions and
jobs, disables their identity/endpoint and parks Pending deliveries in Review.
Their native audit records remain until this dedicated lab is removed. This is
local integration evidence, not real LINE/DMS or browser acceptance.
"""
import copy
from html import escape
import json
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

import frappe
from frappe.utils.background_jobs import get_queue
import requests

from pearnly_core import line_session as store
from pearnly_dms.booking import delivery, recovery
from pearnly_dms.client.gateway import DmsClient
from pearnly_dms.client.transport import Resp
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.line.identity import current

SNAPSHOT_LAB = os.environ.get("PEARNLY_DMS_SNAPSHOT_LAB") == "1"
SITE = "dms-snapshot-lab.localhost" if SNAPSHOT_LAB else "dms-flow-lab.localhost"
RECOVER = "pearnly_dms.booking.recovery.reconcile"
CHECK = "pearnly_dms.booking.recovery.reconcile_record"
DELIVER = "pearnly_dms.booking.delivery.deliver"


def connect():
    if SNAPSHOT_LAB:
        lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))
        lab["connect"]()
        if frappe.conf.get("pearnly_line_channel_secret") != "dms-snapshot-lab-only":
            raise RuntimeError("Refusing foreign snapshot-lab signing credentials")
        return
    frappe.init(site=SITE, force=True)
    frappe.connect()
    if not (frappe.conf.get("pearnly_dms_flow_lab") and frappe.conf.get("db_host") == "db"
            and frappe.conf.get("pearnly_line_channel_access_token") == "lab:" + SITE):
        raise RuntimeError("Refusing a Site outside the dedicated flow lab")
    frappe.set_user("Administrator")


class NativeRecovery(unittest.TestCase):
    def setUp(self):
        connect()
        self.uid = "DMS-RECOVERY-" + uuid.uuid4().hex
        self.user = self.uid.lower() + "@example.invalid"
        self.nonce = uuid.uuid4().hex
        self.number = "BK-" + uuid.uuid4().hex
        self.http, self.dms_calls, self.forbidden, self.profile_reads = [], [], [], []
        self.jobs = set()
        self.revoke_on_download = ""
        self.endpoint = None
        self.enterContext(patch.object(frappe, "in_test", True))
        self.enterContext(patch.object(requests.sessions.Session, "request", self.line_transport))
        self.enterContext(patch.object(recovery, "run_logged_in", self.authenticated_session))
        self.enterContext(patch.object(delivery, "run_logged_in", self.authenticated_session))
        with patch.dict(frappe.flags, {"in_import": True}):
            frappe.get_doc({"doctype": "User", "email": self.user, "first_name": self.uid,
                            "enabled": 1, "send_welcome_email": 0,
                            "roles": [{"role": "DMS Admin"}]}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Pearnly LINE Binding", "line_user_id": self.uid,
                        "user": self.user, "status": "active"}).insert(ignore_permissions=True)
        endpoint = frappe.get_doc({"doctype": "Pearnly ERP Endpoint", "label": self.uid,
            "adapter": "mrerp_dms", "owner_user": self.user, "enabled": 1,
            "system_url": "https://dms.invalid/index.php", "username": self.uid,
            "booking_defaults": "{}"}).insert(ignore_permissions=True)
        self.endpoint = endpoint.name
        frappe.db.commit()
        self.submitted = {"txtdocno": self.number, "cusval": "customer-fixture",
            "txtpeopleid": "1101700203451", "usersval": "advisor-fixture",
            "carval": "car-fixture", "carpaintval": "paint-fixture", "txtprice": "1000"}
        self.forms = {"fixture-order": {**self.submitted, "stsel": "e", "idsel": "fixture-order"}}
        # DmsClient's HTML parser, readback evaluator and attachment implementation
        # stay real. This object substitutes only its browser HTTP transport.
        self.client = DmsClient(self, "https://dms.invalid/dms/")

    def tearDown(self):
        connect()
        queue = get_queue("long")
        for job_id in self.jobs:
            job = queue.fetch_job(job_id)
            if job:
                self.assertTrue(self.owns_job(job), "Refusing cleanup of a changed/foreign job")
                queue.remove(job_id)
                job.delete()
        if frappe.db.exists(store.DT, self.uid):
            session = store.get_session(self.uid)
            if (session or {}).get("payload", {}).get("booking_attempt"):
                self.assertTrue(store.clear_booking_attempt(self.uid, self.nonce))
            else:
                store.clear_session(self.uid)
        for row in self.deliveries():
            if row.status in ("Pending", delivery.UNVERIFIED):
                frappe.db.set_value(delivery.DT, row.name,
                                    {"status": "Review", "last_error": "lab_test_finished"})
        frappe.db.set_value("Pearnly LINE Binding", {"line_user_id": self.uid}, "status", "unbound")
        if self.endpoint:
            frappe.db.set_value("Pearnly ERP Endpoint", self.endpoint, "enabled", 0)
        if frappe.db.exists("User", self.user):
            frappe.db.set_value("User", self.user, "enabled", 0)
        frappe.db.commit()
        frappe.destroy()
        self.assertEqual(self.forbidden, [], "Unexpected network/write attempt was swallowed")

    def line_transport(self, method, url, **kwargs):
        if method.upper() == "GET" and url == "https://api.line.me/v2/bot/profile/" + self.uid:
            # The receipt is written in the recipient's phone language (user 2026-09-27):
            # a read-only profile lookup, recorded apart from the message traffic.
            self.profile_reads.append(url)
            response = requests.Response()
            response.status_code = 200
            response._content = b'{"language": "th"}'
            response._content_consumed = True
            return response
        self.http.append((method.upper(), url, copy.deepcopy(kwargs)))
        is_content = url == "https://api-data.line.me/v2/bot/message/fixture-image/content"
        is_push = url == "https://api.line.me/v2/bot/message/push"
        if not ((method.upper() == "GET" and is_content) or (method.upper() == "POST" and is_push)):
            self.forbidden.append((method, url))
            raise AssertionError("Unexpected HTTP attempt")
        if is_content and self.revoke_on_download:
            # A real native binding revision change during the slow download,
            # not a patched permission predicate. No network is sent.
            # Rebinding to the same employee changes modified/revision even
            # when the current status remains active. Exercise that guard too.
            status = "active" if self.revoke_on_download == "rebind" else "unbound"
            frappe.db.set_value("Pearnly LINE Binding", {"line_user_id": self.uid}, "status", status)
            frappe.db.commit()
        response = requests.Response()
        response.status_code = 200
        response._content = b"\xff\xd8local-fixture" if is_content else b"{}"
        response._content_consumed = True
        response.headers["Content-Type"] = "image/jpeg" if is_content else "application/json"
        return response

    def authenticated_session(self, endpoint, fn, **kwargs):
        self.assertEqual(endpoint_name(endpoint), self.endpoint)
        self.assertTrue(kwargs.get("background"))
        return fn(self.client, None)

    def post(self, url, data=None, files=None, timeout_ms=None):
        """Strict fake browser transport: only three read-only DMS endpoints."""
        data = dict(data or {})
        self.dms_calls.append((url, data, bool(files)))
        parsed = urlsplit(url)
        if parsed.netloc != "dms.invalid" or files:
            self.forbidden.append(("DMS", url))
            raise AssertionError("Unexpected DMS destination/upload")
        if parsed.path == "/dms/drfcbc/view.php":
            body = ""
        elif parsed.path == "/dms/drfcbc/component/showdata.php":
            self.assertEqual(data["sd"], self.number)
            body = "".join('<div data-val="' + escape(key) + '">fixture</div>' for key in self.forms)
        elif parsed.path == "/dms/drfcbc/form.php":
            self.assertEqual(data["status"], "e")
            fields = self.forms[data["id"]]
            body = "<form>" + "".join('<input name="' + escape(key) + '" value="' +
                escape(str(value), quote=True) + '">' for key, value in fields.items()) + "</form>"
        else:
            self.forbidden.append(("DMS", url))
            raise AssertionError("Recovery attempted a non-read DMS endpoint")
        return Resp(200, body, body.encode())

    def seed_unknown(self, *, attachments=False, minutes=20):
        qa = {"customer": {"id": "customer-fixture"}, "advisor": {"id": "advisor-fixture", "name": "Local"},
              "answers": {"car": {"id": "car-fixture", "label": "Local car"},
                          "delivery_date_be": "30/09/2569"},
              "payments": [], "files": {"id_card_mid": "fixture-image"} if attachments else {}}
        live = current(self.uid)
        self.assertIsNotNone(live)
        self.binding_revision = live["binding_revision"]
        context = {"binding": {"user_id": live["user"], "binding_revision": live["binding_revision"]},
                   "endpoint": self.endpoint, "endpoint_fingerprint": recovery.endpoint_fingerprint(self.endpoint),
                   "qa": qa, "submitted": self.submitted}
        store.set_session(self.uid, "booking_review", {"nonce": self.nonce, "qa": qa})
        self.assertTrue(store.consume_nonce(self.uid, "booking_review", self.nonce))
        # Timestamp is immutable after registration. Simulate the original
        # worker's clock at registration, never rewrite its persisted evidence.
        with patch.object(frappe.utils, "now", return_value=str(frappe.utils.add_to_date(minutes=-minutes))):
            self.assertTrue(store.record_booking_attempt(self.uid, self.nonce, self.number,
                                                         "fixture-worker", context=context))
        self.assertTrue(recovery.ready(store.get_session(self.uid)["payload"]["booking_attempt"]))

    def seed_delivery(self):
        self.seed_unknown(attachments=True)
        marker = store.get_session(self.uid)["payload"]["booking_attempt"]
        marker.update(completed_at=frappe.utils.now(), result={"ok": True, "booking_id": "fixture-order",
                      "booking_no": self.number, "attach_ok": False, "attached": 0})
        name = delivery.retain(self.uid, marker)
        self.assertTrue(store.clear_booking_attempt(self.uid, self.nonce))
        return name

    def owns_job(self, job):
        if not job or job.func_name != "frappe.utils.background_jobs.execute_job":
            return False
        envelope = job.kwargs
        args = envelope.get("kwargs") or {}
        return bool(envelope.get("site") == SITE and (
            (envelope.get("method") == RECOVER and args.get("line_user_id") == self.uid)
            or (envelope.get("method") in (DELIVER, CHECK)
                and args.get("name") == delivery.name_for(self.uid, self.nonce))))

    def execute_queued(self, schedule, target):
        queue = get_queue("long")
        before = set(queue.get_job_ids())
        schedule()
        frappe.db.commit()
        created = set(queue.get_job_ids()) - before
        owned = {job_id for job_id in created if self.owns_job(queue.fetch_job(job_id))}
        # Concurrent unrelated jobs must survive even when this assertion fails.
        # Cleanup may only remember identities already proven to be ours.
        self.jobs.update(owned)
        self.assertEqual(created, owned, "Unexpected concurrent/foreign job; left untouched")
        self.assertEqual(len(owned), 1)
        job_id = owned.pop()
        # Refetch from Redis to enforce serialization and dotted-path resolution.
        job = queue.fetch_job(job_id)
        self.assertEqual(job.func_name, "frappe.utils.background_jobs.execute_job")
        self.assertEqual((job.kwargs["site"], job.kwargs["method"]), (SITE, target))
        queue.remove(job_id)
        try:
            job.perform()
        finally:
            connect()
            job.delete()
            self.jobs.discard(job_id)
        self.assertTrue(before.issubset(set(get_queue("long").get_job_ids())))

    def reconcile_queued(self):
        self.execute_queued(lambda: recovery.request(self.uid), RECOVER)

    def delivery_queued(self, name):
        self.execute_queued(lambda: frappe.enqueue(DELIVER, name=name, queue="long", timeout=600), DELIVER)

    def check_queued(self):
        """The record path: what reconcile_due enqueues for a due Unverified record (only ours)."""
        name = delivery.name_for(self.uid, self.nonce)
        self.execute_queued(lambda: frappe.enqueue(CHECK, name=name, queue="long", timeout=600), CHECK)
        return name

    def hold_queued(self):
        """Conversation path: the evidence moves to a durable record without any DMS request."""
        self.reconcile_queued()
        self.assertIsNone(store.get_session(self.uid))
        self.assertFalse(self.dms_calls)
        record, = self.deliveries()
        self.assertEqual(record.status, delivery.UNVERIFIED)
        return record

    def deliveries(self):
        return frappe.get_all(delivery.DT, filters={"line_user_id": self.uid},
                              fields=["name", "status", "last_error", "attempt"])

    def logs(self):
        return frappe.get_all("Pearnly Push Log", filters={"actor": self.user, "business_key": self.number},
                              fields=["status", "endpoint", "actor", "response_body"])

    def assert_unknown_retained(self):
        """Still unverified: the evidence stays durable, no success, no receipt, never a new order."""
        record, = self.deliveries()
        self.assertEqual((record.status, record.booking_id or ""), (delivery.UNVERIFIED, ""))
        marker = json.loads(record.attempt)
        self.assertEqual(marker["context"]["submitted"], self.submitted)
        self.assertFalse(marker.get("result"))
        self.assertEqual([log.status for log in self.logs()], ["manual"])
        self.assertEqual(len(self.http), 1)  # only the one unknown notice from holding
        self.assertIsNone(store.get_session(self.uid))
        return record

    def test_unknown_exact_html_readback_completes_native_ledger_and_delivery_once(self):
        self.seed_unknown()
        self.hold_queued()
        # A crashed worker sent nothing, so holding tells the operator once, with the number.
        self.assertEqual(len(self.http), 1)
        self.assertIn(self.number, str(self.http[0][2]["json"]))
        self.assertEqual([log.status for log in self.logs()], ["manual"])
        self.check_queued()
        rows = self.deliveries()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "Delivered")
        self.assertEqual(json.loads(rows[0].attempt)["result"]["booking_id"], "fixture-order")
        logs = self.logs()
        self.assertEqual(len(logs), 1)  # the manual row became the success row
        self.assertEqual((logs[0].status, logs[0].endpoint, logs[0].actor), ("success", self.endpoint, self.user))
        self.assertEqual(json.loads(logs[0].response_body)["booking_id"], "fixture-order")
        self.assertEqual(len(self.http), 2)
        self.assertIn(self.number, str(self.http[1][2]["json"]))
        self.assertEqual(self.http[1][2]["headers"]["X-Line-Retry-Key"], json.loads(rows[0].attempt)["receipt"]["key"])
        self.assertEqual(sum(url.endswith("form.php") for url, _, _ in self.dms_calls), 2)
        io_counts = (len(self.http), len(self.dms_calls))
        self.delivery_queued(rows[0].name)
        self.assertEqual((len(self.http), len(self.dms_calls)), io_counts)
        self.assertEqual(len(self.logs()), 1)

    def test_unknown_critical_mismatch_keeps_evidence_without_success_or_receipt(self):
        self.seed_unknown()
        self.forms["fixture-order"]["txtprice"] = "1001"
        self.hold_queued()
        self.check_queued()
        self.assertTrue(any(url.endswith("form.php") for url, _, _ in self.dms_calls))
        record = self.assert_unknown_retained()
        # Checked again later (5, 10, 20, 40 minutes, then hourly), never at once.
        self.assertGreater(frappe.utils.get_datetime(frappe.db.get_value(delivery.DT, record.name, "next_attempt_at")),
                           frappe.utils.add_to_date(minutes=9))

    def test_unknown_two_exact_candidates_never_selects_first_order(self):
        self.seed_unknown()
        self.forms["other-order"] = {**self.forms["fixture-order"], "idsel": "other-order"}
        self.hold_queued()
        self.check_queued()
        self.assertEqual(sum(url.endswith("form.php") for url, _, _ in self.dms_calls), 2)
        self.assert_unknown_retained()

    def test_unknown_endpoint_identity_change_refuses_readback(self):
        self.seed_unknown()
        frappe.db.set_value("Pearnly ERP Endpoint", self.endpoint, "username", "different-fixture-identity")
        frappe.db.commit()
        self.hold_queued()
        self.check_queued()
        self.assertFalse(self.dms_calls)
        self.assertEqual(self.assert_unknown_retained().last_error, "endpoint_changed")

    def test_unknown_after_rebind_is_read_back_and_parked_for_review(self):
        # Old bug: a rebind (even of the same employee) changed the binding revision; recovery
        # then skipped this order forever and the evidence kept that LINE locked.
        self.seed_unknown()
        frappe.db.set_value("Pearnly LINE Binding", {"line_user_id": self.uid}, "status", "active")
        frappe.db.commit()
        self.assertNotEqual(current(self.uid)["binding_revision"], self.binding_revision)
        self.hold_queued()
        self.assertFalse(self.http)  # nothing for the newly bound identity
        self.check_queued()
        record, = self.deliveries()
        self.assertEqual((record.status, record.last_error), ("Review", "binding_changed"))
        self.assertEqual(json.loads(record.attempt)["result"]["booking_id"], "fixture-order")
        self.assertEqual([log.status for log in self.logs()], ["success"])
        self.assertFalse(self.http)

    def test_unknown_stops_after_23_hours_without_reading_dms(self):
        self.seed_unknown(minutes=23 * 60 + 1)
        self.hold_queued()
        self.check_queued()
        self.assertFalse(self.dms_calls)
        record, = self.deliveries()
        self.assertEqual((record.status, record.last_error), ("Review", "unverified_expired"))
        self.assertEqual([log.status for log in self.logs()], ["manual"])

    def test_attachment_endpoint_identity_change_refuses_download_and_write(self):
        name = self.seed_delivery()
        frappe.db.set_value("Pearnly ERP Endpoint", self.endpoint, "username", "different-fixture-identity")
        frappe.db.commit()
        self.delivery_queued(name)
        self.assertFalse(self.dms_calls)
        self.assertFalse(self.http)
        self.assertEqual(self.deliveries()[0].status, "Pending")
        self.assertFalse(json.loads(self.deliveries()[0].attempt)["result"]["attach_ok"])
        self.assertFalse(self.logs())

    def assert_download_identity_change_rejected(self, mutation):
        name = self.seed_delivery()
        self.revoke_on_download = mutation
        self.delivery_queued(name)
        self.assertEqual(len(self.http), 1)
        self.assertTrue(self.http[0][1].endswith("/content"))
        self.assertEqual(len(self.dms_calls), 1)
        self.assertTrue(self.dms_calls[0][0].endswith("form.php"))
        if mutation == "rebind":
            self.assertEqual(current(self.uid)["user"], self.user)
            self.assertNotEqual(current(self.uid)["binding_revision"], self.binding_revision)
        else:
            self.assertFalse(current(self.uid))
        self.assertEqual(self.deliveries()[0].status, "Pending")
        self.delivery_queued(name)
        self.assertEqual((self.deliveries()[0].status, self.deliveries()[0].last_error), ("Review", "binding_changed"))
        self.assertEqual((len(self.http), len(self.dms_calls)), (1, 1))
        self.assertFalse(self.logs())

    def test_attachment_binding_revoked_during_download_never_uploads_or_sends(self):
        self.assert_download_identity_change_rejected("unbind")

    def test_attachment_same_employee_rebound_during_download_never_uploads_or_sends(self):
        self.assert_download_identity_change_rejected("rebind")

    def test_concurrent_foreign_job_is_neither_executed_nor_adopted_for_cleanup(self):
        self.seed_unknown()
        foreign_uid = self.uid + "-foreign-fixture"
        foreign = None

        def schedule():
            nonlocal foreign
            recovery.request(self.uid)
            foreign = frappe.enqueue(RECOVER, line_user_id=foreign_uid, queue="long", timeout=600)

        try:
            with self.assertRaisesRegex(AssertionError, "Unexpected concurrent/foreign job"):
                self.execute_queued(schedule, RECOVER)
            queue = get_queue("long")
            self.assertIsNotNone(foreign)
            self.assertIn(foreign.id, queue.get_job_ids())
            self.assertNotIn(foreign.id, self.jobs)
            self.assertEqual(len(self.jobs), 1)
            self.assertFalse(self.http)
            self.assertFalse(self.dms_calls)
        finally:
            # This deliberately foreign-to-the-harness job is itself a precise
            # fixture owned by this test. Never broaden cleanup to queue deltas.
            if foreign:
                self.assertEqual(foreign.kwargs["kwargs"]["line_user_id"], foreign_uid)
                get_queue("long").remove(foreign.id)
                foreign.delete()


if __name__ == "__main__":
    os.chdir("/home/frappe/frappe-bench/sites")
    unittest.main(verbosity=2)
