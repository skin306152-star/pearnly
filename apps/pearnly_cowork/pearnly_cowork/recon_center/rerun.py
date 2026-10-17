"""答了要重跑的预设问题（``presets.RERUN``）：原文件留着，答了按新预设用原文件重跑一遍（用户 2026-09-30 批准）。

为什么要重跑：银行对账读总账时就按科目筛了行，存下的数据里没有别的科目，答了「这个账户对哪个科目」只能从原文件重读。
重跑不再付识别费：每份文件的识别任务按原对账号找回已读出的结果（``document_ocr.reader``），只重做免费的解析与核对。

原文件平时在对账结束时删掉；有这类问题时留着（本人私有、不挂单据），答了重跑完就删；没人答的 KEEP_DAYS 天后由
每日清理删掉，结果照旧（全部科目一起对的那份）。记录只经本模块读写，通用读、写、删、分享一律拒绝（``hooks.py``）。
"""
import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

DOCTYPE = "Cowork Recon Rerun"
KEEP_DAYS = 7
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def keep(workspace, kind, task, recon_job, title, files, options, questions):
    """对账结束时有要重跑的问题：记下原文件与参数（同一任务再记一次就覆盖）。"""
    values = {"workspace": workspace, "recon_kind": kind, "recon_job": recon_job, "title": str(title or "")[:140],
              "files_json": json.dumps(files), "options_json": json.dumps(options or {}),
              "questions_json": json.dumps(questions, ensure_ascii=False),
              "expires_at": now_datetime() + timedelta(days=KEEP_DAYS)}
    doc = (frappe.get_doc(DOCTYPE, task, for_update=True) if frappe.db.exists(DOCTYPE, task)
           else frappe.get_doc({"doctype": DOCTYPE, "task": task}))
    doc.update(values)
    _persist(doc)


def questions(task):
    """这份结果还等着答的重跑问题（原文件还在才算）。"""
    row = frappe.db.get_value(DOCTYPE, task, ["questions_json", "files_json"], as_dict=True)
    if not row:
        return []
    files = json.loads(row.files_json or "{}")
    if not all(frappe.db.exists("File", name) for side in ("left", "right") for name in files.get(side) or []):
        return []
    return json.loads(row.questions_json or "[]")


def prepared(task, lang=None):
    """重跑要的参数：``(对账类型, 工作区, 标题, 原文件, 选项)``。识别结果按原对账号取（``ocr_job``），重跑的结果替换
    原来那份（``replace_task``）；原文件不在了（过期删了）就不能重跑。"""
    doc = frappe.get_doc(DOCTYPE, task, for_update=True)
    if not questions(task):
        frappe.throw(_("reconciliation.rerun_files_gone"))
    options = {**json.loads(doc.options_json or "{}"), "ocr_job": doc.recon_job, "replace_task": task}
    if lang in ("th", "zh", "en", "ja"):
        options["lang"] = lang
    return doc.recon_kind, doc.workspace, doc.title, json.loads(doc.files_json or "{}"), options


def drop(task, files=True):
    """重跑完或任务删了：删记录，连同留着的原文件。"""
    row = frappe.db.get_value(DOCTYPE, task, ["files_json"], as_dict=True)
    if not row:
        return
    if files:
        _delete_files(json.loads(row.files_json or "{}"))
    frappe.flags.cowork_rerun_delete = _WRITE
    try:
        frappe.delete_doc(DOCTYPE, task, ignore_permissions=True, force=True)
    finally:
        frappe.flags.cowork_rerun_delete = None


def expire():
    """每日：过了 KEEP_DAYS 天没人答的，删原文件和记录（结果不动）。"""
    for task in frappe.get_all(DOCTYPE, filters={"expires_at": ["<", now_datetime()]}, pluck="name",
                               ignore_permissions=True, limit_page_length=200):
        try:
            drop(task)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="cowork reconciliation rerun cleanup", message=frappe.get_traceback())


def _delete_files(files):
    from pearnly_cowork.recon_center import storage

    for name in [name for side in ("left", "right") for name in files.get(side) or []]:
        storage.delete(name)  # 原件在原件层（ADR-024）连那边一起删


def _persist(doc):
    doc.flags.cowork_rerun_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


class ReconRerunDocument(Document):
    """只接受本模块的写入；任务、工作区写定后不改。"""

    def _require_service(self):
        if self.flags.get("cowork_rerun_write") is not _WRITE:
            deny()

    def autoname(self):
        self._require_service()
        self.name = self.task

    def before_insert(self):
        self._require_service()

    def before_validate(self):
        self._require_service()

    def validate(self):
        self._require_service()
        previous = self.get_doc_before_save()
        if previous and any(self.get(field) != previous.get(field) for field in ("task", "workspace", "recon_kind")):
            deny()

    def on_trash(self):
        if frappe.flags.get("cowork_rerun_delete") is not _WRITE:
            deny()

    def before_rename(self, *args, **kwargs):
        deny()
