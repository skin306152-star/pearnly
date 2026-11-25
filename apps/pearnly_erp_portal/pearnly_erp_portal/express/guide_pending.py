"""ERP 待处理的引导式（总表 ERP-D-PD）：声明 `express.cc_guide_pending.GUIDES` 点名的产品函数。

清单只转调 `express.pending.pending`（侧栏「待处理」与 CC 工具 ``erp_pending`` 同一个），按本对话公司、只列采购 / 销售单（码写成单据源认的
``P:`` / ``S:``）；确定后 `assistant.guide.run.start(业务, doc_ids=[那张], at="asking")` 交给单据引导（业务按 `express.guide_documents.direction_of`）。
单据源核不过（不是本人能写的、不是这家公司的）退回选单那一步、带原因。
"""
from __future__ import annotations

import contextlib
import hashlib
import json

import frappe
from frappe import _

LIMIT = 200
DOCUMENTS = "documents"   # 单据维护那份声明的业务 id（同一组函数，清单来源不同）
PREFIX = {"Purchase Document": "P", "ERP Sales Document": "S"}   # 同 `express.push.PURCHASE / SALES` 与单据源的 DOCUMENT_PREFIXES


def _items(ctx, search=None) -> list:
    from pearnly_erp_portal.express import pending

    found = pending.pending(str(ctx.get("company") or "") or None, LIMIT, search or None)
    return [{"code": f"{PREFIX[item['doctype']]}:{item['document']}",
             "name": " · ".join(part for part in (item.get("invoice_no"), item.get("counterparty")) if part) or item["document"],
             "note": " · ".join(part for part in (item.get("reason_label"), item.get("amount"), item.get("date")) if part)}
            for item in found.get("items") or () if item.get("doctype") in PREFIX]


def item_options(ctx, txt="", start=0, page_length=20, codes=None):
    """待处理的采购 / 销售单（服务端按待处理页同一口径搜索，清单本身最多 200 项、内存分页）；``codes`` 给了按原样核。"""
    if codes is not None:
        wanted = {str(code) for code in codes}
        return {"options": [row for row in _items(ctx) if row["code"] in wanted], "more": False}
    rows = _items(ctx, str(txt or "").strip())
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def _pending_draft(ctx, values) -> dict:
    return {"draft": str(values["item"])}


def _pending_review(ctx, draft) -> dict:
    rows = {row["code"]: row for row in item_options(ctx, codes=[draft])["options"]}
    row = rows.get(draft) or {"name": draft, "note": ""}
    return {"version": draft, "ready": bool(rows), "problem": "" if rows else "not offered", "problem_text": "", "lines": [],
            "tables": [{"key": "item", "label_key": "guide.step.pending.item", "step": "item",
                        "rows": [{"code": draft, "name": row["name"], "note": row.get("note") or "", "amount": ""}]}], "total": ""}


def _pending_confirm(ctx, values, draft, consent) -> dict:
    """交给单据引导（本段对话里开一次采购 / 销售引导，从判定卡接着走）。"""
    from pearnly_core.assistant.guide import run

    from pearnly_erp_portal.express import guide_documents

    document = str(values["item"])
    business = guide_documents.direction_of(document)
    if not business:
        return {"returned": {"item": {"reason": "not offered"}}}
    try:
        started = run.start(str(ctx.get("session") or ""), business, doc_ids=[document], at="asking")
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"returned": {"item": {"reason": "pending_refused", "text": _(str(error))[:300]}}}
    return {"status": "entered", "docnum": document, "next": {"run": started.get("run"), "business": business}}


# ── 单据（总表第 14 行单据维护，评估 docs/work/2026-11-24-cc-guided-documents-评估.md，主会话 11-24 审定）──────────────────
# 同一组「交给单据引导」的做法，清单换成对话选择器选的公司与月份里某个方向的全部单据（采购 / 销售列表同一个查询、同一道权限，
# 服务端搜索与翻页：`purchasing.records.rows` / `sales.records.rows`，在本对话公司下执行 `masters.company.acting`，网页查询不改）。
# 操作：处理（交采购 / 销售引导，从判定卡接着走）、导出（网页导出同一个工作簿函数，这家公司这个月这个方向整张列表，文件放进对话）、
# 删除（允许卡；网页同一个删草稿函数，已提交的跳过、结果卡说跳过几张与原因）。ERP 改票面进 CC 是第二批（主会话 11-24），本批没有。

