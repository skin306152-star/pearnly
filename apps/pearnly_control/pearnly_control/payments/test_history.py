"""Native Site tests for history removal. Synthetic DB rows only, no remote calls.

Run only on the isolated dms-line-lab-control.localhost billing Site; fixture
transactions roll back. No real balances, OCR requests or third-party writes.
"""
import frappe
from frappe.tests import IntegrationTestCase

from pearnly_control import billing
from pearnly_control.payments import history, usage
from pearnly_control.payments.pricing import public_rates


class TestHistory(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(frappe.local.site, "dms-line-lab-control.localhost")
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, "Administrator")
        self.addCleanup(frappe.db.rollback)
        self.tenant = frappe.get_doc({"doctype": "Pearnly Tenant",
                                      "tenant_name": "_Test history removal"}).insert().name

    def transaction(self, tenant=None):
        return frappe.get_doc({"doctype": billing.TXN, "tenant": tenant or self.tenant,
            "type": "usage", "amount_thb": -1.5, "balance_after": 10,
            "product": "dms", "units": 1, "pages": 1, "billing_mode": "payg",
            "billing_month": usage.billing_month(frappe.utils.now_datetime()), "reference": "_Test history evidence"}).insert()

    def test_usage_bulk_removal_preserves_billing_evidence(self):
        docs = [self.transaction(), self.transaction()]
        before = usage.pages_used(self.tenant, usage.billing_month(frappe.utils.now_datetime()))
        names = [doc.name for doc in docs]
        self.assertEqual(history.delete_records(self.tenant, "usage", names, "Administrator"), {"deleted": 2})
        self.assertEqual(history.delete_records(self.tenant, "usage", names, "Administrator"), {"deleted": 0})
        self.assertEqual(usage.pages_used(self.tenant, usage.billing_month(frappe.utils.now_datetime())), before)
        self.assertEqual(frappe.db.count(billing.TXN, {"tenant": self.tenant,
                         "records_deleted_at": ["is", "not set"]}), 0)
        for doc in docs:
            doc.reload()
            self.assertEqual(doc.amount_thb, -1.5)
            self.assertEqual(doc.reference, "_Test history evidence")
            self.assertTrue(doc.records_deleted_at)
            self.assertEqual(doc.records_deleted_by, "Administrator")

    def test_cross_tenant_and_invalid_selection_are_atomic(self):
        mine = self.transaction()
        other = frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": "_Test other company"}).insert()
        theirs = self.transaction(other.name)
        with self.assertRaises(frappe.PermissionError):
            history.delete_records(self.tenant, "usage", [mine.name, theirs.name], "Administrator")
        self.assertFalse(mine.reload().records_deleted_at)
        for names in ([], "bad json", [None], ["x"] * 2501):
            with self.assertRaises(frappe.ValidationError):
                history.delete_records(self.tenant, "usage", names, "Administrator")
        with self.assertRaises(frappe.ValidationError):
            history.delete_records(self.tenant, "unknown", [mine.name], "Administrator")

    def test_topup_removal_does_not_cancel_review(self):
        doc = frappe.get_doc({"doctype": billing.TOPUP, "tenant": self.tenant,
                              "requested_thb": 100, "status": "pending"}).insert()
        self.assertEqual(history.delete_records(self.tenant, "topup", [doc.name], "Administrator"), {"deleted": 1})
        doc.reload()
        self.assertEqual(doc.status, "pending")
        self.assertEqual(doc.requested_thb, 100)
        self.assertTrue(frappe.db.exists(billing.TOPUP, {"tenant": self.tenant, "status": "pending"}))

    def test_display_rates_match_unchanged_charging_policy(self):
        rates = public_rates()
        self.assertEqual(rates["pdf_first_pages"], 200)
        self.assertEqual(rates["pdf_first_rate"], float(billing.pdf_cost_thb(0, 1)))
        self.assertEqual(rates["pdf_after_rate"], float(billing.pdf_cost_thb(200, 1)))
        self.assertEqual(rates["pdf_after_rate"], 1.0)
        self.assertEqual(rates["chars_per_unit"], 50)
        self.assertEqual(rates["char_rate"], .01)
