"""Dedicated native income reconciliation RPCs.

No file reading, model call or job runner here: ``create`` takes the ledger and
output-VAT rows already decoded by a caller, ``create_from_parsed`` the content a
caller already extracted from a ledger file and, optionally, from an output-VAT
report, and the reads return what the dedicated store holds.
"""
import frappe
from frappe.core.doctype.access_log.access_log import make_access_log

from pearnly_cowork.reconciliation import income_recon_service
from pearnly_cowork.reconciliation.income_recon_access import TASK


def _transaction(function, *args, **kwargs):
	# MariaDB can report snapshot conflict 1020 even after waiting for a task
	# lock. An RPC owns only this local transaction and can restart it, keeping
	# the caller's request id. Internal callers use the service directly.
	for attempt in range(3):
		try:
			return function(*args, **kwargs)
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt == 2:
				raise


@frappe.whitelist(methods=["POST"])
def create(workspace, title, request_id, gl_rows, vat_rows, header=None, parse_info=None,
		gl_filename="", vat_filename=""):
	return _transaction(income_recon_service.create, workspace, title, request_id, gl_rows,
		vat_rows, header, parse_info, gl_filename, vat_filename)


@frappe.whitelist(methods=["POST"])
def create_from_parsed(workspace, title, request_id, gl_source, vat_rows=None, header=None,
		revenue_prefix="4", vat_source=None, gl_filename="", vat_filename=""):
	return _transaction(income_recon_service.create_from_parsed, workspace, title, request_id,
		gl_source, vat_rows, header, revenue_prefix, vat_source, gl_filename, vat_filename)


@frappe.whitelist()
def records(workspace, start=0, limit=50):
	return income_recon_service.records(workspace, start, limit)


@frappe.whitelist()
def detail(task_id, start=0, limit=100):
	return _transaction(income_recon_service.detail, task_id, start, limit)


@frappe.whitelist(methods=["GET"])
def export(task_id, lang="th"):
	result = _transaction(income_recon_service.export, task_id, lang)
	make_access_log(doctype=TASK, document=task_id, file_type="income_recon_workbook")
	frappe.local.response.update(type="download", filename=result["filename"],
		filecontent=result["content"], display_content_as="attachment",
		content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
	frappe.local.cowork_private_response = True


@frappe.whitelist(methods=["POST"])
def delete(task_id):
	return _transaction(income_recon_service.delete, task_id)


@frappe.whitelist(methods=["POST"])
def delete_many(task_ids):
	return _transaction(income_recon_service.delete_many, task_ids)
