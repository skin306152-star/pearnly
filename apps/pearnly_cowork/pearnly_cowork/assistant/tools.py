"""Cowork 的智能体工具（ADR-019）：对话里发来的票据交给录入工作台识别、小助手状态与账套、单据推送到 Express、
文件夹定时任务、推送结果。

- 工具以当前登录用户身份在服务端执行，只调用各域现有的公开服务（与网页同一入口），权限、作用域与连接的允许名单
  都由那些服务自己校验，这里不另开口子、不直接写表。
- 推送与网页「提交」同一顺序（同 `folder_intake.push.push_one`）：先看还缺哪项要人选 → 准备 → Express 确认发送；
  每张单据各自提交，一张失败不连累前面的；同一张单据同一版本用固定请求号，重复调用不会推两次。
- 回给模型的是简短 JSON（不带原件、不带凭据）。
"""
import hashlib
import json
import time
import uuid

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.connections import api as connections_api
from pearnly_cowork.connections import service as connections
from pearnly_cowork.folder_intake import destinations, rules
from pearnly_cowork.history import push_state, storage
from pearnly_cowork.push.application import service as application

HISTORY = "Cowork OCR History"
CONNECTION = "Cowork ERP Connection"
PUSH_MAX = 20
LIST_MAX = 50
DOC_FIELDS = ["name", "invoice_no", "invoice_date", "seller_name", "total_amount", "quality_status", "staged",
              "posting_kind", "intake_target", "push_connection", "push_account_set", "last_push_status",
              "last_push_reason", "last_pushed_at", "filename", "creation"]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ---------------- 小助手状态与账套 ----------------

ACCOUNT_SETS_SHOWN = 30


def companion_status(tool_input) -> str:
    """一台小助手可能报上千个账套（测试机实测 1,068 个）：只回总数和前若干个，按名称搜索再缩小。"""
    search = str((tool_input or {}).get("search") or "").strip().lower()
    out = []
    for row in connections.records(None, None, 0, 100) or []:
        item = {"connection": row["name"], "label": row.get("label"), "adapter": row.get("adapter"),
                "enabled": row.get("enabled")}
        if row.get("adapter") == "express":
            item["companion_online"] = destinations.online("express", row["name"])
        try:
            targets = connections.targets(row["name"])
            sets = [{"key": a["source_id"], "label": a.get("label"), "active": a.get("active", True)}
                    for a in targets.get("account_sets") or []]
            if search:
                sets = [a for a in sets if search in str(a["label"] or "").lower()]
            item["account_sets_total"] = len(sets)
            item["account_sets"] = sets[:ACCOUNT_SETS_SHOWN]
            item["account_sets_status"] = targets.get("status")
            item["refresh_status"] = targets.get("refresh_status")
        except (frappe.ValidationError, frappe.PermissionError):
            frappe.clear_messages()
        out.append(item)
    return _json({"connections": out})


def refresh_account_sets(tool_input) -> str:
    connection = str(tool_input.get("connection") or "")
    result = connections_api.request_refresh(connection, "assistant:" + uuid.uuid4().hex)
    return _json({"connection": connection, "status": result.get("status"), "request_id": result.get("request_id")})


# ---------------- 单据与推送结果 ----------------

def list_documents(tool_input) -> str:
    scope.require_read()
    filters, or_filters = {}, None
    state = tool_input.get("push_state")
    if state == "not_pushed":
        filters.update(last_push_status=["is", "not set"], last_push_reason=["is", "not set"])
    elif state == "failed":
        or_filters = [["last_push_status", "=", "manual"], ["last_push_reason", "is", "set"]]
    elif state in ("pending", "success"):
        filters["last_push_status"] = state
    if tool_input.get("uploaded_from"):
        filters["creation"] = [">=", tool_input["uploaded_from"]]
    if tool_input.get("invoice_date_from") or tool_input.get("invoice_date_to"):
        filters["invoice_date"] = ["between", [tool_input.get("invoice_date_from") or "1900-01-01",
                                               tool_input.get("invoice_date_to") or "2999-12-31"]]
    if tool_input.get("waiting_review") is not None:
        filters["staged"] = 1 if tool_input.get("waiting_review") else 0
    limit = max(1, min(int(tool_input.get("limit") or 20), LIST_MAX))
    # 走 DocType 自己的权限查询条件（与录入列表同一判据），看不见的行取不到。
    rows = frappe.get_list(HISTORY, filters=filters, or_filters=or_filters, fields=DOC_FIELDS,
                           order_by="modified desc, name desc", limit_page_length=limit)
    return _json({"documents": rows, "count": len(rows)})


