"""LINE 识别卡片「保存」的推送（用户 11-18）：不进录入页，直接推送这张票（同录入页右上角「提交」）。

录入页的提交编排在前端（`intake-upload.js` 的 ``submit``）：账套没定先拦 → 存货科目组 → Express 选择项 → 推送 →
Express 确认（`companion.submission.confirm`，同一个 :func:`service.confirm` 加写者锁重来）。这里在服务端走同一串，调同一批
服务端函数，不另写规则。前端会弹窗问的项这里不问：有要人定的就不推，回一句现有原因（`export_facts.reason_text`）。
LINE 一侧（按钮、回话）在 `line.save`。"""
import hashlib
import json

import frappe
from frappe import _

from pearnly_cowork.connections import service as connections
from pearnly_cowork.history import storage
from pearnly_cowork.push import export_facts
from pearnly_cowork.push.application import conflicts, service as application

HISTORY = "Cowork OCR History"
PUSH_TARGETS = ("express", "mrerp")  # 同 `intake-upload.js` 的 PUSH_TARGETS
SENT = ("success", "pending")


def _confirm(name, revision, request_key):
    """同 `companion.submission.confirm`：撞写者锁整笔重来；失败把原因记在票上，不抛。"""
    try:
        return conflicts.retried(application.confirm, name, revision, request_key)
    except (frappe.ValidationError, frappe.PermissionError, frappe.AuthenticationError) as error:
        frappe.db.rollback()
        history = frappe.db.get_value("Cowork Push Record", name, "history")
        if not history:
            raise
        return application.submission_failed(history, str(error))


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, default=str).encode()).hexdigest()


def _stock(record) -> tuple:
    from pearnly_core.express_rules.contracts.history import primary_page_fields

    fields = primary_page_fields(json.loads(record.pages or "[]")) or {}
    kinds = [fields.get("posting_kind") or record.posting_kind] + [
        (item or {}).get("posting_kind") for item in fields.get("items") or [] if isinstance(item, dict)]
    return "stock" in kinds, fields.get("posting_kind") or record.posting_kind or None


def save(name) -> dict:
    """推送这张票：``{"sent": True}`` / ``{"stop": 原因}``。失败不抛（同录入页：原因记在票上）。"""
    record = storage.read(name)  # 读取范围（工作空间 / 客户）由它核
    if record.get("last_push_status") in SENT:
        if record.get("last_push_status") == "pending":
            return {"sent": True}
        return {"stop": export_facts.reason_text("already_success")}
    if not (record.get("intake_target") in PUSH_TARGETS and record.get("push_connection")):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    connection, account_set = record.push_connection, record.get("push_account_set") or ""
    if not account_set:
        return {"stop": _("Choose the year and account set first.")}
    adapter = connections.adapter_of(connection)
    stock, posting_kind = _stock(record)
    if adapter == "express":
        if stock:
            view = connections.stock_acc_group(connection, account_set)
            if view and view.get("known") and not view.get("chosen") and len(view.get("groups") or []) >= 2:
                return {"stop": _("Inventory account group")}
        view = application.choices_view(name, connection, account_set, posting_kind)
        if view.get("wait"):
            return {"stop": view["wait"].get("message") or ""}
        if view.get("needed"):
            return {"stop": export_facts.reason_text("choice_needed")}
    revision = frappe.db.get_value("Cowork Connection", connection, "record_revision")
    key = _key("line-save", name, record.record_version, account_set, record.get("last_push_record") or "")
    result = application.submit(name, connection, key, record.record_version, revision, account_set, None, None,
                                posting_kind, manual=True)
    if result.get("status") == "failed":
        return {"stop": export_facts.reason_text(result.get("reason"))}
    if result.get("status") == "prepared" and adapter == "express":
        confirmed = _confirm(result["name"], result["revision"], "confirm:" + key)
        if (confirmed or {}).get("status") == "failed":
            return {"stop": export_facts.reason_text(confirmed.get("reason"))}
    # 推没推出去以票上的状态为准（列表同一份）：录入中 / 成功算推了，其余（停在要人选、口径没定等）回票上记的原因
    after = frappe.db.get_value(HISTORY, name, ["last_push_status", "last_push_reason"], as_dict=True) or {}
    if after.get("last_push_status") in SENT:
        return {"sent": True}
    return {"stop": export_facts.reason_text(after.get("last_push_reason") or result.get("reason") or "choice_needed")}
