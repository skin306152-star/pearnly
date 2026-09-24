"""Native roster/cache/permission checks in the isolated snapshot lab.

Only authenticated DMS transport is replaced. Real company snapshots, ordinary
DMS Admin roles, encrypted credentials, operator hooks and independent MariaDB
connections remain active. No DMS/LINE network or shared runtime is used.
"""
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import frappe
from pearnly_dms.client.gateway import DmsClient
from pearnly_dms.operators import advisors, roster
from pearnly_dms.snapshots import store

lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))


class NativeOperatorAdvisors(lab["SnapshotLabCase"]):
    def setUp(self):
        super().setUp()
        self.actor, self.operator = self.users[1], self.users[0]
        frappe.get_doc("User", self.actor).add_roles("DMS Admin")
        self.publish({"masters": {"advisors": [["335", "CACHED", "Synthetic cached advisor"]]},
                      "catalogues": {"fixture": {}}}, endpoint=self.template, dataset="company")
        frappe.db.commit()
        self.requests = []
        self.after_read = lambda: None
        frappe.set_user(self.actor)

    def authenticated(self, endpoint, callback, **kwargs):
        self.assertEqual(endpoint, self.template)
        self.assertEqual(kwargs, {"authoritative_read": True})
        def post(url, *, data):
            self.assertEqual(url, "https://snapshot.invalid/dms/drfcbc/component/bshsd.php")
            self.assertEqual(data, {"elemname": "txtusers", "sdt": "", "bshsdamt": "200", "bshsdcurrpage": "1"})
            self.requests.append(dict(data))
            return SimpleNamespace(status_code=200, text=json.dumps([["335", "LIVE", "Synthetic current advisor"]]))
        result = callback(DmsClient(SimpleNamespace(post=post), "https://snapshot.invalid/dms/"), None)
        self.after_read()
        return result

    def test_native_company_only_options_and_live_save_uses_current_name(self):
        with patch.object(advisors, "run_logged_in", self.authenticated):
            cached = roster.api_advisors()
            self.assertEqual(cached, {"items": [{"id": "335", "code": "CACHED", "name": "Synthetic cached advisor"}], "error": ""})
            self.assertEqual(self.requests, [])
            result = roster.update_operator(self.operator, advisor_id="335", advisor_name="Untrusted caller label")
        self.assertEqual((result["advisor_id"], result["advisor_name"]), ("335", "Synthetic current advisor"))
        self.assertEqual(frappe.db.get_value("DMS Operator", self.operator, ["advisor_id", "advisor_name"]),
                         ("335", "Synthetic current advisor"))
        self.assertEqual(len(self.requests), 1)
        for dataset in ("vehicles", "customers", "geo"):
            self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.template, dataset)

    def test_cross_connection_role_revocation_rejects_live_result(self):
        before = frappe.db.get_value("DMS Operator", self.operator, ["advisor_id", "advisor_name"])
        self.assertIn("DMS Admin", frappe.get_roles(self.actor))
        def revoke():
            with store._cache_connection() as database:
                database.sql("DELETE FROM `tabHas Role` WHERE parent=%s AND parenttype='User' AND role=%s",
                             (self.actor, "DMS Admin"))
        self.after_read = revoke
        with patch.object(advisors, "run_logged_in", self.authenticated), self.assertRaises(frappe.PermissionError):
            roster.update_operator(self.operator, advisor_id="335")
        frappe.db.rollback()
        self.assertEqual(frappe.db.get_value("DMS Operator", self.operator, ["advisor_id", "advisor_name"]), before)
        self.assertEqual(len(self.requests), 1)

    def test_cross_connection_credential_rotation_rejects_live_result(self):
        from frappe.utils.password import encrypt
        before = frappe.db.get_value("DMS Operator", self.operator, ["advisor_id", "advisor_name"])
        def rotate():
            # Genuine encrypted native Auth row, changed/committed through a
            # separate connection while the caller still holds its old RR view.
            with store._cache_connection() as database:
                database.sql("UPDATE __Auth SET password=%s WHERE doctype=%s AND name=%s AND fieldname=%s AND encrypted=1",
                    (encrypt(self.password + "-rotated"), "Pearnly ERP Endpoint", self.template, "admin_password"))
        self.after_read = rotate
        with patch.object(advisors, "run_logged_in", self.authenticated), self.assertRaises(frappe.ValidationError) as failure:
            roster.update_operator(self.operator, advisor_id="335")
        self.assertEqual(str(failure.exception), frappe._("roster.advisor.loadError"))
        frappe.db.rollback()
        self.assertEqual(frappe.db.get_value("DMS Operator", self.operator, ["advisor_id", "advisor_name"]), before)
        self.assertEqual(len(self.requests), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
