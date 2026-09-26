"""对账中心页面接口：新建（两份文件 → 后台任务）、查阶段、列表、结果、删除。

归属（公司资料）由服务端解析，页面不传；权限在各自的对账域判（对账角色或 Cowork Admin）。
导出沿用各对账域原有的下载接口（`bank_recon_api.export` / `income_recon_api.export`）。
"""
import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.recon_center import jobs
from pearnly_cowork.reconciliation import bank_recon_service, income_recon_service, vat_check_service

TASKS = {"bank": bank_recon_service.TASKS, "income": income_recon_service.TASKS}


def _tasks(kind):
    if kind not in TASKS:
        frappe.throw(_("reconciliation.type_invalid"))
    return TASKS[kind]


@frappe.whitelist(methods=["POST"])
def start(kind, title="", gl_account_code="", lang="th"):
    """multipart：`left`、`right` 两个上传口（销项税核查每口可多份），其余是表单字段。"""
    files = frappe.request.files if frappe.request else None
    sides = {field: [(stream.filename, stream.stream.read(jobs.MAX_BYTES + 1))
                     for stream in (files.getlist(field) if files else [])] for field in ("left", "right")}
    return jobs.start(kind, title, sides, {"gl_account_code": gl_account_code, "lang": lang})


@frappe.whitelist()
def status(job):
    return jobs.status(job)


@frappe.whitelist()
def tasks(kind, start=0, limit=20, sort_by=None, sort_order=None):
    if kind == "vat":
        return vat_check_service.records(scope.default_workspace(), start, limit, sort_by, sort_order)
    return _tasks(kind).records(scope.default_workspace(), start, limit, sort_by, sort_order)


@frappe.whitelist()
def detail(kind, task_id, start=0, limit=200):
    return _tasks(kind).detail(task_id, start, limit)


@frappe.whitelist(methods=["POST"])
def delete(kind, task_ids):
    result = vat_check_service.delete_many(task_ids) if kind == "vat" else _tasks(kind).delete_many(task_ids)
    frappe.db.commit()
    return result


@frappe.whitelist(methods=["GET"])
def export_vat(task_id, lang=None):
    """销项税报告核查工作簿（由存档的两边输入当场重建，改过就是改过的版本）。"""
    from frappe.core.doctype.access_log.access_log import make_access_log

    result = vat_check_service.export(task_id, lang)
    make_access_log(doctype=vat_check_service.TASK, document=task_id, file_type="vat_check_workbook")
    frappe.local.response.update(type="download", filename=result["filename"], filecontent=result["content"],
        display_content_as="attachment",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
