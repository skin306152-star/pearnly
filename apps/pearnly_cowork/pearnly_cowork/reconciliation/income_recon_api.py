"""Dedicated native income reconciliation RPCs.

No file reading, model call or job runner here: ``create`` takes the ledger and
output-VAT rows already decoded by a caller, ``create_from_parsed`` the content a
caller already extracted from a ledger file and, optionally, from an output-VAT
report, and the reads return what the dedicated store holds.
"""
import frappe

from pearnly_cowork.reconciliation import income_recon_service


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
def create(workspace, title, request_id, gl_rows, vat_rows, header=None):
	return _transaction(income_recon_service.create, workspace, title, request_id, gl_rows,
		vat_rows, header)


@frappe.whitelist(methods=["POST"])
def create_from_parsed(workspace, title, request_id, gl_source, vat_rows=None, header=None,
		revenue_prefix="4", vat_source=None):
	return _transaction(income_recon_service.create_from_parsed, workspace, title, request_id,
		gl_source, vat_rows, header, revenue_prefix, vat_source)


@frappe.whitelist()
def records(workspace, start=0, limit=50):
	return income_recon_service.records(workspace, start, limit)


@frappe.whitelist()
def detail(task_id, start=0, limit=100):
	return _transaction(income_recon_service.detail, task_id, start, limit)


@frappe.whitelist(methods=["POST"])
def delete(task_id):
	return _transaction(income_recon_service.delete, task_id)
