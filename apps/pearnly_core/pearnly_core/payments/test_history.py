"""Native product Site test; synthetic records roll back, no external IO."""
import frappe
from frappe.tests import IntegrationTestCase
from pearnly_core import billing_www


class TestHistory(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(frappe.local.site, "dms-line-lab.localhost")
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, "Administrator")
        self.addCleanup(frappe.db.rollback)

    def test_ocr_selection_and_permissions(self):
        doc = frappe.get_doc({"doctype": "Pearnly OCR Record", "product": "dms",
                              "status": "ok", "page_count": 1, "charged_thb": 1.5}).insert()
        # Product entry must reject an unauthenticated user before touching rows.
        frappe.set_user("Guest")
        with self.assertRaises(frappe.AuthenticationError):
            billing_www.delete_records("ocr", [doc.name])
        from unittest.mock import patch
        frappe.set_user("Administrator")
        with patch("frappe.get_roles", return_value=["DMS Sales"]):
            with self.assertRaises(frappe.PermissionError):
                billing_www.delete_records("ocr", [doc.name])
        self.assertFalse(doc.reload().records_deleted_at)
        self.assertEqual(billing_www.delete_records("ocr", [doc.name]), {"deleted": 1})
        rows = billing_www.records("ocr", limit=5000)
        self.assertNotIn(doc.name, [row.name for row in rows["rows"]])
        self.assertEqual(doc.reload().charged_thb, 1.5)
