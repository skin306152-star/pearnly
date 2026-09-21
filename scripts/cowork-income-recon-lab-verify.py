"""Isolated Site verification for the native income reconciliation store.

Only the dedicated lab bench is accepted, synthetic users/workspaces are created
inside it, and everything rolls back. No file reading, model, ERP, queue, export
or external call happens here: the entry point is fed rows a caller already
decoded.
"""

import json
import os
from pathlib import Path
import runpy
import sys
import unittest
from uuid import uuid4

import frappe
from werkzeug.test import Client
from werkzeug.wrappers import Response

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-backend-lab-verify.py")))
connect, user, grant = (BASE[key] for key in ("connect", "user", "grant"))
ROLE = "Cowork Bank Reconciliation"
TASK = "Cowork Income Reconciliation"
ROW = "Cowork Income Reconciliation Row"

# Two ledger rows that share one VAT document number, one credit note pairing with
# the debit column, one ledger-only receipt and one VAT row the ledger never saw.
GL_ROWS = [
	{"doc_no": "GL-1", "date": "01/06/2569", "account_code": "4110-01", "description": "sale",
		"credit": "1070.00"},
	{"doc_no": "GL-1", "date": "01/06/2569", "account_code": "4110-01", "description": "sale 2",
		"credit": "30.00"},
	{"doc_no": "GL-2", "date": "02/06/2569", "account_code": "4110-01", "description": "credit note",
		"debit": "107.00"},
	{"doc_no": "GL-3", "date": "03/06/2569", "account_code": "4110-02", "description": "no vat row",
		"credit": "500.00"},
]
VAT_ROWS = [
	{"report_ref_no": "GL-1", "report_reference_no": "REF-1", "report_invoice_no": "INV-1",
		"report_date": "01/06/2569", "report_buyer_name": "ACME", "report_amount_pre_vat": "1100.00"},
	{"report_ref_no": "GL-2", "report_reference_no": "REF-2", "report_invoice_no": "INV-2",
		"report_date": "02/06/2569", "report_buyer_name": "ACME", "report_amount_pre_vat": "-107.00"},
	{"report_ref_no": "INV-9", "report_reference_no": "REF-9", "report_invoice_no": "INV-9",
		"report_date": "05/06/2569", "report_buyer_name": "BETA", "report_amount_pre_vat": "250.00"},
]
HEADER = {"gl_account_code": "4110-01"}


