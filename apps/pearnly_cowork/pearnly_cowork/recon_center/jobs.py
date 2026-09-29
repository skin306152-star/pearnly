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
from pearnly_cowork.reconciliation import income_recon_service, vat_check_access

KINDS = {"bank": bank_recon_access, "income": income_recon_access, "vat": vat_check_access}
# 每个上传口可以放几份：收入各一份；销项税核查的报告可多份（同卖方同期间合并）、发票可很多张；
# 银行对账两边都可多份（旧线上 `bank_v2_submit` 收多个账单 / 总账文件逐份读再合并，存折一页一张照片就靠它）。
MULTIPLE = {"vat", "bank"}
MAX_FILES = 200
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


def start(kind, title, sides, options=None):
    """sides: {"left": [(filename, bytes), ...], "right": [...]}——左、右两个上传口。"""
    if kind not in KINDS:
        frappe.throw(_("reconciliation.type_invalid"))
    workspace = scope.default_workspace()
    KINDS[kind].require(workspace)
    everything = [item for side in ("left", "right") for item in sides.get(side) or []]
    if (any(not sides.get(side) for side in ("left", "right")) or any(not content for _name, content in everything)
            or (kind not in MULTIPLE and any(len(sides[side]) != 1 for side in ("left", "right")))
            or len(everything) > MAX_FILES):
        frappe.throw(_("reconciliation.files_required"))
    if any(len(content) > MAX_BYTES for _name, content in everything):
        frappe.throw(_("reconciliation.file_too_large"))
    title = str(title or "").strip() or " / ".join(name for name, _content in everything[:2])
    job_id = uuid.uuid4().hex
    saved = {side: [_save(content, name) for name, content in sides[side]] for side in ("left", "right")}
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
        lefts, rights = _read(files["left"]), _read(files["right"])
        request_id = recon_job
        if kind == "vat":
            from pearnly_cowork.recon_center import vat_check

            result = vat_check.run(workspace, title, request_id, lefts, rights, options.get("lang") or "th",
                                   on_stage=stage, invoice_files=files["right"], recon_job=recon_job,
                                   report_files=files["left"])
        elif kind == "bank":
            from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
                document_model_rows,
            )
            from pearnly_cowork.recon_center import document_ocr

            account = str(options.get("gl_account_code") or "")
            # 扫描件 / 照片 / 要模型兜底的 PDF：每份文件一个持久识别任务（按读到的是哪一份文件找它存的 File）。
            stored = [(content, file_name, side) for side, loaded in (("left", lefts), ("right", rights))
                      for (_name, content), file_name in zip(loaded, files[side])]

            def read(document_type, content, filename):
                index, (_content, file_name, side) = next((index, item) for index, item in enumerate(stored)
                                                          if item[0] is content)
                return document_ocr.reader(recon_job, f"{side}{index}", file_name, frappe.session.user)(
                    document_type, content, filename)

            (left_name, left), (right_name, right) = lefts[0], rights[0]
            result = bank_recon_service.create_from_documents(workspace, title, request_id, left, left_name,
                right, right_name, gl_account_code=account, model_rows=document_model_rows(account, read=read),
                lang=options.get("lang") or "th", on_stage=stage,
                statement_files=[(content, name) for name, content in lefts],
                gl_files=[(content, name) for name, content in rights])
        else:
            from pearnly_cowork.recon_center import document_ocr, sources

            (left_name, left), (right_name, right) = lefts[0], rights[0]
            stage("ledger")
            gl_source = sources.ledger_source(left, left_name,
                document_ocr.reader(recon_job, "left", files["left"][0], frappe.session.user))
            stage("report")
            vat_source = sources.report_source(right, right_name,
                document_ocr.reader(recon_job, "right", files["right"][0], frappe.session.user))
            stage("matching")
            result = income_recon_service.create_from_parsed(workspace, title, request_id, gl_source,
                vat_source=vat_source, gl_filename=left_name, vat_filename=right_name)
        frappe.db.commit()
        _state(recon_job, state="done", stage="done", task=result["name"])
    except Exception as error:
        frappe.db.rollback()
        from pearnly_cowork.reconciliation.messages import error_text

        message = error_text(error)
        if not isinstance(error, (frappe.ValidationError, ValueError)):
            frappe.log_error(title="cowork reconciliation job", message=frappe.get_traceback())
        _state(recon_job, state="failed", stage="failed", error=frappe.utils.strip_html(message)[:500])
    finally:
        # 一份一提交：删除时的文件保护检查会锁住查到的来源行，二十张照片放在一个事务里删，并发的另一个对账
        # 就会等锁超时（2026-09-29 压测）。结果已提交，清理失败只记日志、不把任务改成失败；没删掉的临时文件
        # 是本人私有、不挂单据，按日志里的文件名处理。
        for name in files["left"] + files["right"]:
            try:
                if frappe.db.exists("File", name):
                    frappe.delete_doc("File", name, ignore_permissions=True, force=True)
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.log_error(title="cowork reconciliation cleanup", message=frappe.get_traceback())
