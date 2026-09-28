"""Authorized review-workbook downloads on the isolated Cowork lab; zero network.

Writes synthetic tasks only. Uses the real Frappe/WSGI/DB path; no model, ERP,
LINE, identity provider or external configuration is contacted.
"""
from io import BytesIO
import json
from pathlib import Path
import runpy
import unittest
from urllib.parse import unquote
from uuid import uuid4

import frappe
from openpyxl import load_workbook
from werkzeug.test import Client
from werkzeug.wrappers import Response

from pearnly_cowork.reconciliation.export import labels

FIXTURE = runpy.run_path(str(Path(__file__).with_name("cowork-bank-recon-lab-verify.py")))
BASE = FIXTURE["NativeBankReconciliation"]
connect = FIXTURE["connect"]
TASK = FIXTURE["TASK"]
STATEMENT_ROWS = FIXTURE["STATEMENT_ROWS"]


class NativeBankReconExport(unittest.TestCase):
    setUpClass = classmethod(BASE.setUpClass.__func__)
    setUp, tearDown = BASE.setUp, BASE.tearDown

    def parsed_task(self):
        return self.api.create_from_parsed(workspace=self.workspace, title="June 2569 parsed",
            request_id=self.request_id, statement_pages=FIXTURE["STATEMENT_PAGES"],
            gl_pages=FIXTURE["GL_PAGES"], header=FIXTURE["PARSED_HEADER"],
            filename="STM KBANK.pdf", gl_filename="GL.xlsx")

    def test_export_renders_the_stored_rows_summary_and_file_diagnostics(self):
        task = self.parsed_task()
        exported = self.service.export(task["name"], "th")
        self.assertEqual(exported["filename"], "June 2569 parsed.xlsx")  # 任务名称（2026-09-28 起）
        book = load_workbook(BytesIO(exported["content"]))
        self.assertEqual(len(book.sheetnames), 4)
        summary_sheet, match_sheet = book.worksheets[0], book.worksheets[1]
        # The admitted file names and the parsed opening balance reach the summary.
        summary_labels = [cell.value for cell in summary_sheet["A"]]
        self.assertTrue(any("STM KBANK.pdf" in str(value) for value in summary_labels))
        self.assertTrue(any("GL.xlsx" in str(value) for value in summary_labels))
        gl_close_row = summary_labels.index(labels.t("lbl_gl_close", "th")) + 1
        self.assertEqual(summary_sheet.cell(gl_close_row, 2).value, 500.0)
        stmt_close_row = summary_labels.index(labels.t("lbl_stmt_close", "th")) + 1
        self.assertEqual(summary_sheet.cell(stmt_close_row, 2).value, 560.0)
        # The opening difference row keeps the deployed "statement - GL" wording.
        diff_cell = next(cell for cell in summary_sheet["A"] if isinstance(cell.value, str)
                         and "1,000.00" in cell.value and "990.00" in cell.value)
        self.assertEqual(summary_sheet.cell(diff_cell.row, 2).value, 10.0)
        # Four result rows: one match and the three one-sided findings, as stored.
        statuses = [match_sheet.cell(index, 1).value for index in range(2, match_sheet.max_row + 1)]
        self.assertEqual(sorted(statuses), sorted([
            labels.t("status_matched", "th"), labels.t("st_gl_debit_only", "th"),
            labels.t("st_stmt_dep_only", "th"), labels.t("st_stmt_wd_only", "th")]))
        self.assertEqual(match_sheet.cell(2, 9).value, "GL-1")
        stored = frappe.parse_json(frappe.db.get_value(TASK, task["name"], "parse_info_json"))
        self.assertEqual(stored["stmt_files"][0]["file"], "STM KBANK.pdf")
        self.assertEqual(stored["stmt_files"][0]["bank_code"], "kbank")
        self.assertEqual(stored["gl_files"][0]["accounts"], ["1000"])
        # An unknown language falls back to Thai, exactly like the service contract.
        fallback = self.service.export(task["name"], "de")
        self.assertEqual(load_workbook(BytesIO(fallback["content"])).sheetnames, book.sheetnames)

    def test_the_workbook_shows_the_run_warnings_and_the_manual_trace(self):
        # A statement from another period with one corrected anchor: the task stores a
        # mismatch warning and an override, and the workbook renders both.
        statement_rows = [STATEMENT_ROWS[0], STATEMENT_ROWS[1]]
        gl_rows = [{"date": "2026-09-01", "doc_no": "GL-9", "account_code": "1000",
            "description": "later period", "credit": "400.00", "balance": "600.00"}]
        task = self.api.create(workspace=self.workspace, title="Another period",
            request_id=self.request_id, statement_rows=statement_rows, gl_rows=gl_rows,
            header={"bank_code": "BBL", "gl_account_code": "1000", "stmt_opening": "1000.00",
                "gl_opening": "990.00", "stmt_closing": "510.00", "gl_closing": "500.00"},
            anchors={"stmt_opening": 1000.0, "gl_opening": 990.0, "gl_closing": 500.0,
                "stmt_closing": 510.0, "overrides": {"gl_closing": 495.0}})
        exported = self.service.export(task["name"], "th")
        summary = load_workbook(BytesIO(exported["content"])).worksheets[0]
        texts = [str(cell.value) for cell in summary["A"] if cell.value is not None]
        # The warning banner carries the string the run stored, unchanged.
        self.assertIn(task["run_info"]["warnings"][0], texts)
        # The banner announcing a manual entry, and its trace section, both appear.
        self.assertTrue(any(labels.t("lbl_manual_warn", "th") in text for text in texts),
            texts[:8])
        self.assertTrue(any(labels.t("sec_manual_entry", "th") in text for text in texts), texts)
        self.assertTrue(any(labels.t("lbl_anchor_gl_close", "th") in text for text in texts),
            texts)
        # The overridden anchor's row carries the user value, and the cell comment
        # states the reader's value and the difference (the deployed way to show both).
        label_row = next(cell.row for cell in summary["A"] if isinstance(cell.value, str)
            and labels.t("lbl_anchor_gl_close", "th") in cell.value)
        override_cell = summary.cell(label_row, 2)
        self.assertEqual(override_cell.value, 495.0)
        comment = override_cell.comment.text if override_cell.comment else ""
        self.assertIn("OCR: 500.00", comment)
        self.assertIn("User: 495.00", comment)
        self.assertIn("Diff: -5.00", comment)
        # A task without a run record renders none of these sections.
        self.request_id = "plain-" + uuid4().hex
        plain = self.parsed_task()
        plain_summary = load_workbook(BytesIO(
            self.service.export(plain["name"], "th")["content"])).worksheets[0]
        plain_texts = [str(cell.value) for cell in plain_summary["A"] if cell.value is not None]
        self.assertFalse(any(labels.t("sec_manual_entry", "th") in text for text in plain_texts))

    def test_export_is_scoped_and_entitled_like_the_task_api(self):
        task = self.parsed_task()
        frappe.set_user(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            self.service.export(task["name"])
        frappe.set_user(self.norecon)
        frappe.conf.pearnly_features = ["intake", "master_data", "push_log", "stocktake"]
        with self.assertRaises(frappe.PermissionError):
            self.service.export(task["name"])
        frappe.conf.pearnly_features = ["intake", "master_data", "push_log", "recon", "stocktake"]
        frappe.set_user(self.actor)

    def test_export_download_is_a_private_attachment_and_writes_the_access_log(self):
        task = self.parsed_task()
        site = frappe.local.site
        frappe.set_user("Administrator")
        account = frappe.get_doc("User", self.actor)
        account.api_key, secret = uuid4().hex, uuid4().hex
        account.api_secret = secret
        account.save()
        headers = {"Authorization": f"token {account.api_key}:{secret}"}
        frappe.db.commit()
        frappe.destroy()
        from frappe.app import application
        client = Client(application, Response)
        url = ("/api/method/pearnly_cowork.reconciliation.bank_recon_api.export?task_id="
               + task["name"] + "&lang=th")

        def get(authenticated=True):
            return client.get(url, base_url="http://" + site,
                              headers=headers if authenticated else {})

        try:
            response = get()
            self.assertEqual(response.status_code, 200,
                             response.get_data(as_text=True) if response.status_code != 200 else "")
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertIn("June 2569 parsed.xlsx", unquote(response.headers["Content-Disposition"]))
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("spreadsheetml.sheet", response.headers["Content-Type"])
            self.assertEqual(len(load_workbook(BytesIO(response.data)).sheetnames), 4)
            self.assertEqual(get(False).status_code, 403)
            self.assertEqual(client.post(url, base_url="http://" + site,
                                         headers=headers).status_code, 403)
        finally:
            connect()
        # The real request path defers Access Log writes to the per-Site Redis queue;
        # the isolated lab intentionally runs no scheduler.
        queued = [json.loads(value) for value in frappe.cache.lrange("insert_queue_for_Access Log", 0, -1)]
        rows = [row for batch in queued for row in (batch if isinstance(batch, list) else [batch])]
        self.assertTrue(any(row.get("user") == self.actor and row.get("file_type") == "bank_recon_workbook"
                            and row.get("reference_document") == task["name"] for row in rows))


if __name__ == "__main__":
    connect()
    try:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(NativeBankReconExport))
        print(json.dumps({"bank_recon_export_native": "PASS" if result.wasSuccessful() else "FAIL",
                          "tests": result.testsRun}))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, "db", None):
            frappe.db.rollback()
        frappe.destroy()
