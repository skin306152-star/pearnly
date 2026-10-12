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
import re
import time
import uuid
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.connections import api as connections_api
from pearnly_cowork.connections import service as connections
from pearnly_cowork.folder_intake import agent, destinations, rules
from pearnly_cowork.folder_intake import contracts as folder_contracts
from pearnly_cowork.history import push_state, storage
from pearnly_cowork.push.application import api as application_api
from pearnly_cowork.push.application import service as application

HISTORY = "Cowork OCR History"
CONNECTION = "Cowork ERP Connection"
PUSH_MAX = 20
LIST_MAX = 50
# 按方向筛、算合计时要看每张的主页面（方向、税前额、税额只在识别结果里）：在库里按页取、取一页丢一页的原文，
# 最多看最新的这么多张；再多就说「只看了最新的」，请用日期缩小。
LIST_SCAN_MAX = 2000
LIST_SCAN_PAGE = 200
DOC_FIELDS = ["name", "invoice_no", "invoice_date", "seller_name", "seller_tax", "buyer_name", "buyer_tax",
              "total_amount", "quality_status", "staged",
              "posting_kind", "intake_target", "push_connection", "push_account_set", "last_push_status",
              "last_push_reason", "last_pushed_at", "filename", "creation"]
PAGE_SHOWN = ("document_type", "direction", "subtotal", "vat")
SUMMED = ("subtotal", "vat", "total_amount")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ---------------- 小助手状态与账套 ----------------

def companion_status(tool_input) -> str:
    """连接与小助手在不在线、账套目录读到没有。选录入账套用 `intake_options`（只列能选的、可搜），这里不再给清单，
    免得两份数字（全部 / 能选的）和两条搜索让模型挑错（2026-10-10 测试）。"""
    out = []
    for row in connections.records(None, None, 0, 100) or []:
        item = {"connection": row["name"], "label": row.get("label"), "adapter": row.get("adapter"),
                "enabled": row.get("enabled")}
        if row.get("adapter") == "express":
            item["companion_online"] = destinations.online("express", row["name"])
        try:
            targets = connections.targets(row["name"])
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

def _number(value):
    """识别结果里的金额 → Decimal；空的、读不出的为 None（不当 0 算）。"""
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _with_page(row) -> dict:
    """列表一行补上主页面的单据类型、方向、税前额、税额（识别结果；同复核页读的那一页），去掉整份页面原文。"""
    from pearnly_cowork.contracts import history as history_contracts
    from pearnly_cowork.contracts.push import normalize_direction

    pages = row.pop("pages", None)
    try:
        pages = json.loads(pages) if isinstance(pages, str) else pages
    except ValueError:
        pages = None
    page = history_contracts.primary_page_fields(pages if isinstance(pages, list) else [])
    row.update({key: page.get(key) for key in PAGE_SHOWN})
    row["direction"] = normalize_direction(page.get("direction"))
    return row


def _totals(rows) -> dict:
    """合计（Decimal）：贷项单另算一组；某项没读到值的张数单独报，不当 0。"""
    groups = {}
    for row in rows:
        group = groups.setdefault("credit_notes" if row.get("document_type") == "credit_note" else "documents",
                                  {"count": 0, **{key: Decimal(0) for key in SUMMED}, "without": {}})
        group["count"] += 1
        for key in SUMMED:
            value = _number(row.get(key))
            if value is None:
                group["without"][key] = group["without"].get(key, 0) + 1
            else:
                group[key] += value
    out = {}
    for name, group in groups.items():
        out[name] = {"count": group["count"], **{key: f"{group[key]:.2f}" for key in SUMMED}}
        if group["without"]:
            out[name]["without_value"] = group["without"]
    return out


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
    # 推往哪条连接、哪份账套（键同 cowork_intake_options；库里比较不分大小写）
    if tool_input.get("connection"):
        filters["push_connection"] = str(tool_input["connection"])
    if tool_input.get("account_set_key"):
        filters["push_account_set"] = str(tool_input["account_set_key"])
    limit = max(1, min(int(tool_input.get("limit") or 20), LIST_MAX))
    offset = max(0, int(tool_input.get("offset") or 0))
    direction = tool_input.get("direction") if tool_input.get("direction") in ("purchase", "sales") else None
    search = str(tool_input.get("search") or "").strip()
    failed_only = or_filters is not None
    if search:
        # 按单号、双方名称或税号找（税号去掉分隔符比），不限最近几张；「失败」那组条件改在取回后筛
        digits = "".join(ch for ch in search if ch.isdigit())
        or_filters = [[field, "like", f"%{search}%"] for field in ("invoice_no", "seller_name", "buyer_name")]
        if len(digits) >= 5:
            or_filters += [[field, "like", f"%{digits}%"] for field in ("seller_tax", "buyer_tax", "invoice_no")]
    # 走 DocType 自己的权限查询条件（与录入列表同一判据），看不见的行取不到。
    query = {"filters": filters, "or_filters": or_filters, "fields": DOC_FIELDS + ["pages"],
             "order_by": "modified desc, name desc"}
    out = {}
    if not (search and failed_only) and not direction and not tool_input.get("totals"):
        rows = [_with_page(r) for r in frappe.get_list(HISTORY, limit_start=offset, limit_page_length=limit, **query)]
        if len(rows) < limit and (rows or not offset):
            total = offset + len(rows)
        else:
            counted = frappe.get_list(HISTORY, filters=filters, or_filters=or_filters,
                                      fields=[{"COUNT": "name", "as": "total"}], order_by="", limit_page_length=0)
            total = max(int((counted[0].get("total") if counted else 0) or 0), offset + len(rows))
    else:
        # 要在取回后筛（失败 + 搜索、方向）或要合计：按页看最新的最多 LIST_SCAN_MAX 张，筛完再切这一页
        matched, scanned, complete, no_direction = [], 0, False, 0
        while scanned < LIST_SCAN_MAX:
            page = frappe.get_list(HISTORY, limit_start=scanned,
                                   limit_page_length=min(LIST_SCAN_PAGE, LIST_SCAN_MAX - scanned), **query)
            scanned += len(page)
            for row in page:
                row = _with_page(row)
                if search and failed_only and not (row.get("last_push_status") == "manual"
                                                   or row.get("last_push_reason")):
                    continue
                if direction and row.get("direction") != direction:
                    no_direction += not row.get("direction")
                    continue
                matched.append(row)
            if len(page) < LIST_SCAN_PAGE:
                complete = True
                break
        rows, total = matched[offset:offset + limit], len(matched)
        if tool_input.get("totals"):
            out["totals"] = _totals(matched)
        if no_direction:
            out["without_direction"] = no_direction
        if not complete:
            out["complete"] = False
            out["note"] = (f"Only the newest {LIST_SCAN_MAX} documents were checked; narrow the dates to cover "
                           "the rest.")
    next_offset = offset + len(rows)
    return _json({"documents": rows, "count": len(rows), "total": total, "offset": offset,
                  "next_offset": next_offset if next_offset < total else None, **out})


# 推送要证明这份账套的目录刚由小助手读过（`targets/freeze.py`）；网页选定账套时会先请小助手读一次，对话里没有这一步，
# 没读过的账套就报这几种。遇到时请小助手读这份账套、等它读完再试一次（同网页「刷新」，2026-10-10 测试 t9pdem2）。
CATALOG_STALE = ("catalog_refresh_invalid", "catalog_refresh_required", "catalog_refresh_stale", "catalog_refresh_pending")
REFRESH_WAIT_SECONDS = 60


def _catalog_stale(error) -> bool:
    text = str(error)
    return any(code in text or _(code) in text for code in CATALOG_STALE)


def _refresh_account(connection, account_set) -> tuple[str, str]:
    """请小助手读这份账套的目录与主档，等它读完（最多一分钟）；返回（最后的状态, 失败原因代码）。"""
    try:
        request = connections_api.request_refresh(connection, "assistant:" + uuid.uuid4().hex, account_set)
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return "failed", str(error)[:120]
    status, code = request.get("status"), request.get("error_code") or ""
    deadline = time.monotonic() + REFRESH_WAIT_SECONDS
    while status not in ("succeeded", "failed") and time.monotonic() < deadline:
        time.sleep(3)
        frappe.db.rollback()  # 小助手读完是在别的请求里提交的
        try:
            latest = connections_api.refresh_status(connection, request["request_id"])
        except (frappe.ValidationError, frappe.PermissionError) as error:
            frappe.clear_messages()
            return "failed", str(error)[:120]
        status, code = latest.get("status"), latest.get("error_code") or ""
    return status or "failed", code or ("timeout" if status not in ("succeeded", "failed") else "")


def _choices_view(name, connection, account_set, posting_kind):
    """同复核页的「要人选的项」；目录没读过就先请小助手读一次再看。"""
    frappe.db.commit()  # choices_view 只看不记、结束时整笔回滚：先把前面的落掉
    try:
        return application.choices_view(name, connection, account_set, posting_kind)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        if not _catalog_stale(error):
            raise
        status, code = _refresh_account(connection, account_set)
        if status != "succeeded":
            # 小助手也读不到（账套在 Express 里删了 / 改名了、所在的共享盘连不上）：说清楚，别让用户再去「刷新」
            frappe.throw("The computer could not read this account set ({0}). It may have been removed or renamed in "
                         "Express, or its folder is offline. Choose another account set.".format(code or status))
    return application.choices_view(name, connection, account_set, posting_kind)


def _key(name, version, last) -> str:
    seed = f"{name}\0{version}\0{last or ''}"
    return "assistant:" + hashlib.sha256(seed.encode()).hexdigest()[:56]


def _push_one(name, connection=None, account_set_key=None, posting_kind=None) -> dict:
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
    # 推到哪个账套：用户这次说的优先（例如仅识别的单据要录进某个账套），否则用识别时选的口径
    account_set = account_set_key or (doc.push_account_set if not connection or connection == doc.push_connection
                                      else None) or None
    posting_kind = posting_kind or doc.posting_kind or None
    try:
        view = _choices_view(name, target, account_set, posting_kind)
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
    return _json({"results": [_push_one(name, tool_input.get("connection") or None,
                                        tool_input.get("account_set_key") or None,
                                        tool_input.get("posting_kind") or None) for name in names]})


# ---------------- 对话里的票据：上传识别 ----------------

RECOGNIZE_MAX = 10
RECOGNIZE_WAIT_SECONDS = 90
FINISHED = ("saved", "not_invoice")
BROKEN = ("failed", "uncertain", "cancelled")
ITEMS_SHOWN = 30
# 对话里能交给识别的：票据照片与 PDF（HEIC 照片在对话上传时已转成 JPEG），以及表格（改好发回的复核工作簿走同一条
# 识别路，同网页上传）。文本、Word 等不在这里识别：说不清是什么单据，识别服务只会回「文件是空的」这种误导的话。
RECOGNIZABLE = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif", ".xlsx", ".xls", ".xlsm",
                ".csv")


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
                "reason": "Only document photos, PDF files and spreadsheets (such as a returned review workbook) "
                          "can be recognized."}
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


def _selectable(choices) -> bool:
    """识别时选的账套还在可选清单里（同网页下拉：在用、Express 可写），免得把不存在的账套写进单据口径。"""
    from pearnly_cowork.connections import account_search

    try:
        catalog = connections.targets(choices["connection"])
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.clear_messages()
        return False
    usable = account_search.usable(choices.get("target"), catalog.get("account_sets") or [])
    wanted = str(choices["account_set_key"]).casefold()
    return any(str(row["source_id"]).casefold() == wanted for row in usable)


