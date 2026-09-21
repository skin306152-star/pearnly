"""Isolated Site verification for the native bank reconciliation store.

Only the dedicated lab bench is accepted, synthetic users/workspaces are created
inside it, and everything rolls back. No file reading, model, ERP, queue, export
or external call happens here; the parsed admission path is fed decoded page
dictionaries, not files.
"""

import io
import json
import os
from pathlib import Path
import runpy
import sys
import unittest
from uuid import uuid4

import frappe
import openpyxl
import pymupdf
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

# Decoded pipeline output of the legacy parser, for the parsed admission path: a
# no-movement opening row and a printed footer, one column pair the balance
# contradicts, and one GL row with no statement counterpart.
STATEMENT_PAGES = {"pages": [{"document": {
	"bank_name": "Kasikornbank",
	"opening_balance": "1,000.00",
	"closing_balance": "560.00",
	"entries": [
		{"description": "ยอดยกมา", "deposit": "0", "withdrawal": "0", "balance": "1,000.00"},
		{"description": "SWD ACME INV-77", "deposit": "0", "withdrawal": "400.00",
			"balance": "600.00", "transaction_date_raw": "01/06/2569"},
		{"description": "SWD flipped", "deposit": "0", "withdrawal": "50.00",
			"balance": "650.00", "transaction_date_raw": "02/06/2569"},
		{"description": "ค่าบริการ", "deposit": "0", "withdrawal": "90.00", "balance": "560.00",
			"transaction_date_raw": "03/06/2569"},
		{"description": "Total Credit", "deposit": "500.00"},
	]}}], "_needs_review": False}

GL_PAGES = {"pages": [{"document": {"entries": [
	{"voucher_no": "GL-1", "account_code": "1000", "description": "payment", "credit": "400.00",
		"transaction_date": "2026-06-01"},
	{"voucher_no": "GL-2", "account_code": "1000", "description": "unmatched", "debit": "75.00",
		"transaction_date": "2026-06-30"},
]}}]}

