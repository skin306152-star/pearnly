"""Synthetic validated OCR -> grouping -> native history integration.

Only the existing guarded, isolated Cowork lab can run this. No provider,
billing, real invoice, LINE or other external system is contacted.
"""
import copy
from pathlib import Path
import runpy
import unittest
import uuid

import frappe

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-files-lab-verify.py")))
connect, user, grant, pdf = (BASE[k] for k in ("connect", "user", "grant", "pdf"))


class InvoiceIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.masters import api
        suffix = uuid.uuid4().hex[:10]
        cls.actor = f"cw-invoice-{suffix}@example.invalid"
        cls.other = f"cw-invoice-other-{suffix}@example.invalid"
        user(cls.actor, ["Cowork Intake", "Cowork OCR History"])
        user(cls.other, ["Cowork Intake", "Cowork OCR History"])
        cls.workspace = api.create_workspace("Synthetic Invoice " + suffix)["name"]
        cls.permission = grant(cls.actor, "Cowork Workspace", cls.workspace).name
        frappe.db.commit()

    def setUp(self):
        from pearnly_cowork.files import service
        frappe.set_user(self.actor)
        self.source = service.create(self.workspace, "synthetic-two-page.pdf", pdf(), uuid.uuid4().hex)

    def tearDown(self):
        frappe.db.rollback()
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def result(self):
        from pearnly_cowork.ocr.schema import PipelinePageResult, PipelineResult, validate_invoice
        base = {"invoice_number": "SYNTHETIC-A", "seller_name": "Synthetic seller", "seller_tax": "0105559001671",
                "date": "2026-09-20", "subtotal": "100.00", "vat": "7.00", "total_amount": "107.00"}
        first = copy.deepcopy(base)
        first["additional_invoices"] = [dict(base, invoice_number="SYNTHETIC-B", total_amount="214.00", subtotal="200.00", vat="14.00")]
        pages = [PipelinePageResult(page_number=n, invoice=validate_invoice(value, anchor_year=2026),
                 layer_chain=["image_direct"], final_confidence=1, confidence_band="auto")
                 for n, value in ((1, first), (2, base))]
        return PipelineResult(pages=pages, page_count=2, elapsed_ms=10)

    def prepare(self, result=None):
        from pearnly_cowork.ocr.results import prepare_history_batch
        return prepare_history_batch(result or self.result(), expected_page_count=2, workspace=self.workspace,
                                     source="manual", direction="purchase")

    def save(self, plan):
        from pearnly_cowork.history.recognition import persist_batch
        return persist_batch(self.source["name"], plan["groups"], "synthetic-invoice-manifest")

    def test_complete_multi_page_and_same_page_invoices_roundtrip(self):
        from pearnly_cowork.history.storage import parse
        from pearnly_cowork.files import service
        plan = self.prepare()
        rows = self.save(plan)
        self.assertEqual(len(rows), 2)
        by_number = {row["invoice_no"]: row for row in rows}
        self.assertEqual(by_number["SYNTHETIC-A"]["source_page_indices"], [1, 2])
        self.assertEqual(by_number["SYNTHETIC-B"]["source_page_indices"], [1])
        for row, group in zip(rows, plan["groups"]):
            doc = frappe.get_doc("Cowork OCR History", row["name"])
            self.assertEqual(parse(doc.ai_raw), group["ai_raw"])
            self.assertTrue(doc.staged)
            self.assertTrue(service.page(row["name"], 1).startswith(b"\x89PNG"))
        again = self.save(plan)
        self.assertEqual([r["name"] for r in rows], [r["name"] for r in again])
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 2)

    def test_missing_page_cannot_seal_original(self):
        result = self.result()
        result.pages.pop()
        with self.assertRaises(ValueError):
            self.prepare(result)
        self.assertFalse(frappe.db.get_value("Cowork Source File", self.source["name"], "binding_complete"))
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 0)

    def test_original_ai_result_is_immutable_after_review_edit(self):
        from pearnly_cowork.history import api
        row = self.save(self.prepare())[0]
        before = frappe.db.get_value("Cowork OCR History", row["name"], "ai_raw")
        pages = copy.deepcopy(row["pages"])
        pages[0]["fields"]["invoice_number"] = "SYNTHETIC-CORRECTED"
        api.save(row["name"], pages, row["record_version"])
        self.assertEqual(frappe.db.get_value("Cowork OCR History", row["name"], "ai_raw"), before)

    def test_prepared_result_is_not_permission_to_write(self):
        plan = self.prepare()
        frappe.set_user(self.other)
        with self.assertRaises(frappe.PermissionError):
            self.save(plan)
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 0)

    def test_l3_failure_preserves_l2_as_reviewable_draft(self):
        result = self.result()
        result.pages[0].layer_chain = ["L1", "L2", "L3_failed"]
        result.pages[0].error = "Synthetic refinement failure"
        plan = self.prepare(result)
        self.assertEqual(plan["state"], "review_required")
        self.assertTrue(plan["requires_review"])
        rows = self.save(plan)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row["staged"])
            flagged = [p for p in row["pages"] if p["page_index"] == 1]
            self.assertTrue(all(p["_needs_manual_review"] for p in flagged))
            self.assertTrue(all(p["_confidence_band"] == "needs_review" for p in flagged))
        self.assertEqual(result.pages[0].confidence_band, "auto")

    def test_all_non_invoice_retains_source_without_histories(self):
        result = self.result()
        for page in result.pages:
            page.invoice.is_not_invoice = True
            page.invoice.additional_invoices = []
        plan = self.prepare(result)
        self.assertEqual(plan["state"], "not_invoice")
        self.assertEqual(plan["groups"], [])
        self.assertFalse(plan["has_billable_result"])
        self.assertFalse(frappe.db.get_value("Cowork Source File", self.source["name"], "binding_complete"))


if __name__ == "__main__":
    connect()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(InvoiceIntegration)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.destroy()
