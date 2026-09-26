"""Dedicated native stocktake RPCs and private scan workbook download."""
import frappe
from frappe.core.doctype.access_log.access_log import make_access_log
from pearnly_cowork.stocktake import counting, service, upload


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


@frappe.whitelist()
def lookup(task_id, code=None, item_id=None):
    return _transaction(counting.lookup, task_id, code, item_id)


@frappe.whitelist()
def item_entries(task_id, item_id):
    return _transaction(counting.item_entries, task_id, item_id)


@frappe.whitelist()
def entry_photos(task_id, entry_id):
    return _transaction(counting.entry_photos, task_id, entry_id)


@frappe.whitelist(methods=["POST"])
def recount(task_id, item_id):
    return _transaction(counting.recount, task_id, item_id)


@frappe.whitelist(methods=["POST"])
def close(task_id):
    return _transaction(service.close, task_id)


@frappe.whitelist()
def report(task_id):
    return _transaction(service.report, task_id)


@frappe.whitelist(methods=["GET"])
def export(task_id):
    content = _transaction(service.export, task_id)
    make_access_log(doctype="Cowork Stocktake Task", document=task_id, file_type="stocktake_workbook")
    frappe.local.response.update(type="download", filename="cowork-stocktake.xlsx", filecontent=content,
        display_content_as="attachment", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    frappe.local.cowork_private_response = True


# ── 上传盘点（Cowork 不做在线盘点，见 stocktake.upload）────────────────────────


@frappe.whitelist(methods=["POST"])
def upload_count(title, request_id):
    """multipart：`file` 是盘点表（xlsx），`title` 任务名，`request_id` 一次提交一个 UUID。"""
    stream = (frappe.request.files or {}).get("file") if frappe.request else None
    if not stream:
        service.fail("stocktake.file_invalid")
    content = stream.stream.read(upload.MAX_BYTES + 1)
    return _transaction(upload.create, title, request_id, content)


@frappe.whitelist()
def tasks(start=0, limit=20, sort_by="created_at", sort_order="desc"):
    return upload.records(start, limit, sort_by, sort_order)


@frappe.whitelist()
def task(task_id):
    return _transaction(upload.detail, task_id)


@frappe.whitelist(methods=["POST"])
def delete(task_ids):
    return _transaction(upload.delete, task_ids)


@frappe.whitelist(methods=["GET"])
def template():
    content = upload.template()
    frappe.local.response.update(type="download", filename="cowork-stock-count-template.xlsx",
        filecontent=content, display_content_as="attachment",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