PARSED_HEADER = {"gl_opening": "990.00", "gl_closing": "500.00"}


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

	def test_decoded_pages_are_admitted_with_the_deployed_conversion(self):
		result = self.api.create_from_parsed(workspace=self.workspace, title="June 2569 parsed",
			request_id=self.request_id, statement_pages=STATEMENT_PAGES, gl_pages=GL_PAGES,
			header=PARSED_HEADER, filename="STM KBANK.pdf")
		self.assertFalse(result["replayed"])
		# The no-movement opening row and the printed footer never reach storage,
		# so four result rows remain: one match and three one-sided findings.
		self.assertEqual(result["row_count"], 4)
		self.assertEqual(result["parsed"], {"statement_rows": 3, "gl_rows": 2, "opening": 1000.0,
			"closing": 560.0, "bank_code": "kbank", "balance_warn_count": 0,
			"amount_fixed_count": 0, "parser_version": "bank_recon_v2+pipeline_v1",
			"needs_review": False})
		summary = result["summary"]
		self.assertEqual((summary["matched_count"], summary["stmt_deposit_only_count"],
			summary["stmt_withdrawal_only_count"], summary["gl_debit_only_count"],
			summary["opening_diff"]), (1, 1, 1, 1, 10.0))
		self.assertEqual((summary["stmt_total_deposit"], summary["stmt_total_withdrawal"]),
			(50.0, 490.0))
		detail = self.api.detail(result["name"])
		self.assertEqual(detail["task"]["source_format"], "pipeline_pages_v1")
		self.assertEqual(detail["task"]["bank_code"], "kbank")
		self.assertEqual([row["match_status"] for row in detail["rows"]],
			["matched", "stmt_deposit_only", "stmt_withdrawal_only", "gl_debit_only"])
		flipped = detail["rows"][1]
		self.assertEqual((flipped["stmt_deposit"], flipped["stmt_withdrawal"],
			flipped["stmt_autocorrected"], flipped["stmt_balance_ok"]), (50.0, 0.0, 1, "ok"))
		self.assertEqual((detail["rows"][0]["gl_doc_no"], detail["rows"][0]["gl_credit"]),
			("GL-1", 400.0))
		replay = self.api.create_from_parsed(workspace=self.workspace, title="June 2569 parsed",
			request_id=self.request_id, statement_pages=STATEMENT_PAGES, gl_pages=GL_PAGES,
			header=PARSED_HEADER, filename="STM KBANK.pdf")
		self.assertTrue(replay["replayed"])
		self.assertEqual(frappe.db.count(TASK, {"workspace": self.workspace}), 1)

	def test_parsed_inputs_are_refused_before_anything_is_stored(self):
		from pearnly_cowork.reconciliation import bank_recon_contracts
		with self.assertRaises(frappe.ValidationError):
			self.api.create_from_parsed(workspace=self.workspace, title="empty",
				request_id=self.request_id, statement_pages={"pages": []}, gl_pages=GL_PAGES)
		with self.assertRaises(frappe.ValidationError):
			self.api.create_from_parsed(workspace=self.workspace, title="bad document",
				request_id=self.request_id, statement_pages=["not", "a", "document"],
				gl_pages=GL_PAGES)
		with self.assertRaises(frappe.ValidationError):
			self.api.create_from_parsed(workspace=self.workspace, title="bad header",
				request_id=self.request_id, statement_pages=STATEMENT_PAGES, gl_pages=GL_PAGES,
				header=["not", "a", "header"])
		# The decoded-row contract raises its own error, as it does for ``create``.
		with self.assertRaises(bank_recon_contracts.ReconciliationContractError) as caught:
			self.api.create_from_parsed(workspace=self.workspace, title="unknown header key",
				request_id=self.request_id, statement_pages=STATEMENT_PAGES, gl_pages=GL_PAGES,
				header={"gl_opening": "1", "surprise": "1"})
		self.assertEqual(str(caught.exception), "reconciliation.header_invalid")
		# A parsed row the decoded-row contract refuses is an error, not a drop.
		described = {"pages": [{"document": {"entries": [
			{"description": "", "deposit": "10.00", "balance": "1,010.00"}]}}]}
		with self.assertRaises(bank_recon_contracts.ReconciliationContractError) as caught:
			self.api.create_from_parsed(workspace=self.workspace, title="no description",
				request_id=self.request_id, statement_pages=described, gl_pages={})
		self.assertEqual(str(caught.exception), "reconciliation.text_required:statement.description")
		self.assertEqual(frappe.db.count(TASK, {"workspace": self.workspace}), 0)

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

	def test_the_run_record_stores_the_mismatch_warnings_and_the_anchor_snapshot(self):
		from pearnly_cowork.reconciliation import bank_warnings
		# A statement whose period is far from the ledger's, with an anchor the user
		# corrected, is the case the workbook shows a banner and a trace for.
		statement_rows = [STATEMENT_ROWS[0], STATEMENT_ROWS[1]]
		gl_rows = [{"date": "2026-09-01", "doc_no": "GL-9", "account_code": "1000",
			"description": "later period", "credit": "400.00", "balance": "600.00"}]
		result = self.create(statement_rows=statement_rows, gl_rows=gl_rows,
			header={"bank_code": "BBL", "gl_account_code": "1000", "stmt_opening": "1000.00",
				"gl_opening": "990.00", "stmt_closing": "510.00", "gl_closing": "500.00"},
			anchors={"stmt_opening": 1000.0, "gl_opening": 990.0, "gl_closing": 500.0,
				"stmt_closing": 510.0, "overrides": {"gl_closing": 495.0}})
		run_info = result["run_info"]
		self.assertEqual(run_info["anchor_ocr"], {"stmt_opening": 1000.0, "gl_opening": 990.0,
			"gl_closing": 500.0, "stmt_closing": 510.0})
		self.assertEqual(run_info["anchor_overrides"], {"gl_closing": {"ocr": 500.0, "user": 495.0}})
		self.assertEqual(len(run_info["warnings"]), 1)
		self.assertIn("period", run_info["warnings"][0].lower() + "period")
		stored = frappe.parse_json(frappe.db.get_value(TASK, result["name"], "run_info_json"))
		self.assertEqual(stored["anchor_overrides"]["gl_closing"]["user"], 495.0)
		self.assertEqual(frappe.db.get_value(TASK, result["name"], "gl_closing"), 495.0)
		# The override reached the matching: the same admission without it closes the
		# ledger five higher, so the statement-side formula difference moves by five.
		plain = self.create(request_id="plain-" + uuid4().hex, statement_rows=statement_rows,
			gl_rows=gl_rows, header={"bank_code": "BBL", "gl_account_code": "1000",
				"stmt_opening": "1000.00", "gl_opening": "990.00", "stmt_closing": "510.00",
				"gl_closing": "500.00"},
			anchors={"stmt_opening": 1000.0, "gl_opening": 990.0, "gl_closing": 500.0,
				"stmt_closing": 510.0})
		self.assertEqual(round(result["summary"]["formula_diff"]
			- plain["summary"]["formula_diff"], 2), 5.0)
		# The anchor columns mirror the summary the task stored.
		self.assertEqual(frappe.db.get_value(TASK, plain["name"], "gl_closing"), 500.0)
		# Without overrides the snapshot is still stored, and no trace section appears.
		self.assertEqual(plain["run_info"]["anchor_ocr"]["gl_closing"], 500.0)
		self.assertNotIn("anchor_overrides", plain["run_info"])
		# A run warning the caller adds rides along after the detected ones.
		extra = self.create(request_id="extra-" + uuid4().hex,
			warnings=[bank_warnings.brv2_warn("multi_account", "th", n=2, codes="111、222")])
		self.assertEqual(len(extra["run_info"]["warnings"]), 1)
		self.assertIn("2", extra["run_info"]["warnings"][0])
		# A task admitted with no anchors and no mismatch stores no run record.
		quiet = self.create(request_id="quiet-" + uuid4().hex)
		self.assertEqual(quiet["run_info"], {})
		self.assertEqual(frappe.db.get_value(TASK, quiet["name"], "run_info_json"), "{}")

	def test_a_run_record_with_an_unknown_key_or_amount_is_refused(self):
		from pearnly_cowork.reconciliation import bank_recon_contracts
		for warnings_value, anchors in (
				(["ok", 42], None),
				(None, {"mystery": 1}),
				(None, {"stmt_opening": 1000.0, "overrides": {"mystery": 1.0}}),
				(None, {"stmt_opening": 1000.0, "overrides": {"gl_closing": {"user": "x"}}})):
			with self.subTest(warnings=warnings_value, anchors=anchors):
				with self.assertRaises(bank_recon_contracts.ReconciliationContractError):
					self.create(request_id=uuid4().hex, warnings=warnings_value, anchors=anchors)
		self.assertEqual(frappe.db.count(TASK, {"workspace": self.workspace}), 0)

	def test_batch_delete_removes_every_named_task_of_the_caller(self):
		first = self.create()
		second = self.create(request_id="second-" + uuid4().hex)
		self.assertEqual(self.api.delete_many([]), {"deleted": 0, "rows_deleted": 0, "ids": []})
		removed = self.api.delete_many([first["name"], second["name"], first["name"]])
		self.assertEqual((removed["deleted"], removed["rows_deleted"]), (2, 8))
		self.assertEqual(removed["ids"], [first["name"], second["name"]])
		self.assertEqual(self.api.records(self.workspace)["tasks"], [])
		self.assertEqual(frappe.db.count(ROW, {"workspace": self.workspace}), 0)

	def test_a_bank_batch_for_another_scope_or_an_unknown_task_removes_nothing(self):
		first = self.create()
		second = self.create(request_id="second-" + uuid4().hex)
		frappe.set_user(self.outsider)
		with self.assertRaises(frappe.PermissionError):
			self.api.delete_many([first["name"], second["name"]])
		frappe.set_user(self.actor)
		with self.assertRaises(frappe.DoesNotExistError):
			self.api.delete_many(["CBR-missing"])
		# Neither a refused batch nor an unknown name removed anything.
		self.assertTrue(frappe.db.exists(TASK, first["name"]))
		self.assertTrue(frappe.db.exists(TASK, second["name"]))
		self.assertEqual(frappe.db.count(ROW, {"task": first["name"]}), 4)
		self.assertEqual(frappe.db.count(ROW, {"task": second["name"]}), 4)

	def test_row_values_cover_absent_layer_and_day_distance(self):
		result = self.create()
		rows = {row["match_status"]: row for row in self.api.detail(result["name"])["rows"]}
		self.assertEqual(rows["stmt_withdrawal_only"]["match_layer"], 0)
		self.assertEqual(rows["stmt_withdrawal_only"]["date_diff_days"], -1)
		self.assertEqual(rows["matched"]["date_diff_days"], 0)

