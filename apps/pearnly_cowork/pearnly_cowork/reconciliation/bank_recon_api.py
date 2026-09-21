"""Dedicated native reconciliation RPCs. No parsing, job runner or export here."""
import frappe

from pearnly_cowork.reconciliation import bank_recon_service


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


@frappe.whitelist()
def records(workspace, start=0, limit=50):
	return bank_recon_service.records(workspace, start, limit)


@frappe.whitelist()
def detail(task_id, start=0, limit=100):
	return _transaction(bank_recon_service.detail, task_id, start, limit)


@frappe.whitelist(methods=["POST"])
def delete(task_id):
	return _transaction(bank_recon_service.delete, task_id)
