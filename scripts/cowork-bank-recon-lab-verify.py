"""Isolated Site verification for the native bank reconciliation store.

Only the dedicated lab bench is accepted, synthetic users/workspaces are created
inside it, and everything rolls back. No parsing, model, ERP, queue, export or
external call happens here.
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
TASK = "Cowork Bank Reconciliation"
ROW = "Cowork Bank Reconciliation Row"

STATEMENT_ROWS = [
	{"date": "2026-06-01", "description": "ยอดยกมา", "balance": "1000.00"},
	{"date": "2026-06-01", "description": "SWD ACME INV-77", "withdrawal": "400.00",
		"balance": "600.00", "confidence": "high", "balance_ok": "ok"},
	{"date": "2026-06-02", "description": "ค่าบริการ", "withdrawal": "90.00", "balance": "510.00"},
]
GL_ROWS = [
	{"date": "2026-06-01", "doc_no": "GL-1", "account_code": "1000", "description": "payment",
		"credit": "400.00", "balance": "600.00"},
	{"date": "2026-06-30", "doc_no": "GL-2", "account_code": "1000", "description": "unmatched",
		"debit": "75.00", "balance": "675.00"},
]
HEADER = {"bank_code": "BBL", "gl_account_code": "1000", "stmt_opening": "1000.00",
	"gl_opening": "990.00", "stmt_closing": "510.00", "gl_closing": "500.00"}


class NativeBankReconciliation(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		if not frappe.db.exists("Role", ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert()
		suffix = uuid4().hex[:10]
		cls.actor = f"cw-bank-actor-{suffix}@example.invalid"
		cls.manager = f"cw-bank-manager-{suffix}@example.invalid"
		cls.outsider = f"cw-bank-outsider-{suffix}@example.invalid"
		cls.norecon = f"cw-bank-norecon-{suffix}@example.invalid"
		user(cls.actor, [ROLE])
		user(cls.manager, ["Cowork Admin"])
		user(cls.outsider, [ROLE])
		user(cls.norecon, ["Cowork Admin"])
		from pearnly_cowork.masters import api as masters
		cls.workspace = masters.create_workspace("Bank recon lab " + suffix)["name"]
		cls.foreign = masters.create_workspace("Bank recon lab other " + suffix)["name"]
		cls.permission = grant(cls.actor, "Cowork Workspace", cls.workspace).name
		grant(cls.manager, "Cowork Workspace", cls.workspace)
		grant(cls.norecon, "Cowork Workspace", cls.workspace)
		frappe.db.commit()

	def setUp(self):
		from pearnly_cowork.reconciliation import bank_recon_api, bank_recon_service
		self.api, self.service = bank_recon_api, bank_recon_service
		self.previous = dict(frappe.conf)
		frappe.conf.pearnly_cowork_erp_push_enabled = True
		frappe.conf.pearnly_features = ["intake", "master_data", "push_log", "recon", "stocktake"]
		frappe.set_user(self.actor)
		self.request_id = "bank-recon-" + uuid4().hex

	def tearDown(self):
		frappe.db.rollback()
		frappe.conf.clear()
		frappe.conf.update(self.previous)
		frappe.set_user("Administrator")
		frappe.clear_cache()

	def create(self, **changes):
		arguments = {"workspace": self.workspace, "title": "June 2569 statement", "request_id": self.request_id,
			"statement_rows": STATEMENT_ROWS, "gl_rows": GL_ROWS, "header": HEADER}
		arguments.update(changes)
		return self.api.create(**arguments)

	def test_store_summary_rows_and_replay(self):
		result = self.create()
		self.assertFalse(result["replayed"])
		self.assertEqual(result["row_count"], 4)
		summary = result["summary"]
		self.assertEqual((summary["matched_count"], summary["stmt_withdrawal_only_count"],
			summary["gl_debit_only_count"], summary["gl_credit_only_count"],
			summary["stmt_deposit_only_count"]), (1, 1, 1, 0, 1))
		self.assertEqual(summary["stmt_total_withdrawal"], 490.0)
		self.assertEqual(summary["stmt_total_deposit"], 0.0)
		self.assertEqual(summary["gl_total_credit"], 400.0)
		self.assertEqual(summary["gl_total_debit"], 75.0)
		self.assertEqual(summary["stmt_withdrawal_only_amount"], 90.0)
		self.assertEqual(summary["gl_debit_only_amount"], 75.0)
		self.assertEqual(summary["opening_diff"], 10.0)
		# gl_closing + opening_diff - gl_debit_only - stmt_withdrawal_only
		self.assertEqual(summary["formula_stmt_closing"], 500.0 + 10.0 - 75.0 - 90.0)
		self.assertEqual(summary["formula_diff"], round(510.0 - summary["formula_stmt_closing"], 2))

		detail = self.api.detail(result["name"])
		self.assertEqual(detail["total"], 4)
		# Matched rows sort first; the remaining rows follow by date, so the
		# opening row (no movement) precedes the later one-sided rows.
		self.assertEqual([row["match_status"] for row in detail["rows"]],
			["matched", "stmt_deposit_only", "stmt_withdrawal_only", "gl_debit_only"])
		self.assertEqual([row["row_index"] for row in detail["rows"]], [1, 2, 3, 4])
		matched = detail["rows"][0]
		self.assertEqual((matched["match_layer"], matched["date_diff_days"]), (1, 0))
		self.assertEqual((matched["gl_doc_no"], matched["gl_credit"]), ("GL-1", 400.0))
		self.assertEqual((matched["stmt_confidence"], matched["stmt_balance_ok"]), ("high", "ok"))
		self.assertEqual(matched["stmt_autocorrected"], 0)
		listed = self.api.records(self.workspace)
		self.assertIn(result["name"], [row["name"] for row in listed["tasks"]])

		replay = self.create()
		self.assertTrue(replay["replayed"])
		self.assertEqual(replay["summary"], summary)
		self.assertEqual(frappe.db.count(TASK, {"workspace": self.workspace}), 1)
		self.assertEqual(frappe.db.count(ROW, {"task": result["name"]}), 4)

	def test_same_request_key_with_different_content_is_refused(self):
		self.create()
		with self.assertRaises(frappe.ValidationError):
			self.create(statement_rows=STATEMENT_ROWS[:2])

	def test_unknown_balance_round_trips_as_unknown(self):
		result = self.create(statement_rows=[{"description": "continuation page", "deposit": "10.00"}],
			gl_rows=[], header=None)
		row = self.api.detail(result["name"])["rows"][0]
		self.assertEqual((row["stmt_balance_known"], row["stmt_balance"]), (0, 0.0))
		self.assertEqual(row["match_status"], "stmt_deposit_only")

	def test_scope_and_entitlement_are_enforced(self):
		frappe.set_user(self.outsider)
		with self.assertRaises(frappe.PermissionError):
			self.create()
		frappe.set_user(self.actor)
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
		# Only an operator who may create a share reaches the registered guard.
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "DocShare", "share_doctype": TASK,
				"share_name": result["name"], "user": self.actor, "read": 1}).insert()
		frappe.set_user(self.manager)
		row = frappe.get_all(ROW, filters={"task": result["name"]}, pluck="name")[0]
		# Generic saves are refused for every caller; only the service may write.
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(ROW, row).save()
		frappe.set_user(self.actor)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(ROW, row).save()

	def test_service_delete_removes_rows_and_task(self):
		result = self.create()
		frappe.set_user(self.actor)
		removed = self.api.delete(result["name"])
		self.assertEqual(removed, {"ok": True, "rows_deleted": 4})
		self.assertFalse(frappe.db.exists(TASK, result["name"]))
		self.assertEqual(frappe.db.count(ROW, {"task": result["name"]}), 0)
		self.assertEqual(self.api.records(self.workspace)["tasks"], [])

	def test_row_values_cover_absent_layer_and_day_distance(self):
		result = self.create()
		rows = {row["match_status"]: row for row in self.api.detail(result["name"])["rows"]}
		self.assertEqual(rows["stmt_withdrawal_only"]["match_layer"], 0)
		self.assertEqual(rows["stmt_withdrawal_only"]["date_diff_days"], -1)
		self.assertEqual(rows["matched"]["date_diff_days"], 0)

if __name__ == "__main__":
	connect(os.environ.get("PEARNLY_COWORK_TEST_SITE"))
	try:
		suite = (unittest.TestSuite(NativeBankReconciliation(name) for name in sys.argv[1:])
			if len(sys.argv) > 1 else unittest.defaultTestLoader.loadTestsFromTestCase(NativeBankReconciliation))
		result = unittest.TextTestRunner(verbosity=2).run(suite)
		frappe.db.rollback()
		frappe.destroy()
		connect(os.environ.get("PEARNLY_COWORK_TEST_SITE"))
		from frappe.app import application
		client = Client(application, Response)
		base = f"http://{frappe.local.site}"
		rpc = "/api/method/pearnly_cowork.reconciliation.bank_recon_api."
		anonymous = {
			"create": client.post(rpc + "create", base_url=base, json={
				"workspace": "x", "title": "t", "request_id": "x",
				"statement_rows": [], "gl_rows": []}).status_code,
			"records": client.get(rpc + "records", base_url=base,
				query_string={"workspace": "x"}).status_code,
			"detail": client.get(rpc + "detail", base_url=base,
				query_string={"task_id": "x"}).status_code,
		}
		print(json.dumps({"anonymous_http": anonymous}))
		if set(anonymous.values()) != {403}:
			raise SystemExit(1)
		raise SystemExit(0 if result.wasSuccessful() else 1)
	finally:
		frappe.db.rollback()
		frappe.destroy()
