"""Native bank reconciliation: store decoded rows and the deployed matching result.

Runs the already-migrated pure matching core over validated decoded rows and
persists one task with its result rows. Not migrated here: statement/GL file
parsing, model extraction, asynchronous job orchestration, exports and the
accountant review actions.
"""
import hashlib

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.reconciliation import bank_matching, bank_recon_access, bank_recon_contracts
from pearnly_cowork.reconciliation.bank_recon_access import ROW, TASK
from pearnly_cowork.reconciliation.bank_recon_documents import persist

ROW_LIMIT = 200
LIST_LIMIT = 100


def _identity(request_id):
	if (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 120):
		frappe.throw(_("reconciliation.request_invalid"))
	key = hashlib.sha256(
		f"{frappe.local.site}|{frappe.session.user}|{request_id}".encode()).hexdigest()
	return "CBR-" + key[:40], key


def create(workspace, title, request_id, statement_rows, gl_rows, header=None, reconciliation_type="bank"):
	"""Validate, match and store one reconciliation inside a single transaction."""
	bank_recon_access.require(workspace)
	name, request_key = _identity(request_id)
	title = bank_recon_contracts.title_text(title)
	if reconciliation_type not in ("bank", "income"):
		frappe.throw(_("reconciliation.type_invalid"))
	rows = bank_recon_contracts.decode_rows(statement_rows, gl_rows)
	header_values = bank_recon_contracts.header(header)
	request_digest = bank_recon_contracts.digest(header_values, *rows)
	existing = frappe.db.get_value(TASK, name, ["request_digest", "summary_json"], as_dict=True)
	if existing:
		if existing.request_digest != request_digest:
			frappe.throw(_("reconciliation.request_conflict"))
		return {"name": name, "replayed": True,
			"summary": bank_recon_contracts.json_loads(existing.summary_json)}
	results, summary = bank_matching.reconcile(
		bank_recon_contracts.matching_statement_rows(rows[0]),
		bank_recon_contracts.matching_gl_rows(rows[1]),
		stmt_opening=float(header_values["stmt_opening"]), gl_opening=float(header_values["gl_opening"]),
		stmt_closing=float(header_values["stmt_closing"]), gl_closing=float(header_values["gl_closing"]),
		bank_code=header_values["bank_code"], gl_account_code=header_values["gl_account_code"])
	summary_value = {field: getattr(summary, field) for field in summary.__dataclass_fields__}
	task = frappe.get_doc({"doctype": TASK, "record_id": name, "workspace": workspace,
		"title": title, "reconciliation_type": reconciliation_type,
		"status": "completed",
		"bank_code": header_values["bank_code"], "gl_account_code": header_values["gl_account_code"],
		"source_format": bank_recon_contracts.SOURCE_FORMAT, "source_digest": request_digest,
		"request_key": request_key, "request_digest": request_digest,
		"row_count": len(results),
		"summary_json": bank_recon_contracts.json_dumps(summary_value),
		"created_by": frappe.session.user, "created_at": now_datetime(), **{
			field: summary_value[field] for field in TASK_SUMMARY_FIELDS}})
	persist(task)
	for index, result in enumerate(results):
		persist(frappe.get_doc({"doctype": ROW, "record_id": f"{name}-{index + 1}",
			"workspace": workspace, "task": name, "row_index": index + 1,
			**row_values(result)}))
	return {"name": name, "replayed": False, "summary": summary_value, "row_count": len(results)}


TASK_SUMMARY_FIELDS = ("matched_count", "gl_debit_only_count", "gl_credit_only_count",
	"stmt_withdrawal_only_count", "stmt_deposit_only_count", "stmt_total_deposit",
	"stmt_total_withdrawal", "gl_total_credit", "gl_total_debit", "gl_debit_only_amount",
	"gl_credit_only_amount", "stmt_withdrawal_only_amount", "stmt_deposit_only_amount",
	"opening_diff", "formula_stmt_closing", "formula_diff")


def row_values(result):
	"""Store one matching result; -1 marks an absent layer or day distance."""
	return {
		"match_status": result.match_status,
		"match_layer": 0 if result.match_layer is None else int(result.match_layer),
		"date_diff_days": -1 if result.date_diff_days is None else int(result.date_diff_days),
		"stmt_date": result.stmt_date or "",
		"stmt_desc": result.stmt_desc or "",
		"stmt_withdrawal": result.stmt_withdrawal or 0.0,
		"stmt_deposit": result.stmt_deposit or 0.0,
		"stmt_balance": result.stmt_balance or 0.0,
		"stmt_balance_known": 0 if result.stmt_balance is None else 1,
		"stmt_confidence": result.stmt_confidence or "high",
		"stmt_balance_ok": "" if result.stmt_balance_ok is None else ("ok" if result.stmt_balance_ok else "mismatch"),
		"stmt_autocorrected": 1 if result.stmt_autocorrected else 0,
		"gl_date": result.gl_date or "",
		"gl_doc_no": result.gl_doc_no or "",
		"gl_account_code": result.gl_account_code or "",
		"gl_desc": result.gl_desc or "",
		"gl_debit": result.gl_debit or 0.0,
		"gl_credit": result.gl_credit or 0.0,
		"gl_balance": result.gl_balance or 0.0,
		"source_stmt_file": result.source_stmt_file or "",
		"source_gl_file": result.source_gl_file or "",
	}


def records(workspace, start=0, limit=50):
	bank_recon_access.require(workspace)
	try:
		start, limit = max(0, int(start)), max(1, min(int(limit), LIST_LIMIT))
	except (TypeError, ValueError):
		frappe.throw(_("reconciliation.request_invalid"))
	rows = frappe.get_all(TASK, filters={"workspace": workspace},
		fields=["name", "title", "reconciliation_type", "bank_code", "status", "row_count",
			"matched_count", "formula_diff", "created_at"],
		order_by="creation desc, name desc", limit_start=start, limit_page_length=limit)
	return {"tasks": rows, "total": frappe.db.count(TASK, {"workspace": workspace})}


def detail(task_id, start=0, limit=100):
	doc = frappe.get_doc(TASK, task_id)
	bank_recon_access.require(doc.workspace)
	try:
		start, limit = max(0, int(start)), max(1, min(int(limit), ROW_LIMIT))
	except (TypeError, ValueError):
		frappe.throw(_("reconciliation.request_invalid"))
	rows = frappe.get_all(ROW, filters={"task": doc.name}, fields=["*"],
		order_by="row_index asc", limit_start=start, limit_page_length=limit)
	return {"task": {field: doc.get(field) for field in (
			"name", "title", "reconciliation_type", "bank_code", "gl_account_code", "status",
			"row_count", "source_format", "source_digest", "created_by", "created_at")},
		"summary": bank_recon_contracts.json_loads(doc.summary_json),
		"rows": rows, "total": frappe.db.count(ROW, {"task": doc.name})}


def delete(task_id):
	doc = frappe.get_doc(TASK, task_id)
	bank_recon_access.require(doc.workspace)
	removed = frappe.db.count(ROW, {"task": doc.name})
	frappe.db.delete(ROW, {"task": doc.name})
	frappe.db.delete(TASK, {"name": doc.name})
	return {"ok": True, "rows_deleted": removed}
