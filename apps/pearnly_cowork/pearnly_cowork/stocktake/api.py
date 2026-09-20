"""Dedicated native stocktake RPCs; no files, photos, LINE or external posting."""
import frappe
from pearnly_cowork.stocktake import service


def _transaction(function, *args, **kwargs):
    # MariaDB can report snapshot conflict 1020 even after waiting for a task
    # lock. An RPC owns only this local transaction and can restart it, retaining
    # the caller's request/operation ID. Internal workflows call service directly
    # and remain responsible for their larger transaction boundary.
    for attempt in range(3):
        try:
            return function(*args, **kwargs)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=["POST"])
def create(workspace, title, request_id, header, rows):
    return _transaction(service.create, workspace, title, request_id, header, rows)


@frappe.whitelist()
def records(workspace, start=0, limit=50):
    return service.records(workspace, start, limit)


@frappe.whitelist()
def detail(task_id):
    return _transaction(service.detail, task_id)


@frappe.whitelist()
def entries(task_id, start=0, limit=100):
    return _transaction(service.entries, task_id, start, limit)


@frappe.whitelist(methods=["POST"])
def add_entry(task_id, item_id, request_id, quantity, warehouse, location="", photos=None):
    if service.parse(photos):
        service.fail("stocktake.photos_not_supported")
    return _transaction(service.write, task_id, item_id, request_id, quantity, warehouse, location)


@frappe.whitelist(methods=["POST"])
def edit_entry(task_id, entry_id, request_id, quantity, warehouse, version, location="", voided=False, photos=None):
    if service.parse(photos):
        service.fail("stocktake.photos_not_supported")
    return _transaction(service.write, task_id, entry_id, request_id, quantity, warehouse, location,
                        version=version, voided=voided)


@frappe.whitelist(methods=["POST"])
def close(task_id):
    return _transaction(service.close, task_id)


@frappe.whitelist()
def report(task_id):
    return _transaction(service.report, task_id)
