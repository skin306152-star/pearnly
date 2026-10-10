"""Cowork 的智能体工具（ADR-019）：对话里发来的票据交给录入工作台识别、复核与修改、小助手状态与账套、
单据推送到 Express、文件夹定时任务、推送结果。

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
from decimal import Decimal, InvalidOperation

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
# 对话里能交给识别的：票据照片与 PDF（HEIC 照片在对话上传时已转成 JPEG）。表格、文本等不在这里识别。
RECOGNIZABLE = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif")


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
    except frappe.ValidationError:
        frappe.clear_messages()
        return {"file": file_id, "status": "not_found", "attachments_in_this_chat": chat_files.attachments()}
    if not filename.lower().endswith(RECOGNIZABLE):
        return {"file": file_id, "filename": filename, "status": "unsupported_type",
                "reason": "Only document photos and PDF files can be recognized."}
    try:
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


def _push_target(doc) -> dict:
    """录入口径里「仅识别」在库里记作 intake_target=none，模型会说成「推送目标：不推送」；只说还没选推送去哪。"""
    return {} if doc.get("push_connection") else {"push_target": "not chosen yet"}


def _filled(values: dict) -> dict:
    """空值不给：模型会把「币种：未识别到」之类的空项当成要说的事。"""
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


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
                                          "posting_kind", "push_connection", "push_account_set", "duplicate_warning")}
    out.update(_push_target(doc))
    out.update({key: page.get(key) for key in ("document_type", "seller_tax", "seller_branch", "buyer_name",
                                                "buyer_tax", "subtotal", "vat", "discount", "wht_amount", "currency")})
    out["items"] = (page.get("items") or [])[:ITEMS_SHOWN]
    return _filled(out)


def _result(batch) -> dict:
    # 扣费等内部状态不交给模型（识别刚存完那一刻还是「待扣」，模型会误报成扣了两次）；扣费口径写在工具说明里。
    detail = _recognition().detail(batch)
    state = detail.get("state")
    out = {"batch": batch, "state": state, "pages": detail.get("page_count")}
    if state == "saved":
        out["documents"] = [_document(name) for name in detail.get("history_records") or []]
        out["needs_review"] = bool(detail.get("requires_review"))
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


# ---------------- 录入口径（同网页「新建录入」与 LINE；问一次、记住） ----------------

BATCH = "Cowork OCR Batch"
OPTIONS_SHOWN = 30
MRERP_READ_WAIT_SECONDS = 60


def _target_state(row) -> str:
    """同 LINE `flow._state`：没连、停用、Express 的小助手不在线都不能选。"""
    if not row:
        return "not_connected"
    if not row.get("enabled"):
        return "disabled"
    if row.get("adapter") == "express" and not destinations.online("express", row["name"]):
        return "companion_offline"
    return "ready"


def _last_used() -> dict:
    """上一次识别用的口径（不论在网页、LINE 还是这里）：问过一次就沿用，不另存一份。"""
    rows = frappe.get_list(BATCH, filters={"actor": frappe.session.user},
                           fields=["intake_target", "target_connection", "target_account_set", "direction",
                                   "posting_kind", "payment"], order_by="creation desc", limit_page_length=1)
    if not rows:
        return {}
    row = rows[0]
    return _filled({"target": row.get("intake_target"), "connection": row.get("target_connection"),
                    "account_set_key": row.get("target_account_set"), "direction": row.get("direction"),
                    "posting_kind": row.get("posting_kind"), "payment": row.get("payment")})


def intake_options(tool_input) -> str:
    from pearnly_cowork.connections import account_search

    scope.require("intake")
    search = str(tool_input.get("search") or "").strip()
    first = {}
    for row in connections.records(None, None, 0, 100) or []:
        first.setdefault(row.get("adapter"), row)  # 每种 ERP 取第一条连接，同 LINE
    last = _last_used()
    targets = []
    for adapter in ("express", "mrerp"):
        row = first.get(adapter)
        item = {"target": adapter, "state": _target_state(row)}
        if row:
            item["connection"] = row["name"]
            item["label"] = row.get("label")
        if item["state"] == "ready":
            try:
                catalog = connections.targets(row["name"])
            except (frappe.ValidationError, frappe.PermissionError):
                frappe.clear_messages()
                catalog = {}
            usable = account_search.usable(adapter, catalog.get("account_sets") or [])
            item["account_sets_total"] = len(usable)
            if search:
                found = account_search.search(adapter, usable, search)
            else:
                found = [{"value": r["source_id"], "label": account_search.label(adapter, r)} for r in usable]
                remembered = last.get("account_set_key") if last.get("connection") == row["name"] else None
                found.sort(key=lambda r: r["value"] != remembered)  # 上次用的排第一
            item["account_sets"] = [{"key": r["value"], "label": r["label"]} for r in found[:OPTIONS_SHOWN]]
            if not usable:
                item["account_sets_status"] = catalog.get("status") or "missing"
        targets.append(item)
    targets.append({"target": "none", "state": "ready"})
    return _json({"targets": targets, "last_used": last,
                  "modes": {"express": "posting_kind: service or stock", "mrerp": "payment: cash or credit "
                            "(purchases are credit only)", "none": "recognize only, nothing else to choose"}})


def _connection_call(fn, *args):
    try:
        return fn(*args)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": "failed", "reason": str(error)[:300]}


def stock_group(tool_input) -> str:
    """Express 记成存货时的存货科目组：每个账套选一次、记住（大家共用，同网页）。"""
    scope.require("intake")
    name, key = str(tool_input.get("connection") or ""), str(tool_input.get("account_set_key") or "")
    if tool_input.get("acccod"):
        result = _connection_call(connections_api.choose_stock_acc_group, name, key, str(tool_input["acccod"]))
        frappe.db.commit()
        return _json(result)
    return _json(_connection_call(connections_api.stock_acc_group, name, key))


def mrerp_setup(tool_input) -> str:
    """MR.ERP 推送用的导入菜单与默认值：每个账套每类单据选一次、记住（同网页推送前那一步）。"""
    scope.require("intake")
    name, key = str(tool_input.get("connection") or ""), str(tool_input.get("account_set_key") or "")
    if tool_input.get("read_again"):
        request = _connection_call(connections_api.request_mrerp_menu, name, key)
        frappe.db.commit()
        if request.get("status") == "failed":
            return _json(request)
        deadline = time.monotonic() + MRERP_READ_WAIT_SECONDS
        while time.monotonic() < deadline:
            frappe.db.rollback()  # 后台读完提交的结果要重新读才看得到
            view = _connection_call(connections_api.mrerp_menu, name, key)
            stamps = [view.get("observed_at"), view.get("error_at")]
            if any(str(stamp or "") >= str(request["requested_at"]) for stamp in stamps):
                break
            time.sleep(2)
    for doc_type, idmenu in (tool_input.get("menus") or {}).items():
        result = _connection_call(connections_api.choose_mrerp_menu, name, key, doc_type, str(idmenu))
        if result.get("status") == "failed":
            return _json(result)
    for field, code in (tool_input.get("defaults") or {}).items():
        result = _connection_call(connections_api.choose_mrerp_default, name, key, field, str(code))
        if result.get("status") == "failed":
            return _json(result)
    frappe.db.commit()
    return _json(_connection_call(connections_api.mrerp_menu, name, key))


# ---------------- 复核与修改（同录入工作台复核页） ----------------

# 复核页能改的字段（intake-upload.js FIELD_KEYS）；日期按原文写进 date_raw，由单据服务解析（泰历、两位年份都认）。
EDITABLE = ("seller_name", "seller_tax", "seller_addr", "seller_branch", "buyer_name", "buyer_tax", "buyer_addr",
            "buyer_branch", "invoice_number", "date", "document_type", "subtotal", "vat", "discount", "total_amount",
            "wht_amount", "currency", "notes", "direction", "posting_kind")
AMOUNTS = ("subtotal", "vat", "discount", "total_amount", "wht_amount")
TAX_IDS = ("seller_tax", "buyer_tax")
CHOICES = {"document_type": ("tax_invoice", "simplified_tax_invoice", "receipt", "credit_note", "payment_evidence",
                             "order_evidence", "other"),
           "direction": ("purchase", "sales"), "posting_kind": ("service", "stock")}
ITEM_COLUMNS = ("name", "qty", "price", "subtotal")
DETAIL_FIELDS = ("document_type", "invoice_number", "date", "date_raw", "seller_name", "seller_tax", "seller_branch",
                 "seller_addr", "buyer_name", "buyer_tax", "buyer_branch", "buyer_addr", "subtotal", "vat", "discount",
                 "total_amount", "wht_amount", "currency", "notes", "direction", "posting_kind")


def _amount(value) -> str:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        number = Decimal("NaN")
    if not number.is_finite():
        raise ValueError(f"{value!r} is not an amount")
    return str(number)


def _tax_id(value) -> str:
    from pearnly_core.tax_documents.amounts import valid_tax_id

    raw = "".join(ch for ch in str(value) if ch.isdigit())
    if len(raw) != 13 or not valid_tax_id(raw):
        raise ValueError(f"{value!r} is not a valid 13-digit Thai tax ID")
    return raw


def _clean(key, value) -> str:
    if key in AMOUNTS:
        return _amount(value)
    if key in TAX_IDS:
        return _tax_id(value)
    if key in CHOICES and value not in CHOICES[key]:
        raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
    return str(value).strip()


def _primary(pages) -> int:
    from pearnly_cowork.contracts import history as history_contracts

    page = history_contracts.primary_page(pages)
    return next((i for i, p in enumerate(pages) if p is page), 0)


def _detail(doc) -> dict:
    pages = doc.get("pages") or []
    fields = (pages[_primary(pages)].get("fields") or {}) if pages else {}
    out = {"document": doc.get("name"), "version": doc.get("record_version"), "filename": doc.get("filename"),
           "push_status": doc.get("last_push_status"), "push_reason": doc.get("last_push_reason"),
           "push_connection": doc.get("push_connection"), "push_account_set": doc.get("push_account_set"),
           "duplicate_warning": doc.get("duplicate_warning"), "pages": len(pages), **_push_target(doc)}
    out.update({key: fields.get(key) for key in DETAIL_FIELDS})
    out["items"] = [_filled({key: row.get(key) if key != "subtotal" else row.get("subtotal", row.get("amount"))
                             for key in ITEM_COLUMNS}) for row in (fields.get("items") or [])[:ITEMS_SHOWN]
                    if isinstance(row, dict)]
    reason = str(doc.get("last_push_reason") or "")
    if reason.startswith("document_policy_needed_"):
        out["handling_choice_needed"] = reason[len("document_policy_needed_"):]
    return _filled(out)


def _read(name):
    try:
        return storage.project(storage.read(name))
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return None


def document_detail(tool_input) -> str:
    scope.require_read()
    name = str(tool_input.get("document") or "")
    doc = _read(name)
    return _json(_detail(doc) if doc else {"document": name, "status": "not_found"})


def edit_document(tool_input) -> str:
    """按用户说的改复核字段：只动主页（同复核页），其余页原样；版本号对不上不覆盖别人刚改的。"""
    scope.require("intake")
    name = str(tool_input.get("document") or "")
    changes = dict(tool_input.get("changes") or {})
    items = tool_input.get("items")
    if not changes and items is None:
        frappe.throw(_("No changes were given."))
    if not tool_input.get("version"):
        # 不带版本就等于跳过「别人刚改过不覆盖」：先查详情拿版本
        return _json({"document": name, "status": "not_saved",
                      "reason": "Read the document with cowork_document_detail first and pass its version."})
    unknown = [key for key in changes if key not in EDITABLE]
    if unknown:
        return _json({"status": "not_saved", "reason": f"Not editable: {', '.join(unknown)}", "editable": EDITABLE})
    try:
        cleaned = {key: (None if value in (None, "") else _clean(key, value)) for key, value in changes.items()}
        rows = None if items is None else [
            _filled({"name": str(row.get("name") or "").strip(),
                     **{key: _amount(row[key]) for key in ("qty", "price", "subtotal") if row.get(key) not in (None, "")}})
            for row in items if isinstance(row, dict)]
    except ValueError as error:
        return _json({"status": "not_saved", "reason": str(error)})
    doc = _read(name)
    if not doc:
        return _json({"document": name, "status": "not_found"})
    pages = doc.get("pages") or []
    if not pages:
        return _json({"document": name, "status": "not_saved", "reason": "This document has no recognized page."})
    index = _primary(pages)
    fields = dict(pages[index].get("fields") or {})
    for key, value in cleaned.items():
        target = "date_raw" if key == "date" else key
        if value is None:
            fields.pop(target, None)
            if key == "date":
                fields.pop("date", None)
        else:
            fields[target] = value
    if rows is not None:
        fields["items"] = rows
    pages[index] = {**pages[index], "fields": fields}
    try:
        saved = storage.update_pages(name, pages, str(tool_input["version"]))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_saved", "reason": str(error)[:300]})
    return _json({"status": "saved", **_detail(saved)})


def choose_document_policy(tool_input) -> str:
    from pearnly_cowork.history import document_policy

    scope.require("intake")
    name = str(tool_input.get("document") or "")
    try:
        result = document_policy.choose(name, tool_input.get("value"))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_saved", "reason": str(error)[:300]})
    return _json({"status": "saved", **result})


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
                    "to recognize only. target express or mrerp also needs connection and account_set_key, as listed "
                    "by cowork_intake_options; if the user wants the documents entered into an ERP and has not said "
                    "where in this conversation, get the choices there first. Otherwise call it directly, without "
                    "listing documents or checking the companion first. Waits up to about 90 seconds and returns the "
                    "recognized documents; anything still running comes back with its batch for "
                    "cowork_recognition_results. Each attachment is charged once like a web upload; calling again "
                    "for the same attachment returns the same result at no extra charge, so never tell the user it "
                    "was charged "
                    "twice. needs_review true means the workbench flagged it for a person to check. Report the "
                    "recognized values, not your own reading of the image, and leave out fields that are not given.",
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
    {"name": "cowork_intake_options", "label": "Checked intake choices",
     "description": "Where recognized documents can go, as on the web's New intake and in LINE: each target ERP "
                    "(express, mrerp) with its state and account sets (the last-used one first), recognize only "
                    "(none), and last_used: the choices of the user's previous recognition anywhere. Ask once: "
                    "propose last_used, and once the user agrees reuse it for the rest of the conversation without "
                    "asking again. Only ready targets can be chosen. Large lists are cut to 30; pass search (company "
                    "name, tax ID or folder) to narrow them.",
     "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
     "handler": "pearnly_cowork.assistant.tools.intake_options"},
    {"name": "cowork_stock_group", "label": "Checked the stock account group",
     "description": "Express documents posted as stock need the account set's stock account group, chosen once per "
                    "account set and shared by everyone. Without acccod it shows the groups and the current choice "
                    "(needed only when there is no choice and two or more groups); with acccod it records the "
                    "user's choice.",
     "input_schema": {"type": "object", "properties": {"connection": {"type": "string"},
                                                        "account_set_key": {"type": "string"},
                                                        "acccod": {"type": "string"}},
                      "required": ["connection", "account_set_key"]},
     "handler": "pearnly_cowork.assistant.tools.stock_group"},
    {"name": "cowork_mrerp_setup", "label": "Checked MR.ERP import settings",
     "description": "MR.ERP import menus per document kind and default values, chosen once per account set before "
                    "pushing (shared by everyone, like on the web). read_again reads the menus and defaults from "
                    "MR.ERP first (up to a minute). menus maps document kind to idmenu, defaults maps field to code; "
                    "both only with values listed in the result, and only after the user chose them. Items with "
                    "needed true still need a choice.",
     "input_schema": {"type": "object", "properties": {
         "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "read_again": {"type": "boolean"},
         "menus": {"type": "object", "additionalProperties": {"type": "string"}},
         "defaults": {"type": "object", "additionalProperties": {"type": "string"}}},
         "required": ["connection", "account_set_key"]},
     "handler": "pearnly_cowork.assistant.tools.mrerp_setup"},
    {"name": "cowork_document_detail", "label": "Viewed a document",
     "description": "All recognized fields of one document on the Intake workbench (the review page): seller and "
                    "buyer, tax IDs, invoice number, date, amounts, line items, push status, and the version needed "
                    "to edit it. handling_choice_needed means the push waits for cowork_choose_document_policy.",
     "input_schema": {"type": "object", "properties": {"document": {"type": "string"}}, "required": ["document"]},
     "handler": "pearnly_cowork.assistant.tools.document_detail"},
    {"name": "cowork_edit_document", "label": "Edited a document",
     "description": "Change recognized fields of one document, like correcting it on the review page. Only when the "
                    "user asked for the change; change only what they asked. changes maps field to new value (an "
                    "empty value clears it); date takes the date as written (Thai Buddhist years are fine); amounts "
                    "are plain numbers; tax IDs are 13 digits. items replaces all line items. Pass the version from "
                    "cowork_document_detail so someone else's newer edit is not overwritten; if it was, read it again "
                    "and tell the user. A pushed document must be pushed again to change the ERP.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"},
         "version": {"type": "integer"},
         "changes": {"type": "object", "properties": {key: {"type": ["string", "number", "null"]} for key in EDITABLE},
                     "additionalProperties": False},
         "items": {"type": "array", "items": {"type": "object", "properties": {
             "name": {"type": "string"}, "qty": {"type": ["string", "number"]},
             "price": {"type": ["string", "number"]}, "subtotal": {"type": ["string", "number"]}}}}},
         "required": ["document", "version"]},
     "handler": "pearnly_cowork.assistant.tools.edit_document"},
    {"name": "cowork_choose_document_policy", "label": "Chose how a kind of document is handled",
     "description": "For a transfer slip or withholding-tax certificate whose push waits for a handling choice: enter "
                    "records documents of this kind, skip leaves them out. It applies to every document of this kind "
                    "for the whole company from now on, so ask the user first and say so. Then push the document "
                    "again.",
     "input_schema": {"type": "object", "properties": {"document": {"type": "string"},
                                                        "value": {"type": "string", "enum": ["enter", "skip"]}},
                      "required": ["document", "value"]},
     "handler": "pearnly_cowork.assistant.tools.choose_document_policy"},
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