def recognize_attachments(tool_input) -> str:
    scope.require("intake")
    file_ids = [str(f) for f in (tool_input.get("files") or [])][:RECOGNIZE_MAX]
    if not file_ids:
        frappe.throw(_("No attachments were given."))
    choices = _choices(tool_input)
    if choices.get("account_set_key") and not _selectable(choices):
        return _json({"results": [{"file": f, "status": "not_started", "reason": "account_set_unavailable: this account "
                                   "set is not in the current list; choose one from cowork_intake_options"}
                                  for f in file_ids]})
    if choices.get("target") == "express" and choices.get("account_set_key"):
        # 同网页选定 Express 账套后：请小助手趁识别时读这份账套，推送前就有它的目录与主档（读不到不拦识别）
        try:
            connections_api.request_refresh(choices["connection"], "assistant:" + uuid.uuid4().hex,
                                            choices["account_set_key"])
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError):
            frappe.db.rollback()
            frappe.clear_messages()
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


def _blank_template(row) -> bool:
    """Express 每份数据文件夹里自带的空白模板账套（DAT 文件夹）排到最后：没人往里录。"""
    return str(row.get("source_id") or "").replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1].casefold() == "dat"


def _year(row) -> int:
    """账套所在年度（Express 数据文件夹名里的两位年份，如 70EXP；MR.ERP 的年度名），排序用：新的在前。"""
    import re

    attributes = row.get("attributes") or {}
    text = " ".join(str(attributes.get(key) or "") for key in ("root_label", "root", "year", "year_name"))
    years = [int(y) for y in re.findall(r"(?<!\d)(\d{2}|25\d{2})(?!\d)", text)]
    return max(years, default=-1)


def _last_used() -> dict:
    """上一次录进 ERP 的识别用的口径（不论在网页、LINE 还是这里；仅识别的不算）：问过一次就沿用，不另存一份。
    识别批次的读权限不对普通用户开放列表，这里按「本人提交的」明确过滤（actor 即提交人）。"""
    rows = frappe.get_all(BATCH, filters={"actor": frappe.session.user, "intake_target": ["in", ["express", "mrerp"]]},
                           fields=["intake_target", "target_connection", "target_account_set", "direction",
                                   "posting_kind", "payment"], order_by="creation desc", limit_page_length=1)
    if not rows:
        return {}
    row = rows[0]
    return _filled({"target": row.get("intake_target"), "connection": row.get("target_connection"),
                    "account_set_key": row.get("target_account_set"), "direction": row.get("direction"),
                    "posting_kind": row.get("posting_kind"), "payment": row.get("payment")})


def _still_available(last, targets) -> bool:
    for item in targets:
        if item.get("connection") == last.get("connection"):
            if item.get("state") != "ready":
                return False
            return item.get("_keys") is None or str(last.get("account_set_key") or "").casefold() in item["_keys"]
    return False


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
            item["_keys"] = {str(r["source_id"]).casefold() for r in usable}
            item["account_sets_total"] = len(usable)
            if search:
                found = account_search.search(adapter, usable, search)
            else:
                remembered = str(last.get("account_set_key") or "").casefold() \
                    if last.get("connection") == row["name"] else ""
                ranked = sorted(usable, key=lambda r: (str(r["source_id"]).casefold() != remembered,
                                                       _blank_template(r), -_year(r)))
                found = [{"value": r["source_id"], "label": account_search.label(adapter, r)} for r in ranked]
            item["account_sets"] = [{"key": r["value"], "label": r["label"]} for r in found[:OPTIONS_SHOWN]]
            if not usable:
                item["account_sets_status"] = catalog.get("status") or "missing"
        targets.append(item)
    targets.append({"target": "none", "state": "ready"})
    extra = {}
    if last and not _still_available(last, targets):
        # 上次用的账套已不在可选清单（删了、改名、不可写或那条连接停了）：不推荐，告诉用户另选
        extra["last_used_unavailable"] = last
        last = {}
    for item in targets:
        item.pop("_keys", None)
    return _json({"suggested": last or None, **extra, "targets": targets,
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


ORIGINAL_PAGES_MAX = 3


def document_original(tool_input) -> str:
    """同复核页右侧的原件：把单据的原件页图放进对话（智能体能看着原件核对字段，用户能点开看）。
    取图与网页同一入口（`files.service.page_source` 判权限 → 存好的预览图或现画一页），并照网页记访问日志。"""
    import requests
    from frappe.core.doctype.access_log.access_log import make_access_log
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.files import access as file_access
    from pearnly_cowork.files import service as file_service
    from pearnly_cowork.ocr import originals

    name = str(tool_input.get("document") or "")
    try:
        doc = storage.read(name)
        pages = sorted(file_access.page_indices(doc.source_page_indices))
        wanted = [int(tool_input["page"])] if tool_input.get("page") else pages[:ORIGINAL_PAGES_MAX]
        shown = []
        for number in wanted:
            source, number = file_service.page_source(name, number)
            link = file_service.preview_link(source, number)
            data = requests.get(link, timeout=30).content if link else file_service.render_page(source, number)
            make_access_log(doctype="Cowork OCR History", document=name, file_type="page_png")
            kind = originals.preview_type(data)
            label = re.sub(r"[^\w.-]+", "-", str(doc.invoice_no or name)).strip("-") or "document"
            shown.append(chat_files.add_to_conversation(f"{label}-p{number}.{'jpg' if kind == 'image/jpeg' else 'png'}",
                                                        data, kind))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError, requests.RequestException) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_available", "reason": str(error)[:300]})
    return _json({"document": name, "pages_total": len(pages), "files": shown})


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


# ---------------- 推送前要人选的项、结果不明的确认（同复核页） ----------------

CHOICE_OPTIONS_SHOWN = 30


def _push_target_of(doc, tool_input):
    connection = tool_input.get("connection") or doc.push_connection
    account_set = tool_input.get("account_set_key") or doc.push_account_set or None
    return connection, account_set


def push_choices(tool_input) -> str:
    """Express 推送前这张单据在这份账套里要人选一次的（对方档 / 商品 / 费用科目 / 付款 / 抵进项 / 部门）与可选值。"""
    scope.require("intake")
    name = str(tool_input.get("document") or "")
    try:
        doc = storage.read(name)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return _json({"document": name, "status": "not_found"})
    connection, account_set = _push_target_of(doc, tool_input)
    if not connection:
        return _json({"document": name, "status": "failed", "reason": "no_push_target"})
    try:
        view = _choices_view(name, connection, account_set, tool_input.get("posting_kind") or doc.posting_kind)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "failed", "reason": str(error)[:300]})
    search = str(tool_input.get("search") or "").strip().casefold()
    options = {}
    for kind, rows in (view.get("options") or {}).items():
        found = [r for r in rows if not search or search in f"{r.get('code')} {r.get('name')}".casefold()]
        options[kind] = {"total": len(found), "shown": found[:CHOICE_OPTIONS_SHOWN]}
    out = {"document": name, "connection": connection, "account_set": view.get("account_set") or account_set,
           "needed": view.get("needed") or [], "options": options}
    if view.get("fixed"):
        # 来自定时任务、推往该任务账套的票：任务默认处理已定的类别（推送服务按任务取，不再问、不记进账套）
        out["task_defaults"] = view["fixed"]
    return _json(out)


def save_push_choices(tool_input) -> str:
    scope.require("intake")
    name = str(tool_input.get("document") or "")
    try:
        doc = storage.read(name)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return _json({"document": name, "status": "not_found"})
    connection, account_set = _push_target_of(doc, tool_input)
    try:
        result = application_api.save_choices(name, connection, account_set, json.dumps(tool_input.get("items") or []))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_saved", "reason": str(error)[:300]})
    return _json({"document": name, "status": "saved", "result": result})


def resolve_push_result(tool_input) -> str:
    """推送结果不明：用户在 ERP 里核对后告诉 CC 已入账（可带 ERP 单号）或未入账（之后可重推）。"""
    scope.require("intake")
    name = str(tool_input.get("document") or "")
    try:
        doc = storage.read(name)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return _json({"document": name, "status": "not_found"})
    if not doc.last_push_record:
        return _json({"document": name, "status": "not_saved", "reason": "This document has no push to confirm."})
    try:
        result = application_api.resolve_unknown(doc.last_push_record, 1 if tool_input.get("entered") else 0,
                                                 str(tool_input.get("docnum") or "") or None)
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_saved", "reason": str(error)[:300]})
    return _json({"document": name, "status": "saved", "push": _filled(
        {key: result.get(key) for key in ("status", "docnum", "reason")} if isinstance(result, dict) else {})})


# ---------------- 删除记录、导出复核工作簿（同录入工作台列表） ----------------

DELETE_MAX = 500
EXPORT_MAX = 100
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def delete_documents(tool_input) -> str:
    """同列表「删除」：真删记录与原件，已推送的跳过（由删除服务判断）。"""
    from pearnly_cowork.history import removal

    scope.require("intake")
    names = [str(n) for n in (tool_input.get("documents") or [])][:DELETE_MAX]
    if not names:
        frappe.throw(_("No documents were given."))
    try:
        result = removal.delete(json.dumps(names))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_deleted", "reason": str(error)[:300]})
    return _json(result)