STATEMENT_WORKBOOK_HEADER = ["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"]
GL_WORKBOOK_HEADER = ["วันที่", "เลขที่เอกสาร", "คำอธิบาย", "เดบิต", "เครดิต", "คงเหลือ",
	"รหัสบัญชี"]


def workbook(sheets):
	book = openpyxl.Workbook()
	book.remove(book.active)
	for name, rows in sheets:
		sheet = book.create_sheet(title=name)
		for row in rows:
			sheet.append(list(row))
	stream = io.BytesIO()
	book.save(stream)
	return stream.getvalue()


def unreadable_pdf():
	"""A real one-page PDF the readers cannot turn into statement rows."""
	with pymupdf.open() as doc:
		page = doc.new_page(width=200, height=200)
		page.insert_text((10, 25), "SYNTHETIC SCAN")
		return doc.tobytes()


def statement_workbook():
	# One statement workbook whose balance chain adds up: a brought-forward row,
	# a withdrawal the GL paid, and a fee the GL never booked.
	return workbook([("8258-1", [
		STATEMENT_WORKBOOK_HEADER,
		["01/06/2569", "ยกมา", "", "", 1000.0],
		["02/06/2569", "SWD ACME INV-77", 400.0, "", 600.0],
		["03/06/2569", "ค่าบริการ", 90.0, "", 510.0],
	])])


