"""Native income reconciliation: store the decoded rows and the pairing result.

One entry point persists one task with its result rows: ``create`` takes the two
sides already decoded by a caller, validates them with the row contract and stores
what the migrated core (``income_vat``) pairs. Reading the ledger and output-VAT
files, running OCR and building the report workbook stay in the legacy project and
are later slices of this family.

The deployed income reconciliation stored two generations of records
(``vat_recon_*`` and ``gl_vat_store``); this platform keeps one native task/row
pair instead, holding the decoded rows and the pairing verdict. Result rows follow
the VAT report, so ``row_count`` counts them and ``gl_row_count`` records how many
ledger rows went in; an absent ledger side is a zero flagged by ``has_gl_match``.
"""
import frappe
from frappe.utils import now_datetime

from pearnly_cowork.reconciliation import (
	income_recon_access,
	income_recon_contracts,
	income_vat,
	recon_tasks,
)
from pearnly_cowork.reconciliation.income_recon_access import ROW, TASK
from pearnly_cowork.reconciliation.income_recon_documents import persist

LIST_FIELDS = ("name", "title", "status", "row_count", "gl_row_count", "matched_count",
	"diff_count", "unmatched_count", "gl_total", "vat_total", "created_at")
DETAIL_FIELDS = ("name", "title", "status", "gl_account_code", "amount_tolerance", "row_count",
	"gl_row_count", "matched_count", "diff_count", "unmatched_count", "gl_total", "gl_only_credit",
	"gl_only_debit", "vat_only_positive", "vat_only_negative", "vat_total", "source_format",
	"source_digest", "created_by", "created_at")
TASK_SUMMARY_FIELDS = ("gl_total", "gl_only_credit", "gl_only_debit", "vat_only_positive",
	"vat_only_negative", "vat_total")
TASKS = recon_tasks.ReconTasks(TASK, ROW, income_recon_access, "CIR", LIST_FIELDS, DETAIL_FIELDS)


def create(workspace, title, request_id, gl_rows, vat_rows, header=None):
	"""Validate, pair and store one reconciliation inside a single transaction."""
	income_recon_access.require(workspace)
	name, request_key = TASKS.identity(request_id)
	title = income_recon_contracts.title_text(title)
	rows = income_recon_contracts.decode_rows(gl_rows, vat_rows)
	header_values = income_recon_contracts.header(header)
	request_digest = income_recon_contracts.digest(header_values, *rows)
	replayed = TASKS.replay(name, request_digest)
	if replayed:
		return replayed
	results, summary = income_vat.reconcile_gl_vat(
		income_recon_contracts.matching_gl_rows(rows[0]),
		income_recon_contracts.matching_vat_rows(rows[1]),
		amount_tolerance=float(header_values["amount_tolerance"]))
	summary_value = {field: getattr(summary, field) for field in summary.__dataclass_fields__}
	matched, findings, unmatched = result_counts(results)
	task = frappe.get_doc({"doctype": TASK, "record_id": name, "workspace": workspace,
		"title": title, "status": "completed",
		"gl_account_code": header_values["gl_account_code"],
		"source_format": income_recon_contracts.SOURCE_FORMAT, "source_digest": request_digest,
		"request_key": request_key, "request_digest": request_digest,
		"row_count": len(results), "gl_row_count": len(rows[0]),
		"matched_count": matched, "diff_count": findings, "unmatched_count": unmatched,
		"amount_tolerance": float(header_values["amount_tolerance"]),
		"summary_json": income_recon_contracts.json_dumps(summary_value),
		"created_by": frappe.session.user, "created_at": now_datetime(), **{
			field: summary_value[field] for field in TASK_SUMMARY_FIELDS}})
	persist(task)
	for index, result in enumerate(results):
		persist(frappe.get_doc({"doctype": ROW, "record_id": f"{name}-{index + 1}",
			"workspace": workspace, "task": name, "row_index": index + 1,
			**row_values(result)}))
	return {"name": name, "replayed": False, "summary": summary_value, "row_count": len(results)}


def result_counts(results):
	"""The three counters the deployed run showed beside the detail rows.

	The deployed ``/recon/gl-vat/run`` derived them outside its migrated core: a
	row without a ledger counterpart is unmatched, and of the rows that have one a
	zero difference is a match while any other difference is a finding.
	"""
	matched = sum(1 for row in results if row.gl_amount is not None and (row.diff or 0) == 0)
	findings = sum(1 for row in results if row.gl_amount is not None and (row.diff or 0) != 0)
	unmatched = sum(1 for row in results if row.gl_amount is None)
	return matched, findings, unmatched


def row_values(result):
	"""Store one pairing result; an absent ledger side is a zero the Check explains."""
	return {
		"doc_no": result.doc_no or "",
		"vat_date": result.date or "",
		"customer_name": result.customer_name or "",
		"vat_amount": result.vat_amount or 0.0,
		"has_gl_match": 0 if result.gl_amount is None else 1,
		"gl_amount": 0.0 if result.gl_amount is None else result.gl_amount,
		"diff": 0.0 if result.diff is None else result.diff,
		"account_codes": result.account_codes or "",
	}


def records(workspace, start=0, limit=50):
	return TASKS.records(workspace, start, limit)


def detail(task_id, start=0, limit=100):
	return TASKS.detail(task_id, start, limit)


def delete(task_id):
	return TASKS.delete(task_id)
