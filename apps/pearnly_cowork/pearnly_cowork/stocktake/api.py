"""Dedicated native stocktake RPCs and private scan workbook download."""
import frappe
from frappe.core.doctype.access_log.access_log import make_access_log
from pearnly_cowork.stocktake import counting, service


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


def _write_with_photos(task_id, photos, *args, **kwargs):
    # 记录与照片同一个事务：照片挂不上（超过 5 张、不是本人的文件）整笔不落。
    result = service.write(task_id, *args, **kwargs)
    counting.attach_photos(task_id, result["entry_id"], photos)
    return result


@frappe.whitelist(methods=["POST"])
def add_entry(task_id, item_id, request_id, quantity, warehouse, location="", photos=None):
    return _transaction(_write_with_photos, task_id, photos, item_id, request_id, quantity, warehouse, location)


@frappe.whitelist(methods=["POST"])
def edit_entry(task_id, entry_id, request_id, quantity, warehouse, version, location="", voided=False, photos=None):
    return _transaction(_write_with_photos, task_id, photos, entry_id, request_id, quantity, warehouse, location,
                        version=version, voided=voided)


@frappe.whitelist(methods=["POST"])
def close(task_id):
    return _transaction(service.close, task_id)


@frappe.whitelist()
def report(task_id):
    return _transaction(service.report, task_id)


@frappe.whitelist(methods=["GET"])
def export(task_id):
    """小的当场回文件，大的排后台、做完推送下载（``pearnly_core.exports``，权限在排任务时和下载时各查一次）。"""
    from pearnly_core.exports import service as exports

    frappe.local.cowork_private_response = True
    job = exports.deliver(count="pearnly_cowork.stocktake.service.export_size",
        build="pearnly_cowork.stocktake.service.export", args={"task_id": task_id},
        filename="cowork-stocktake.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    make_access_log(doctype="Cowork Stocktake Task", document=task_id, file_type="stocktake_workbook")
    return job
