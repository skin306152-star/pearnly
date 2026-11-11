#!/usr/bin/env python3
"""No Frappe: the tenant list shows the username and products, kept by the deployment records."""
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / "apps/pearnly_control/pearnly_control"
DOCTYPES = ROOT / "pearnly_control/doctype"
DEPLOYMENT = DOCTYPES / "pearnly_deployment/pearnly_deployment.py"


class _Row(dict):
    __getattr__ = dict.get


class TenantListSummary(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text
        frappe._dict = _Row
        frappe.db = Mock()
        frappe.get_all = Mock()
        document = types.ModuleType("frappe.model.document")
        document.Document = object
        modules = patch.dict(sys.modules, {
            "frappe": frappe,
            "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": document,
        })
        modules.start()
        self.addCleanup(modules.stop)
        self.frappe = frappe
        self.ns = {"__file__": str(DEPLOYMENT)}
        exec(compile(DEPLOYMENT.read_text(), str(DEPLOYMENT), "exec"), self.ns)

    def test_products_in_order_without_duplicates_and_username_from_the_issue(self):
        self.frappe.db.exists.return_value = True
        self.frappe.get_all.return_value = [
            _Row(product="erp", issue="ISS-1"), _Row(product="cowork", issue="ISS-1"), _Row(product="erp", issue=None)]
        self.frappe.db.get_value.return_value = "klm.pearnly"
        self.ns["refresh_tenant_summary"]("T-1")
        self.frappe.db.get_value.assert_called_once_with("Pearnly Issue", "ISS-1", "account_username")
        self.frappe.db.set_value.assert_called_once_with(
            "Pearnly Tenant", "T-1", {"account_username": "klm.pearnly", "products": "erp, cowork"},
            update_modified=False)

    def test_without_a_deployment_issue_the_username_comes_from_the_issue_naming_the_tenant(self):
        self.frappe.db.exists.return_value = True
        self.frappe.get_all.return_value = []
        self.frappe.db.get_value.return_value = "gizerpearnly"
        self.ns["refresh_tenant_summary"]("T-1")
        self.frappe.db.get_value.assert_called_once_with(
            "Pearnly Issue", {"tenant": "T-1"}, "account_username", order_by="creation asc")
        self.frappe.db.set_value.assert_called_once_with(
            "Pearnly Tenant", "T-1", {"account_username": "gizerpearnly", "products": ""}, update_modified=False)

    def test_a_deleted_tenant_is_left_alone(self):
        self.frappe.db.exists.return_value = False
        self.ns["refresh_tenant_summary"]("T-1")
        self.frappe.db.set_value.assert_not_called()


class Wiring(unittest.TestCase):
    def test_both_columns_are_read_only_and_listed(self):
        meta = json.loads((DOCTYPES / "pearnly_tenant/pearnly_tenant.json").read_text())
        fields = {f["fieldname"]: f for f in meta["fields"]}
        for name in ("account_username", "products"):
            self.assertIn(name, meta["field_order"])
            self.assertEqual(fields[name].get("read_only"), 1)
            self.assertEqual(fields[name].get("in_list_view"), 1)

    def test_deployment_changes_refresh_the_tenant_and_the_patch_backfills(self):
        source = DEPLOYMENT.read_text()
        self.assertIn("def on_update(self):", source)
        self.assertIn("def after_delete(self):", source)
        self.assertIn("refresh_tenant_summary(self.tenant)", source)
        self.assertIn("pearnly_control.patches.v0_0_5_tenant_list_summary", (ROOT / "patches.txt").read_text())
        self.assertTrue((ROOT / "patches/v0_0_5_tenant_list_summary.py").is_file())


if __name__ == "__main__":
    unittest.main()
