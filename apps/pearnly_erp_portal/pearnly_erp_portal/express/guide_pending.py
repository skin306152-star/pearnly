"""ERP 待处理的引导式（总表 ERP-D-PD）：声明 `express.cc_guide_pending.GUIDES` 点名的产品函数。

清单只转调 `express.pending.pending`（侧栏「待处理」与 CC 工具 ``erp_pending`` 同一个），按本对话公司、只列采购 / 销售单（码写成单据源认的
``P:`` / ``S:``）；确定后 `assistant.guide.run.start(业务, doc_ids=[那张], at="asking")` 交给单据引导（业务按 `express.guide_documents.direction_of`）。
单据源核不过（不是本人能写的、不是这家公司的）退回选单那一步、带原因。
"""
from __future__ import annotations

import frappe
from frappe import _

LIMIT = 200
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


def draft(ctx, values) -> dict:
    return {"draft": str(values["item"])}


def review(ctx, draft) -> dict:
    rows = {row["code"]: row for row in item_options(ctx, codes=[draft])["options"]}
    row = rows.get(draft) or {"name": draft, "note": ""}
    return {"version": draft, "ready": bool(rows), "problem": "" if rows else "not offered", "problem_text": "", "lines": [],
            "tables": [{"key": "item", "label_key": "guide.step.pending.item", "step": "item",
                        "rows": [{"code": draft, "name": row["name"], "note": row.get("note") or "", "amount": ""}]}], "total": ""}


def confirm(ctx, values, draft, consent) -> dict:
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
