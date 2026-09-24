"""Writes synthetic data only in the isolated local ERP lab; never calls external identity.

Run with bench run-tests --module pearnly_erp_portal.access.test_native_boundaries.
Fixtures are retained for diagnosis; this module never deletes pre-existing business records.
"""
import json
import unittest
from uuid import uuid4

import frappe
from frappe import client

from pearnly_erp_portal.access import guard, menu
from pearnly_erp_portal.access.setup import sync_roles
from pearnly_erp_portal.cashier import sales, shifts
from pearnly_erp_portal.inventory import api, count, openings, stock
from pearnly_erp_portal.masters import company, selection
from pearnly_erp_portal.products import service as products
from pearnly_erp_portal.team import service as team


class NativeBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert frappe.conf.pearnly_erp_pos_lab and frappe.conf.db_host == "db"
        assert team._local_password_allowed(), "Synthetic fixtures must not contact identity services"
        from pearnly_core.provisioning import ensure_feature_roles
        from pearnly_control.features import codes_of, roles_payload

        frappe.set_user("Administrator")
        cls.features = codes_of("erp")
        frappe.conf.pearnly_features = cls.features
        ensure_feature_roles(json.dumps(roles_payload("erp", cls.features)), admin_user="Administrator")
        sync_roles()
        cls.prefix = "boundary" + uuid4().hex[:8]
        cls.a = company.create_company(company_name=cls.prefix + " A", pos_enabled=True)
        cls.b = company.create_company(company_name=cls.prefix + " B", pos_enabled=True)
        company.set_active_company(cls.a["name"])
        cls.product = products.create_product(product_name=cls.prefix, unit="pcs",
                                              default_cost=17, unit_price=29)["name"]
        cls.users = {}
        for key in ("purchase", "inventory", "stock-card", "stock-count", "cashier", "pos-shifts", "team", "products"):
            name = cls.prefix + key + "@example.test"
            team.add_member(username=cls.prefix + key.replace("-", ""), email=name, password="lab-only",
                            roles=[menu.role(key)], companies=[cls.a["name"]])
            cls.users[key] = name
        cls.outside = cls.prefix + "outside@example.test"
        team.add_member(username=cls.prefix + "outside", email=cls.outside, password="lab-only",
                        roles=[menu.role("purchase")], companies=[cls.b["name"]])
        cls.mixed = cls.prefix + "mixed@example.test"
        team.add_member(username=cls.prefix + "mixed", email=cls.mixed, password="lab-only",
                        roles=[menu.role("purchase"), menu.role("inventory"), menu.role("stock-card")],
                        companies=[cls.a["name"]])
        cls.owner = cls.prefix + "owner@example.test"
        frappe.get_doc({"doctype": "User", "email": cls.owner, "first_name": cls.prefix + "owner",
            "send_welcome_email": 0, "roles": [{"role": "Desk User"}, {"role": "ERP Admin"}]}).insert()
        cls.ledger, cls.counts, cls.receipts, cls.shift_docs = {}, {}, {}, {}
        for key, target in (("a", cls.a), ("b", cls.b)):
            company.set_active_company(target["name"])
            moved = stock.apply_movement(product=cls.product, warehouse=target["warehouse"],
                movement_type="stock_in", qty=3, unit_cost=17, internal=True, client_uuid=uuid4().hex)
            cls.ledger[key] = {"movement": moved["movement"], "balance": frappe.db.get_value(
                "ERP Stock Balance", {"product": cls.product, "warehouse": target["warehouse"]}, "name")}
            cls.counts[key] = count.create_count(title=cls.prefix + key, warehouse=target["warehouse"])["name"]
            terminal = frappe.db.get_value("ERP POS Terminal", {"company": target["name"]}, "name")
            shift = frappe.get_doc({"doctype": "ERP Shift", "terminal": terminal,
                "cashier": cls.users["cashier"], "status": "open", "opened_at": "2026-09-24 09:00:00"}).insert()
            cls.shift_docs[key] = shift.name
            sale = frappe.get_doc({"doctype": "ERP Sale", "cashier": cls.users["cashier"],
                "terminal": terminal, "shift": shift.name, "warehouse": target["warehouse"],
                "sale_type": "sale", "status": "completed", "sold_at": "2026-09-24 09:01:00",
                "lines": [{"product": cls.product, "description": cls.prefix, "qty": 1, "unit_price": 29,
                           "line_total": 29, "warehouse": target["warehouse"]}]}).insert()
            cls.receipts[key] = sale.name
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

    def as_user(self, key):
        frappe.set_user(self.users.get(key, key))
        company.set_active_company(self.a["name"])

    def denied(self, fn, *args, **kwargs):
        with self.assertRaises(frappe.PermissionError):
            fn(*args, **kwargs)

    def test_native_inventory_permission_and_cost_projection(self):
        self.as_user("purchase")
        for doctype, field in (("ERP Stock Balance", "balance"), ("ERP Stock Movement", "movement")):
            self.denied(client.get_list, doctype, fields=["*"])
            self.denied(client.get, doctype, self.ledger["a"][field])
            self.assertFalse(frappe.has_permission(doctype, "read", doc=self.ledger["b"][field]))
        native = client.get("ERP Product", self.product)
        self.assertIsNone(native.get("default_cost"))
        self.as_user("inventory")
        rows = client.get_list("ERP Stock Balance", fields=["name", "unit_cost", "warehouse"])
        self.assertIn(self.ledger["a"]["balance"], {row.name for row in rows})
        self.assertNotIn(self.ledger["b"]["balance"], {row.name for row in rows})
        self.denied(client.get, "ERP Stock Balance", self.ledger["b"]["balance"])
        self.assertEqual(products.get_product(self.product)["default_cost"], 17)

    def test_disabled_inventory_roles_do_not_leak_costs_through_purchase(self):
        self.as_user(self.mixed)
        frappe.conf.pearnly_features = ["purchase"]
        self.assertFalse(guard.cost_visible())
        self.assertIsNone(client.get("ERP Product", self.product).get("default_cost"))
        self.assertIsNone(products.get_product(self.product)["default_cost"])
        choices = selection.api_choices("product", "purchase", self.prefix)
        self.assertTrue(choices)
        self.assertNotIn("default_cost", choices[0])
        for doctype, field in (("ERP Stock Balance", "balance"), ("ERP Stock Movement", "movement")):
            self.assertEqual(client.get_list(doctype, fields=["name", "unit_cost"]), [])
            self.denied(client.get, doctype, self.ledger["a"][field])
        frappe.conf.pearnly_features = self.features
        self.assertEqual(products.get_product(self.product)["default_cost"], 17)

    def test_count_native_services_exports_and_other_positions_are_scoped(self):
        self.as_user("stock-count")
        self.assertEqual(count.get_count(self.counts["a"])["name"], self.counts["a"])
        self.denied(count.get_count, self.counts["b"])
        self.denied(api.api_stock_count_export, name=self.counts["b"])
        self.denied(count.recount_product, count=self.counts["b"], product=self.product)
        self.denied(client.get, "ERP Stock Count", self.counts["b"])
        self.assertNotIn(self.counts["b"], {row.name for row in client.get_list("ERP Stock Count")})
        elsewhere = count.lookup_count(count=self.counts["a"], product=self.product)["elsewhere"]
        self.assertFalse(any(row["warehouse_name"] == frappe.db.get_value(
            "ERP Warehouse", self.b["warehouse"], "warehouse_name") for row in elsewhere))
        created = count.create_count(title=self.prefix + " allowed", warehouse=self.a["warehouse"])
        self.assertTrue(count.list_items(count=created["name"]))
        self.assertTrue(count.template_rows(warehouse=self.a["warehouse"]))

    def test_openings_and_native_exports_respect_company(self):
        self.as_user("stock-card")
        with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
            openings.list_openings(warehouse=self.b["warehouse"])
        openings.upsert_opening(product=self.product, warehouse=self.a["warehouse"],
                                as_of_date="2026-09-24", qty=1, unit_cost=17)
        self.assertTrue(openings.list_openings(warehouse=self.a["warehouse"]))
        self.as_user("stock-count")
        from frappe.core.doctype.data_export.exporter import DataExporter

        exporter = DataExporter("ERP Stock Count", file_type="CSV", export_without_column_meta=True,
                                filters={"name": ["in", [self.counts["a"], self.counts["b"]]]})
        exporter.build_response()
        self.assertIn(self.counts["a"], {row.name for row in exporter.data})
        self.assertNotIn(self.counts["b"], {row.name for row in exporter.data})

    def test_pos_native_parent_child_and_old_ids_recheck_company(self):
        self.as_user("cashier")
        self.assertEqual(sales.get_sale(self.receipts["a"])["name"], self.receipts["a"])
        self.denied(sales.get_sale, self.receipts["b"])
        self.denied(shifts.summary, self.shift_docs["b"])
        self.denied(client.get, "ERP Sale", self.receipts["b"])
        lines = client.get_list("ERP Sale Line", parent="ERP Sale", fields=["parent", "product"])
        self.assertTrue(any(row.parent == self.receipts["a"] for row in lines))
        self.assertFalse(any(row.parent == self.receipts["b"] for row in lines))
        self.assertEqual(shifts.current_shift()["shift"]["name"], self.shift_docs["a"])
        self.as_user("pos-shifts")
        terminal = frappe.db.get_value("ERP POS Terminal", {"company": self.b["name"]}, "name")
        with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
            shifts.list_shifts(terminal=terminal)

    def test_team_delegate_cannot_expand_or_manage_outside_scope(self):
        self.as_user("team")
        manager = self.users["team"]
        self.denied(team.set_member_companies, manager, companies=[])
        self.denied(team.set_member_companies, manager, companies=[self.b["name"]])
        self.denied(team.set_member_active, self.outside, 0)
        self.denied(team.set_member_password, "Guest", "not-used")
        self.assertNotIn(self.outside, {row["email"] for row in team.list_members()})
        team.set_member_companies(self.users["purchase"], companies=[self.a["name"]])
        frappe.set_user("Administrator")
        team.set_member_companies(self.outside, companies=[self.a["name"], self.b["name"]])
        self.assertEqual(set(team.member_companies(self.outside)), {self.a["name"], self.b["name"]})
        team.set_member_companies(self.outside, companies=[self.b["name"]])

    def test_disabled_members_stay_manageable_and_cannot_be_newly_granted(self):
        self.as_user("team")
        frappe.conf.pearnly_features = ["purchase"]
        employee = self.users["inventory"]
        self.assertIn(employee, {row["email"] for row in team.list_members()})
        self.assertNotIn(menu.role("inventory"), team.assignable_roles())
        team.set_member_roles(employee, roles=[], companies=[self.a["name"]])
        self.assertIn(menu.role("inventory"), frappe.get_roles(employee))
        self.denied(team.set_member_roles, self.users["purchase"], roles=[menu.role("inventory")])

    def test_tenant_owner_stays_visible_and_can_manage_all_companies(self):
        self.as_user(self.owner)
        row = next(row for row in team.list_members() if row["email"] == self.owner)
        self.assertTrue(row["is_admin"])
        self.denied(team.set_member_active, self.owner, 0)
        self.assertIn(self.outside, {row["email"] for row in team.list_members()})
        team.set_member_companies(self.outside, companies=[self.a["name"], self.b["name"]])
        self.assertEqual(set(team.member_companies(self.outside)), {self.a["name"], self.b["name"]})
        team.set_member_companies(self.outside, companies=[self.b["name"]])

    def test_role_sync_revokes_legacy_cost_grants_and_sharing_is_closed(self):
        from pearnly_core.provisioning import _grant
        from frappe.share import add

        _grant("ERP Stock Balance", menu.role("purchase"), {"read": 1})
        _grant("ERP Product", "ERP Stock", {"read": 1}, permlevel=1)
        sync_roles()
        sync_roles()
        self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": "ERP Stock Balance",
            "role": menu.role("purchase"), "permlevel": 0}))
        self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": "ERP Product",
            "role": "ERP Stock", "permlevel": 1}))
        self.denied(add, "ERP Stock Balance", self.ledger["b"]["balance"], self.users["purchase"], read=1)

    def test_product_management_keeps_its_non_cost_operations(self):
        self.as_user("products")
        products.update_product(self.product, min_stock=4)
        self.assertEqual(products.get_product(self.product)["min_stock"], 4)
        self.assertIsNone(products.get_product(self.product)["default_cost"])
