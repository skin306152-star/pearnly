"""LINE 识别卡片「保存」的推送（用户 11-18）：不进录入页，直接推送这张票（同录入页右上角「提交」）。

录入页的提交编排在前端（`intake-upload.js` 的 ``submit``）：账套没定先拦 → 存货科目组 → Express 选择项 → 推送 →
Express 确认（`companion.submission.confirm`，同一个 :func:`service.confirm` 加写者锁重来）。这里在服务端走同一串，调同一批
服务端函数，不另写规则。前端会弹窗问的项这里不问：有要人定的就不推，回一句现有原因（`export_facts.reason_text`）——11-19 起这些题
在出卡前已在 LINE 里问完（`line.ask`），这里是兜底（旧卡、题没答完、LINE 里问不了的项）。
「仅识别」的票保存 = 录入页的「完成」（`history.storage.complete`）；「取消」= 这一张什么都不做、记录删掉（`history.removal.delete`，
同列表删除：真删记录与原件）。点过取消的票（已删）再点保存只回已取消。LINE 一侧（按钮、回话）在 `line.save`。"""
import hashlib
import json

import frappe
from frappe import _

from pearnly_cowork.connections import service as connections
from pearnly_cowork.history import removal, storage
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


def stock_group_stop(view):
    """选择项视图（`service.choices_view`）判出新建库存品挂哪个存货科目组还没定（``stock_group``：零主档那一档或
    `stock_acc_group.new_item_group`，11-19）→ 停下那句现有原因；没有回空。写账前置判定（`assistant.precheck_source`：LINE 出卡前、
    CC 卡片里问这一题）与这里同一份。"""
    return str(_("intake.error.STOCK_ACC_GROUP_UNFIT")) if (view or {}).get("stock_group") else ""


def save(name) -> dict:
    """推送这张票：``{"sent": True}`` / ``{"stop": 原因}``。失败不抛（同录入页：原因记在票上）。"""
    if not frappe.db.exists(HISTORY, name):
        return {"cancelled": True}  # 点过「取消」（已删）：保存什么都不做
    record = storage.read(name)  # 读取范围（工作空间 / 客户）由它核
    if not (record.get("intake_target") in PUSH_TARGETS and record.get("push_connection")):
        # 「仅识别」：保存 = 完成这张票（同录入页「完成」，再点不重复写）
        storage.complete([{"name": name, "record_version": record.record_version}])
        return {"saved": True}
    if record.get("last_push_status") in SENT:
        if record.get("last_push_status") == "pending":
            return {"sent": True}
        return {"stop": export_facts.reason_text("already_success")}
    connection, account_set = record.push_connection, record.get("push_account_set") or ""
    if not account_set:
        return {"stop": _("Choose the year and account set first.")}
    adapter = connections.adapter_of(connection)
    posting_kind = _stock(record)[1]
    if adapter == "express":
        view = application.choices_view(name, connection, account_set, posting_kind)
        if view.get("wait"):
            # 科目表此刻不能用（`service.masters_wait`）：还在读的带 ``wait``，读好了 LINE 接着保存（`line.save`）；电脑离线、读不到等
            # 等也不会好的照旧只回那句原因
            reading = (view["wait"].get("state") or "reading") == "reading"
            return {"stop": view["wait"].get("message") or "", **({"wait": True, "connection": connection} if reading else {})}
        if view.get("needed"):
            return {"stop": export_facts.reason_text("choice_needed")}
        if stock_group_stop(view):
            return {"stop": stock_group_stop(view)}
    revision = frappe.db.get_value(connections.CONNECTION, connection, "record_revision")
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


def outcome(name) -> dict:
    """这张票此刻的推送结果（同录入列表，票上的推送状态）：``{"status": "" | pending | success | failed, "docnum", "reason"}``；
    ``reason`` 是给人看的那句（同列表悬浮，`export_facts.reason_text`）。没推过 / 已删回空状态。LINE 卡片「保存」后的回执用（`line.save`）。"""
    row = frappe.db.get_value(HISTORY, name, ["last_push_status", "last_push_reason", "last_push_record"], as_dict=True) or {}
    status = row.get("last_push_status") or ""
    if status == "success":
        docnum = frappe.db.get_value("Cowork Push Record", row.get("last_push_record"), "external_docnum") \
            if row.get("last_push_record") else ""
        return {"status": "success", "docnum": docnum or "", "reason": ""}
    if status == "pending":
        return {"status": "pending", "docnum": "", "reason": ""}
    if status:
        return {"status": "failed", "docnum": "", "reason": export_facts.reason_text(row.get("last_push_reason") or status)}
    return {"status": "", "docnum": "", "reason": ""}


def cancel(name) -> dict:
    """卡片「取消」（用户 11-19）：这一张什么都不做——不推送、不保存，记录删掉（同列表删除）。已推送 / 已完成的不动（``kept``）；
    已删的回已取消。"""
    if not frappe.db.exists(HISTORY, name):
        return {"cancelled": True}
    record = storage.read(name)
    if record.get("last_push_status") or not record.get("staged"):
        return {"kept": True}
    removal.delete([name])
    return {"cancelled": True}