def export_review_workbook(tool_input) -> str:
    """同列表「导出复核工作簿」（仅识别的单据给会计在 Excel 里核对）；文件放进对话，智能体再放进结果目录。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.roundtrip import service as roundtrip
    from pearnly_cowork.roundtrip.export import ExportError

    scope.require_read()
    names = list(dict.fromkeys(str(n) for n in (tool_input.get("documents") or [])))[:EXPORT_MAX]
    if not names:
        frappe.throw(_("No documents were given."))
    try:
        content = roundtrip.export(json.dumps(names))
    except ExportError as error:
        return _json({"status": "not_exported", "reason": _(error.code)})
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.clear_messages()
        return _json({"status": "not_exported", "reason": str(error)[:300]})
    # 每次导出一个新名字：同名会盖掉上一份，对话里就不再出新的文件卡片
    # 站点时区的时间（容器里的 time.strftime 是 UTC，2026-10-11 测试：凌晨 2 点导出的文件名写成前一天 19 点）
    stamp = frappe.utils.now_datetime().strftime("%Y%m%d-%H%M%S")
    path = chat_files.add_to_conversation(f"cowork-review-{stamp}.xlsx", content, XLSX)
    return _json({"status": "exported", "documents": len(names), "file": path})


# ---------------- 对账：银行对账、收入对账、销项税核查（同一套 recon_center，按 kind） ----------------

RECON_KINDS = ("bank", "income", "vat")
RECON_WAIT_SECONDS = 90
RECON_DEDUP_SECONDS = 600
RECON_STILL_RUNNING = ("Still running in the background. Tell the user it is still being reconciled and that you "
                       "will give the result when they ask; do not wait with sleep.")
RECON_ROWS_SHOWN = 30


def _recon_kind(tool_input) -> str:
    kind = str(tool_input.get("kind") or "")
    if kind not in RECON_KINDS:
        frappe.throw(_("reconciliation.type_invalid"))
    return kind


def _recon_status(job) -> dict:
    from pearnly_cowork.recon_center import jobs

    state = jobs.status(job)
    files = state.get("files") or []
    return _filled({"job": job, "state": state.get("state"), "stage": state.get("stage"), "task": state.get("task"),
                    "error": state.get("error"), "title": state.get("title"),
                    "pages": sum(int(f.get("pages") or 0) for f in files) or None,
                    "pages_done": sum(int(f.get("done") or 0) for f in files) or None})


def _recon_wait(job) -> dict:
    """等到对账结束或等满 RECON_WAIT_SECONDS；每次都重新读（后台进程刚写的状态）。"""
    from pearnly_core.assistant import toolrun

    state = {}
    deadline = time.monotonic() + RECON_WAIT_SECONDS
    while True:
        toolrun.fresh()
        state = _recon_status(job)
        if state.get("state") not in ("queued", "running") or time.monotonic() >= deadline:
            break
        time.sleep(4)
    if state.get("state") in ("queued", "running"):
        state["note"] = RECON_STILL_RUNNING
    return state


def _recon_started_key(kind, sides) -> str:
    conversation = (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or ""
    files = "|".join(",".join(sides.get(side) or []) for side in ("left", "right"))
    return f"pearnly_assistant:recon:{frappe.local.site}:{conversation}:{kind}:{files}"


def recon_start(tool_input) -> str:
    """同对账页「新建」：两口文件取自对话附件（银行：账单 / 总账；收入：总账 / 销项税报告；销项核查：报告 / 发票）。
    同一对话同一组附件只开一套（模型一次发两个同样的调用时，第二个接着等第一套，不重复对账）。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.recon_center import jobs

    kind = _recon_kind(tool_input)
    ids = {side: [str(f) for f in tool_input.get(side + "_files") or []] for side in ("left", "right")}
    key = _recon_started_key(kind, ids)
    if not frappe.cache.set(key, "", nx=True, ex=RECON_DEDUP_SECONDS):
        job = ""
        for _attempt in range(30):  # 第一个调用还在存文件、排任务：等它记下任务号
            job = (frappe.cache.get(key) or b"").decode()
            if job:
                break
            time.sleep(1)
        if job:
            return _json(_recon_wait(job))
    sides = {}
    for side in ("left", "right"):
        sides[side] = []
        for file_id in ids[side]:
            try:
                sides[side].append(chat_files.attachment(file_id))
            except frappe.ValidationError:
                frappe.clear_messages()
                frappe.cache.delete(key)
                return _json({"status": "not_started", "reason": f"attachment {file_id} is not in this conversation",
                              "attachments_in_this_chat": chat_files.attachments()})
    try:
        started = jobs.start(kind, str(tool_input.get("title") or ""), sides, {"lang": frappe.local.lang or "th"})
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.cache.delete(key)
        return _json({"status": "not_started", "reason": str(error)[:300]})
    frappe.cache.set(key, started["job"], ex=RECON_DEDUP_SECONDS)
    return _json(_recon_wait(started["job"]))


def recon_status(tool_input) -> str:
    try:
        return _json(_recon_wait(str(tool_input.get("job") or "")))
    except frappe.DoesNotExistError:
        frappe.clear_messages()
        return _json({"job": tool_input.get("job"), "state": "not_found"})


def recon_list(tool_input) -> str:
    from pearnly_cowork.recon_center import api as recon_api

    kind = _recon_kind(tool_input)
    # 页面列表接口回 {"tasks", "total"}（2026-10-10 测试：读成 data，三种对账都列成空的）
    page = recon_api.tasks(kind, 0, max(1, min(int(tool_input.get("limit") or 20), LIST_MAX))) or {}
    rows = page.get("tasks") if isinstance(page, dict) else page
    return _json({"kind": kind, "total": page.get("total") if isinstance(page, dict) else None,
                  "tasks": [_filled(dict(r)) for r in rows or []]})


def recon_result(tool_input) -> str:
    """结果页的要点：标题、状态、系统提示、要定一次的问题；银行 / 收入另给没对上的行（最多 30 行）。"""
    from pearnly_cowork.recon_center import api as recon_api
    from pearnly_cowork.recon_center import sheets

    kind = _recon_kind(tool_input)
    task = str(tool_input.get("task") or "")
    try:
        view = sheets.load(kind, task, frappe.local.lang or "th")
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return _json({"task": task, "status": "not_found"})
    if view.get("moved"):
        return _json({"task": task, "moved_to": view["moved"]})
    head = view.get("task") or {}
    out = {key: head.get(key) for key in ("name", "title", "status", "notes", "questions")}
    if kind in ("bank", "income"):
        # 汇总用对账自己存的数（与结果页同一份）；没对上的行翻完全部明细再数（一页最多 200 行，2026-10-10 测试只看了
        # 第一页：368 笔流水说成 200 行、未匹配说成 0）。
        rows, total, summary = [], None, None
        while total is None or len(rows) < min(total, RECON_ROWS_READ):
            page = recon_api.detail(kind, task, len(rows), RECON_PAGE) or {}
            total, summary = int(page.get("total") or 0), page.get("summary")
            rows += page.get("rows") or []
            if not page.get("rows"):
                break
        unmatched = [r for r in rows if not _recon_row_matched(kind, r)]
        out.update({"summary": summary, "rows_total": total, "unmatched_total": len(unmatched),
                    "unmatched": [_recon_row(r) for r in unmatched[:RECON_ROWS_SHOWN]]})
        if kind == "bank":
            out["matched_by_kind"] = _recon_matched_by_kind(rows)
    else:
        # 销项核查：逐张对照由存下的两边输入现算（同结果页、导出）；只给有问题的（2026-10-10 测试：没有明细，
        # 智能体只好先导出工作簿再读）
        from pearnly_cowork.reconciliation import vat_check_service

        doc = vat_check_service.task(task)
        _content, summary = vat_check_service.compute(vat_check_service.stored_inputs(doc),
                                                       frappe.local.lang or "th")
        rows = summary.get("rows") or []
        issues = [r for r in rows if r.get("status_key") not in VAT_FINE]
        counted = {}
        for r in issues:
            counted[r.get("status") or r.get("status_key")] = counted.get(r.get("status") or r.get("status_key"), 0) + 1
        out.update({"summary": {key: summary.get(key) for key in ("n_total", "n_ok", "n_diff", "n_void",
                                                                    "diff_amount_total")},
                    "problems_by_type": counted,
                    "rows_total": len(rows), "unmatched_total": len(issues),
                    "unmatched": [_filled({"status": r.get("status"), "customer": r.get("customer"),
                                           "invoice_no": r.get("invoice_no"), "amount_invoice": r.get("amount_inv"),
                                           "amount_report": r.get("amount_rep"),
                                           "differences": _filled(dict(r.get("dims") or {}))})
                                  for r in issues[:RECON_ROWS_SHOWN]]})
    return _json(_filled(out))


# 销项核查里不算问题的：一致、现金单、作废（含报告里作废、没有发票的）
VAT_FINE = ("st_ok", "st_cash", "st_void", "st_void_no_inv")


RECON_PAGE = 200
RECON_ROWS_READ = 5000
RECON_ROW_SKIP = ("name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "record_id", "workspace",
                  "task", "row_index", "parent", "parentfield", "parenttype")


def _recon_row_matched(kind, row) -> bool:
    """银行：配上了才算；收入：总账里有、且金额没差额。"""
    if kind == "bank":
        return str(row.get("match_status") or "") == "matched"
    return bool(row.get("has_gl_match")) and not Decimal(str(row.get("diff") or 0))


RECON_SIDES = (("bank_deposits", "stmt_deposit"), ("bank_withdrawals", "stmt_withdrawal"),
               ("gl_debits", "gl_debit"), ("gl_credits", "gl_credit"))


def _recon_matched_by_kind(rows) -> list:
    """银行对账配上的行按匹配依据（L1–L6，同结果页「匹配依据」栏）分类：每类几组、几行、两边各多少钱。
    两边合计对不上的数，多半就是某一类组里另一边的金额：2026-10-11 TTB 总账贷方比银行支出多 5,306.04，是 L6 组里
    总账实际记的刷卡手续费；只给没配上的行时，智能体看不到这一层，猜成冲销或转账。"""
    from pearnly_cowork.reconciliation.export import labels

    kinds = {}
    for row in rows:
        if str(row.get("match_status") or "") != "matched":
            continue
        layer = row.get("match_layer")
        item = kinds.setdefault(layer, {"groups": set(), "lines": 0, **{name: Decimal(0) for name, _f in RECON_SIDES}})
        item["lines"] += 1
        if row.get("group_no"):
            item["groups"].add(row["group_no"])
        for name, field in RECON_SIDES:
            item[name] += Decimal(str(row.get(field) or 0))
    out = []
    for layer in sorted(kinds, key=lambda value: (value is None, value or 0)):
        item = kinds[layer]
        out.append({"kind": labels.layer_label(layer, frappe.local.lang or "th") or str(layer or ""),
                    "groups": len(item["groups"]), "lines": item["lines"],
                    **{name: f"{item[name]:.2f}" for name, _f in RECON_SIDES if item[name]}})
    return out


def _recon_row(row) -> dict:
    return _filled({key: value for key, value in dict(row).items() if key not in RECON_ROW_SKIP})


def recon_answer(tool_input) -> str:
    from pearnly_cowork.recon_center import presets as recon_presets

    kind = _recon_kind(tool_input)
    try:
        result = recon_presets.answer(kind, str(tool_input.get("task") or ""), str(tool_input.get("rule") or ""),
                                      str(tool_input.get("value") or ""), tool_input.get("subject") or None,
                                      frappe.local.lang or "th")
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": str(error)[:300]})
    if isinstance(result, dict) and result.get("job"):
        return _json({"status": "saved", "rerun_job": result["job"],
                      "note": "The reconciliation runs again from the original files; check it with cowork_recon_status."})
    head = (result or {}).get("task") or {}
    return _json({"status": "saved", "task": head.get("name"), "questions": head.get("questions"),
                  "notes": head.get("notes")})


def recon_presets(tool_input) -> str:
    from pearnly_cowork.recon_center import presets as recon_presets

    kind = _recon_kind(tool_input)
    action = tool_input.get("action") or "list"
    try:
        if action == "change":
            rows = recon_presets.change(kind, tool_input.get("scope_key"), tool_input.get("rule"), tool_input.get("value"))
        elif action == "remove":
            rows = recon_presets.remove(kind, tool_input.get("scope_key"), tool_input.get("rule"))
        else:
            rows = recon_presets.listing(kind)
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": str(error)[:300]})
    return _json({"kind": kind, "presets": rows})


