"""Cowork 待处理的引导式（CC 对话里点「待处理」，总表 CW-D-PD；评估 docs/work/2026-11-16-cc-guided-pending-评估.md，主会话 11-16 改做法）。

``GUIDES`` 是纯字面量（闸门按源码读）。待处理卡只做「选哪一项」，之后按单据的事交给单据引导（采购 / 销售，documents 源）——判定卡与
复核卡只有那一份，这里不另出题：

- 处理：选一项（待处理清单，同侧栏，按本对话公司）→ 确定 → `assistant.guide.run.start(业务, doc_ids=[这张], at="asking")`：
  补选要人选的项、MR.ERP 菜单与默认值、换账套、结果不明核对都在那张判定卡上（页面接着显示新开的那次引导）；
- 改票面：同上，``at="review"``（从复核卡接着走，识别值就地改）；
- 导出复核工作簿：多选 → 确定 → CC 工具同一个函数（`assistant.tools.export_review_workbook`），文件放进对话；
- 删除：多选 → 允许卡 → CC 工具同一个函数（`assistant.tools.delete_documents`，真删记录与原件，已推送的跳过）。

清单只转调 `companies.pending.pending`（侧栏「待处理」同一个），按本对话公司过滤（主会话 11-16）。单据的方向（交给采购还是销售）问
单据源（`assistant.guide_documents.direction_of`，同它复核卡的判法）。
"""
from __future__ import annotations

import contextlib
import hashlib
import json

import frappe
from frappe import _

HANDLE, REVIEW, EXPORT, DELETE = "handle", "review", "export", "delete"
ACTIONS = ((HANDLE, "guide.option.pending.handle"), (REVIEW, "guide.option.pending.review"),
           (EXPORT, "guide.option.pending.export"), (DELETE, "Delete"))
AT = {HANDLE: "asking", REVIEW: "review"}
LIMIT = 200
DRAFT_SEP = "@"


@contextlib.contextmanager
def _conversation(ctx):
    """调 CC 工具时带上这段对话（文件放进本对话、本对话公司都按它认），出来还原。"""
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def allowed(user) -> bool:
    """菜单上出不出「待处理」：有录入功能的人（同待处理页当场处理 `assistant.pending_api` 的 `scope.require("intake")`）。"""
    from pearnly_cowork.access import scope

    if not user or user == "Guest":
        return False
    try:
        scope.require("intake")
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


def _answer(ctx, key, default=""):
    return (ctx.get("answers") or {}).get(key) or default


def _only(rows, codes):
    wanted = {str(code) for code in codes}
    return {"options": [row for row in rows if row["code"] in wanted], "more": False}


def action_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in ACTIONS]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def _items(ctx, search=None) -> list:
    """待处理清单（`companies.pending.pending`，侧栏同一个；最多 200 项，按本对话公司）。"""
    from pearnly_cowork.companies import pending

    company = str(ctx.get("company") or "") or None
    found = pending.pending(company, LIMIT, search or None)
    return [{"code": item["document"],
             "name": " · ".join(part for part in (item.get("invoice_no") or item.get("filename"), item.get("counterparty")) if part)
             or item["document"],
             "note": " · ".join(part for part in (item.get("reason_label"), item.get("amount"), item.get("date")) if part)}
            for item in found.get("items") or ()]


def item_options(ctx, txt="", start=0, page_length=20, codes=None):
    """待处理的单：按单号、对方、原因、金额、日期搜索（同待处理页搜索口径，服务端），内存分页（清单本身最多 200 项）。"""
    if codes is not None:
        return _only(_items(ctx), codes)
    rows = _items(ctx, str(txt or "").strip())
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def draft(ctx, values) -> dict:
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": DRAFT_SEP.join((values["action"], digest))}


def review(ctx, draft) -> dict:
    """复核卡：做什么 + 那一张 / 那几张（单号、对方、原因）。"""
    action = _answer(ctx, "action")
    chosen = [_answer(ctx, "item")] if action in AT else list(_answer(ctx, "items", []))
    rows = {row["code"]: row for row in _only(_items(ctx), chosen)["options"]}
    return {"version": draft, "ready": True, "problem": "", "problem_text": "",
            "lines": [{"key": "action", "label_key": "guide.step.pending.action", "value": _(dict(ACTIONS).get(action, "")),
                       "step": "action"}],
            "tables": [{"key": "item" if action in AT else "items", "label_key": "guide.step.pending.items",
                        "step": "item" if action in AT else "items",
                        "rows": [{"code": code, "name": rows[code]["name"], "note": rows[code].get("note") or "", "amount": ""}
                                 for code in chosen if code in rows]}], "total": ""}


def _hand_over(ctx, document, at) -> dict:
    """交给单据引导（`assistant.guide.run.start(..., doc_ids, at)`：本段对话里开一次采购 / 销售引导，从判定或复核接着走）。"""
    from pearnly_core.assistant.guide import run

    from pearnly_cowork.assistant import guide_documents

    with _conversation(ctx):
        business = guide_documents.direction_of(document)
    if not business:
        return {"returned": {"item": {"reason": "not offered"}}}
    try:
        started = run.start(str(ctx.get("session") or ""), business, doc_ids=[document], at=at)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"returned": {"item": {"reason": "pending_refused", "text": str(error)[:300]}}}
    return {"status": "entered", "docnum": document, "next": {"run": started.get("run"), "business": business}}


def confirm(ctx, values, draft, consent) -> dict:
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.assistant import tools

    action = values["action"]
    if action in AT:
        return _hand_over(ctx, values["item"], AT[action])
    with _conversation(ctx):
        if action == EXPORT:
            found = json.loads(tools.export_review_workbook({"documents": list(values["items"])}))
            if found.get("status") != "exported":
                return {"returned": {"items": {"reason": "pending_refused", "text": str(found.get("reason") or "")[:300]}}}
            path = found["file"]
            return {"status": "entered", "files": [{"file": chat_files.resolve(path), "name": path.rsplit("/", 1)[-1]}]}
        found = json.loads(tools.delete_documents({"documents": list(values["items"])}))
    if found.get("status") == "not_deleted":
        return {"returned": {"items": {"reason": "pending_refused", "text": str(found.get("reason") or "")[:300]}}}
    return {"status": "entered", **{key: found[key] for key in ("deleted", "skipped") if key in found}}


GUIDES = [
    {"id": "pending", "label_key": "guide.business.pending", "icon": "list-checks", "order": 100,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_cowork.assistant.guided.pending.allowed",
     "opening_key": "guide.open.pending", "source": "form", "writes": True,
     "consent": {"key": "action", "value": "delete"},
     "steps": [
         {"key": "action", "input": "choice", "rule": "always", "options": "pearnly_cowork.assistant.guided.pending.action_options"},
         {"key": "item", "input": "choice", "rule": "always", "search": True, "after": ["action"],
          "when": {"key": "action", "in": ["handle", "review"]}, "options": "pearnly_cowork.assistant.guided.pending.item_options"},
         {"key": "items", "input": "multi", "rule": "always", "search": True, "after": ["action"],
          "when": {"key": "action", "in": ["export", "delete"]}, "options": "pearnly_cowork.assistant.guided.pending.item_options"},
     ],
     "draft": "pearnly_cowork.assistant.guided.pending.draft", "review": "pearnly_cowork.assistant.guided.pending.review",
     "confirm": "pearnly_cowork.assistant.guided.pending.confirm",
     "tool": "cowork_pending", "expires": ["company"]},
]