class NativeIncomeReconciliation(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		if not frappe.db.exists("Role", ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert()
		suffix = uuid4().hex[:10]
		cls.actor = f"cw-income-actor-{suffix}@example.invalid"
		cls.manager = f"cw-income-manager-{suffix}@example.invalid"
		cls.outsider = f"cw-income-outsider-{suffix}@example.invalid"
		cls.norecon = f"cw-income-norecon-{suffix}@example.invalid"
		user(cls.actor, [ROLE])
		user(cls.manager, ["Cowork Admin"])
		user(cls.outsider, [ROLE])
		user(cls.norecon, ["Cowork Admin"])
		from pearnly_cowork.masters import api as masters
		cls.workspace = masters.create_workspace("Income recon lab " + suffix)["name"]
		cls.foreign = masters.create_workspace("Income recon lab other " + suffix)["name"]
		cls.permission = grant(cls.actor, "Cowork Workspace", cls.workspace).name
		grant(cls.manager, "Cowork Workspace", cls.workspace)
		grant(cls.norecon, "Cowork Workspace", cls.workspace)
		frappe.db.commit()

	def setUp(self):
		from pearnly_cowork.reconciliation import income_recon_api, income_recon_service
		self.api, self.service = income_recon_api, income_recon_service
		self.previous = dict(frappe.conf)
		frappe.conf.pearnly_features = ["intake", "master_data", "push_log", "recon", "stocktake"]
		frappe.set_user(self.actor)
		self.request_id = "income-recon-" + uuid4().hex

	def tearDown(self):
		frappe.db.rollback()
		frappe.conf.clear()
		frappe.conf.update(self.previous)
		frappe.set_user("Administrator")
		frappe.clear_cache()

	def create(self, **changes):
		arguments = {"workspace": self.workspace, "title": "June 2569 output VAT",
			"request_id": self.request_id, "gl_rows": GL_ROWS, "vat_rows": VAT_ROWS,
			"header": HEADER}
		arguments.update(changes)
		return self.api.create(**arguments)

	def test_store_summary_rows_and_replay(self):
		result = self.create()
		self.assertFalse(result["replayed"])
		self.assertEqual(result["row_count"], 3)
		summary = result["summary"]
		self.assertEqual((summary["gl_total"], summary["vat_total"]), (1493.0, 1243.0))
		self.assertEqual((summary["gl_only_credit"], summary["gl_only_debit"]), (500.0, 0.0))
		self.assertEqual((summary["vat_only_positive"], summary["vat_only_negative"]),
			(250.0, 0.0))
		# The deployed summary also carries the itemized adjustment lines.
		self.assertEqual([item["doc_no"] for item in summary["gl_only_credit_items"]], ["GL-3"])
		self.assertEqual([item["doc_no"] for item in summary["vat_only_positive_items"]],
			["INV-9"])

		detail = self.api.detail(result["name"])
		self.assertEqual(detail["total"], 3)
		self.assertEqual([row["row_index"] for row in detail["rows"]], [1, 2, 3])
		# Result rows follow the VAT report, in the order it printed them.
		paired, note, unmatched = detail["rows"]
		self.assertEqual((paired["doc_no"], paired["vat_date"], paired["customer_name"]),
			("GL-1", "01/06/2569", "ACME"))
		self.assertEqual((paired["vat_amount"], paired["gl_amount"], paired["diff"]),
			(1100.0, 1100.0, 0.0))
		self.assertEqual((paired["has_gl_match"], paired["account_codes"]), (1, "4110-01"))
		# A credit note pairs the negative report amount with the negated debit column.
		self.assertEqual((note["vat_amount"], note["gl_amount"], note["diff"]),
			(-107.0, -107.0, 0.0))
		# No ledger counterpart: stored as zero, told apart by the Check.
		self.assertEqual((unmatched["has_gl_match"], unmatched["gl_amount"], unmatched["diff"],
			unmatched["account_codes"]), (0, 0.0, 0.0, ""))
		self.assertEqual(detail["task"]["gl_row_count"], 4)
		self.assertEqual((detail["task"]["matched_count"], detail["task"]["diff_count"],
			detail["task"]["unmatched_count"]), (2, 0, 1))
		self.assertEqual(detail["task"]["gl_account_code"], "4110-01")
		self.assertEqual(detail["task"]["source_format"], "decoded_vat_rows_v1")
		self.assertEqual(detail["summary"], summary)
		listed = self.api.records(self.workspace)
		self.assertIn(result["name"], [row["name"] for row in listed["tasks"]])
		stored = [row for row in listed["tasks"] if row["name"] == result["name"]][0]
		self.assertEqual((stored["matched_count"], stored["diff_count"],
			stored["unmatched_count"]), (2, 0, 1))

		replay = self.create()
		self.assertTrue(replay["replayed"])
		self.assertEqual(replay["summary"], summary)
		self.assertEqual(frappe.db.count(TASK, {"workspace": self.workspace}), 1)
		self.assertEqual(frappe.db.count(ROW, {"task": result["name"]}), 3)

	def test_same_request_key_with_different_content_is_refused(self):
		self.create()
		with self.assertRaises(frappe.ValidationError):
			self.create(vat_rows=VAT_ROWS[:1])

	def test_header_tolerance_decides_whether_a_small_difference_matches(self):
		rows = [{"doc_no": "GL-1", "account_code": "4110-01", "credit": "1070.00"}]
		vat = [{"report_ref_no": "GL-1", "report_amount_pre_vat": "1071.00"}]
		strict = self.create(gl_rows=rows, vat_rows=vat, header={"amount_tolerance": "0.01"})
		self.assertEqual(self.api.detail(strict["name"])["rows"][0]["diff"], 1.0)
		loose = self.create(gl_rows=rows, vat_rows=vat, request_id="loose-" + uuid4().hex,
			header={"amount_tolerance": "1.00"})
		self.assertEqual(self.api.detail(loose["name"])["rows"][0]["diff"], 0.0)
		self.assertEqual(self.api.detail(loose["name"])["task"]["amount_tolerance"], 1.0)

	def test_inputs_are_refused_before_anything_is_stored(self):
		from pearnly_cowork.reconciliation import income_recon_contracts as contracts
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			self.create(header={"gl_account_code": "4110-01", "surprise": "1"})
		self.assertEqual(str(caught.exception), "reconciliation.header_invalid")
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			self.create(gl_rows=[{"doc_no": "GL-1", "account": "4110-01"}])
		self.assertEqual(str(caught.exception), "reconciliation.row_unknown_field:income_gl")
		with self.assertRaises(contracts.ReconciliationContractError) as caught:
			self.create(vat_rows=[{"report_ref_no": "GL-1", "report_amount_pre_vat": "1100.00",
				"report_vat_amount": "77.00"}])
		self.assertEqual(str(caught.exception), "reconciliation.row_unknown_field:income_vat")
		with self.assertRaises(contracts.ReconciliationContractError):
			self.create(gl_rows={"doc_no": "GL-1"})
		self.assertEqual(frappe.db.count(TASK, {"workspace": self.workspace}), 0)
		self.assertEqual(frappe.db.count(ROW, {"workspace": self.workspace}), 0)

	def test_scope_and_entitlement_are_enforced(self):
		frappe.set_user(self.outsider)
		with self.assertRaises(frappe.PermissionError):
			self.create()
		frappe.set_user(self.actor)
		with self.assertRaises(frappe.PermissionError):
			self.create(workspace=self.foreign)
		result = self.create()
		frappe.set_user(self.outsider)
		with self.assertRaises(frappe.PermissionError):
			self.api.detail(result["name"])
		frappe.set_user(self.norecon)
		frappe.conf.pearnly_features = ["intake", "master_data", "push_log", "stocktake"]
		with self.assertRaises(frappe.PermissionError):
			self.api.records(self.workspace)
		frappe.conf.pearnly_features = ["intake", "master_data", "push_log", "recon", "stocktake"]
		frappe.set_user(self.actor)

	def test_revoked_workspace_permission_blocks_read_and_write(self):
		result = self.create()
		frappe.db.delete("User Permission", {"name": self.permission})
		frappe.set_user(self.actor)
		with self.assertRaises(frappe.PermissionError):
			self.api.detail(result["name"])
		with self.assertRaises(frappe.PermissionError):
			self.create(request_id="another-" + uuid4().hex)

	def test_generic_writes_and_shares_are_refused(self):
		result = self.create()
		frappe.set_user(self.manager)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc(TASK, result["name"])
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "DocShare", "share_doctype": ROW,
				"share_name": frappe.get_all(ROW, filters={"task": result["name"]}, pluck="name")[0],
				"user": self.actor, "read": 1}).insert()
		frappe.set_user(self.manager)
		row = frappe.get_all(ROW, filters={"task": result["name"]}, pluck="name")[0]
		# Generic saves are refused for every caller; only the service may write.
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(ROW, row).save()
		frappe.set_user(self.actor)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(ROW, row).save()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": TASK, "workspace": self.workspace, "title": "hand made",
				"record_id": "CIR-hand", "status": "completed", "source_format": "x",
				"source_digest": "0" * 64, "request_key": "k-" + uuid4().hex,
				"request_digest": "0" * 64}).insert()

	def test_stored_identity_cannot_be_rewritten(self):
		result = self.create()
		doc = frappe.get_doc(TASK, result["name"])
		doc.workspace = self.foreign
		# The new workspace is outside the caller's scope, so the gate refuses the
		# move before the identity check runs.
		with self.assertRaises(frappe.PermissionError):
			self.service.persist(doc)
		doc = frappe.get_doc(TASK, result["name"])
		doc.record_id = "CIR-moved"
		with self.assertRaises(frappe.ValidationError):
			self.service.persist(doc)

	def test_service_delete_removes_rows_and_task(self):
		result = self.create()
		frappe.set_user(self.actor)
		removed = self.api.delete(result["name"])
		self.assertEqual(removed, {"ok": True, "rows_deleted": 3})
		self.assertFalse(frappe.db.exists(TASK, result["name"]))
		self.assertEqual(frappe.db.count(ROW, {"task": result["name"]}), 0)
		self.assertEqual(self.api.records(self.workspace)["tasks"], [])

	def test_reads_are_paginated_and_stay_inside_the_window(self):
		result = self.create()
		first = self.api.detail(result["name"], limit=2)
		self.assertEqual([row["row_index"] for row in first["rows"]], [1, 2])
		self.assertEqual(first["total"], 3)
		second = self.api.detail(result["name"], start=2, limit=2)
		self.assertEqual([row["row_index"] for row in second["rows"]], [3])