def recon_export(tool_input) -> str:
    """结果工作簿（须已完成），放进对话、结果面板下载。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.reconciliation import bank_recon_service, income_recon_service, vat_check_service

    kind = _recon_kind(tool_input)
    service = {"bank": bank_recon_service, "income": income_recon_service, "vat": vat_check_service}[kind]
    task = str(tool_input.get("task") or "")
    try:
        built = service.export(task, frappe.local.lang or "th")
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.clear_messages()
        return _json({"status": "not_exported", "reason": str(error)[:300]})
    if isinstance(built, dict):
        filename, content = built.get("filename"), built.get("content")
    elif isinstance(built, (tuple, list)):
        filename, content = built[0], built[1]
    else:
        filename, content = None, built
    filename = str(filename or f"recon-{kind}-{task}.xlsx")
    path = chat_files.add_to_conversation(filename, content, XLSX)
    return _json({"status": "exported", "file": path})


def recon_delete(tool_input) -> str:
    from pearnly_cowork.recon_center import api as recon_api

    kind = _recon_kind(tool_input)
    names = [str(n) for n in (tool_input.get("tasks") or [])]
    if not names:
        frappe.throw(_("No tasks were given."))
    try:
        result = recon_api.delete(kind, json.dumps(names))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_deleted", "reason": str(error)[:300]})
    return _json(result if isinstance(result, dict) else {"deleted": result})


# ---------------- 库存盘点（同盘点页，`stocktake.desk_api`） ----------------

STOCK_ROWS_SHOWN = 40


def _call(fn, *args, **kwargs) -> dict:
    """调页面同一个接口；业务拒绝回 not_saved 与原因（已翻译），不抛给模型。"""
    try:
        result = fn(*args, **kwargs)
        frappe.db.commit()
        return result if isinstance(result, dict) else {"result": result}
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": "not_saved", "reason": str(error)[:300]}


def stocktake_list(tool_input) -> str:
    from pearnly_cowork.stocktake import desk_api

    return _json(_call(desk_api.api_list_stock_counts, 0, max(1, min(int(tool_input.get("limit") or 20), 100))))


def stocktake_template(_input) -> str:
    """建盘点用的 Excel 模板（产品代码、名称、单位、账面数，可带实盘数），放进对话给用户下载填写。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.stocktake import upload

    path = chat_files.add_to_conversation("stocktake-template.xlsx", upload.template(), XLSX)
    return _json({"status": "ready", "file": path})


