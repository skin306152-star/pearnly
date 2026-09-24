"""Customer dispatch ledger with real MariaDB/Redis in the guarded snapshot lab.

Reuses SnapshotLabCase's exact Site/marker/fake-credential/internal-host guard.
No LINE, OCR, third-party calls, workers or queue consumption. UUID-owned ledger
rows are precisely removed; synthetic accounts are disabled by the shared fixture.
"""
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
import uuid

import frappe

from pearnly_dms.intake.customer_attempt import Attempt, LOG
from pearnly_dms.contracts.client_base import DmsClientError

lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))


def attempt(endpoint, actor, request_id):
    return Attempt(endpoint, request_id, actor, fields={"people_id": "1000001205453", "name": "SYNTHETIC"},
                   mode="create", customer_id=None, addresses={}, baseline=None)


class NativeCustomerAttempt(lab["SnapshotLabCase"]):
    def setUp(self):
        super().setUp()
        self.names = set()
        self.approvals = set()

    def make(self, suffix="a", owner=0):
        marker = attempt(self.endpoints[owner], self.users[owner], self.uid + "-" + suffix)
        self.names.add(marker.name)
        return marker

    def tearDown(self):
        frappe.db.rollback()
        for name in self.names:
            frappe.db.delete(LOG, {"name": name, "actor": ["in", self.users]})
        for name in self.approvals:
            frappe.db.delete("DMS Change Request", {"name": name, "operator_user": ["in", self.users]})
        frappe.db.commit()
        self.assertFalse(any(frappe.db.exists(LOG, name) for name in self.names))
        self.assertFalse(any(frappe.db.exists("DMS Change Request", name) for name in self.approvals))
        super().tearDown()

    def test_before_dispatch_has_no_marker_and_success_recovers_only_this_request(self):
        marker = self.make()
        self.assertIsNone(marker.previous())
        self.assertFalse(frappe.db.exists(LOG, marker.name))
        marker.begin()
        self.assertEqual(frappe.db.get_value(LOG, marker.name, "status"), "manual")
        marker.finish({"ok": True, "success": True, "customer_id": "SYNTHETIC-CUSTOMER", "mode": "create"})
        replay = marker.previous()
        self.assertEqual(replay["customer_id"], "SYNTHETIC-CUSTOMER")
        self.assertTrue(replay["recovered"])
        other = self.make("different")
        self.assertIsNone(other.previous())

    def test_unknown_survives_db_reconnect_and_does_not_unlock_new_request(self):
        marker = self.make()
        marker.begin()
        result = marker.finish({"ok": False, "error_code": "ERR_UNEXPECTED"})
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_UNCONFIRMED")
        frappe.destroy()
        lab["connect"]()
        for candidate in (marker, self.make("new", owner=1)):
            with self.assertRaises(DmsClientError) as caught:
                candidate.previous()
            self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(frappe.db.get_value(LOG, marker.name, "status"), "manual")

    def test_definite_rejection_releases_identity_not_old_request(self):
        marker = self.make()
        marker.begin()
        marker.finish({"ok": False, "error_code": "ERR_DMS_CUSTOMER_SAVE",
                       "response_body": {"submitted": False, "rejected": True}})
        self.assertEqual(frappe.db.get_value(LOG, marker.name, "status"), "failed")
        with self.assertRaises(DmsClientError):
            marker.previous()
        other = self.make("new")
        self.assertIsNone(other.previous())
        other.begin()
        self.assertEqual(frappe.db.get_value(LOG, other.name, "status"), "manual")

    def test_two_processes_same_identity_reserve_exactly_once_across_employee_endpoints(self):
        requests = [self.uid + "-race-" + uuid.uuid4().hex for _ in self.endpoints]
        for endpoint, actor, request in zip(self.endpoints, self.users, requests):
            self.names.add(attempt(endpoint, actor, request).name)
        command = [sys.executable, str(Path(__file__).resolve()), "--reserve"]
        processes = [subprocess.Popen(command + [endpoint, actor, request], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
                     for endpoint, actor, request in zip(self.endpoints, self.users, requests)]
        outcomes = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr[-1500:])
            outcomes.append(json.loads(stdout)["outcome"])
        frappe.db.commit()
        self.assertEqual(sorted(outcomes), ["blocked", "reserved"])
        self.assertEqual(sum(bool(frappe.db.exists(LOG, name)) for name in self.names), 1)

    def test_pending_create_blocks_phone_only_edit_across_url_spelling_and_actor(self):
        marker = self.make()
        marker.begin()
        original = frappe.db.get_value("Pearnly ERP Endpoint", self.endpoints[1], "system_url")
        try:
            frappe.db.set_value("Pearnly ERP Endpoint", self.endpoints[1], "system_url",
                                "https://SNAPSHOT.invalid:443/dms/index.php")
            frappe.db.commit()
            edit = Attempt(self.endpoints[1], self.uid + "-edit", self.users[1],
                fields={"phone": "0800000999"}, mode="overwrite", customer_id="SYNTHETIC-CUSTOMER",
                addresses={}, baseline={"people_id": "1000001205453"})
            self.names.add(edit.name)
            with self.assertRaises(DmsClientError):
                edit.previous()
            with self.assertRaises(DmsClientError):
                edit.begin()
            self.assertFalse(frappe.db.exists(LOG, edit.name))
        finally:
            frappe.db.set_value("Pearnly ERP Endpoint", self.endpoints[1], "system_url", original)
            frappe.db.commit()

    def test_pending_identity_change_guards_native_id_and_both_old_and_new_ids(self):
        marker = Attempt(self.endpoint, self.uid + "-claim", self.users[0],
            fields={"people_id": "1000000730317"}, mode="overwrite", customer_id="SYNTHETIC-CUSTOMER",
            addresses={}, baseline={"people_id": "1000001205453"})
        self.names.add(marker.name)
        marker.begin()
        for index, (fields, cid, baseline) in enumerate((
            ({"people_id": "1000000730317"}, None, None),
            ({"people_id": "1000001205453"}, None, None),
            ({"phone": "0800000999"}, "SYNTHETIC-CUSTOMER", None),
        )):
            other = Attempt(self.endpoints[1], self.uid + "-alias-" + str(index), self.users[1],
                fields=fields, mode="overwrite" if cid else "create", customer_id=cid,
                addresses={}, baseline=baseline)
            self.names.add(other.name)
            with self.assertRaises(DmsClientError):
                other.begin()
            self.assertFalse(frappe.db.exists(LOG, other.name))

    def test_stale_approval_worker_cannot_finish_another_claim_from_old_rr_view(self):
        from pearnly_dms.approval import approval_store
        request = approval_store.create_request(self.users[0], endpoint_id=self.endpoint,
                                               customer_id="SYNTHETIC-CUSTOMER")
        self.assertTrue(request)
        self.approvals.add(request)
        claimed = approval_store.claim(request, self.users[0])
        self.assertEqual(claimed["status"], "processing")
        # Establish this connection's old RR view before another process claims.
        self.assertEqual(approval_store.get_request(request)["decided_by"], self.users[0])
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--reclaim",
                                 request, self.users[1]], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertTrue(json.loads(result.stdout)["claimed"])
        self.assertFalse(approval_store.finish(request, "approved", expected_claim=claimed))
        frappe.db.commit()
        live = approval_store.get_request(request)
        self.assertEqual((live["status"], live["decided_by"]), ("processing", self.users[1]))
        self.assertTrue(approval_store.finish(request, "rejected", expected_claim=live))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--reclaim":
        lab["connect"]()
        try:
            from pearnly_dms.approval import approval_store
            request, actor = sys.argv[2:4]
            # Only the UUID-owned fixture request can be expired/reclaimed.
            operator = frappe.db.get_value(approval_store.DT, request, "operator_user")
            if not operator or not operator.startswith("snapshot-") or not actor.startswith("snapshot-"):
                raise RuntimeError("Refusing foreign approval fixture")
            frappe.db.set_value(approval_store.DT, request, "processing_at",
                                frappe.utils.add_to_date(minutes=-11))
            frappe.db.commit()
            print(json.dumps({"claimed": bool(approval_store.claim(request, actor))}))
        finally:
            frappe.destroy()
    elif len(sys.argv) > 1 and sys.argv[1] == "--reserve":
        lab["connect"]()
        try:
            try:
                attempt(*sys.argv[2:5]).begin()
                outcome = "reserved"
            except DmsClientError as exc:
                if exc.error_code != "ERR_DMS_CUSTOMER_UNCONFIRMED":
                    raise
                outcome = "blocked"
            print(json.dumps({"outcome": outcome}))
        finally:
            frappe.destroy()
    else:
        unittest.main(defaultTest="NativeCustomerAttempt", verbosity=2)