if __name__ == "__main__":
	connect(os.environ.get("PEARNLY_COWORK_TEST_SITE"))
	try:
		suite = (unittest.TestSuite(NativeIncomeReconciliation(name) for name in sys.argv[1:])
			if len(sys.argv) > 1 else unittest.defaultTestLoader.loadTestsFromTestCase(NativeIncomeReconciliation))
		result = unittest.TextTestRunner(verbosity=2).run(suite)
		frappe.db.rollback()
		frappe.destroy()
		connect(os.environ.get("PEARNLY_COWORK_TEST_SITE"))
		from frappe.app import application
		client = Client(application, Response)
		base = f"http://{frappe.local.site}"
		rpc = "/api/method/pearnly_cowork.reconciliation.income_recon_api."
		anonymous = {
			"create": client.post(rpc + "create", base_url=base, json={
				"workspace": "x", "title": "t", "request_id": "x",
				"gl_rows": [], "vat_rows": []}).status_code,
			"records": client.get(rpc + "records", base_url=base,
				query_string={"workspace": "x"}).status_code,
			"detail": client.get(rpc + "detail", base_url=base,
				query_string={"task_id": "x"}).status_code,
			"delete": client.post(rpc + "delete", base_url=base, json={"task_id": "x"}).status_code,
		}
		print(json.dumps({"anonymous_http": anonymous}))
		if set(anonymous.values()) != {403}:
			raise SystemExit(1)
		raise SystemExit(0 if result.wasSuccessful() else 1)
	finally:
		frappe.db.rollback()
		frappe.destroy()
