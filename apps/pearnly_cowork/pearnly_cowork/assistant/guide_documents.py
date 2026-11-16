"""Cowork 采购 / 销售的引导式（CC 对话里点「采购」「销售」交票，documents 出题源）：声明 `assistant.guides.GUIDES` 点名的产品函数。

与 ERP（`pearnly_erp_portal.express.guide_documents`）同一组签名、同一条路（`pearnly_core.assistant.guide.documents`）；
每个函数只转调已有的服务函数，不另写规则：

- 交文件 / 识别：代录资料区同一个入口（`operator_source.intake` / `uploads`：存成来源文件、按这家公司现用的连接与账套交识别，
  同录入工作台上传）。方向由识别判（票上卖方是本公司 = 销售），选了采购交了销售票由题卡的「业务对不上」一题问。
- 复核：票面形状与标记照共用契约 `pearnly_core.write_precheck.review`；改一格写回单据主页（`history.storage.update_pages`，带
  版本号、别人刚改过不覆盖），改动记进改动记录（`document_checks.record`，依据 = 复核卡上本人改的），同复核页与 CC 改单据。
- 确定（直写）：CC 推送工具同一个服务函数（`tools.push_documents`：只推写入计划已开始的单、本对话账套）。
- 结果：单据上的推送状态（同推送结果）。

``ctx`` 同引擎：``{session, user, business, company, ledger, period, answers, run, draft}``。
"""
from __future__ import annotations

import contextlib
import json

import frappe
from frappe import _

from pearnly_core.write_precheck import review as card

FILES_MAX = 20
DOCS_MAX = 200
HISTORY = "Cowork OCR History"
# 票面格 ↔ 单据主页字段（对方按方向取：采购是卖方，销售是买方）
PARTY = {"purchase": ("seller_name", "seller_tax", "seller_branch"), "sales": ("buyer_name", "buyer_tax", "buyer_branch")}
FACE_FIELDS = {"doc_no": "invoice_number", "date": "date", "subtotal": "subtotal", "vat": "vat", "total": "total_amount"}
LINE_FIELDS = {"description": "name", "qty": "qty", "unit_price": "price", "amount": "subtotal"}
BASIS = "CC review card"        # 改动记录的依据（复核卡上本人改的，同 CC 改单据要写依据）
NOT_AVAILABLE = "not_available"
ENTERED, QUEUED, HELD, FAILED = "entered", "queued", "held", "failed"


@contextlib.contextmanager
def _conversation(ctx):
    """调服务时带上这段对话（本对话账套、对话附件、写入计划都按它认），出来还原。"""
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def _ids(doc_ids) -> list:
    return list(dict.fromkeys(str(doc_id) for doc_id in doc_ids or () if doc_id))[:DOCS_MAX]


# ── 交文件 / 识别 ────────────────────────────────────────────────────────────


def intake(ctx, file_ids) -> dict:
    """卡上交的文件交识别（代录资料区同一个入口）：回识别批次。请求键 = 对话文件编号（同一份再交回原来那件，不重复收费）。"""
    from pearnly_core.assistant import files
    from pearnly_cowork.assistant import operator_source

    batches, refused = [], {}
    with _conversation(ctx):
        for file_id in list(dict.fromkeys(str(item) for item in file_ids or () if item))[:FILES_MAX]:
            filename, content = files.attachment(file_id)  # loop-query: 卡上一次交的文件，最多 FILES_MAX 份
            try:
                # 每份存成来源文件交识别（同代录资料区）
                found = operator_source.intake(str(ctx.get("company") or ""), filename, content, file_id)  # loop-query: 最多 FILES_MAX 份
            except (frappe.ValidationError, frappe.PermissionError) as error:
                frappe.clear_messages()
                refused[file_id] = str(error)[:200]
                continue
            batch = str(found.get("name") or found.get("batch") or "")
            if found.get("accepted") and batch:
                batches.append(batch)
            else:
                refused[file_id] = str(found.get("reason") or "not_accepted")
    return {"batches": batches, "refused": refused}


def uploads(ctx, batches) -> dict:
    """这几份识别到哪了（只读，同代录资料区）；识别完带出识别出的单，还有在读的回 ``wait``。"""
    from pearnly_cowork.assistant import operator_source

    names = [str(batch) for batch in batches or () if batch]
    found = operator_source.uploads(str(ctx.get("company") or ""), "", names) if names else {}
    files, doc_ids, wait = [], [], False
    for batch in names:
        state = found.get(batch)
        if state is None:
            files.append({"batch": batch, "state": FAILED, "reason": NOT_AVAILABLE})
            continue
        files.append({"batch": batch, "state": state["state"], **({"reason": state["code"]} if state.get("code") else {})})
        wait = wait or state["state"] in ("queued", "reading")
        doc_ids.extend(document["doc_id"] for document in state.get("documents") or ())
    return {"files": files, "doc_ids": list(dict.fromkeys(doc_ids))[:DOCS_MAX], "wait": wait}


# ── 复核 ─────────────────────────────────────────────────────────────────────


def _read(name):
    from pearnly_cowork.history import storage

    try:
        return storage.project(storage.read(name))
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return None


def _primary(doc):
    from pearnly_cowork.assistant import tools

    pages = doc.get("pages") or []
    return (tools._primary(pages), pages) if pages else (None, pages)


def _direction(fields) -> str:
    return "sales" if str(fields.get("direction") or "") == "sales" else "purchase"


def _face(fields) -> dict:
    party = PARTY[_direction(fields)]
    out = {face: fields.get(key) for face, key in FACE_FIELDS.items()}
    out.update(party_name=fields.get(party[0]), party_tax=fields.get(party[1]), party_branch=fields.get(party[2]))
    if not out["date"]:
        out["date"] = fields.get("date_raw")
    out["lines"] = [{face: row.get(key) if key != "subtotal" else row.get("subtotal", row.get("amount"))
                     for face, key in LINE_FIELDS.items()} for row in fields.get("items") or () if isinstance(row, dict)]
    return out