def _key(name, version, last) -> str:
    seed = f"{name}\0{version}\0{last or ''}"
    return "assistant:" + hashlib.sha256(seed.encode()).hexdigest()[:56]


def _push_one(name, connection=None) -> dict:
    try:
        doc = storage.read(name)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return {"document": name, "status": "not_found"}
    if doc.last_push_status in ("pending", "success"):
        return {"document": name, "status": "already_" + doc.last_push_status}
    target = connection or doc.push_connection
    if not target:
        return {"document": name, "status": "failed", "reason": "no_push_target"}
    adapter, revision = frappe.db.get_value(CONNECTION, target, ["adapter", "record_revision"]) or (None, None)
    if not adapter:
        return {"document": name, "status": "failed", "reason": "no_push_target"}
    account_set, posting_kind = doc.push_account_set or None, doc.posting_kind or None
    frappe.db.commit()  # choices_view 只看不记、结束时整笔回滚：先把前面的落掉
    try:
        view = application.choices_view(name, target, account_set, posting_kind)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        reason = str(error)[:300]
        application.submission_failed(name, reason)
        frappe.db.commit()
        return {"document": name, "status": "failed", "reason": reason}
    needed = view.get("needed") or []
    if needed:
        reason = "choice_needed:" + ",".join(dict.fromkeys(item["kind"] for item in needed))
        push_state.record_failure(name, reason)
        frappe.db.commit()
        return {"document": name, "status": "needs_choice", "reason": reason}
    key = _key(name, doc.record_version, doc.last_push_record or doc.last_push_reason)
    result = application.submit(name, target, key, doc.record_version, revision, account_set, None, None, posting_kind)
    frappe.db.commit()
    if result.get("status") in ("failed", "cancelled", "manual"):
        return {"document": name, "status": result.get("status"), "reason": str(result.get("reason") or "")[:300]}
    if result.get("status") == "prepared":
        try:
            destinations.of(adapter).confirm(target, result, "confirm:" + key)
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError, frappe.AuthenticationError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            try:
                application.cancel_prepared(result["name"], result["revision"])
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.clear_messages()
            application.submission_failed(name, str(error))
            frappe.db.commit()
            return {"document": name, "status": "failed", "reason": str(error)[:300]}
    sent = {"document": name, "status": "sent", "adapter": adapter}
    if adapter == "express":
        # Express 由那台电脑上的小助手写入：不在线就先排着，连上后自动写。
        sent["companion_online"] = destinations.online(adapter, target)
    return sent


def push_documents(tool_input) -> str:
    scope.require("intake")
    names = [str(n) for n in (tool_input.get("documents") or [])][:PUSH_MAX]
    if not names:
        frappe.throw(_("No documents were given."))
    return _json({"results": [_push_one(name, tool_input.get("connection") or None) for name in names]})


# ---------------- 对话里的票据：上传识别 ----------------

RECOGNIZE_MAX = 10
RECOGNIZE_WAIT_SECONDS = 90
FINISHED = ("saved", "not_invoice")
BROKEN = ("failed", "uncertain", "cancelled")
ITEMS_SHOWN = 30


def _recognition():
    from pearnly_cowork.ocr.execution import service

    return service


def _admit(file_id, choices) -> dict:
    """一个附件 → 录入工作台的原件 + 识别任务，与 LINE 发票据同一条路（`line/intake._admit`）：扣费口径与网页一致；
    请求号按附件固定，同一个附件再调一次拿回原来的任务，不重复扣费。"""
    from pearnly_core.assistant import files as chat_files
    from pearnly_core.execution.errors import Deferred, QueueFull

    from pearnly_cowork.files import service as originals

    key = "assistant:" + str(file_id)
    try:
        filename, content = chat_files.attachment(file_id)
        source = originals.create(scope.default_workspace(), filename, content, key)
        frappe.db.commit()
        batch = _recognition().submit(source["name"], key, **choices)
        frappe.db.commit()
    except (Deferred, QueueFull) as exc:
        frappe.db.rollback()
        return {"file": file_id, "status": "not_started",
                "reason": exc.reason if isinstance(exc, Deferred) else "queue_full"}
    except frappe.ValidationError as exc:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"file": file_id, "status": "not_started", "reason": str(exc)[:300]}
    return {"file": file_id, "filename": filename, "batch": batch["name"]}


def _choices(tool_input) -> dict:
    target = tool_input.get("target") or None
    push = target in ("express", "mrerp") and tool_input.get("connection")
    choices = {"direction": tool_input.get("direction") or None, "posting_kind": tool_input.get("posting_kind") or None,
               "target": target, "connection": tool_input.get("connection") if push else None,
               "account_set_key": tool_input.get("account_set_key") if push else None}
    if tool_input.get("payment"):
        choices["payment"] = tool_input["payment"]
    return choices


