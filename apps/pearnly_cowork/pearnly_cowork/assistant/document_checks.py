"""CC 查单据问题、改单据留来源（2026-11-03「CC 通用疑难处理」）：只给 CC 通用的「查」和「改的依据」，不按情况加规则。

- :func:`checks`：单据详情里的自检（`cowork_document_detail` 的 ``checks``）：识别时留下的复核原因、写账前护栏的原因码
  （`tax_documents.ledger_guard`，与推送同一判据）、明细对不上、推送停下的原因。CC 据此去看原件、找根因。
- :func:`record` / :func:`recent`：CC 改金额、税号、单号、日期、明细时必须给依据（``basis``：用户原话、原件第几页、按什么算的），
  每次改动存一条记录（谁、何时、改前改后、依据），挂在单据上（Frappe 原生 Comment，随单据删除一起删）；单据详情的 ``edits`` 读它。
"""
from __future__ import annotations

import json

import frappe

DOCTYPE = "Cowork OCR History"
SUBJECT = "pearnly.document_edit"  # Comment.subject：认出这类记录（不是人写的评论）
EDITS_SHOWN = 10
# 动钱 / 身份的字段：改这些要给依据（明细整组替换同样要）
NEEDS_BASIS = ("subtotal", "vat", "discount", "total_amount", "wht_amount", "seller_tax", "buyer_tax",
               "invoice_number", "date", "document_type", "currency")


def needs_basis(changes, items) -> bool:
    return items is not None or any(key in NEEDS_BASIS for key in changes or {})


def _differences(before, after, keys) -> dict:
    out = {}
    for key in keys:
        old, new = before.get(key), after.get(key)
        if old != new:
            out[key] = [old, new]
    return out


def record(name, before, after, keys, *, items_changed=False, basis="", by="cc") -> None:
    """存一条改动记录（不提交，跟着改单据的那次提交）。``before`` / ``after``：主页字段改前改后；``keys``：这次动到的字段。"""
    changes = _differences(before or {}, after or {}, keys)
    if items_changed:
        changes["items"] = [len(before.get("items") or []), len(after.get("items") or [])]  # 明细只记行数，整组见版本记录
    if not changes and not basis:
        return
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "subject": SUBJECT,
                    "reference_doctype": DOCTYPE, "reference_name": name,
                    "content": json.dumps({"by": by, "basis": str(basis or "")[:500], "changes": changes},
                                          ensure_ascii=False, default=str)}).insert(ignore_permissions=True)


def recent(name, limit=EDITS_SHOWN) -> list:
    """最近的改动记录，新 → 旧：``[{at, user, by, basis, changes}]``。"""
    rows = frappe.get_all("Comment", filters={"reference_doctype": DOCTYPE, "reference_name": name, "subject": SUBJECT},
                          fields=["owner", "creation", "content"], order_by="creation desc", limit_page_length=limit)
    out = []
    for row in rows:
        try:
            body = json.loads(row.content or "{}")
        except ValueError:
            continue
        out.append({"at": str(row.creation), "user": row.owner, "by": body.get("by") or "",
                    "basis": body.get("basis") or "", "changes": body.get("changes") or {}})
    return out


def checks(doc, fields) -> dict:
    """这张单的自检（空的项不给）：``recognition`` 识别时留下的复核原因码；``ledger`` 写账前护栏的原因码（不为空就不会写账）；
    ``items`` 明细对账（mismatch / incomplete：表头闭合时照写表头，明细没写进去）；``push`` 推送停下 / 失败的原因码。"""
    from pearnly_core.express_rules.contracts.line_items import review_status
    from pearnly_core.express_rules.push import ledger

    recognition = []
    for reason in doc.get("review_reasons") or []:
        code = reason.get("code") if isinstance(reason, dict) else reason
        if code and code not in recognition:
            recognition.append(str(code))
    out = {"recognition": recognition, "ledger": ledger.reasons({"fields": fields or {}}),
           "items": review_status(fields or {}), "push": str(doc.get("last_push_reason") or "")[:300]}
    return {key: value for key, value in out.items() if value}