def _row(doc_id, doc) -> dict:
    index, pages = _primary(doc)
    fields = (pages[index].get("fields") or {}) if index is not None else {}
    face = card.face(_face(fields))
    marks = card.check(face)
    editable = [*card.EDITABLE, *(f"lines.{index}.{column}" for index in range(len(face["lines"])) for column in card.LINE_FIELDS)]
    return {"doc_id": doc_id, "direction": _direction(fields), "version": str(doc.get("record_version") or ""), "face": face,
            "marks": marks["marks"], "cells": marks["cells"], "blocking": marks["blocking"], "editable": sorted(editable)}


def review(ctx, doc_ids) -> dict:
    """复核卡：每张一行票面与标记（标记不拦；``blocking`` 那几格写不进账本、这一张这次不写）。读不到的不列。"""
    rows = []
    for doc_id in _ids(doc_ids):
        doc = _read(doc_id)  # loop-query: 单据带各页字段，按存储同一个读法逐张读（同复核页），一次引导最多 DOCS_MAX 张
        if doc and doc.get("pages"):
            rows.append(_row(doc_id, doc))
    return {"rows": rows}


def edit(ctx, doc_id, field, value) -> dict:
    """就地改一格，写回单据主页（版本号对上才存，别人刚改过不覆盖），记一条改动。回改后的这一行。"""
    from pearnly_cowork.assistant import document_checks
    from pearnly_cowork.history import storage

    doc = _read(str(doc_id))
    if not doc:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    index, pages = _primary(doc)
    if index is None:
        return {"refused": NOT_AVAILABLE}
    before = dict(pages[index].get("fields") or {})
    field = str(field or "")
    face, _marks = card.edit(card.face(_face(before)), field, value)   # 改不了的格这里就拒（契约）
    fields = dict(before)
    party = PARTY[_direction(before)]
    keys = []
    if field in ("party_name", "party_tax", "party_branch"):
        target = party[("party_name", "party_tax", "party_branch").index(field)]
        fields[target] = face[field]
        keys.append(target)
    elif field in FACE_FIELDS:
        target = FACE_FIELDS[field]
        fields[target] = face[field]
        keys.append(target)
    items_changed = False
    if field.startswith("lines."):
        _head, index_text, column = field.split(".")
        items = [dict(row) for row in before.get("items") or ()]
        items[int(index_text)][LINE_FIELDS[column]] = face["lines"][int(index_text)][column]
        fields["items"] = items
        items_changed = True
    pages[index] = {**pages[index], "fields": fields}
    try:
        saved = storage.update_pages(str(doc_id), pages, str(doc.get("record_version") or ""))
        document_checks.record(str(doc_id), before, fields, keys, items_changed=items_changed, basis=BASIS)
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.db.rollback()
        frappe.clear_messages()
        return {"refused": "changed"}
    return _row(str(doc_id), storage.project(saved) if not isinstance(saved, dict) else saved)


# ── 确定（直写）与结果 ────────────────────────────────────────────────────────


def confirm(ctx, doc_ids, consent) -> dict:
    """直写：CC 推送工具同一个服务函数（只推写入计划已开始的单）。``consent`` 由引擎存档（计划上也记了）。"""
    from pearnly_cowork.assistant import tools

    with _conversation(ctx):
        found = json.loads(tools.push_documents({"documents": _ids(doc_ids)}))
    docs = [{"doc_id": str(item.get("document") or ""), "status": QUEUED if item.get("status") in ("queued", "success", "entered")
             else HELD, **({"reason_text": str(item["reason"])} if item.get("reason") else {})}
            for item in found.get("results") or () if item.get("document")]
    return {"status": "done", "docs": docs}


def result(ctx, doc_ids) -> dict:
    """每张一行：已入账（账本单号）/ 在写 / 留下（原因）/ 没成；还有在写的回 ``wait``。推送状态与单号各一次取回。"""
    from pearnly_cowork.assistant import tools

    names = _ids(doc_ids)
    found = {row.name: row for row in frappe.get_all(
        HISTORY, filters={"name": ["in", names or [""]]}, fields=["name", "last_push_status", "last_push_reason", "last_push_record"],
        limit_page_length=len(names) or 1)}
    pushed = [row.last_push_record for row in found.values() if row.last_push_status == "success" and row.last_push_record]
    numbers = {row.name: row.external_docnum for row in frappe.get_all(
        "Cowork Push Record", filters={"name": ["in", pushed]}, fields=["name", "external_docnum"],
        limit_page_length=len(pushed))} if pushed else {}
    rows, wait = [], False
    for doc_id in names:
        row = found.get(doc_id)
        if not row:
            rows.append({"doc_id": doc_id, "status": FAILED, "reason": NOT_AVAILABLE})
            continue
        status = {"success": ENTERED, "pending": QUEUED}.get(str(row.last_push_status or ""),
                                                              FAILED if row.last_push_status == "failed" else HELD)
        wait = wait or status == QUEUED
        out = {"doc_id": doc_id, "status": status}
        if status == ENTERED and numbers.get(row.last_push_record):
            out["docnum"] = str(numbers[row.last_push_record])
        reason = tools._reason(row.last_push_reason) if row.last_push_reason and status != ENTERED else ""
        if reason:
            out["reason_text"] = str(reason)
        rows.append(out)
    return {"rows": rows, "wait": wait}


__all__ = ["ENTERED", "FAILED", "HELD", "QUEUED", "confirm", "edit", "intake", "result", "review", "uploads"]