def stocktake_create(tool_input) -> str:
    """同盘点页「新建」：用对话里发来的 Excel（账面数，可带实盘数）建一张盘点单。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.stocktake import upload

    try:
        _name, content = chat_files.attachment(str(tool_input.get("file") or ""))
    except frappe.ValidationError:
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": "This attachment is not in this conversation.",
                      "attachments_in_this_chat": chat_files.attachments()})
    # 盘点单号要 UUID：按对话 + 附件算一个固定的，同一份表重复建只得到同一张（页面接口的幂等）
    conversation = (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or ""
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pearnly-assistant:{conversation}:{tool_input.get('file')}"))
    title = (str(tool_input.get("title") or "").strip() or _name.rsplit(".", 1)[0])[:120]
    return _json(_call(upload.create, title, key, content))


def stocktake_report(tool_input) -> str:
    """盘点结果：各项账面数、实盘数、差异；默认只列有差异或还没盘的。"""
    from pearnly_cowork.stocktake import desk_api

    count = str(tool_input.get("count") or "")
    data = _call(desk_api.api_stock_count_review, count)
    if data.get("status") == "not_saved":
        return _json(data)
    items = data.get("items") or []
    if not tool_input.get("all_items"):
        items = [r for r in items if not r.get("counted") or r.get("difference")]
    keep = ("code", "product_name", "unit", "warehouse", "location", "book_qty", "counted_qty", "difference")
    rows = [{k: r.get(k) for k in keep if r.get(k) not in (None, "")} | {"counted": bool(r.get("counted"))}
            for r in items[:STOCK_ROWS_SHOWN]]
    head = data.get("task") or {}
    return _json({"count": count, "title": head.get("title"), "status": head.get("status"),
                  "items_total": head.get("item_total"), "counted_total": head.get("counted_total"),
                  "difference_total": head.get("difference_total"), "listed": len(items), "items": rows})


def stocktake_count(tool_input) -> str:
    """按产品代码或条码记一笔实盘数（同扫码录入：同一产品多笔累加）；recount 先清掉这个产品已记的数再记。"""
    from pearnly_cowork.stocktake import desk_api

    count = str(tool_input.get("count") or "")
    found = _call(desk_api.api_stock_count_lookup, count, str(tool_input.get("code") or ""))
    if found.get("status") == "not_saved":
        return _json(found)
    item = found["product"]["name"]
    # 仓库必填（同页面录入框「实际仓库」）：没说就用这一项账面上的仓库、库位（页面也是默认带出账面位置）
    book = (found.get("rows") or [{}])[0]
    warehouse = str(tool_input.get("warehouse") or book.get("warehouse") or "").strip()
    location = str(tool_input.get("location") or book.get("location") or "").strip()
    if tool_input.get("recount"):
        cleared = _call(desk_api.api_stock_count_recount, count, item)
        if cleared.get("status") == "not_saved" or tool_input.get("qty") is None:
            return _json(cleared)
    if tool_input.get("qty") is None:
        return _json({"status": "not_saved", "reason": "qty is required."})
    return _json(_call(desk_api.api_stock_count_add_entry, count, tool_input.get("qty"), item=item,
                       warehouse=warehouse, location=location, request_id=str(uuid.uuid4())))


def stocktake_close(tool_input) -> str:
    from pearnly_cowork.stocktake import desk_api

    return _json(_call(desk_api.api_stock_count_close, str(tool_input.get("count") or "")))


def stocktake_export(tool_input) -> str:
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.stocktake import service as stocktake_service

    count = str(tool_input.get("count") or "")
    try:
        content = stocktake_service.export(count)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.clear_messages()
        return _json({"status": "not_exported", "reason": str(error)[:300]})
    # 文件名用盘点名称 + 日期（不用内部单号，2026-10-10 测试）；文件名里不能有的字符换成「-」
    title = frappe.db.get_value("Cowork Stocktake Task", count, "title") or "stocktake"
    name = re.sub(r'[\\/:*?"<>|]+', "-", str(title)).strip(" .-")[:80] or "stocktake"
    stamp = frappe.utils.now_datetime().strftime("%Y%m%d")
    return _json({"status": "exported",
                  "file": chat_files.add_to_conversation(f"{name}-{stamp}.xlsx", content, XLSX)})


def stocktake_delete(tool_input) -> str:
    from pearnly_cowork.stocktake import desk_api

    names = [str(n) for n in (tool_input.get("counts") or [])]
    if not names:
        frappe.throw(_("No tasks were given."))
    results = {name: _call(desk_api.api_stock_count_delete, name) for name in names}
    return _json({"deleted": [n for n, r in results.items() if r.get("status") != "not_saved"],
                  "not_deleted": {n: r["reason"] for n, r in results.items() if r.get("status") == "not_saved"}})


# ---------------- 任务管理（团队看板，同任务页，`work.api`） ----------------

WORK_CARDS_SHOWN = 100


def _card_brief(card, lists) -> dict:
    return _filled({"card": card.get("name"), "title": card.get("title"), "list": lists.get(card.get("board_list")),
                    "due": card.get("due_date"), "done": bool(card.get("completed")),
                    "assignees": card.get("assignees"), "labels": card.get("labels")})


def work_boards(_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.boards_list))


def work_board(tool_input) -> str:
    """一块看板（不给就是默认看板）：各列表与卡片要点（标题、列表、截止、完成、负责人、标签）。"""
    from pearnly_cowork.work import api as work_api

    data = _call(work_api.board, tool_input.get("board") or None)
    if data.get("status") == "not_saved":
        return _json(data)
    lists = {row.get("name"): row.get("title") for row in data.get("lists") or []}
    cards = [c for c in data.get("cards") or [] if not c.get("is_template") and not c.get("archived")]
    if tool_input.get("mine"):
        cards = [c for c in cards if frappe.session.user in (c.get("assignees") or [])]
    return _json({"board": data.get("board"),
                  "lists": [_filled({"list": row.get("name"), "title": row.get("title"),
                                     "done_list": bool(row.get("is_done_list"))}) for row in data.get("lists") or []],
                  "cards_total": len(cards), "cards": [_card_brief(c, lists) for c in cards[:WORK_CARDS_SHOWN]]})


def work_card(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.card, str(tool_input.get("card") or "")))


def work_create_card(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.create, str(tool_input.get("title") or ""), str(tool_input.get("list") or "")))


def work_update_card(tool_input) -> str:
    """改卡片（标题、描述、开始 / 截止、提醒、重复、负责人、标签、清单、完成）：带上查卡片时的 modified，别人刚改过就不覆盖。"""
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.update, str(tool_input.get("card") or ""), str(tool_input.get("modified") or ""),
                       json.dumps(tool_input.get("values") or {})))


def work_move_card(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    if tool_input.get("toggle_done"):
        return _json(_call(work_api.toggle_done, str(tool_input.get("card") or "")))
    return _json(_call(work_api.move, str(tool_input.get("card") or ""), str(tool_input.get("list") or "")))


def work_comment(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.comment, str(tool_input.get("card") or ""), str(tool_input.get("content") or "")))


def work_search(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.search, str(tool_input.get("keyword") or ""), tool_input.get("board") or None))


# ---------------- 文件夹定时任务 ----------------

# 一次保存几个任务：允许卡片上一个任务一行，共用层卡片最多 20 行（`PREVIEW_ROWS`），多了会被截掉、用户看不到
FOLDER_BATCH_MAX = 20
TASK_KEYS = ("name", "title", "connection", "account_set_key", "direction", "posting_kind", "payment", "reader",
             "root_id", "folder", "run_day", "run_time")


def _conversation():
    """这次调用所在的对话：在 CC 里存或启用的任务，提醒发回这段对话（任务服务只从参数取，不从表单取）。"""
    return (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or None


def folder_tasks(_input) -> str:
    listing = rules.listing()
    keep = ("name", "title", "enabled", "connection_label", "account_label", "direction", "posting_kind", "payment",
            "folder", "root_label", "run_day", "run_time", "defaults", "remind", "include_existing", "last_run",
            "counts", "results", "waiting", "waiting_partial", "agent_fresh", "folder_state")
    return _json({"tasks": [{k: r.get(k) for k in keep} for r in listing.get("rules") or []]})


def run_folder_task(tool_input) -> str:
    row = rules.run_now(str(tool_input.get("task") or ""))
    return _json({"task": row.get("name"), "title": row.get("title"), "requested": True,
                  "note": "The companion picks this up on its next sync, usually within a minute."})


def folder_task_runs(tool_input) -> str:
    data = rules.runs(str(tool_input.get("task") or ""))
    keep = ("trigger", "started_at", "scanned_at", "found", "new_files", "skipped", "upload_failed", "note",
            "results", "states")
    return _json({"task": data.get("rule"), "runs": [{k: r.get(k) for k in keep} for r in data.get("runs") or []][:10]})


FOLDERS_SHOWN = 50


def _task_call(fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        frappe.db.commit()
        return result
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": "not_saved", "reason": str(error)[:300]}


def folder_task_options(tool_input) -> str:
    """同定时任务表单：能读文件夹的电脑与它们开放给网页的文件夹（只在那台电脑的 CC 里加减）、Express 账套的科目与部门。"""
    search = str(tool_input.get("search") or "").strip().casefold()
    data = _task_call(rules.options, str(tool_input.get("connection") or ""), tool_input.get("account_set_key") or None)
    if data.get("status") == "not_saved":
        return _json(data)
    readers = []
    for reader in data.get("readers") or []:
        roots = []
        for root in reader.get("roots") or []:
            folders = [f for f in root.get("folders") or [] if not search or search in str(f).casefold()]
            item = {"root_id": root.get("id"), "label": root.get("label"), "folders_total": len(folders),
                    "folders": folders[:FOLDERS_SHOWN]}
            # 小助手报的各文件夹（含子文件夹）现有几份会被录入的文件；没报过就不给，不拿 0 冒充
            counts = root.get("counts") or {}
            shown = {folder: counts[folder] for folder in [""] + item["folders"] if folder in counts}
            if shown:
                item["files"] = shown
                if root.get("partial"):
                    item["files_partial"] = True
            roots.append(item)
        readers.append({"reader": reader.get("connection"), "computer": reader.get("label"), "online": reader.get("fresh"),
                        "roots": roots})
    out = {"computers": readers, "stock_group": data.get("stock_group")}
    for kind in ("accounts", "departments"):
        rows = [r for r in data.get(kind) or [] if not search or search in f"{r.get('code')} {r.get('name')}".casefold()]
        if data.get(kind) is not None:
            out[kind] = {"total": len(rows), "shown": rows[:CHOICE_OPTIONS_SHOWN]}
    return _json(_filled(out))


def _task_rows(items) -> dict:
    """要改的已有任务的现值（任务列表只读一次）；全是新任务就不读。"""
    if not any(isinstance(item, dict) and item.get("name") for item in items):
        return {}
    return {row.get("name"): row for row in rules.listing().get("rules") or []}


def _given_defaults(item):
    """模型给的默认值：对象（或写成 JSON 字符串的对象）；没给为 None。"""
    raw = item.get("defaults")
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _task_values(item, rows) -> dict:
    """要存的一份任务表单。改已有任务：以库里现值为底，只换这次说的几项（保存接口要整份表单；2026-10-10 测试只传了
    账套，缺入账方式被拒）；默认值按类合并、空串清掉这一类；提醒只在说了时带（不带由任务服务保留原值）。
    来源对话不进表单（由 `_conversation` 作参数交给任务服务）。"""
    current = rows.get(str(item.get("name") or ""), {}) if item.get("name") else {}
    values = {key: current[key] for key in TASK_KEYS if current.get(key) not in (None, "")}
    if "run_time" in values:
        values["run_time"] = _time(values["run_time"])
    values.update({key: item[key] for key in TASK_KEYS if key in item})
    given = _given_defaults(item)
    if isinstance(given, dict):
        merged = {**(current.get("defaults") or {}), **given}
        values["defaults"] = {kind: value for kind, value in merged.items() if value not in (None, "")}
    elif given not in (None, ""):
        values["defaults"] = given  # 形状不对：交给任务服务照现有口径拒绝
    elif current.get("defaults"):
        values["defaults"] = dict(current["defaults"])
    if item.get("remind") is not None:
        values["remind"] = 1 if item["remind"] else 0
    return values


def _unknown_defaults(items) -> dict:
    """默认值里不认识的类（模型拼错的键）：任务服务会把它们静默丢掉，这里先拒，指出第几个任务、哪个键。"""
    failed = []
    for number, item in enumerate(items, 1):
        given = _given_defaults(item)
        unknown = [str(kind) for kind in given if kind not in folder_contracts.KINDS] if isinstance(given, dict) else []
        if unknown:
            failed.append({"task": number, "title": str(item.get("title") or item.get("name") or "")[:140],
                           "reason": "unknown default kind: " + ", ".join(unknown)})
    if not failed:
        return {}
    return {"status": "not_saved", "reason": "Nothing was saved. Default kinds are only "
                                            + ", ".join(folder_contracts.KINDS) + ".", "failed": failed}


def _saved_task(row, values) -> dict:
    """存下的任务回给模型：实际存下的默认值与提醒；说了却因不适用（方向、入账方式、ERP）没存的默认值另列。"""
    saved = row.get("defaults") or {}
    given = values.get("defaults") if isinstance(values.get("defaults"), dict) else {}
    out = {"task": row.get("name"), "title": row.get("title"), "enabled": row.get("enabled"), "defaults": saved,
           "remind": row.get("remind")}
    skipped = [kind for kind, value in given.items() if value not in (None, "") and kind not in saved]
    if skipped:
        out["defaults_not_applicable"] = skipped
    return {key: value for key, value in out.items() if value is not None}


def save_folder_task(tool_input) -> str:
    refused = _unknown_defaults([tool_input])
    if refused:
        return _json(refused)
    values = _task_values(tool_input, _task_rows([tool_input]))
    row = _task_call(rules.save, values, cc_session=_conversation())
    return _json(row if row.get("status") == "not_saved" else {"status": "saved", **_saved_task(row, values)})


def save_folder_tasks(tool_input) -> str:
    """一次建 / 改几个任务（一张允许卡片）：全有或全无，任一个不通过就一个都不存，回每个不通过的序号、名称、原因。"""
    items = tool_input.get("tasks")
    if not isinstance(items, list) or not items:
        frappe.throw(_("No tasks were given."))
    if len(items) > FOLDER_BATCH_MAX:
        return _json({"status": "not_saved", "reason": f"At most {FOLDER_BATCH_MAX} tasks at a time; split the list."})
    if not all(isinstance(item, dict) for item in items):
        return _json({"status": "not_saved", "reason": "Each task must be an object."})
    refused = _unknown_defaults(items)
    if refused:
        return _json(refused)
    rows = _task_rows(items)
    values = [_task_values(item, rows) for item in items]
    try:
        result = rules.save_many(values, cc_session=_conversation())
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": str(error)[:300]})
    if result.get("failed"):
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": "Nothing was saved. Fix the tasks listed in failed and call "
                                                       "again with the whole list.", "failed": result["failed"]})
    frappe.db.commit()
    return _json({"status": "saved", "tasks": [_saved_task(row, value)
                                               for row, value in zip(result.get("saved") or [], values)]})


def set_folder_task_enabled(tool_input) -> str:
    enabled = None if tool_input.get("enabled") is None else (1 if tool_input["enabled"] else 0)
    remind = None if tool_input.get("remind") is None else (1 if tool_input["remind"] else 0)
    if enabled is None and remind is None:
        return _json({"status": "not_saved", "reason": "Give enabled, remind or both."})
    row = _task_call(rules.set_enabled, str(tool_input.get("task") or ""), enabled, remind=remind,
                     cc_session=_conversation())
    return _json(row if row.get("status") == "not_saved" else {
        "status": "saved", "task": row.get("name"), "enabled": row.get("enabled"), "remind": row.get("remind")})


def delete_folder_tasks(tool_input) -> str:
    names = [str(n) for n in (tool_input.get("tasks") or [])]
    if not names:
        frappe.throw(_("No tasks were given."))
    return _json(_task_call(rules.delete, json.dumps(names)))


# ---------------- 要「允许」的那一步：卡片上先说清改什么（现在 → 改后），不写任何东西 ----------------

DIRECTION_LABELS = {"purchase": "intake.dir.purchase", "sales": "intake.dir.sales"}
POSTING_LABELS = {"service": "Service", "stock": "Stock"}
PAYMENT_LABELS = {"cash": "Cash", "credit": "Credit"}
POLICY_CLASS_LABELS = {"payment_evidence": "Transfer slips", "withholding_certificate": "Withholding tax certificates"}
POLICY_VALUE_LABELS = {"enter": "cowork.policy.enter", "skip": "cowork.policy.skip"}


def _shown(value) -> str:
    return "" if value in (None, "") else str(value)


def _change(field, before, after) -> dict:
    return {"field": _(field), "before": _shown(before), "after": _shown(after)}


def _time(value) -> str:
    text = _shown(value)
    return text[:5] if len(text) >= 5 and text[2:3] == ":" else ("0" + text)[:5] if text[1:2] == ":" else text


def _task_row(name) -> dict:
    return next((r for r in rules.listing().get("rules") or [] if r.get("name") == name), {}) if name else {}


def _day(value) -> str:
    """每月第几天；0 是月末（同任务页）。"""
    text = _shown(value).strip()
    if not text.isdigit():
        return text
    return _("cowork.folder.day").format(int(text)) if int(text) else _("cowork.folder.lastDay")


def _task_shown(row, account=None, folder=None) -> dict:
    """定时任务各项的显示值（同任务页的叫法）。"""
    return {"cowork.folder.field.title": row.get("title"),
            "intake.target.accountSet": account if account is not None else row.get("account_label"),
            "intake.field.direction": _(DIRECTION_LABELS.get(row.get("direction"), _shown(row.get("direction")))),
            "intake.field.itemType": _(POSTING_LABELS[row["posting_kind"]]) if row.get("posting_kind") in POSTING_LABELS else _shown(row.get("posting_kind")),
            "cowork.choice.payment": _(PAYMENT_LABELS[row["payment"]]) if row.get("payment") in PAYMENT_LABELS else _shown(row.get("payment")),
            "cowork.folder.field.folder": folder if folder is not None else "\\".join(
                p for p in (row.get("root_label"), row.get("folder")) if p),
            "cowork.folder.field.day": _day(row.get("run_day")),
            "cowork.folder.field.time": _time(row.get("run_time"))}


# 默认处理各类的取值叫法（同任务页的选项）；科目、部门显示「代码 · 名称」
DEFAULT_VALUE_LABELS = {"payment": PAYMENT_LABELS,
                        "vat": {"claim": "cowork.folder.vat.claim", "expense": "cowork.folder.vat.expense"},
                        "item": {folder_contracts.NEW: "cowork.folder.item.new", "ask": "cowork.folder.item.ask"}}
REMIND_FIELD = "cowork.folder.field.remind"
PAIRED = tuple(f"cowork.folder.default.{kind}" for kind in folder_contracts.KINDS) + (REMIND_FIELD,)


def _cached(cache, key, read):
    if key not in cache:
        try:
            cache[key] = read()
        except Exception:
            # 卡片只是给人看：读不到名称就照原值显示，不让整张卡片退回逐个列参数
            frappe.clear_messages()
            cache[key] = None
    return cache[key]


def _folder_path(value) -> str:
    """文件夹相对路径统一成「\\」分隔（同任务服务的写法）。"""
    return "\\".join(part.strip() for part in re.split(r"[\\/]+", _shown(value)) if part.strip())


def _same_place(row) -> tuple:
    """读哪台电脑、哪个根目录下的哪个文件夹（比较用：分隔符、大小写不计）。"""
    return (_shown(row.get("reader") or row.get("connection")), _shown(row.get("root_id")),
            _folder_path(row.get("folder")).casefold())


def _account_key(value) -> str:
    return _shown(value).replace("/", "\\").rstrip("\\").casefold()


def _kept_defaults(values, adapter) -> dict:
    """会存下的默认值（同任务服务：只有 Express 有，只留这条任务用得到的类）。"""
    given = values.get("defaults")
    if adapter != "express" or not isinstance(given, dict):
        return {}
    kinds = folder_contracts.applicable(values.get("direction"), values.get("posting_kind"))
    return {kind: _shown(given.get(kind)).strip() for kind in folder_contracts.KINDS
            if kind in kinds and _shown(given.get(kind)).strip()}


def _default_shown(kind, value, names) -> str:
    if not value:
        return ""
    labels = DEFAULT_VALUE_LABELS.get(kind)
    if labels:
        return _(labels[value]) if value in labels else value
    name = ((names or {}).get("accounts" if kind == "account" else "departments") or {}).get(value)
    return f"{value} · {name}" if name else value


def _remind_shown(value) -> str:
    return _("cowork.folder.remind.on") if value else _("cowork.folder.remind.off")


def _waiting_text(values) -> str:
    """文件夹（含子文件夹）里现有几份会被录入的文件（小助手上报的数）：没报过不说，不拿 0 冒充；数到上限说「N+」。"""
    try:
        found = rules.waiting(values.get("reader") or values.get("connection"), values.get("root_id"),
                              _shown(values.get("folder")))
    except Exception:
        frappe.clear_messages()
        return ""
    if not found or found.get("files") is None:
        return ""
    return _("cowork.folder.waiting").format(f"{found['files']}+" if found.get("partial") else found["files"])


def _task_lines(item, rows, cache) -> tuple:
    """卡片上一个任务要列的：（现值，[(字段, 现在, 改后)]，文件夹现有份数那一句）。新任务只列有值的项，改任务只列
    变了的项；份数只在新建或换了文件夹时说（之后看任务列表的待处理数）。"""
    current = rows.get(str(item.get("name") or ""), {}) if item.get("name") else {}
    values = _task_values(item, rows)
    connection, key = values.get("connection"), values.get("account_set_key")
    if current and _account_key(key) == _account_key(current.get("account_set_key")):
        account = current.get("account_label")
    else:
        account = (_cached(cache, ("account", connection, key), lambda: rules.account_label(connection, key))
                   or _shown(key)) if key else ""
    before = _task_shown(current) if current else {}
    moved = not current or _same_place(values) != _same_place(current)
    if moved:
        reader = values.get("reader") or connection
        roots = _cached(cache, ("roots", reader), lambda: {r.get("id"): r.get("label")
                                                           for r in agent.read(reader).get("roots") or []}) or {}
        folder = "\\".join(p for p in (roots.get(values.get("root_id")), _folder_path(values.get("folder"))) if p)
    else:
        folder = before.get("cowork.folder.field.folder", "")
    after = _task_shown(values, account, folder)
    adapter = current.get("adapter") or _cached(cache, ("adapter", connection),
                                                lambda: frappe.db.get_value(CONNECTION, connection, "adapter"))
    kept, had = _kept_defaults(values, adapter), (current.get("defaults") or {}) if current else {}
    names = {}
    for side, chosen, row in (("after", kept, values), ("before", had, current)):
        if any(kind in chosen for kind in ("account", "department")):
            pair = (row.get("connection"), row.get("account_set_key"))
            names[side] = _cached(cache, ("names",) + pair, lambda: rules.master_names(*pair))
    for kind in folder_contracts.KINDS:
        field = f"cowork.folder.default.{kind}"
        before[field] = _default_shown(kind, _shown(had.get(kind)), names.get("before"))
        after[field] = _default_shown(kind, kept.get(kind, ""), names.get("after"))
    # 新任务提醒默认关：卡片写「关 → 开」
    before[REMIND_FIELD] = _remind_shown(current.get("remind"))
    after[REMIND_FIELD] = _remind_shown(values["remind"]) if "remind" in values else before[REMIND_FIELD]
    lines = [(field, _shown(before.get(field)), _shown(text)) for field, text in after.items()
             if _shown(text) != _shown(before.get(field))]
    return current, lines, _waiting_text(values) if moved else ""


def preview_save_folder_task(tool_input) -> list:
    current, lines, waiting = _task_lines(tool_input, _task_rows([tool_input]), {})
    rows = [_change(field, before, after) for field, before, after in lines]
    if current and not any(line[0] == "cowork.folder.field.title" for line in lines):
        rows = [_change("cowork.folder.field.title", "", current.get("title"))] + rows
    if waiting:
        rows.append({"field": "", "before": "", "after": waiting})
    return rows


def _part(field, text) -> str:
    """批量卡片一行里的一段：默认处理与提醒带上项名（单看取值分不清是哪一项）。"""
    if not text:
        return ""
    return _("cowork.folder.pair").format(_(field), text) if field in PAIRED else text


def preview_save_folder_tasks(tool_input) -> list:
    """一张卡片列全部任务，一个任务一行：新任务写全它的口径，改任务只写变了的（现在 → 改后）。"""
    items = [item for item in tool_input.get("tasks") or [] if isinstance(item, dict)][:FOLDER_BATCH_MAX]
    rows, cache, out = _task_rows(items), {}, []
    for item in items:
        current, lines, waiting = _task_lines(item, rows, cache)
        if current:
            title, shown = current.get("title"), lines
        else:
            title = next((after for field, _before, after in lines if field == "cowork.folder.field.title"), "")
            shown = [line for line in lines if line[0] != "cowork.folder.field.title"]
        before = [_part(field, text) for field, text, _after in shown] if current else []
        after = [_part(field, text) for field, _before, text in shown] + [waiting]
        out.append({"field": _shown(title) or _("cowork.folder.new"), "before": " · ".join(p for p in before if p),
                    "after": " · ".join(p for p in after if p)})
    return out


def preview_set_folder_task_enabled(tool_input) -> list:
    row = _task_row(str(tool_input.get("task") or ""))
    state = lambda on: _("cowork.folder.on") if on else _("cowork.folder.off")
    rows = [_change("cowork.folder.field.title", "", row.get("title") or tool_input.get("task"))]
    if tool_input.get("enabled") is not None:
        rows.append(_change("intake.col.status", state(row.get("enabled")) if row else "", state(tool_input["enabled"])))
    if tool_input.get("remind") is not None:
        rows.append(_change(REMIND_FIELD, _remind_shown(row.get("remind")) if row else "",
                            _remind_shown(tool_input["remind"])))
    return rows


def preview_delete_folder_tasks(tool_input) -> list:
    names = {r.get("name"): r.get("title") for r in rules.listing().get("rules") or []}
    return [_change("Delete", "", names.get(str(n)) or n) for n in tool_input.get("tasks") or []]


def preview_choose_document_policy(tool_input) -> list:
    from pearnly_cowork.contracts import document_policy as policy_rules
    from pearnly_cowork.contracts.history import primary_page_fields
    from pearnly_cowork.history import document_policy, storage

    doc = storage.read(str(tool_input.get("document") or ""))
    kind = policy_rules.document_class(primary_page_fields(json.loads(doc.pages or "[]")))
    now = document_policy.read(doc.workspace).get(kind)
    label = lambda value: _(POLICY_VALUE_LABELS[value]) if value in POLICY_VALUE_LABELS else ""
    return [_change(POLICY_CLASS_LABELS.get(kind, kind or "Document"), label(now), label(tool_input.get("value")))]


def preview_stock_group(tool_input) -> list:
    current = _connection_call(connections_api.stock_acc_group, str(tool_input.get("connection") or ""),
                               str(tool_input.get("account_set_key") or ""))
    names = {g.get("acccod"): g.get("label") for g in current.get("groups") or []}
    chosen = current.get("chosen")
    chosen = chosen.get("acccod") if isinstance(chosen, dict) else chosen
    shown = lambda code: names.get(str(code or "").strip()) or _shown(code)
    return [_change("Inventory account group", shown(chosen), shown(tool_input.get("acccod")))]


_OBJ = {"type": "object", "properties": {}}
# 定时任务的各项：单个保存与批量保存共用一份，免得两份说明走样
FOLDER_TASK_PROPS = {
    "name": {"type": "string", "description": "Existing task to change; leave out to create one."},
    "title": {"type": "string"}, "connection": {"type": "string"}, "account_set_key": {"type": "string"},
    "direction": {"type": "string", "enum": ["purchase", "sales"]},
    "posting_kind": {"type": "string", "enum": ["service", "stock"], "description": "Express."},
    "payment": {"type": "string", "enum": ["cash", "credit"], "description": "MR.ERP; purchases are credit."},
    "reader": {"type": "string", "description": "The computer (reader) whose folder is used."},
    "root_id": {"type": "string"}, "folder": {"type": "string", "description": "Subfolder; empty for the root."},
    "run_day": {"type": "integer", "minimum": 0, "maximum": 31, "description": "Day of month; 0 = last day."},
    "run_time": {"type": "string", "description": "HH:MM, Bangkok time."},
    "defaults": {"type": "object", "additionalProperties": False,
                 "description": "Express only. How this task's documents are handled when the choice comes up; a kind "
                                "is used only where it applies (payment and vat: purchases; account: service "
                                "purchases; item: stock). A kind left out or empty stops and asks the first time. "
                                "When changing a task, only the kinds given change; an empty string clears one.",
                 "properties": {
                     "payment": {"type": "string", "enum": ["cash", "credit", ""],
                                 "description": "When the document does not show cash or credit; what the document "
                                                "shows still wins."},
                     "vat": {"type": "string", "enum": ["claim", "expense", ""],
                             "description": "VAT on receipts and abbreviated tax invoices: claim as input VAT, or "
                                            "record as expense."},
                     "account": {"type": "string",
                                 "description": "Expense account code from cowork_folder_task_options. It replaces "
                                                "the account remembered for each supplier for every document of this "
                                                "task (a category-to-account mapping still wins); set it only when the "
                                                "user says they all go to one account."},
                     "item": {"type": "string", "enum": ["__new__", "ask", ""],
                              "description": "A stock item not found: __new__ creates it from the document; ask "
                                             "stops for a choice."},
                     "department": {"type": "string", "description": "Department code from cowork_folder_task_options."}}},
    "remind": {"type": "boolean",
               "description": "Remind the task owner the day before each run, at 08:00 and 14:00 Bangkok time, to put "
                              "the documents in the folder; the reminder is posted in the latest chat it was saved or "
                              "turned on from. Left out keeps the current setting; new tasks start off."}}
TOOLS = [
    {"name": "cowork_recognize_attachments", "label": "Recognized documents",
     "description": "Recognize receipts and invoices the user attached in this chat with Pearnly's own recognition, "
                    "exactly like uploading them on the Intake workbench (same billing, the documents then appear in "
                    "the workbench and can be reviewed and pushed). Use it whenever the user sends a document photo or "
                    "PDF and wants it recognized, read, entered or recorded, or sends back a review workbook (Excel) "
                    "they corrected. Pass each attachment's file id: the folder "
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
                    "(none), and suggested: the choices of the user's previous recognition that went to an ERP, from "
                    "the web, LINE or this chat. Ask once: offer suggested first (say which account set it is), and "
                    "once the user agrees reuse it for the rest of the conversation without asking again. Only ready targets can be chosen. Large lists are cut to 30; pass search (company "
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
     "approval": ["acccod"],
     "ask": "Change the inventory account group?",
     "preview": "pearnly_cowork.assistant.tools.preview_stock_group",
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
     "approval": ["menus", "defaults"],
     "ask": "Change the MR.ERP import settings?",
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
                    "and tell the user. This changes the record in Pearnly only: a document already entered into the "
                    "ERP is not changed there and is not pushed again; tell the user to correct it in the ERP.",
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
                    "for the whole company from now on (the user allows it in the chat). Then push the document "
                    "again.",
     "input_schema": {"type": "object", "properties": {"document": {"type": "string"},
                                                        "value": {"type": "string", "enum": ["enter", "skip"]}},
                      "required": ["document", "value"]},
     "approval": True,
     "ask": "Change how this kind of document is handled?",
     "preview": "pearnly_cowork.assistant.tools.preview_choose_document_policy",
     "handler": "pearnly_cowork.assistant.tools.choose_document_policy"},
    {"name": "cowork_document_original", "label": "Opened the original",
     "description": "Put the original page image(s) of a document into this conversation, like the original beside "
                    "the review page: look at it to check recognized fields against the original, or when the user "
                    "wants to see it (the user can open it from the chat). The first 3 pages unless page is given.",
     "input_schema": {"type": "object", "properties": {"document": {"type": "string"},
                                                        "page": {"type": "integer", "minimum": 1}},
                      "required": ["document"]},
     "handler": "pearnly_cowork.assistant.tools.document_original"},
    {"name": "cowork_recognition_results", "label": "Checked recognition results",
     "description": "Status and recognized documents of recognition tasks started by cowork_recognize_attachments.",
     "input_schema": {"type": "object", "properties": {
         "batches": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": RECOGNIZE_MAX}},
         "required": ["batches"]},
     "handler": "pearnly_cowork.assistant.tools.recognition_results"},
    {"name": "cowork_companion_status", "label": "Checked CC Desktop",
     "description": "The user's ERP connections (Express / MR.ERP): enabled or not, whether the Windows companion is "
                    "online, and whether the account set list has been read. To find or choose an account set for "
                    "recognition or pushing, use cowork_intake_options instead.",
     "input_schema": {"type": "object", "properties": {}},
     "handler": "pearnly_cowork.assistant.tools.companion_status"},
    {"name": "cowork_refresh_account_sets", "label": "Refreshed account sets",
     "description": "Ask a connection to re-read its account sets from the accounting program. Check the result later "
                    "with cowork_companion_status.",
     "input_schema": {"type": "object", "properties": {"connection": {"type": "string"}}, "required": ["connection"]},
     "handler": "pearnly_cowork.assistant.tools.refresh_account_sets"},
    {"name": "cowork_list_documents", "label": "Listed documents",
     "description": "List recognized documents (receipts, tax invoices) the user can see, newest first, with both "
                    "parties' names and tax IDs, document type, direction, and the recognized subtotal, VAT and "
                    "total. Filter by push state (not_pushed, pending, success, failed), upload date, invoice date, "
                    "waiting for review, where they are pushed (connection and account_set_key as listed by "
                    "cowork_intake_options) or direction; search finds documents by invoice number, seller or buyer "
                    "name, or tax ID across all documents, not only the latest ones. Up to 50 per call: total is how "
                    "many match; pass next_offset as offset for the next page. totals adds the sums of all matching "
                    "documents (credit notes summed separately; without_value counts documents with no recognized "
                    "value). Amounts are as recognized, not the ERP's figures.",
     "input_schema": {"type": "object", "properties": {
         "search": {"type": "string"},
         "push_state": {"type": "string", "enum": ["not_pushed", "pending", "success", "failed"]},
         "uploaded_from": {"type": "string", "description": "YYYY-MM-DD"},
         "invoice_date_from": {"type": "string", "description": "YYYY-MM-DD"},
         "invoice_date_to": {"type": "string", "description": "YYYY-MM-DD"},
         "waiting_review": {"type": "boolean"},
         "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "direction": {"type": "string", "enum": ["purchase", "sales"]},
         "totals": {"type": "boolean"},
         "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}}},
     "handler": "pearnly_cowork.assistant.tools.list_documents"},
    {"name": "cowork_push_documents", "label": "Pushed documents",
     "description": "Push recognized documents to their accounting target (Express through the companion, or MR.ERP), "
                    "the same as Submit on the web page. Only when the user asked to push. Up to 20 at a time; each "
                    "result says sent, needs_choice (get the choices with cowork_push_choices, ask the user, save them "
                    "and push again), failed with a reason, or already_success / already_pending (already entered or "
                    "on its way; it is not pushed again). connection and account_set_key choose "
                    "where to push (from cowork_intake_options), e.g. for a recognize-only document; left out, the "
                    "target chosen at upload is used.",
     "input_schema": {"type": "object", "properties": {
         "documents": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": PUSH_MAX},
         "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "posting_kind": {"type": "string", "enum": ["service", "stock"], "description": "Express."}},
         "required": ["documents"]},
     "handler": "pearnly_cowork.assistant.tools.push_documents"},
    {"name": "cowork_push_choices", "label": "Checked push choices",
     "description": "Before pushing to Express, what this document still needs a person to choose once in this "
                    "account set (party, product, expense account, payment, VAT claim, department; kinds in needed) "
                    "and the allowed values (up to 30 per kind; pass search to narrow by code or name). Use it when a "
                    "push came back needs_choice. Ask the user, then save with cowork_save_push_choices and push "
                    "again. For a document from a scheduled folder task pushed to that task's account set, the "
                    "task's default handling (task_defaults) is applied and not asked; to change it, change the task.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"}, "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "posting_kind": {"type": "string", "enum": ["service", "stock"]}, "search": {"type": "string"}},
         "required": ["document"]},
     "handler": "pearnly_cowork.assistant.tools.push_choices"},
    {"name": "cowork_save_push_choices", "label": "Saved push choices",
     "description": "Record what the user chose for the items listed by cowork_push_choices: each item is the needed "
                    "entry's kind and subject with the chosen value (a listed code; party and product also accept "
                    "the NEW value given there). The account set reuses them for later documents of the same kind.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"}, "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "items": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
             "kind": {"type": "string"}, "subject": {"type": "string"}, "value": {"type": "string"},
             "label": {"type": "string"}}, "required": ["kind", "value"]}}},
         "required": ["document", "items"]},
     "handler": "pearnly_cowork.assistant.tools.save_push_choices"},
    {"name": "cowork_resolve_push_result", "label": "Confirmed a push result",
     "description": "When a push result is unknown (the ERP may or may not have it), record what the user found "
                    "after checking in the ERP: entered true (with the ERP document number if they have it) or "
                    "false, so it can be pushed again. Only with the user's own answer; never guess.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"}, "entered": {"type": "boolean"}, "docnum": {"type": "string"}},
         "required": ["document", "entered"]},
     "handler": "pearnly_cowork.assistant.tools.resolve_push_result"},
    {"name": "cowork_delete_documents", "label": "Deleted documents",
     "description": "Permanently delete documents from the Intake workbench with their original files, like Delete "
                    "in the list; documents already pushed are skipped. When the user clearly named the documents, "
                    "delete them right away (no extra confirmation step); only when it is unclear which ones they "
                    "mean, list them by number, seller and amount and ask first. Returns deleted and skipped counts.",
     "input_schema": {"type": "object", "properties": {
         "documents": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": DELETE_MAX}},
         "required": ["documents"]},
     "handler": "pearnly_cowork.assistant.tools.delete_documents"},
    {"name": "cowork_export_review_workbook", "label": "Exported the review workbook",
     "description": "Export documents (up to 100) to the review workbook, the Excel file accountants check and send "
                    "back on the Intake workbench. The file is added to this conversation at the returned path; copy "
                    "it into the results folder so the user can open and download it, and name it only by file name.",
     "input_schema": {"type": "object", "properties": {
         "documents": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": EXPORT_MAX}},
         "required": ["documents"]},
     "handler": "pearnly_cowork.assistant.tools.export_review_workbook"},
    {"name": "cowork_folder_task_options", "label": "Checked folder task options",
     "description": "What a scheduled folder task can use, like the task form: each computer (reader) that reads "
                    "folders, whether it is online, the folders its CC desktop app opened to the website (root_id and "
                    "subfolders; up to 50, pass search), and for Express with account_set_key the accounts, departments "
                    "and stock account group for defaults. files is how many photos and PDFs each folder holds now, "
                    "subfolders included (\"\" is the root; files_partial: at least that many). Folders can only be "
                    "added in the CC app on that computer; if the one the user wants is missing, tell them to add it "
                    "there.",
     "input_schema": {"type": "object", "properties": {
         "connection": {"type": "string"}, "account_set_key": {"type": "string"}, "search": {"type": "string"}},
         "required": ["connection"]},
     "handler": "pearnly_cowork.assistant.tools.folder_task_options"},
    {"name": "cowork_save_folder_task", "label": "Saved a folder task",
     "description": "Create or change one scheduled folder task: on run_day at run_time it recognizes, with these "
                    "intake choices, the photos and PDFs in the folder (subfolders included) that it has not "
                    "processed yet, including those already there when it is created. Same rules as the web form: "
                    "Express needs posting_kind, MR.ERP needs payment; folders of enabled tasks cannot overlap; the "
                    "target ERP of an existing task cannot change. When a task is changed (name given), values left "
                    "out keep their current ones. For two or more tasks use cowork_save_folder_tasks.",
     "input_schema": {"type": "object", "properties": FOLDER_TASK_PROPS,
                      "required": ["title", "connection", "direction", "root_id", "run_day", "run_time"]},
     "approval": True,
     "ask": "Save this folder task?",
     "preview": "pearnly_cowork.assistant.tools.preview_save_folder_task",
     "handler": "pearnly_cowork.assistant.tools.save_folder_task"},
    {"name": "cowork_save_folder_tasks", "label": "Saved folder tasks",
     "description": "Create or change several scheduled folder tasks at once with one Allow from the user, for example "
                    "one task per folder (purchases, expenses, sales) for each company. Use it whenever two or more "
                    "tasks are saved together. Each task follows the same rules as cowork_save_folder_task: a new "
                    "task needs title, connection, direction, root_id, run_day and run_time; one folder holds one "
                    "direction and one posting kind; folders of enabled tasks, including the others in this list, "
                    "cannot be the same or inside each other. All or nothing: if any task is refused, nothing is "
                    "saved and failed lists each refused task (its position, title and reason); fix them and call "
                    "again with the whole list. Up to 20 tasks.",
     "input_schema": {"type": "object", "properties": {
         "tasks": {"type": "array", "minItems": 1, "maxItems": FOLDER_BATCH_MAX,
                   "items": {"type": "object", "properties": FOLDER_TASK_PROPS}}},
         "required": ["tasks"]},
     "approval": True,
     "ask": "Save these folder tasks?",
     "preview": "pearnly_cowork.assistant.tools.preview_save_folder_tasks",
     "handler": "pearnly_cowork.assistant.tools.save_folder_tasks"},
    {"name": "cowork_set_folder_task_enabled", "label": "Turned a folder task on or off",
     "description": "Pause or resume a scheduled folder task, or turn its reminder on or off (give enabled, remind or "
                    "both). After resuming, the next run handles the files in the folder it has not processed yet, "
                    "including those put in while it was paused; a task with include_existing false only handles "
                    "files put in after it is resumed.",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}, "enabled": {"type": "boolean"},
                                                        "remind": FOLDER_TASK_PROPS["remind"]},
                      "required": ["task"]},
     "approval": True,
     "ask": "Turn this folder task on or off?",
     "preview": "pearnly_cowork.assistant.tools.preview_set_folder_task_enabled",
     "handler": "pearnly_cowork.assistant.tools.set_folder_task_enabled"},
    {"name": "cowork_delete_folder_tasks", "label": "Deleted folder tasks",
     "description": "Delete scheduled folder tasks with their run history; documents they already created stay on the "
                    "Intake workbench. Only ask which ones when it is unclear.",
     "input_schema": {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "string"},
                                                                 "minItems": 1}}, "required": ["tasks"]},
     "approval": True,
     "ask": "Delete these folder tasks?",
     "preview": "pearnly_cowork.assistant.tools.preview_delete_folder_tasks",
     "handler": "pearnly_cowork.assistant.tools.delete_folder_tasks"},
    {"name": "cowork_recon_start", "label": "Started a reconciliation",
     "description": "Start a reconciliation like New on the reconciliation pages, with files the user attached in "
                    "this chat. bank: left_files = bank statements, right_files = general ledger. income: left_files "
                    "= one general ledger, right_files = output VAT reports. vat: left_files = output VAT reports, "
                    "right_files = sales invoices. Excel/CSV and text PDFs are read free; scanned PDFs and photos are "
                    "read with paid recognition per page like invoices, so say so before starting when files are "
                    "scanned. The bank, account and period are worked out from the files. Waits up to 90 seconds; "
                    "if it is still running, tell the user and check again with cowork_recon_status when they ask "
                    "(never wait with sleep). Use this rather than reconciling yourself.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."},
         "left_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
         "right_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
         "title": {"type": "string"}}, "required": ["kind", "left_files", "right_files"]},
     "handler": "pearnly_cowork.assistant.tools.recon_start"},
    {"name": "cowork_recon_status", "label": "Checked a reconciliation's progress",
     "description": "Progress of a reconciliation started by cowork_recon_start or a preset answer that re-runs it: "
                    "state, stage, pages read, and the finished task. Waits up to 90 seconds for it to finish.",
     "input_schema": {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]},
     "handler": "pearnly_cowork.assistant.tools.recon_status"},
    {"name": "cowork_recon_list", "label": "Listed reconciliations",
     "description": "The user's reconciliations of one kind, newest first.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."},
         "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}}, "required": ["kind"]},
     "handler": "pearnly_cowork.assistant.tools.recon_list"},
    {"name": "cowork_recon_result", "label": "Viewed a reconciliation",
     "description": "A finished reconciliation: title, status (needs_review means the statement was read from a "
                    "scan and must be checked on the page before export), notes, questions to answer once "
                    "(cowork_recon_answer), the summary counts and amounts, rows_total, unmatched_total and the first 30 "
                    "rows that did not match (for vat: invoices and report lines with a difference or missing on one "
                    "side, with which fields differ). For bank, matched_by_kind sums the matched lines by how they "
                    "were matched (groups, lines and the amounts on each side); when the two sides' totals differ, explain the "
                    "difference from it (for example ledger credits above bank withdrawals that are card fees the ledger "
                    "recorded in the card net-of-fee groups) instead of guessing. The reconciliation compares the "
                    "bank with one GL account only; report its figures as they are and do not tell the user to post "
                    "entries from them (for example fees the user says are booked in another account).",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."}, "task": {"type": "string"}},
                      "required": ["kind", "task"]},
     "handler": "pearnly_cowork.assistant.tools.recon_result"},
    {"name": "cowork_recon_answer", "label": "Answered a reconciliation question",
     "description": "Answer one of the result's questions (rule and the allowed values come from the question; for "
                    "example grouped yes/no, gl_account = one of the offered account codes, card_fee = a rate or "
                    "no). It is remembered for that account or company and the result is recomputed; a gl_account "
                    "answer re-runs from the original files (returns rerun_job).",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."}, "task": {"type": "string"},
         "rule": {"type": "string"}, "value": {"type": "string"}, "subject": {"type": "string"}},
         "required": ["kind", "task", "rule", "value"]},
     "handler": "pearnly_cowork.assistant.tools.recon_answer"},
    {"name": "cowork_recon_presets", "label": "Checked reconciliation presets",
     "description": "The remembered reconciliation answers of one kind (action list), or change / remove one by "
                    "scope_key and rule as listed.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."},
         "action": {"type": "string", "enum": ["list", "change", "remove"]}, "scope_key": {"type": "string"},
         "rule": {"type": "string"}, "value": {"type": "string"}}, "required": ["kind"]},
     "handler": "pearnly_cowork.assistant.tools.recon_presets"},
    {"name": "cowork_recon_export", "label": "Exported a reconciliation",
     "description": "The reconciliation workbook (Excel) of a completed task, added to this conversation at the "
                    "returned path; copy it into the results folder for the user, naming it only by file name.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."}, "task": {"type": "string"}},
                      "required": ["kind", "task"]},
     "handler": "pearnly_cowork.assistant.tools.recon_export"},
    {"name": "cowork_recon_delete", "label": "Deleted reconciliations",
     "description": "Delete reconciliations the user clearly named (ask only when it is unclear which).",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."},
         "tasks": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, "required": ["kind", "tasks"]},
     "handler": "pearnly_cowork.assistant.tools.recon_delete"},
    {"name": "cowork_stocktake_list", "label": "Listed stock counts",
     "description": "The company's stock counts (stocktakes), newest first.",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": []},
     "handler": "pearnly_cowork.assistant.tools.stocktake_list"},
    {"name": "cowork_stocktake_template", "label": "Prepared a stock count template",
     "description": "The Excel template for a new stock count (product code, name, unit, book quantity, optional counted quantity), added to this conversation; copy it into the results folder for the user to fill in.",
     "input_schema": {"type": "object", "properties": {}, "required": []},
     "handler": "pearnly_cowork.assistant.tools.stocktake_template"},
    {"name": "cowork_stocktake_create", "label": "Created a stock count",
     "description": "Create a stock count from an Excel file the user attached in this chat (the template's columns; book quantities, counted quantities optional), like New on the stocktake page. Returns the count and rows that were skipped with reasons.",
     "input_schema": {"type": "object", "properties": {"file": {"type": "string"}, "title": {"type": "string"}}, "required": ["file"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_create"},
    {"name": "cowork_stocktake_report", "label": "Viewed a stock count",
     "description": "Book quantity, counted quantity and difference per item of a stock count; by default only items with a difference or not counted yet (up to 40), all_items for everything.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}, "all_items": {"type": "boolean"}}, "required": ["count"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_report"},
    {"name": "cowork_stocktake_count", "label": "Recorded a count",
     "description": "Record a counted quantity for a product by code or barcode, like scanning on the page: entries for the same product add up. Warehouse and location default to the product's book position; give them when it was found elsewhere. recount true clears that product's earlier counts first and then records qty.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}, "code": {"type": "string"}, "qty": {"type": "number", "minimum": 0}, "warehouse": {"type": "string"}, "location": {"type": "string"}, "recount": {"type": "boolean"}}, "required": ["count", "code"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_count"},
    {"name": "cowork_stocktake_close", "label": "Finished a stock count",
     "description": "Finish a stock count; it becomes read-only (export only). Only when the user says counting is done.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}}, "required": ["count"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_close"},
    {"name": "cowork_stocktake_export", "label": "Exported a stock count",
     "description": "The variance workbook of a stock count, added to this conversation; copy it into the results folder for the user.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}}, "required": ["count"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_export"},
    {"name": "cowork_stocktake_delete", "label": "Deleted stock counts",
     "description": "Delete stock counts the user clearly named (ask only when unclear which).",
     "input_schema": {"type": "object", "properties": {"counts": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, "required": ["counts"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_delete"},
    {"name": "cowork_work_boards", "label": "Listed task boards",
     "description": "The team task boards the user can see.",
     "input_schema": {"type": "object", "properties": {}, "required": []},
     "handler": "pearnly_cowork.assistant.tools.work_boards"},
    {"name": "cowork_work_board", "label": "Viewed a task board",
     "description": "One task board (default board when left out): its lists and cards with title, list, due date, done, assignees and labels (up to 100). mine true keeps only cards assigned to the user.",
     "input_schema": {"type": "object", "properties": {"board": {"type": "string"}, "mine": {"type": "boolean"}}, "required": []},
     "handler": "pearnly_cowork.assistant.tools.work_board"},
    {"name": "cowork_work_card", "label": "Viewed a task",
     "description": "A task card's full detail: description, dates, reminder, recurrence, assignees, labels, checklists, attachments, recent activity, and modified (needed to change it).",
     "input_schema": {"type": "object", "properties": {"card": {"type": "string"}}, "required": ["card"]},
     "handler": "pearnly_cowork.assistant.tools.work_card"},
    {"name": "cowork_work_create_card", "label": "Created a task",
     "description": "Create a task card at the end of a list (list id from cowork_work_board).",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "list": {"type": "string"}}, "required": ["title", "list"]},
     "handler": "pearnly_cowork.assistant.tools.work_create_card"},
    {"name": "cowork_work_update_card", "label": "Updated a task",
     "description": "Change a task card: values may hold title, description, start_date, due_date, due_time, reminder (none, at_due, 5m, 10m, 15m, 1h, 2h, 1d, 2d), recurrence (never, daily, weekly, monthly, yearly), assignees (user ids of board editors), labels, checklists, completed. Pass modified from cowork_work_card so a newer edit by someone else is not overwritten.",
     "input_schema": {"type": "object", "properties": {"card": {"type": "string"}, "modified": {"type": "string"}, "values": {"type": "object"}}, "required": ["card", "modified", "values"]},
     "handler": "pearnly_cowork.assistant.tools.work_update_card"},
    {"name": "cowork_work_move_card", "label": "Moved a task",
     "description": "Move a task card to another list (moving into the done list completes it), or toggle_done to mark it done / not done (a recurring task rolls its due date forward instead).",
     "input_schema": {"type": "object", "properties": {"card": {"type": "string"}, "list": {"type": "string"}, "toggle_done": {"type": "boolean"}}, "required": ["card"]},
     "handler": "pearnly_cowork.assistant.tools.work_move_card"},
    {"name": "cowork_work_comment", "label": "Commented on a task",
     "description": "Add a comment to a task card.",
     "input_schema": {"type": "object", "properties": {"card": {"type": "string"}, "content": {"type": "string"}}, "required": ["card", "content"]},
     "handler": "pearnly_cowork.assistant.tools.work_comment"},
    {"name": "cowork_work_search", "label": "Searched tasks",
     "description": "Search task cards by keyword (title, description, comments, checklist items, field values; at least 2 characters).",
     "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}, "board": {"type": "string"}}, "required": ["keyword"]},
     "handler": "pearnly_cowork.assistant.tools.work_search"},
    {"name": "cowork_push_results", "label": "Checked push results",
     "description": "Show recent push results: documents whose push failed (with the reason), is pending, or succeeded.",
     "input_schema": {"type": "object", "properties": {
         "push_state": {"type": "string", "enum": ["pending", "success", "failed"]},
         "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}}, "required": ["push_state"]},
     "handler": "pearnly_cowork.assistant.tools.list_documents"},
    {"name": "cowork_folder_tasks", "label": "Checked folder tasks",
     "description": "List the scheduled folder tasks (scan a folder on the user's PC, recognize new files and push them), "
                    "with their schedule, default handling, reminder, last run and whether the companion reading the "
                    "folder is online. waiting is how many files in the folder the task has not processed yet "
                    "(waiting_partial: at least that many; null when not known). results counts invoices entered, "
                    "invoices needing attention, and files still moving.",
     "input_schema": _OBJ, "handler": "pearnly_cowork.assistant.tools.folder_tasks"},
    {"name": "cowork_run_folder_task", "label": "Started a folder task",
     "description": "Run one folder task now. Only when the user asked.",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
     "handler": "pearnly_cowork.assistant.tools.run_folder_task"},
    {"name": "cowork_folder_task_runs", "label": "Checked folder task runs",
     "description": "Show the recent runs of one folder task: files found, uploaded, skipped and failed, and per run "
                    "how many invoices were entered, need attention, or are still moving (results), and the files in "
                    "each step (states).",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
     "handler": "pearnly_cowork.assistant.tools.folder_task_runs"},
]