def _document(name) -> dict:
    from pearnly_cowork.contracts import history as history_contracts

    doc = storage.project(storage.read(name))
    page = history_contracts.primary_page_fields(doc.get("pages") or [])
    out = {key: doc.get(key) for key in ("name", "invoice_no", "invoice_date", "seller_name", "total_amount",
                                          "quality_status", "posting_kind", "intake_target", "push_connection",
                                          "push_account_set")}
    out.update({key: page.get(key) for key in ("document_type", "seller_tax", "seller_branch", "buyer_name",
                                                "buyer_tax", "subtotal", "vat", "discount", "wht_amount", "currency")})
    out["waiting_review"] = bool(doc.get("staged"))
    out["items"] = (page.get("items") or [])[:ITEMS_SHOWN]
    if doc.get("duplicate_warning"):
        out["duplicate_warning"] = doc.get("duplicate_warning")
    return out


def _result(batch) -> dict:
    detail = _recognition().detail(batch)
    state = detail.get("state")
    out = {"batch": batch, "state": state, "pages": detail.get("page_count"), "billing": detail.get("billing_state")}
    if state == "saved":
        out["documents"] = [_document(name) for name in detail.get("history_records") or []]
    elif state in BROKEN:
        out["reason"] = detail.get("last_code") or state
    return out


def recognize_attachments(tool_input) -> str:
    scope.require("intake")
    file_ids = [str(f) for f in (tool_input.get("files") or [])][:RECOGNIZE_MAX]
    if not file_ids:
        frappe.throw(_("No attachments were given."))
    choices = _choices(tool_input)
    admitted = [_admit(file_id, choices) for file_id in file_ids]
    waiting = [a for a in admitted if a.get("batch")]
    deadline = time.monotonic() + RECOGNIZE_WAIT_SECONDS
    while waiting:
        frappe.db.rollback()  # 识别在队列里另一进程做：每次重新读，看得到它刚提交的结果
        for item in waiting:
            item.update(_result(item["batch"]))
        waiting = [a for a in waiting if a.get("state") not in FINISHED + BROKEN]
        if not waiting or time.monotonic() >= deadline:
            break
        time.sleep(3)
    return _json({"results": admitted})


def recognition_results(tool_input) -> str:
    scope.require_read()
    batches = [str(b) for b in (tool_input.get("batches") or [])][:RECOGNIZE_MAX]
    if not batches:
        frappe.throw(_("No recognition tasks were given."))
    out = []
    for batch in batches:
        try:
            out.append(_result(batch))
        except (frappe.DoesNotExistError, frappe.PermissionError):
            frappe.clear_messages()
            out.append({"batch": batch, "state": "not_found"})
    return _json({"results": out})


# ---------------- 文件夹定时任务 ----------------

def folder_tasks(_input) -> str:
    listing = rules.listing()
    keep = ("name", "title", "enabled", "connection_label", "account_label", "direction", "folder", "root_label",
            "run_day", "run_time", "last_run", "counts", "results", "agent_fresh", "folder_state")
    return _json({"tasks": [{k: r.get(k) for k in keep} for r in listing.get("rules") or []]})


def run_folder_task(tool_input) -> str:
    row = rules.run_now(str(tool_input.get("task") or ""))
    return _json({"task": row.get("name"), "title": row.get("title"), "requested": True,
                  "note": "The companion picks this up on its next sync, usually within a minute."})


def folder_task_runs(tool_input) -> str:
    data = rules.runs(str(tool_input.get("task") or ""))
    keep = ("trigger", "started_at", "scanned_at", "found", "new_files", "skipped", "upload_failed", "note")
    return _json({"task": data.get("rule"), "runs": [{k: r.get(k) for k in keep} for r in data.get("runs") or []][:10]})


