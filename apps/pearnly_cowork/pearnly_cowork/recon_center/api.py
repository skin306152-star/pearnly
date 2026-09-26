"""对账中心页面接口：新建（两份文件 → 后台任务）、查阶段、列表、结果、删除。

归属（公司资料）由服务端解析，页面不传；权限在各自的对账域判（对账角色或 Cowork Admin）。
导出沿用各对账域原有的下载接口（`bank_recon_api.export` / `income_recon_api.export`）。
"""
import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.recon_center import jobs
from pearnly_cowork.reconciliation import bank_recon_service, income_recon_service

TASKS = {"bank": bank_recon_service.TASKS, "income": income_recon_service.TASKS}


def _tasks(kind):
    if kind not in TASKS:
        frappe.throw(_("reconciliation.type_invalid"))
    return TASKS[kind]


@frappe.whitelist(methods=["POST"])
def start(kind, title="", gl_account_code="", lang="th"):
    """multipart：`left`、`right` 两个文件（左、右上传口），其余是表单字段。"""
    files = (frappe.request.files or {}) if frappe.request else {}
    picked = []
    for field in ("left", "right"):
        stream = files.get(field)
        if not stream:
            frappe.throw(_("reconciliation.files_required"))
        picked.append((stream.filename, stream.stream.read(jobs.MAX_BYTES + 1)))
    return jobs.start(kind, title, picked, {"gl_account_code": gl_account_code, "lang": lang})


@frappe.whitelist()
def status(job):
    return jobs.status(job)


@frappe.whitelist()
def tasks(kind, start=0, limit=20, sort_by=None, sort_order=None):
    return _tasks(kind).records(scope.default_workspace(), start, limit, sort_by, sort_order)


@frappe.whitelist()
def detail(kind, task_id, start=0, limit=200):
    return _tasks(kind).detail(task_id, start, limit)


@frappe.whitelist(methods=["POST"])
def delete(kind, task_ids):
    result = _tasks(kind).delete_many(task_ids)
    frappe.db.commit()
    return result
