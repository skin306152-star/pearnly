"""Real-Site company/warehouse transactions; isolated synthetic ERP lab only."""
import json
import unittest
from decimal import Decimal
from uuid import uuid4

import frappe

from pearnly_erp_portal.access import menu
from pearnly_erp_portal.access.setup import sync_roles
from pearnly_erp_portal.inventory import documents, stock
from pearnly_erp_portal.masters import company
from pearnly_erp_portal.products import service as products
from pearnly_erp_portal.purchasing import entry as purchase, service as legacy_purchase, suppliers
from pearnly_erp_portal.sales import entry as sales
from pearnly_erp_portal.team import service as team


class EntryCompanyIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert frappe.conf.pearnly_erp_pos_lab and frappe.conf.db_host == "db"
        frappe.set_user("Administrator")
        from pearnly_core.provisioning import ensure_feature_roles
        from pearnly_control.features import codes_of, roles_payload

        ensure_feature_roles(json.dumps(roles_payload("erp", codes_of("erp"))), admin_user="Administrator")
        frappe.conf.pearnly_features = codes_of("erp")
        sync_roles()
        cls.prefix = "companycheck" + uuid4().hex[:10]
        cls.a = company.create_company(company_name=cls.prefix + " A", pos_enabled=True)
        cls.b = company.create_company(company_name=cls.prefix + " B", pos_enabled=True)
        cls.product = products.create_product(product_name=cls.prefix + " goods", unit="pcs",
                                              default_cost=10, unit_price=20)["name"]
        cls.supplier = suppliers.create_supplier(supplier_name=cls.prefix + " supplier")["name"]
        cls.user = cls.prefix + "@example.test"
        team.add_member(username=cls.prefix, email=cls.user, password="lab-only",
                        roles=[menu.role(key) for key in ("purchase", "sales", "stock-in", "stock-out")],
                        companies=[cls.a["name"], cls.b["name"]])
        cls.restricted = cls.prefix + "restricted@example.test"
        team.add_member(username=cls.prefix + "restricted", email=cls.restricted, password="lab-only",
                        roles=[menu.role("purchase"), menu.role("sales")], companies=[cls.a["name"]])
        frappe.db.commit()

    def setUp(self):
        frappe.set_user(self.user)
        company.set_active_company(self.a["name"])
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        frappe.clear_cache()
        frappe.set_user("Administrator")

    def payload(self, kind, **changes):
        data = {"company": self.b["name"], "doc_date": "2026-09-24", "has_vat": 0,
                "is_paid": 1, "payment_method": "cash",
                "lines": [{"product": self.product, "description": self.prefix + " goods",
                           "qty": 2, "unit": "pcs", "unit_price": 10}]}
        if kind == "purchase":
            data.update(supplier=self.supplier, currency="THB", fx_rate=1,
                        auto_create_product=0, doc_no=uuid4().hex)
        else:
            data.update(doc_kind="receipt", buyer_name=self.prefix + " buyer", vat_rate=0)
        data.update(changes)
        return data

    def balances(self):
        return tuple(stock.current_balance(self.product, place["warehouse"]).qty
                     for place in (self.a, self.b))

    def legacy_sale_draft(self, source="manual"):
        # Fixture-only native insertion models a draft saved before direct creation
        # was restricted. Frappe stamps a new document's owner from the session user.
        frappe.set_user(self.user)
        doc = frappe.get_doc({"doctype": "ERP Sales Document", "company": self.b["name"],
            "source": source, "note": self.prefix + " saved draft",
            "status": "draft", "doc_kind": "receipt", "doc_date": "2026-09-24",
            "buyer_name": self.prefix + " buyer", "lines": self.payload("sales")["lines"]})
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, "owner"), self.user)
        return doc

    def assert_posted_in_b(self, module, doctype, kind):
        before = self.balances()
        payload, request = self.payload(kind), uuid4().hex
        result = module.post(payload, request_id=request)
        doc = frappe.get_doc(doctype, result["name"])
        self.assertEqual(doc.company, self.b["name"])
        self.assertEqual({row.warehouse for row in doc.lines}, {self.b["warehouse"]})
        self.assertEqual(company.active_company(), self.a["name"])
        self.assertEqual(Decimal(str(doc.paid_amount)), Decimal(20))
        if kind == "sales":
            self.assertEqual(doc.source, "manual")
        delta = Decimal(2) if kind == "purchase" else Decimal(-2)
        self.assertEqual(self.balances(), (before[0], before[1] + delta))
        replay = module.post(payload, request_id=request)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["name"], result["name"])
        self.assertEqual(self.balances(), (before[0], before[1] + delta))
        return result, before

    def test_purchase_explicit_company_warehouse_and_replay(self):
        result, before = self.assert_posted_in_b(purchase, "Purchase Document", "purchase")
        purchase.cancel(result["name"])
        self.assertEqual(self.balances(), before)
        doc = frappe.get_doc("Purchase Document", result["name"])
        self.assertEqual(doc.paid_amount, 0)
        self.assertEqual(sum(Decimal(str(row.amount)) for row in doc.payments), Decimal(0))
        purchase.cancel(result["name"])
        self.assertEqual(self.balances(), before)

    def test_sale_explicit_company_warehouse_and_cancel_from_other_company(self):
        result, before = self.assert_posted_in_b(sales, "ERP Sales Document", "sales")
        sales.cancel(result["name"])
        self.assertEqual(self.balances(), before)

    def test_explicit_warehouse_uses_document_company(self):
        for module, kind, doctype in ((purchase, "purchase", "Purchase Document"),
                                     (sales, "sales", "ERP Sales Document")):
            with self.subTest(kind=kind):
                payload = self.payload(kind)
                payload["lines"][0]["warehouse"] = self.b["warehouse"]
                result = module.post(payload, request_id=uuid4().hex)
                doc = frappe.get_doc(doctype, result["name"])
                self.assertEqual(doc.company, self.b["name"])
                self.assertEqual(doc.lines[0].warehouse, self.b["warehouse"])

    def test_foreign_line_warehouse_and_unassigned_company_rejected(self):
        for module, kind, doctype in ((purchase, "purchase", "Purchase Document"),
                                     (sales, "sales", "ERP Sales Document")):
            with self.subTest(kind=kind):
                before, request = self.balances(), uuid4().hex
                payload = self.payload(kind)
                payload["lines"][0]["warehouse"] = self.a["warehouse"]
                with self.assertRaises(frappe.ValidationError):
                    module.post(payload, request_id=request)
                self.assertFalse(frappe.db.exists(doctype, {"request_id": request}))
                self.assertEqual(self.balances(), before)
                frappe.set_user(self.restricted)
                company.set_active_company(self.a["name"])
                with self.assertRaises(frappe.PermissionError):
                    module.post(self.payload(kind), request_id=uuid4().hex)
                frappe.set_user(self.user)

    def test_purchase_correction_and_failed_correction_keep_original_company(self):
        original, _before = self.assert_posted_in_b(purchase, "Purchase Document", "purchase")
        payload = self.payload("purchase", company=self.a["name"])
        payload["lines"][0]["qty"] = 3
        before, request = self.balances(), uuid4().hex

        def fail(result):
            self.assertEqual(frappe.db.get_value("Purchase Document", original["name"], "status"), "void")
            raise RuntimeError("synthetic correction failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic correction"):
            purchase.post(payload, request_id=request, amended_from=original["name"], finalize=fail)
        self.assertEqual(self.balances(), before)
        self.assertEqual(frappe.db.get_value("Purchase Document", original["name"], "status"), "submitted")
        self.assertEqual(Decimal(str(frappe.db.get_value("Purchase Document", original["name"], "paid_amount"))), Decimal(20))
        result = purchase.post(payload, request_id=request, amended_from=original["name"])
        doc = frappe.get_doc("Purchase Document", result["name"])
        self.assertEqual(doc.company, self.b["name"])
        self.assertEqual(doc.lines[0].warehouse, self.b["warehouse"])
        self.assertEqual(self.balances(), (before[0], before[1] + Decimal(1)))
        self.assertEqual(frappe.db.get_value("Purchase Document", original["name"], "status"), "void")

    def test_drafts_retain_their_company_after_switching(self):
        draft = legacy_purchase.create_purchase(supplier=self.supplier,
            supplier_name=self.prefix + " supplier", doc_date="2026-09-24", company=self.b["name"],
            has_vat=False, lines=self.payload("purchase")["lines"])
        self.assertEqual(legacy_purchase.get_purchase(draft["name"])["lines"][0]["warehouse"], self.b["warehouse"])
        legacy_purchase.update_purchase(draft["name"], lines=self.payload("purchase")["lines"])
        result = purchase.post(self.payload("purchase", company=self.a["name"]),
                               name=draft["name"], request_id=uuid4().hex)
        doc = frappe.get_doc("Purchase Document", result["name"])
        self.assertEqual(doc.company, self.b["name"])
        self.assertEqual(doc.lines[0].warehouse, self.b["warehouse"])
        for source in ("manual", "ocr"):
            with self.subTest(source=source):
                sale_draft = self.legacy_sale_draft(source=source)
                self.assertEqual(company.active_company(), self.a["name"])
                self.assertTrue(frappe.has_permission("ERP Sales Document", "write", doc=sale_draft.name))
                result = sales.post(self.payload("sales", company=self.a["name"]), name=sale_draft.name,
                                    request_id=uuid4().hex)
                saved = frappe.get_doc("ERP Sales Document", result["name"])
                self.assertEqual((saved.company, saved.lines[0].warehouse), (self.b["name"], self.b["warehouse"]))
                self.assertEqual(saved.source, source)
                self.assertEqual(saved.note, self.prefix + " saved draft")
                self.assertEqual(company.active_company(), self.a["name"])

    def test_saved_drafts_recheck_ownership_and_revoked_company(self):
        purchase_draft = legacy_purchase.create_purchase(supplier=self.supplier,
            supplier_name=self.prefix + " supplier", doc_date="2026-09-24", company=self.b["name"],
            has_vat=False, lines=self.payload("purchase")["lines"])
        sale_draft = self.legacy_sale_draft()
        drafts = ((purchase, "purchase", "Purchase Document", purchase_draft["name"]),
                  (sales, "sales", "ERP Sales Document", sale_draft.name))
        before = self.balances()
        try:
            # Both users can access B; document ownership must still reject a peer.
            frappe.set_user("Administrator")
            team.set_member_companies(self.restricted, companies=[self.a["name"], self.b["name"]])
            frappe.set_user(self.restricted)
            company.set_active_company(self.a["name"])
            for module, kind, doctype, name in drafts:
                with self.subTest(kind=kind, reason="owner"):
                    with self.assertRaises(frappe.PermissionError):
                        module.post(self.payload(kind), name=name, request_id=uuid4().hex)
                    self.assertEqual(frappe.db.get_value(doctype, name, "status"), "draft")
            # The original owner also loses access when B is removed from their grant.
            frappe.set_user("Administrator")
            team.set_member_companies(self.user, companies=[self.a["name"]])
            frappe.set_user(self.user)
            company.set_active_company(self.a["name"])
            for module, kind, doctype, name in drafts:
                with self.subTest(kind=kind, reason="company revoked"):
                    with self.assertRaises(frappe.PermissionError):
                        module.post(self.payload(kind), name=name, request_id=uuid4().hex)
                    self.assertEqual(frappe.db.get_value(doctype, name, "status"), "draft")
            self.assertEqual(self.balances(), before)
        finally:
            frappe.set_user("Administrator")
            team.set_member_companies(self.user, companies=[self.a["name"], self.b["name"]])
            team.set_member_companies(self.restricted, companies=[self.a["name"]])

    def test_legacy_submit_and_stock_entry_update_keep_saved_company(self):
        before = self.balances()
        draft = legacy_purchase.create_purchase(supplier=self.supplier,
            supplier_name=self.prefix + " supplier", doc_date="2026-09-24", company=self.b["name"],
            has_vat=False, lines=self.payload("purchase")["lines"])
        legacy_purchase.set_status(draft["name"], "submitted", receive_stock=True)
        self.assertEqual(self.balances(), (before[0], before[1] + Decimal(2)))
        legacy_purchase.set_status(draft["name"], "void")
        self.assertEqual(self.balances(), before)
        company.set_active_company(self.b["name"])
        incoming = documents.create_entry(direction="in", posting_date="2026-09-24",
            warehouse=self.b["warehouse"], lines=[{"product": self.product, "qty": 1}])
        company.set_active_company(self.a["name"])
        documents.update_entry(incoming["name"], lines=[{"product": self.product, "qty": 3}], submit=True)
        self.assertEqual(documents.get_entry(incoming["name"])["lines"][0]["warehouse"], self.b["warehouse"])
        self.assertEqual(self.balances(), (before[0], before[1] + Decimal(3)))
        documents.void_entry(incoming["name"])
        self.assertEqual(self.balances(), before)

    def test_late_failure_rolls_back_document_stock_payment_and_attachment(self):
        def fail_after_binding(result):
            self.assertTrue(result["name"])
            raise RuntimeError("synthetic finalization failure")

        for module, kind, doctype in ((purchase, "purchase", "Purchase Document"),
                                     (sales, "sales", "ERP Sales Document")):
            with self.subTest(kind=kind):
                evidence = frappe.get_doc({"doctype": "File", "file_name": uuid4().hex + ".txt",
                    "content": "synthetic company integrity " + uuid4().hex, "is_private": 1})
                evidence.insert(ignore_permissions=True)
                frappe.db.commit()
                payload = self.payload(kind, attachments=[{"file": evidence.name, "kind": "bill"}])
                request, before = uuid4().hex, self.balances()
                with self.assertRaisesRegex(RuntimeError, "synthetic finalization"):
                    module.post(payload, request_id=request, finalize=fail_after_binding)
                self.assertFalse(frappe.db.exists(doctype, {"request_id": request}))
                self.assertFalse(frappe.db.get_value("File", evidence.name, "attached_to_name"))
                self.assertEqual(self.balances(), before)
                result = module.post(payload, request_id=request)
                self.assertEqual(frappe.db.get_value("File", evidence.name, "attached_to_name"), result["name"])
                self.assertTrue(module.post(payload, request_id=request)["duplicate"])

    def test_reversal_preserves_inactive_saved_warehouse(self):
        result, before = self.assert_posted_in_b(purchase, "Purchase Document", "purchase")
        frappe.set_user("Administrator")
        frappe.db.set_value("ERP Warehouse", self.b["warehouse"], "is_active", 0)
        try:
            frappe.set_user(self.user)
            purchase.cancel(result["name"])
            self.assertEqual(self.balances(), before)
        finally:
            frappe.set_user("Administrator")
            frappe.db.set_value("ERP Warehouse", self.b["warehouse"], "is_active", 1)
            frappe.db.commit()