HANDLE, EXPORT, DELETE = "handle", "export", "delete"
ACTIONS = ((HANDLE, "guide.option.pending.handle"), (EXPORT, "trade.export.action"), (DELETE, "Delete"))
PURCHASE, SALES = "purchase", "sales"
DIRECTIONS = ((PURCHASE, "guide.option.direction.purchase"), (SALES, "guide.option.direction.sales"))
PAGE_MAX = 50
DRAFT = "draft"   # 采购 / 销售草稿状态（`purchasing.contracts` / `sales.contracts` 的 DRAFT 同值）
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@contextlib.contextmanager
def _acting(ctx):
    from pearnly_erp_portal.masters import company as companies

    with companies.acting(str(ctx.get("company") or "")):
        yield


@contextlib.contextmanager
def _conversation(ctx):
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def _answer(ctx, key, default=""):
    return (ctx.get("answers") or {}).get(key) or default


def _only(rows, codes):
    wanted = {str(code) for code in codes}
    return {"options": [row for row in rows if row["code"] in wanted], "more": False}


def action_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in ACTIONS]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def direction_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in DIRECTIONS]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def _period(ctx) -> str:
    """对话选择器选的月份（YYYY-MM）；不像月份回空串。"""
    period = str(ctx.get("period") or "")[:7]
    return period if len(period) == 7 and period[4] == "-" and period.replace("-", "").isdigit() else ""


def _records(direction):
    if direction == SALES:
        from pearnly_erp_portal.sales import records
    else:
        from pearnly_erp_portal.purchasing import records
    return records


def _params(ctx, direction, search="") -> dict:
    """列表那一页的筛选：这个月（按单据日期，同网页按月）；销售只列销售记录（收银小票不是 ``S:`` 单，不在这里处理 / 删除）。"""
    params = {"month": _period(ctx)}
    if direction == SALES:
        from pearnly_erp_portal.sales import contracts

        params["source"] = [source for source in contracts.SOURCES if source != contracts.SOURCE_POS]
    if search:
        params["q"] = search
    return params


def _row(direction, row) -> dict:
    party = row.get("buyer_name") if direction == SALES else row.get("supplier_name")
    number = row.get("doc_no") or row.get("system_no") or row.get("name")
    return {"code": f"{'S' if direction == SALES else 'P'}:{row['name']}",
            "name": " · ".join(str(part) for part in (number, party) if part) or row["name"],
            "note": " · ".join(str(part) for part in (row.get("grand_total"), str(row.get("doc_date") or "")[:10]) if part),
            "status": str(row.get("status") or "")}


def _named(records, ctx, direction, codes) -> list:
    """选过的那几张：列表同一份条件（本人范围、本公司、这个月）再加按名，一次查回。"""
    prefix = "S" if direction == SALES else "P"
    names = [code.partition(":")[2] for code in codes if code.partition(":")[0] == prefix and code.partition(":")[2]]
    if not names:
        return []
    guard_read = "sales.read" if direction == SALES else "purchase.read"
    from pearnly_erp_portal.access import guard

    guard.require(guard_read)
    filters, _or, _datefield = records.query(_params(ctx, direction))
    return frappe.get_list(records.DOCTYPE if direction == SALES else "Purchase Document",
                           filters=[*filters, ["name", "in", names]], fields=list(records.FIELDS), distinct=True,
                           limit_page_length=len(names))


def document_options(ctx, txt="", start=0, page_length=20, codes=None):
    """这家公司这个月这个方向的单据（列表同一个查询：本人范围、本公司、按单据日期），服务端搜索、翻页；``codes`` 给了按原样核
    （同一个查询里按名取回，不在这家这个月里的不回）。"""
    direction = _answer(ctx, "direction")
    if direction not in dict(DIRECTIONS) or not _period(ctx):
        return {"options": [], "more": False}
    records = _records(direction)
    with _acting(ctx):
        if codes is not None:
            wanted = [str(code) for code in codes][:LIMIT]
            by = {item["code"]: item for item in (_row(direction, row) for row in _named(records, ctx, direction, wanted))}
            return {"options": [by[code] for code in wanted if code in by], "more": False}
        start, size = max(0, int(start or 0)), max(1, min(int(page_length or 20), PAGE_MAX))
        rows = records.rows(_params(ctx, direction, str(txt or "").strip()), start=start, limit=size + 1)
    return {"options": [_row(direction, row) for row in rows[:size]], "more": len(rows) > size}


def _documents(ctx) -> bool:
    return str(ctx.get("business") or "") == DOCUMENTS


def draft(ctx, values) -> dict:
    if not _documents(ctx):
        return _pending_draft(ctx, values)
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": "@".join((str(values.get("action") or ""), digest))}


def _chosen(ctx) -> list:
    action = _answer(ctx, "action")
    if action == HANDLE:
        return [_answer(ctx, "item")]
    return list(_answer(ctx, "items", [])) if action == DELETE else []


