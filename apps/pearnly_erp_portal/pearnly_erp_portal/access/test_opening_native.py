"""Opening write boundaries in the isolated ERP lab; no external identity calls.

Run with bench run-tests --module pearnly_erp_portal.access.test_opening_native.
Only synthetic fixtures are created; pre-existing business records are never changed.
"""
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe import client

from pearnly_erp_portal.access import menu
from pearnly_erp_portal.access.setup import sync_roles
from pearnly_erp_portal.inventory import access, openings, stock
from pearnly_erp_portal.masters import company
from pearnly_erp_portal.products import service as products


DOCTYPE = "ERP Stock Opening"


class OpeningNativeBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert frappe.conf.pearnly_erp_pos_lab and frappe.conf.db_host == "db"
        from pearnly_core.provisioning import ensure_feature_roles
        from pearnly_control.features import codes_of, roles_payload

        frappe.set_user("Administrator")
        cls.features = codes_of("erp")
        frappe.conf.pearnly_features = cls.features
        ensure_feature_roles(json.dumps(roles_payload("erp", cls.features)), admin_user="Administrator")
        sync_roles()
        cls.prefix = "openingnative" + uuid4().hex[:8]
        cls.a = company.create_company(company_name=cls.prefix + " A", pos_enabled=False)
        cls.b = company.create_company(company_name=cls.prefix + " B", pos_enabled=False)
        company.set_active_company(cls.a["name"])
        cls.product = products.create_product(product_name=cls.prefix, unit="pcs",
                                              default_cost=17, unit_price=29)["name"]
        cls.reader = cls.prefix + "@example.test"
        frappe.get_doc({"doctype": "User", "email": cls.reader, "first_name": cls.prefix,
            "send_welcome_email": 0, "roles": [{"role": "Desk User"},
                                              {"role": menu.role("stock-card")}]}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": cls.reader, "allow": "ERP Company",
                        "for_value": cls.a["name"], "apply_to_all_doctypes": 1}).insert()
        cls.names = {}
        for key, target in (("a", cls.a), ("b", cls.b)):
            company.set_active_company(target["name"])
            cls.names[key] = openings.upsert_opening(product=cls.product, warehouse=target["warehouse"],
                as_of_date="2026-09-24", qty=3, unit_cost=17)["name"]
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.conf.pearnly_features = self.features
        company.set_active_company(self.a["name"])

    def tearDown(self):
        frappe.db.rollback()
        frappe.conf.pearnly_features = self.features
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def denied(self, fn, *args, **kwargs):
        # on_change and after_rename run after SQL. HTTP rolls the request back
        # when the hook denies it; direct Python tests must do that explicitly.
        savepoint = "opening_guard_" + uuid4().hex
        frappe.db.savepoint(savepoint)
        try:
            with self.assertRaises(frappe.PermissionError):
                fn(*args, **kwargs)
        finally:
            frappe.db.rollback(save_point=savepoint)

    def new_opening(self):
        return frappe.get_doc({"doctype": DOCTYPE, "product": self.product,
            "warehouse": self.a["warehouse"], "location": "native-attempt",
            "as_of_date": "2026-09-24", "qty": 99, "unit_cost": 17, "posted_qty": 99})

    def assert_original(self):
        doc = frappe.get_doc(DOCTYPE, self.names["a"])
        self.assertEqual((doc.qty, doc.posted_qty, doc.unit_cost), (3, 3, 17))
        self.assertEqual(stock.current_balance(self.product, self.a["warehouse"]).qty, 3)
        self.assertEqual(frappe.db.count("ERP Stock Movement", {"product": self.product,
                          "warehouse": self.a["warehouse"]}), 1)

    def test_reader_keeps_company_scoped_native_reads(self):
        frappe.set_user(self.reader)
        company.set_active_company(self.a["name"])
        self.assertEqual(client.get(DOCTYPE, self.names["a"])["qty"], 3)
        self.denied(client.get, DOCTYPE, self.names["b"])
        rows = client.get_list(DOCTYPE, filters={"product": self.product}, fields=["name", "unit_cost"])
        self.assertEqual({row.name for row in rows}, {self.names["a"]})
        self.assertEqual(rows[0].unit_cost, 17)

    def test_native_crud_cannot_bypass_the_ledger_service(self):
        for user in (self.reader, "Administrator"):
            frappe.set_user(user)
            company.set_active_company(self.a["name"])
            self.denied(client.insert, self.new_opening().as_dict())
            tampered = frappe.get_doc(DOCTYPE, self.names["a"]).as_dict()
            tampered.update(qty=99, posted_qty=99, unit_cost=99, as_of_date="2026-09-25")
            tampered["flags"] = {"ignore_permissions": True, "ignore_validate": True,
                                 "stock_opening_service_write": True}
            self.denied(client.save, tampered)
            self.denied(client.set_value, DOCTYPE, self.names["a"], "qty", 99)
            self.denied(client.delete, DOCTYPE, self.names["a"])
            self.assert_original()

    def test_permission_and_validation_flags_cannot_forge_service_capability(self):
        doc = frappe.get_doc(DOCTYPE, self.names["a"])
        doc.qty = doc.posted_qty = 99
        doc.flags.ignore_validate = True
        doc.flags.stock_opening_service_write = True
        self.denied(doc.save, ignore_permissions=True)
        created = self.new_opening()
        created.flags.ignore_validate = True
        created.flags.stock_opening_service_write = True
        self.denied(created.insert, ignore_permissions=True)
        # A rejected save still updates this Python object's modified value.
        # Reload for each attack so the permission guard, not a stale timestamp, is exercised.
        doc = frappe.get_doc(DOCTYPE, self.names["a"])
        doc.qty = doc.posted_qty = 99
        doc.flags.ignore_validate = True
        doc.flags.stock_opening_service_write = True
        previous = getattr(frappe.local, "stock_opening_service_write", None)
        try:
            frappe.local.stock_opening_service_write = (doc, True)
            self.denied(doc.save, ignore_permissions=True)
        finally:
            frappe.local.stock_opening_service_write = previous
        self.denied(frappe.delete_doc, DOCTYPE, self.names["a"], ignore_permissions=True, force=True)
        self.denied(frappe.get_doc(DOCTYPE, self.names["a"]).db_set, "qty", 99)
        self.assert_original()

    def test_document_rename_cannot_skip_validation(self):
        from frappe.handler import run_doc_method
        from werkzeug.wrappers import Request

        frappe.set_user(self.reader)
        company.set_active_company(self.a["name"])
        renamed = self.prefix + "-renamed"
        missing = object()
        previous_request = getattr(frappe.local, "request", missing)
        try:
            frappe.local.request = Request.from_values(method="POST")
            self.denied(run_doc_method, "rename", dt=DOCTYPE, dn=self.names["a"],
                        args={"name": renamed, "validate_rename": False})
        finally:
            if previous_request is missing:
                del frappe.local.request
            else:
                frappe.local.request = previous_request
        self.assertTrue(frappe.db.exists(DOCTYPE, self.names["a"]))
        self.assertFalse(frappe.db.exists(DOCTYPE, renamed))
        self.assert_original()

    def test_service_replay_does_not_duplicate_stock_or_leave_write_authority(self):
        result = openings.upsert_opening(product=self.product, warehouse=self.a["warehouse"],
                                        as_of_date="2026-09-24", qty=3, unit_cost=17)
        self.assertEqual(result["name"], self.names["a"])
        self.assert_original()
        doc = frappe.get_doc(DOCTYPE, self.names["a"])
        doc.qty = 99
        self.denied(doc.save, ignore_permissions=True)

    def test_service_capability_is_object_bound_and_restored_on_failure(self):
        doc, other = self.new_opening(), self.new_opening()
        previous = getattr(frappe.local, "stock_opening_service_write", None)
        ignored = doc.flags.ignore_permissions

        def failed_save(**kwargs):
            self.assertTrue(kwargs["ignore_permissions"])
            access.validate_opening_write(doc)
            with self.assertRaises(frappe.PermissionError):
                access.validate_opening_write(other)
            doc.flags.ignore_permissions = True
            raise RuntimeError("synthetic opening write failure")

        with patch.object(doc, "insert", side_effect=failed_save):
            with self.assertRaisesRegex(RuntimeError, "synthetic opening write failure"):
                access.write_opening(doc)
        self.assertIs(getattr(frappe.local, "stock_opening_service_write", None), previous)
        self.assertEqual(doc.flags.ignore_permissions, ignored)
        with self.assertRaises(frappe.PermissionError):
            access.validate_opening_write(doc)
