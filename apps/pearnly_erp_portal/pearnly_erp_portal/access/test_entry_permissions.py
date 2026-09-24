"""Real-Site regression, run only in the isolated synthetic ERP lab."""
import json
import unittest
from decimal import Decimal
from uuid import uuid4

import frappe

from pearnly_erp_portal.access import guard, menu
from pearnly_erp_portal.access.setup import sync_roles, migrate_members
from pearnly_erp_portal.inventory import count, documents, stock, api as stock_api
from pearnly_erp_portal.masters import company, selection, customers
from pearnly_erp_portal.products import service as products
from pearnly_erp_portal.purchasing import entry, recognition, suppliers
from pearnly_erp_portal.sales import entry as sales
from pearnly_erp_portal.team import service as team


class EntryPermissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert frappe.conf.pearnly_erp_pos_lab and frappe.conf.db_host == "db"
        frappe.set_user("Administrator")
        from pearnly_core.provisioning import ensure_feature_roles
        from pearnly_control.features import codes_of, roles_payload

        ensure_feature_roles(json.dumps(roles_payload("erp", codes_of("erp"))), admin_user="Administrator")
        frappe.conf.pearnly_features = codes_of("erp")
        sync_roles()
        cls.prefix = "entry" + uuid4().hex[:8]
        cls.a = company.create_company(company_name=cls.prefix + " A", pos_enabled=True)
        cls.b = company.create_company(company_name=cls.prefix + " B", pos_enabled=True)
        company.set_active_company(cls.a["name"])
        cls.product = products.create_product(product_name=cls.prefix + " item", unit="pcs",
                                              barcode=cls.prefix, default_cost=10, unit_price=20)["name"]
        cls.supplier = suppliers.create_supplier(supplier_name=cls.prefix + " supplier",
                                                tax_id=cls.prefix, branch_type="branch", branch_no="0001")["name"]
        cls.customer = customers.create_customer(customer_name=cls.prefix + " customer", party_type="company")["name"]
        cls.users = {}
        for key in menu.CATALOG:
            existing = frappe.get_all("User", filters={"name": ["like", "entry%" + key + "@example.test"]},
                                      pluck="name", order_by="creation desc", limit_page_length=1)
            user = existing[0] if existing else cls.prefix + key + "@example.test"
            if existing:
                team.set_member_roles(user, roles=[menu.role(key)], companies=[cls.a["name"]])
            else:
                team.add_member(username=cls.prefix + key.replace("-", ""), email=user, password="lab-only",
                                roles=[menu.role(key)], companies=[cls.a["name"]])
            cls.users[key] = user
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        company.set_active_company(self.a["name"])

    def tearDown(self):
        frappe.db.rollback()
        frappe.clear_cache()
        frappe.set_user("Administrator")

    def as_role(self, key):
        frappe.set_user(self.users[key])
        company.set_active_company(self.a["name"])

    def denied(self, fn, *args, **kwargs):
        with self.assertRaises((frappe.PermissionError, frappe.AuthenticationError)):
            fn(*args, **kwargs)

    def test_purchase_only_lookup_post_and_supplier_management_isolation(self):
        self.as_role("purchase")
        found = selection.api_choices("supplier", "purchase", self.prefix, scan=True)
        self.assertEqual(found[0].name, self.supplier)
        found = selection.api_choices("product", "purchase", self.prefix, scan=True)
        self.assertEqual(found[0].name, self.product)
        self.assertNotIn("default_cost", found[0])
        self.denied(suppliers.list_suppliers)
        self.denied(suppliers.update_supplier, self.supplier, supplier_name="no")
        self.denied(products.update_product, self.product, product_name="no")
        before_qty = stock.current_balance(self.product, self.a["warehouse"]).qty
        result = entry.post({"doc_date": "2026-09-24", "doc_no": self.prefix, "currency": "THB", "fx_rate": 1,
            "supplier_name": self.prefix + " supplier", "supplier_tax_id": self.prefix,
            "supplier_branch_type": "branch", "supplier_branch_no": "0001", "has_vat": 0,
            "lines": [{"description": self.prefix + " item", "qty": 2, "unit": "pcs", "unit_price": 10}]},
            request_id=uuid4().hex)
        doc = frappe.get_doc("Purchase Document", result["name"])
        self.assertEqual(doc.supplier, self.supplier)
        self.assertEqual(doc.company, self.a["name"])
        self.assertEqual(doc.lines[0].product, self.product)
        self.assertTrue(doc.stock_posted_at)
        self.assertEqual(stock.current_balance(self.product, self.a["warehouse"]).qty, before_qty + Decimal(2))

    def test_sales_only_customer_and_item_lookup(self):
        self.as_role("sales")
        found = selection.api_choices("customer", "sales", self.prefix)
        self.assertEqual(found[0].name, self.customer)
        self.denied(customers.create_customer, customer_name="no")
        result = sales.post({"doc_kind": "receipt", "doc_date": "2026-09-24", "buyer_name": self.prefix + " customer",
                             "has_vat": 0, "vat_rate": 7, "is_paid": 1,
                             "lines": [{"description": self.prefix + " item", "qty": 1, "unit_price": 20}]},
                            request_id=uuid4().hex)
        doc = frappe.get_doc("ERP Sales Document", result["name"])
        self.assertEqual(doc.customer, self.customer)
        self.assertEqual(doc.lines[0].product, self.product)
        self.assertEqual(doc.company, self.a["name"])

    def test_leaf_menu_and_user_menu_visibility(self):
        from pearnly_core.desk.navigation import visible_pages

        for key in self.users:
            self.as_role(key)
            pages = visible_pages("erp")
            if key != "profile":
                self.assertIn("pearnly-" + key, pages)
            if key != "team":
                self.assertNotIn("pearnly-team", pages)
            if key != "company":
                self.assertNotIn("pearnly-company", pages)
            if key != "warehouses":
                self.assertNotIn("pearnly-warehouses", pages)
            for other in self.users:
                if other != key:
                    self.assertNotIn("pearnly-" + other, pages)

    def test_home_profile_team_are_independent(self):
        from pearnly_core import billing_www
        from pearnly_core.workspace import landing_url
        from pearnly_core.desk.navigation import _can_open_page
        from pearnly_core.desk.permissions import user_permission
        self.as_role("purchase")
        self.assertEqual(landing_url(), "/desk/pearnly-purchase")
        self.denied(billing_www.home)
        self.assertFalse(user_permission(frappe.get_doc("User", self.users["purchase"])))
        self.assertFalse(frappe.has_permission("User", "read", doc=self.users["purchase"]))
        self.denied(team.list_members)
        self.as_role("profile")
        self.assertIsNone(user_permission(frappe.get_doc("User", self.users["profile"])))
        self.assertTrue(landing_url().startswith("/desk/user/"))
        self.denied(products.list_products)
        self.assertFalse(_can_open_page("erp", "pearnly-home"))
        self.as_role("team")
        self.assertTrue(team.list_members())
        self.denied(billing_www.home)
        self.assertFalse(user_permission(frappe.get_doc("User", self.users["team"])))
        keys = {row["name"] for row in team.role_catalog()}
        self.assertEqual(keys, set(menu.roles()))

    def test_profile_revocation_and_other_product_default(self):
        from pearnly_core.desk.permissions import user_permission
        user = self.users["profile"]
        team.set_member_roles(user, roles=[menu.role("profile")], companies=[self.a["name"]])
        frappe.set_user(user)
        self.assertTrue(frappe.has_permission("User", "read", doc=user))
        frappe.set_user(self.users["team"])
        team.set_member_roles(user, roles=[menu.role("purchase")], companies=[self.a["name"]])
        frappe.set_user(user)
        self.assertFalse(frappe.has_permission("User", "read", doc=user))
        product = frappe.conf.pearnly_product
        try:
            frappe.conf.pearnly_product = "dms"
            self.assertIsNone(user_permission(frappe.get_doc("User", user)))
            self.assertFalse(user_permission(frappe.get_doc("User", self.users["team"])))
        finally:
            frappe.conf.pearnly_product = product

    def test_receipts_and_shifts_can_load_their_terminal_filters(self):
        from pearnly_erp_portal.cashier import terminals, sales as receipts, shifts
        self.as_role("pos-receipts")
        terminals.list_terminals()
        receipts.list_sales()
        self.denied(shifts.list_shifts)
        self.as_role("pos-shifts")
        terminals.list_terminals()
        shifts.list_shifts()
        self.denied(receipts.list_sales)
        self.denied(receipts.list_sales, current_only=True)

    def test_direction_write_and_native_document_isolation(self):
        self.as_role("stock-in")
        kwargs = {"posting_date": "2026-09-24", "warehouse": self.a["warehouse"],
                  "lines": [{"product": self.product, "qty": 1}], "submit": True}
        self.denied(documents.create_entry, direction="out", **kwargs)
        self.denied(stock_api.api_issue, lines=kwargs["lines"], warehouse=self.a["warehouse"])
        incoming = documents.create_entry(direction="in", **kwargs)
        self.as_role("stock-out")
        self.denied(documents.get_entry, incoming["name"])
        self.assertFalse(frappe.has_permission("ERP Stock Entry", "read", doc=incoming["name"]))

    def test_stock_count_template_includes_zero_stock(self):
        self.as_role("stock-count")
        self.denied(stock_api.api_count)
        self.denied(stock_api.api_adjust)
        rows = count.template_rows(warehouse=self.a["warehouse"])
        self.assertTrue(any(row["product_name"] == self.prefix + " item" for row in rows))
        self.assertTrue(all(row["counted_qty"] is None for row in rows))
        frappe.set_user("Administrator")
        untouched = products.create_product(product_name=self.prefix + " zero", unit="pcs")["name"]
        self.as_role("stock-count")
        rows = count.template_rows(warehouse=self.a["warehouse"])
        self.assertEqual(next(row for row in rows if row["product_name"] == self.prefix + " zero")["book_qty"], 0)
        task = count.create_count(title=self.prefix + " count", warehouse=self.a["warehouse"], snapshot=False)
        count.add_entry(count=task["name"], product=untouched, qty=3)
        result = count.post_count(task["name"])
        self.assertEqual(result["status"], "Posted")
        self.assertEqual(stock.current_balance(untouched, self.a["warehouse"]).qty, Decimal(3))

    def test_atomic_team_edit_and_company_switch(self):
        user = self.users["purchase"]
        before = set(frappe.get_roles(user))
        with self.assertRaises(frappe.PermissionError):
            team.set_member_roles(user, roles=[menu.role("sales")], companies=["missing-company"])
        self.assertEqual(set(frappe.get_roles(user)), before)
        self.as_role("purchase")
        self.assertEqual(company.allowed_companies(), [self.a["name"]])
        self.denied(company.set_active_company, self.b["name"])
        self.denied(company.get_profile, self.b["name"])
        frappe.set_user("Administrator")
        team.set_member_roles(user, roles=[menu.role("purchase")], companies=[self.a["name"], self.b["name"]])
        self.as_role("purchase")
        company.set_active_company(self.b["name"])
        self.assertEqual(company.active_company(), self.b["name"])
        self.assertEqual(sum(row["is_current"] for row in company.company_options()), 1)

    def test_ocr_content_cannot_inject_ids_or_posting_options(self):
        clean = recognition.clean_result({"supplier": "1234", "supplier_name": "M C Winner",
            "company": self.b["name"], "is_paid": 1, "lines": [{"product": "1234", "warehouse": self.b["warehouse"],
                "description": "Printed item", "qty": 1, "unit_price": 10}]})
        self.assertNotIn("supplier", clean)
        self.assertNotIn("company", clean)
        self.assertNotIn("is_paid", clean)
        self.assertNotIn("product", clean["lines"][0])
        self.assertNotIn("warehouse", clean["lines"][0])
        self.assertEqual(clean["supplier_name"], "M C Winner")
        with self.assertRaises(frappe.ValidationError):
            entry._supplier({"supplier": "1234", "supplier_name": "M C Winner"})

    def test_ambiguous_party_requires_selection(self):
        title = self.prefix + " duplicate"
        for tax in (self.prefix + "11", self.prefix + "22"):
            suppliers.create_supplier(supplier_name=title, tax_id=tax)
        with self.assertRaises(frappe.ValidationError):
            selection.match_party("supplier", title)
        self.assertIsNotNone(selection.match_party("supplier", title, tax_id=self.prefix + "11"))

    def test_legacy_migration_preserves_grants_and_is_idempotent(self):
        existing = frappe.get_all("User", filters={"name": ["like", "entry%legacy@example.test"]},
                                  pluck="name", order_by="creation desc", limit_page_length=1)
        name = existing[0] if existing else self.prefix + "legacy@example.test"
        user = frappe.get_doc("User", name) if existing else frappe.get_doc({"doctype": "User",
            "email": name, "first_name": "legacy", "send_welcome_email": 0})
        user.set("roles", [{"role": "Desk User"}, {"role": "ERP Purchase"}])
        user.save(ignore_permissions=True)
        migrate_members()
        self.assertNotIn("ERP Purchase", frappe.get_roles(name))
        self.assertIn(menu.role("purchase"), frappe.get_roles(name))
        self.assertIn(menu.role("suppliers"), frappe.get_roles(name))
        self.assertNotIn(menu.role("sales"), frappe.get_roles(name))
        before = set(frappe.get_roles(name))
        migrate_members()
        self.assertEqual(set(frappe.get_roles(name)), before)

    def test_guest_and_wrong_context_rejected(self):
        self.as_role("purchase")
        self.denied(selection.api_choices, "customer", "purchase")
        self.denied(selection.api_choices, "product", "sales")
        frappe.set_user("Guest")
        self.denied(selection.api_choices, "product", "purchase")
