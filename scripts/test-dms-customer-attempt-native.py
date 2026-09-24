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

    def make(self, suffix="a", owner=0):
        marker = attempt(self.endpoints[owner], self.users[owner], self.uid + "-" + suffix)
        self.names.add(marker.name)
        return marker

    def tearDown(self):
        frappe.db.rollback()
        for name in self.names:
            frappe.db.delete(LOG, {"name": name, "actor": ["in", self.users]})
        frappe.db.commit()
        self.assertFalse(any(frappe.db.exists(LOG, name) for name in self.names))
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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--reserve":
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