_OBJ = {"type": "object", "properties": {}}
TOOLS = [
    {"name": "cowork_recognize_attachments", "label": "Recognized documents",
     "description": "Recognize receipts and invoices the user attached in this chat with Pearnly's own recognition, "
                    "exactly like uploading them on the Intake workbench (same billing, the documents then appear in "
                    "the workbench and can be reviewed and pushed). Use it whenever the user sends a document photo or "
                    "PDF and wants it recognized, read, entered or recorded. Pass each attachment's file id: the folder "
                    "name after /mnt/session/uploads/ in the attachment note. Choices are optional; leave them all out "
                    "to recognize only. target express or mrerp also needs connection (and account_set_key for "
                    "Express), as listed by cowork_companion_status. Waits up to about 90 seconds and returns the "
                    "recognized documents; anything still running comes back with its batch for "
                    "cowork_recognition_results. Report the recognized values, not your own reading of the image.",
     "input_schema": {"type": "object", "properties": {
         "files": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": RECOGNIZE_MAX},
         "direction": {"type": "string", "enum": ["purchase", "sales"]},
         "target": {"type": "string", "enum": ["express", "mrerp", "none"]},
         "connection": {"type": "string"},
         "account_set_key": {"type": "string"},
         "posting_kind": {"type": "string", "enum": ["service", "stock"], "description": "Express only."},
         "payment": {"type": "string", "enum": ["cash", "credit"], "description": "MR.ERP only."}},
         "required": ["files"]},
     "handler": "pearnly_cowork.assistant.tools.recognize_attachments"},
    {"name": "cowork_recognition_results", "label": "Checked recognition results",
     "description": "Status and recognized documents of recognition tasks started by cowork_recognize_attachments.",
     "input_schema": {"type": "object", "properties": {
         "batches": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": RECOGNIZE_MAX}},
         "required": ["batches"]},
     "handler": "pearnly_cowork.assistant.tools.recognition_results"},
    {"name": "cowork_companion_status", "label": "Checked the companion",
     "description": "List the user's ERP connections (Express / MR.ERP) with whether the Windows companion is online "
                    "and the account sets it reports (total plus the first 30; pass search to find one by company name). "
                    "Use before pushing to Express or when asked about the companion.",
     "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
     "handler": "pearnly_cowork.assistant.tools.companion_status"},
    {"name": "cowork_refresh_account_sets", "label": "Refreshed account sets",
     "description": "Ask a connection to re-read its account sets from the accounting program. Check the result later "
                    "with cowork_companion_status.",
     "input_schema": {"type": "object", "properties": {"connection": {"type": "string"}}, "required": ["connection"]},
     "handler": "pearnly_cowork.assistant.tools.refresh_account_sets"},
    {"name": "cowork_list_documents", "label": "Listed documents",
     "description": "List recognized documents (receipts, tax invoices) the user can see, newest first. Filter by push "
                    "state (not_pushed, pending, success, failed), upload date, invoice date or waiting for review.",
     "input_schema": {"type": "object", "properties": {
         "push_state": {"type": "string", "enum": ["not_pushed", "pending", "success", "failed"]},
         "uploaded_from": {"type": "string", "description": "YYYY-MM-DD"},
         "invoice_date_from": {"type": "string", "description": "YYYY-MM-DD"},
         "invoice_date_to": {"type": "string", "description": "YYYY-MM-DD"},
         "waiting_review": {"type": "boolean"},
         "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}}},
     "handler": "pearnly_cowork.assistant.tools.list_documents"},
    {"name": "cowork_push_documents", "label": "Pushed documents",
     "description": "Push recognized documents to their accounting target (Express through the companion, or MR.ERP), "
                    "the same as Submit on the web page. Only when the user asked to push. Up to 20 at a time; each "
                    "result says sent, needs_choice (the user must pick in the web page), failed with a reason, or already pushed.",
     "input_schema": {"type": "object", "properties": {
         "documents": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": PUSH_MAX},
         "connection": {"type": "string", "description": "Optional; defaults to the target chosen at upload."}},
         "required": ["documents"]},
     "handler": "pearnly_cowork.assistant.tools.push_documents"},
    {"name": "cowork_push_results", "label": "Checked push results",
     "description": "Show recent push results: documents whose push failed (with the reason), is pending, or succeeded.",
     "input_schema": {"type": "object", "properties": {
         "push_state": {"type": "string", "enum": ["pending", "success", "failed"]},
         "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}}, "required": ["push_state"]},
     "handler": "pearnly_cowork.assistant.tools.list_documents"},
    {"name": "cowork_folder_tasks", "label": "Checked folder tasks",
     "description": "List the scheduled folder tasks (scan a folder on the user's PC, recognize new files and push them), "
                    "with their schedule, last run and whether the companion reading the folder is online.",
     "input_schema": _OBJ, "handler": "pearnly_cowork.assistant.tools.folder_tasks"},
    {"name": "cowork_run_folder_task", "label": "Started a folder task",
     "description": "Run one folder task now. Only when the user asked.",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
     "handler": "pearnly_cowork.assistant.tools.run_folder_task"},
    {"name": "cowork_folder_task_runs", "label": "Checked folder task runs",
     "description": "Show the recent runs of one folder task: files found, uploaded, skipped and failed.",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
     "handler": "pearnly_cowork.assistant.tools.folder_task_runs"},
]