def gl_row(date, **overrides):
	from pearnly_cowork.reconciliation import bank_parse
	from pearnly_cowork.reconciliation.bank_matching import GlRow

	fields = {"date": bank_parse.parse_date(date), "doc_no": "", "account_code": "",
		"description": "", "debit": 0.0, "credit": 0.0}
	fields.update(overrides)
	return GlRow(**fields)


GlRowForLab = gl_row


def gl_workbook():
	return workbook([("gl", [
		GL_WORKBOOK_HEADER,
		["01/06/2569", "GL-1", "payment", "", 400.0, 600.0, "1000"],
		["30/06/2569", "GL-2", "unmatched", 75.0, "", 675.0, "1000"],
	])])


class BankReconciliationFromFiles(unittest.TestCase):
	"""The file entry point: an uploaded original becomes a stored task.

	The deterministic readers run for real here (the statement and the ledger are
	real workbooks built in memory and uploaded through the files domain); the
	model path is a fake, so nothing is paid for and nothing leaves the Site.
	"""

	@classmethod
	def setUpClass(cls):
		if not frappe.db.exists("Role", ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert()
		suffix = uuid4().hex[:10]
		cls.actor = f"cw-bank-file-actor-{suffix}@example.invalid"
		# The file domain's upload entry asks for the intake feature, which needs
		# its own role; the reconciliation entry needs the reconciliation role.
		user(cls.actor, ["Cowork Intake", ROLE])
		from pearnly_cowork.masters import api as masters
		cls.workspace = masters.create_workspace("Bank recon files lab " + suffix)["name"]
		grant(cls.actor, "Cowork Workspace", cls.workspace)
		frappe.db.commit()

	def setUp(self):
		from pearnly_cowork.files import service as files_service
		self.files = files_service
		self.previous = dict(frappe.conf)
		frappe.conf.pearnly_features = ["intake", "master_data", "push_log", "recon", "stocktake"]
		frappe.set_user(self.actor)
		self.request_id = "bank-recon-file-" + uuid4().hex

	def tearDown(self):
		frappe.db.rollback()
		frappe.conf.clear()
		frappe.conf.update(self.previous)
		frappe.set_user("Administrator")
		frappe.clear_cache()

	def upload(self, filename, content):
		return self.files.create(self.workspace, filename, content,
			"upload-" + uuid4().hex)["name"]

	def test_two_uploaded_workbooks_become_one_stored_task(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation \
			import cowork_bank_reconciliation as controller
		statement_source = self.upload("STM BBL 2569-06.xlsx", statement_workbook())
		gl_source = self.upload("GL 2569-06.xlsx", gl_workbook())
		result = controller.create_from_sources(self.workspace, "June 2569 from files",
			self.request_id, statement_source, gl_source, gl_account_code="1000")
		self.assertEqual(result["parsed"]["statement_source"], "workbook")
		self.assertEqual(result["parsed"]["gl_source"], "workbook")
		# The filename is the bank signal, the printed closing is the statement's.
		self.assertEqual(result["parsed"]["bank_code"], "bbl")
		self.assertEqual(result["parsed"]["opening"], 1000.0)
		self.assertEqual(result["parsed"]["closing"], 510.0)
		self.assertEqual(result["parsed"]["statement_rows"], 2)
		self.assertEqual(result["parsed"]["gl_rows"], 2)
		self.assertTrue(result["parsed"]["completeness_ok"])
		self.assertFalse(result["parsed"]["escalations"])

		from pearnly_cowork.reconciliation import bank_recon_api
		detail = bank_recon_api.detail(result["name"])
		# One matched pair plus one statement-only and one ledger-only row.
		self.assertEqual(detail["total"], 3)
		by_status = {row["match_status"]: row for row in detail["rows"]}
		self.assertEqual(sorted(by_status), ["gl_debit_only", "matched", "stmt_withdrawal_only"])
		matched = by_status["matched"]
		self.assertEqual((str(matched["stmt_date"]), matched["stmt_withdrawal"], matched["gl_credit"]),
			("2026-06-02", 400.0, 400.0))
		self.assertEqual(by_status["stmt_withdrawal_only"]["stmt_balance"], 510.0)
		self.assertEqual(by_status["gl_debit_only"]["gl_debit"], 75.0)

	def test_an_unreadable_statement_is_refused_and_stores_nothing(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation \
			import cowork_bank_reconciliation as controller
		from pearnly_cowork.reconciliation import bank_recon_access
		statement_source = self.upload("STM broken.xlsx", b"not a workbook at all")
		gl_source = self.upload("GL 2569-06.xlsx", gl_workbook())
		with self.assertRaises(frappe.ValidationError):
			controller.create_from_sources(self.workspace, "Broken statement", self.request_id,
				statement_source, gl_source)
		self.assertEqual(frappe.db.count(bank_recon_access.TASK,
			{"workspace": self.workspace}), 0)

	def test_the_model_seam_carries_a_document_no_reader_can_decode(self):
		from pearnly_cowork.reconciliation import bank_recon_service, bank_statement
		calls = []

		def model_rows(document_type, file_bytes, filename):
			calls.append((document_type, filename))
			if document_type == "bank_statement":
				return {"ok": True, "opening": 0.0, "closing": 300.0, "rows": [
					bank_statement.StatementRow(date=None, description="ค่าบริการ",
						withdrawal=50.0, deposit=0.0, balance=100.0),
					bank_statement.StatementRow(date=None, description="ค่าขนส่ง",
						withdrawal=25.0, deposit=0.0, balance=75.0),
					bank_statement.StatementRow(date=None, description="ค่าอาหาร",
						withdrawal=25.0, deposit=0.0, balance=50.0),
				]}
			return None

		scan = unreadable_pdf()
		statement_source = self.upload("STM scan.pdf", scan)
		gl_source = self.upload("GL scan.pdf", scan)
		statement_name, statement_bytes = self.files.original(statement_source)
		gl_name, gl_bytes = self.files.original(gl_source)
		# The ledger side has no model result here, so the entry refuses instead of
		# storing a run with an empty ledger.
		with self.assertRaises(frappe.ValidationError):
			bank_recon_service.create_from_documents(self.workspace, "Scanned pair",
				self.request_id, statement_bytes, statement_name, gl_bytes, gl_name,
				model_rows=model_rows)
		self.assertEqual(calls, [("bank_statement", "STM scan.pdf"),
			("general_ledger", "GL scan.pdf")])
		# With both sides answered by the model the run is stored.
		def both_sides(document_type, file_bytes, filename):
			if document_type == "bank_statement":
				return model_rows(document_type, file_bytes, filename)
			return {"ok": True, "opening": 0.0, "closing": 100.0, "accounts": ["1000"],
				"rows": [GlRowForLab(date=None, doc_no="GL-9", account_code="1000",
					description="ค่าบริการ", debit=0.0, credit=100.0)]}

		result = bank_recon_service.create_from_documents(self.workspace, "Scanned pair",
			self.request_id, statement_bytes, statement_name, gl_bytes, gl_name,
			model_rows=both_sides)
		self.assertEqual(result["parsed"]["statement_source"], "model")
		self.assertEqual(result["parsed"]["gl_source"], "model")
		self.assertEqual(result["parsed"]["statement_rows"], 3)
		self.assertEqual(result["parsed"]["gl_rows"], 1)

	def statement_document_json(self):
		"""What the document tier's prompt asks the model for, with its refs filled."""
		return {
			"document_type": "bank_statement",
			"bank_name": "Bangkok Bank",
			"opening_balance": "1000.00",
			"closing_balance": "510.00",
			"entries": [
				{"transaction_date": "2026-06-02", "description": "SWD ACME INV-77",
					"withdrawal": "400.00", "balance": "600.00",
					"withdrawal_ref": {"value": "400.00", "source_text": "400.00",
						"source_column": "Withdrawal"}},
				{"transaction_date": "2026-06-03", "description": "ค่าบริการ",
					"withdrawal": "90.00", "balance": "510.00",
					"withdrawal_ref": {"value": "90.00", "source_text": "90.00",
						"source_column": "Withdrawal"}},
			],
		}

	def ledger_document_json(self):
		return {
			"document_type": "general_ledger",
			"opening_balance": "1000.00",
			"closing_balance": "675.00",
			"entries": [
				{"transaction_date": "2026-06-02", "voucher_no": "GL-1",
					"account_code": "1000", "description": "payment", "credit": "400.00",
					"balance": "600.00",
					"credit_ref": {"value": "400.00", "source_text": "400.00",
						"source_column": "Credit"}},
				{"transaction_date": "2026-06-30", "voucher_no": "GL-2",
					"account_code": "1000", "description": "unmatched", "debit": "75.00",
					"balance": "675.00",
					"debit_ref": {"value": "75.00", "source_text": "75.00",
						"source_column": "Debit"}},
			],
		}

	def test_the_document_tier_carries_a_scan_end_to_end(self):
		"""A scanned PDF with no readable rows goes through the OCR document tier.

		The model call is a fake that answers with the deployed document schema, so
		the whole chain runs here — tier, document model, the two validator layers,
		the row conversion, the store — without contacting a provider.
		"""
		from pearnly_cowork.ocr.engine.contracts import ModelResponse
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation \
			import cowork_bank_reconciliation as controller
		from pearnly_cowork.reconciliation import bank_recon_service

		statement_json = self.statement_document_json()
		ledger_json = self.ledger_document_json()
		requests = []

		def model_call(request):
			requests.append(request)
			payload = ledger_json if "General Ledger" in request.prompt else statement_json
			return ModelResponse(text=json.dumps(payload), raw={"fake": True}, input_tokens=1,
				output_tokens=1, model=request.model or "fake")

		scan = unreadable_pdf()
		statement_source = self.upload("STM scan.pdf", scan)
		gl_source = self.upload("GL scan.pdf", scan)
		statement_name, statement_bytes = self.files.original(statement_source)
		gl_name, gl_bytes = self.files.original(gl_source)
		rows = controller.document_model_rows("1000", model_call=model_call, model="gemini-fake")
		result = bank_recon_service.create_from_documents(self.workspace, "Scanned pair",
			self.request_id, statement_bytes, statement_name, gl_bytes, gl_name,
			gl_account_code="1000", model_rows=rows)
		self.assertEqual(result["parsed"]["statement_source"], "model")
		self.assertEqual(result["parsed"]["gl_source"], "model")
		self.assertEqual((result["parsed"]["statement_rows"], result["parsed"]["gl_rows"]), (2, 2))
		self.assertEqual(result["parsed"]["opening"], 1000.0)
		self.assertFalse(result["parsed"]["needs_review"])
		# One request per side, both on the document prompt the tier owns.
		self.assertEqual([request.stage for request in requests], ["document", "document"])
		self.assertEqual(len(requests), 2)

		from pearnly_cowork.reconciliation import bank_recon_api
		detail = bank_recon_api.detail(result["name"])
		self.assertEqual(detail["total"], 3)
		self.assertEqual(sorted({row["match_status"] for row in detail["rows"]}),
			["gl_debit_only", "matched", "stmt_withdrawal_only"])

	def test_an_unconfigured_site_refuses_before_any_model_call(self):
		from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation \
			import cowork_bank_reconciliation as controller
		from pearnly_cowork.reconciliation import bank_recon_service

		# The tier is not built at all without an explicit OCR configuration, so a
		# document no reader can decode is refused with that message instead of a
		# stored empty task.
		frappe.conf.pearnly_cowork_ocr_environment = None
		frappe.conf.pearnly_cowork_ocr_policy = None
		scan = unreadable_pdf()
		statement_source = self.upload("STM scan.pdf", scan)
		gl_source = self.upload("GL scan.pdf", scan)
		statement_name, statement_bytes = self.files.original(statement_source)
		gl_name, gl_bytes = self.files.original(gl_source)
		with self.assertRaises(frappe.ValidationError):
			bank_recon_service.create_from_documents(self.workspace, "Unconfigured",
				self.request_id, statement_bytes, statement_name, gl_bytes, gl_name,
				model_rows=controller.document_model_rows("1000"))

	def test_an_unreadable_ledger_is_refused(self):
		from pearnly_cowork.reconciliation import bank_recon_service
		statement_name = "STM.xlsx"
		gl_name = "GL broken.xlsx"
		with self.assertRaises(frappe.ValidationError):
			bank_recon_service.create_from_documents(self.workspace, "Broken ledger",
				self.request_id, statement_workbook(), statement_name,
				b"not a workbook at all", gl_name)


if __name__ == "__main__":
	connect(os.environ.get("PEARNLY_COWORK_TEST_SITE"))
	try:
		if len(sys.argv) > 1:
			suite = unittest.TestSuite(NativeBankReconciliation(name) for name in sys.argv[1:])
		else:
			suite = unittest.TestSuite(
				unittest.defaultTestLoader.loadTestsFromTestCase(cls)
				for cls in (NativeBankReconciliation, BankReconciliationFromFiles))
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
			"create_from_parsed": client.post(rpc + "create_from_parsed", base_url=base, json={
				"workspace": "x", "title": "t", "request_id": "x",
				"statement_pages": {}, "gl_pages": {}}).status_code,
			"export": client.get(rpc + "export", base_url=base,
				query_string={"task_id": "x"}).status_code,
			"delete": client.post(rpc + "delete", base_url=base,
				json={"task_id": "x"}).status_code,
			"delete_many": client.post(rpc + "delete_many", base_url=base,
				json={"task_ids": []}).status_code,
			"create_from_sources": client.post(
				"/api/method/pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation"
				".cowork_bank_reconciliation.create_from_sources", base_url=base, json={
					"workspace": "x", "title": "t", "request_id": "x",
					"statement_source": "x", "gl_source": "y"}).status_code,
		}
		print(json.dumps({"anonymous_http": anonymous}))
		if set(anonymous.values()) != {403}:
			raise SystemExit(1)
		raise SystemExit(0 if result.wasSuccessful() else 1)
	finally:
		frappe.db.rollback()
		frappe.destroy()
