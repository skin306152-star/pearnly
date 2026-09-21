"""Dedicated native reconciliation RPCs.

No file reading, model call or job runner here: ``create`` takes decoded rows,
``create_from_parsed`` the already-decoded pages of the legacy parser, and ``export``
returns the review workbook of one stored task as a private download.
"""
import frappe
from frappe.core.doctype.access_log.access_log import make_access_log

from pearnly_cowork.reconciliation import bank_recon_service
from pearnly_cowork.reconciliation.bank_recon_access import TASK


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
def create(workspace, title, request_id, statement_rows, gl_rows, header=None,
		reconciliation_type="bank"):
	return _transaction(bank_recon_service.create, workspace, title, request_id,
		statement_rows, gl_rows, header, reconciliation_type)


@frappe.whitelist(methods=["POST"])
def create_from_parsed(workspace, title, request_id, statement_pages, gl_pages, header=None,
		filename="", gl_account_code="", reconciliation_type="bank", gl_filename=""):
	return _transaction(bank_recon_service.create_from_parsed, workspace, title, request_id,
		statement_pages, gl_pages, header, filename, gl_account_code, reconciliation_type,
		gl_filename)


@frappe.whitelist(methods=["GET"])
def export(task_id, lang="th"):
	result = _transaction(bank_recon_service.export, task_id, lang)
	make_access_log(doctype=TASK, document=task_id, file_type="bank_recon_workbook")
	frappe.local.response.update(type="download", filename=result["filename"],
		filecontent=result["content"], display_content_as="attachment",
		content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
	frappe.local.cowork_private_response = True


@frappe.whitelist()
def records(workspace, start=0, limit=50):
	return bank_recon_service.records(workspace, start, limit)


@frappe.whitelist()
def detail(task_id, start=0, limit=100):
	return _transaction(bank_recon_service.detail, task_id, start, limit)


@frappe.whitelist(methods=["POST"])
def delete(task_id):
	return _transaction(bank_recon_service.delete, task_id)
