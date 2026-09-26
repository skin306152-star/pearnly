"""对账后台任务：接两份文件 → 排队 → 按阶段报进度 → 出任务（照旧线上 recon_jobs 的做法）。

旧线上提交后拿 job_id，前端每 1.5 秒问一次状态，只显示阶段文字（解析 / 核对 / 保存），不显示百分比；
用户 2026-09-26 定的也是“只显示阶段文字”。这里同样：状态只有 queued / running / done / failed，
另带当前阶段；PDF 走识别时可能要几十秒，所以放到后台队列，不占网页请求。

两份文件先存成本人的私有 File（不挂任何单据），任务读完即删；状态放缓存（24 小时），
只有发起的人能查。对账结果本身落在各自的任务表里，缓存丢了也不影响结果。
"""
import json
import uuid

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.reconciliation import bank_recon_access, bank_recon_service, income_recon_access
from pearnly_cowork.reconciliation import income_recon_service

KINDS = {"bank": bank_recon_access, "income": income_recon_access}
MAX_BYTES = 25 * 1024 * 1024
TTL = 24 * 3600


def _key(job_id):
    return f"cowork:recon-job:{job_id}"


def _state(job_id, **values):
    current = frappe.cache.get_value(_key(job_id)) or {}
    current.update(values)
    frappe.cache.set_value(_key(job_id), current, expires_in_sec=TTL)
    return current


def _save(content, filename):
    from frappe.utils.file_manager import save_file

    return save_file(filename, content, None, None, is_private=1).name


def start(kind, title, files, options=None):
    """files: [(filename, bytes), (filename, bytes)]——左、右两个上传口，顺序固定。"""
    if kind not in KINDS:
        frappe.throw(_("reconciliation.type_invalid"))
    workspace = scope.default_workspace()
    KINDS[kind].require(workspace)
    title = str(title or "").strip() or " / ".join(name for name, _content in files)
    if len(files) != 2 or any(not content for _name, content in files):
        frappe.throw(_("reconciliation.files_required"))
    if any(len(content) > MAX_BYTES for _name, content in files):
        frappe.throw(_("reconciliation.file_too_large"))
    job_id = uuid.uuid4().hex
    saved = [_save(content, name) for name, content in files]
    _state(job_id, user=frappe.session.user, kind=kind, state="queued", stage="queued", task=None, error=None)
    frappe.enqueue("pearnly_cowork.recon_center.jobs.run", queue="long", timeout=1800, job_id=f"cowork-recon-{job_id}",
                   enqueue_after_commit=True, recon_job=job_id, kind=kind, workspace=workspace, title=title[:140],
                   files=saved, options=json.dumps(options or {}))
    return {"job": job_id}


def status(job_id):
    current = frappe.cache.get_value(_key(str(job_id or "")))
    if not current or current.get("user") != frappe.session.user:
        frappe.throw(_("reconciliation.task_not_found"), frappe.DoesNotExistError)
    return {key: current.get(key) for key in ("kind", "state", "stage", "task", "error")}


def _read(names):
    out = []
    for name in names:
        doc = frappe.get_doc("File", name)
        content = doc.get_content()
        out.append((doc.file_name, content if isinstance(content, bytes) else content.encode()))
    return out


def run(recon_job, kind, workspace, title, files, options="{}"):
    options = json.loads(options or "{}")
    stage = lambda name: _state(recon_job, state="running", stage=name)  # noqa: E731
    try:
        stage("reading")
        (left_name, left), (right_name, right) = _read(files)
        request_id = recon_job
        if kind == "bank":
            from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
                document_model_rows,
            )

            account = str(options.get("gl_account_code") or "")
            result = bank_recon_service.create_from_documents(workspace, title, request_id, left, left_name,
                right, right_name, gl_account_code=account, model_rows=document_model_rows(account),
                lang=options.get("lang") or "th", on_stage=stage)
        else:
            from pearnly_cowork.recon_center import sources

            stage("ledger")
            gl_source = sources.ledger_source(left, left_name)
            stage("report")
            vat_source = sources.report_source(right, right_name)
            stage("matching")
            result = income_recon_service.create_from_parsed(workspace, title, request_id, gl_source,
                vat_source=vat_source, gl_filename=left_name, vat_filename=right_name)
        frappe.db.commit()
        _state(recon_job, state="done", stage="done", task=result["name"])
    except Exception as error:
        frappe.db.rollback()
        message = _(str(error)) if str(error) else _("reconciliation.failed")
        if not isinstance(error, (frappe.ValidationError, ValueError)):
            frappe.log_error(title="cowork reconciliation job", message=frappe.get_traceback())
        _state(recon_job, state="failed", stage="failed", error=frappe.utils.strip_html(message)[:500])
    finally:
        for name in files:
            if frappe.db.exists("File", name):
                frappe.delete_doc("File", name, ignore_permissions=True, force=True)
        frappe.db.commit()
