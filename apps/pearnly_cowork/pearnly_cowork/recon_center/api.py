"""对账中心页面接口：新建（两份文件 → 后台任务）、查阶段、列表、结果、删除。

归属（公司资料）由服务端解析，页面不传；权限在各自的对账域判（对账角色或 Cowork Admin）。
导出沿用各对账域原有的下载接口（`bank_recon_api.export` / `income_recon_api.export`）。
"""
import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.recon_center import jobs, progress, rerun
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
    # 和发票上传同样：撞上同租户并发写原件表的 1020 / 死锁，回滚整段重来（入队在最后一步，之前失败不会重复排任务）。
    for attempt in range(3):
        try:
            return jobs.start(kind, title, sides, {"gl_account_code": gl_account_code, "lang": lang})
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist()
def status(job):
    return jobs.status(job)


@frappe.whitelist()
def active(kind):
    """本人进行中的对账（列表页「进行中」那一页，点进去接着看进度）：标题、阶段、开始时间、总页数、已读完页数。"""
    found = []
    for item in progress.active(frappe.session.user, kind):
        try:
            state = jobs.status(item["job"])
        except frappe.DoesNotExistError:
            frappe.clear_last_message()
            progress.active_remove(frappe.session.user, item["job"])
            continue
        if state.get("state") not in ("queued", "running"):
            progress.active_remove(frappe.session.user, item["job"])
            continue
        files = state.get("files") or []
        found.append({"job": item["job"], "title": item.get("title") or "", "stage": state.get("stage"),
                      "started": item.get("started") or "",
                      "pages": sum(entry["pages"] for entry in files),
                      "done": sum(min(entry["done"] + len(entry["unread"]), entry["pages"]) for entry in files)})
    return found


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
    # 页面把勾选的编号以 JSON 字符串发来（表单参数），服务层只收列表：这里先解开（2026-09-28 录制发现批量删除会失败）。
    if isinstance(task_ids, str):
        task_ids = frappe.parse_json(task_ids) if task_ids.strip().startswith("[") else task_ids
    result = vat_check_service.delete_many(task_ids) if kind == "vat" else _tasks(kind).delete_many(task_ids)
    from pearnly_cowork.recon_center import view_store

    for task in result.get("ids") or []:
        rerun.drop(task)  # 等着答预设问题而留着的原文件一起删
        view_store.drop(task)  # 结果页存档一起删
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