def review(ctx, draft) -> dict:
    """复核卡：做什么、哪个方向 + 那一张 / 那几张（导出是这个月这个方向的整张列表，不列单）。"""
    if not _documents(ctx):
        return _pending_review(ctx, draft)
    action, direction = _answer(ctx, "action"), _answer(ctx, "direction")
    chosen = _chosen(ctx)
    rows = {row["code"]: row for row in document_options(ctx, codes=chosen)["options"]} if chosen else {}
    tables = []
    if action != EXPORT:
        key = "item" if action == HANDLE else "items"
        tables.append({"key": key, "label_key": "guide.step.pending.items", "step": key,
                       "rows": [{"code": code, "name": rows[code]["name"], "note": rows[code].get("note") or "", "amount": ""}
                                for code in chosen if code in rows]})
    return {"version": draft, "ready": True, "problem": "", "problem_text": "",
            "lines": [{"key": "action", "label_key": "guide.step.pending.action", "value": _(dict(ACTIONS).get(action, "")),
                       "step": "action"},
                      {"key": "direction", "label_key": "guide.q.direction", "value": _(dict(DIRECTIONS).get(direction, "")),
                       "step": "direction"}],
            "tables": tables, "total": ""}


def _export(ctx, direction) -> dict:
    """网页导出同一个工作簿（`purchasing.output.workbook` / `sales.output.workbook`，同一份筛选与权限）：这家公司这个月这个方向整张列表。"""
    from pearnly_core.assistant import files as chat_files

    if direction == SALES:
        from pearnly_erp_portal.sales import output
    else:
        from pearnly_erp_portal.purchasing import output
    filename = f"{'sales' if direction == SALES else 'purchase'}-{_period(ctx)}.xlsx"
    with _acting(ctx), _conversation(ctx):
        try:
            content = output.workbook({"month": _period(ctx)})
            path = chat_files.add_to_conversation(filename, content, XLSX)
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.clear_messages()
            return {"returned": {"direction": {"reason": "pending_refused", "text": _(str(error))[:300]}}}
        return {"status": "entered", "files": [{"file": chat_files.resolve(path), "name": filename}]}


def _delete(ctx, direction, codes) -> dict:
    """网页同一个删草稿函数（`purchasing.entry.delete_drafts` / `sales.entry.delete_drafts`：本人范围、整批行锁、全是草稿才删）。网页那个
    函数一批里有非草稿就整批拒；这里先按同一份列表查询筛出草稿交给它，其余跳过（结果卡说跳过几张、为什么）。"""
    if direction == SALES:
        from pearnly_erp_portal.sales import entry
    else:
        from pearnly_erp_portal.purchasing import entry
    with _acting(ctx):
        rows = _named(_records(direction), ctx, direction, codes)
        drafts = [row["name"] for row in rows if str(row.get("status") or "") == DRAFT]
        skipped = len(codes) - len(drafts)
        if not drafts:
            return {"status": "entered", "deleted": 0, "skipped": skipped}
        try:
            done = entry.delete_drafts(drafts)
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"returned": {"items": {"reason": "pending_refused", "text": _(str(error))[:300]}}}
    return {"status": "entered", "deleted": int(done.get("deleted") or 0), "skipped": skipped}


def confirm(ctx, values, draft, consent) -> dict:
    if not _documents(ctx):
        return _pending_confirm(ctx, values, draft, consent)
    action, direction = values["action"], values["direction"]
    if action == EXPORT:
        return _export(ctx, direction)
    if action == DELETE:
        return _delete(ctx, direction, [str(code) for code in values["items"]][:LIMIT])
    document = str(values["item"])
    shown = next(iter(document_options(ctx, codes=[document])["options"]), None)
    if not shown:
        return {"returned": {"item": {"reason": "not offered"}}}
    handed = _pending_confirm(ctx, {"item": document}, draft, consent)
    return {**handed, "label": shown["name"]} if handed.get("next") else handed


def result(ctx, draft):
    """结果卡（现算，确定回包在 ``ctx["done"]``）：交给单据引导的写那张单（单号 · 对方）；导出的放文件；删除的说删了几张，
    有跳过的（不是草稿）带网页同一句原因；一张没删标「已跳过」。"""
    done = ctx.get("done") or {}
    if not isinstance(done, dict):
        return None
    if done.get("next"):
        return {**done, "status": "ready", "docnum": str(done.get("label") or "")}
    if done.get("files") is not None:
        return {**done, "status": "ready"}
    if "deleted" in done:
        deleted, skipped = int(done.get("deleted") or 0), int(done.get("skipped") or 0)
        why = _("Only Draft documents can be deleted")
        if not deleted:
            return {**done, "status": "held", "reason_text": why}
        text = _("intake.delete.done").format(deleted)
        return {**done, "status": "ready", "reason_text": f"{text} · {why}" if skipped else text}
    return None
