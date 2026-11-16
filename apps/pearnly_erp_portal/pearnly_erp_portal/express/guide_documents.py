"""ERP 采购 / 销售的引导式（CC 对话里点「采购」「销售」交票，documents 出题源）：声明 `express.cc_guides.GUIDES` 点名的产品函数。

一次引导：交文件 → 识别 → 题卡（出题与记答全走写账前置判定，`express.precheck` 认 ``I:<收单记录>``，不在这里）→ 复核 → 确定 → 结果。
直写、代录同一条路，只在确定之后分叉（直写 = 这里的 :func:`confirm` 提交成 ERP 单、提交即排直写；代录 = 引擎交 `hand_in`）。
每个函数只转调已有的服务函数，不另写规则：

- 交文件 / 识别：ERP 对话收单 `integrations.intake.receive_chat` / `run_chat`（同 LINE 收单、同代录放文件；方向按菜单上点的业务，不猜），
  识别状态同代录资料区（`operator.uploads`）。
- 复核：票面形状与标记照共用契约 `pearnly_core.write_precheck.review`；改一格写回收单记录的识别结果（提交时 `build_payload` 照它拼，
  直写与代录都带上）。合计与税额按网页同一个算法算（`intake.totals`），不能直接改。
- 确定：网页同一个入账函数（`intake.submit_chat`，请求号按记录固定、一张只入一次）。在对话里提交，排直写带上这段对话
  （`conversation.for_session`），写进本对话账套、过写入计划的门。
- 结果：单据上的写账状态（`cc_tools._outcome`，同对话推送结果）。

``ctx`` 同引擎：``{session, user, business, company, ledger, period, answers, run, draft}``。
"""
from __future__ import annotations

import contextlib
import json
from decimal import Decimal

import frappe
from frappe import _

from pearnly_core.write_precheck import review as card

INTAKE = "I"
FILES_MAX = 20                  # 一次交几份文件
DOCS_MAX = 200                  # 一次引导几张票（同写入计划 DIRECT_MAX）
# 票面格 ↔ 收单记录识别结果的键（两个方向各一份；合计、税额是算出来的，不在这里）
FACE_KEYS = {
    "purchase": {"doc_no": "doc_no", "party_name": "supplier_name", "party_tax": "supplier_tax_id",
                 "party_branch": "supplier_branch_no", "date": "doc_date"},
    "sales": {"doc_no": "doc_no", "party_name": "buyer_name", "party_tax": "buyer_tax_id",
              "party_branch": "buyer_branch_no", "date": "doc_date"},
}
LINE_KEYS = ("description", "qty", "unit_price")   # 明细里能改的列（金额 = 数量 × 单价，网页同算）
# 改不了的格（原因码给页面，页面按码出四语）
COMPUTED = "computed"
NOT_AVAILABLE = "not_available"
# 结果行状态（引擎约定 entered / queued / held / failed）
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


def _company(ctx) -> str:
    return str(ctx.get("company") or "")


def _direction(ctx) -> str:
    business = str(ctx.get("business") or "")
    if business not in FACE_KEYS:
        frappe.throw(_("Invalid request"))
    return business


def _record(ctx, doc_id):
    """``I:<记录>`` → 本人、对话来的、这家公司的收单记录行（`operator._intake`）；别的回 None。"""
    from pearnly_erp_portal.express import operator

    if str(doc_id or "").partition(":")[0] != INTAKE:
        return None
    return operator._intake(_company(ctx), str(doc_id))


def _ids(doc_ids) -> list:
    return list(dict.fromkeys(str(doc_id) for doc_id in doc_ids or () if doc_id))[:DOCS_MAX]


# ── 交文件 / 识别 ────────────────────────────────────────────────────────────


def intake(ctx, file_ids) -> dict:
    """卡上交的文件交识别：每份一条收单记录（方向按业务），回识别批次（= 收单记录名）。同一份文件再交回原来那条
    （请求键 = 对话文件编号：不重复识别、不重复收费；上次失败的才重来）。交不进去的回 ``refused``（文件 → 原因码）。"""
    from pearnly_core.assistant import files
    from pearnly_erp_portal.integrations import intake as chat_intake

    direction = _direction(ctx)
    batches, refused = [], {}
    with _conversation(ctx):
        for file_id in list(dict.fromkeys(str(item) for item in file_ids or () if item))[:FILES_MAX]:
            filename, content = files.attachment(file_id)
            found = chat_intake.receive_chat(company=_company(ctx), direction=direction, filename=filename,
                                             content=content, request_key=file_id, session_id=str(ctx.get("session") or ""))
            if found.get("status") == chat_intake.FAILED:
                refused[file_id] = str(found.get("reason") or "failed")
            else:
                batches.append(found["name"])
    return {"batches": batches, "refused": refused}


def uploads(ctx, batches) -> dict:
    """这几份识别到哪了（只读，同代录资料区）：每份的状态与识别出的票（``I:<记录>``，一个文件几张票各一条）；
    还有在读的回 ``wait``。识别出的票只列还等着提交的（已提交的另由结果读）。"""
    from pearnly_erp_portal.express import operator

    names = [str(batch) for batch in batches or () if batch]
    found = operator.uploads(_company(ctx), "", names) if names else {}
    files, doc_ids, wait = [], [], False
    for batch in names:
        state = found.get(batch)
        if state is None:
            files.append({"batch": batch, "state": FAILED, "reason": NOT_AVAILABLE})
            continue
        files.append({"batch": batch, "state": state["state"], **({"reason": state["code"]} if state.get("code") else {})})
        if state["state"] in ("queued", "reading"):
            wait = True
        for document in state.get("documents") or ():
            doc_ids.append(document["doc_id"])
    return {"files": files, "doc_ids": list(dict.fromkeys(doc_ids))[:DOCS_MAX], "wait": wait}


# ── 复核 ─────────────────────────────────────────────────────────────────────


def _fields(doc, result) -> dict:
    """收单记录 → 共用复核卡的票面（金额按网页同一算法算）。"""
    from pearnly_erp_portal.integrations import intake as chat_intake

    keys = FACE_KEYS[doc.direction]
    totals = chat_intake.totals(doc, result)
    fields = {face: result.get(key) for face, key in keys.items()}
    fields.update(subtotal=totals.get("subtotal"), vat=totals.get("vat_amount"), total=totals.get("grand_total"))
    lines = []
    for row in result.get("lines") or ():
        qty, price = card.amount(row.get("qty")), card.amount(row.get("unit_price"))
        line_amount = qty * price if isinstance(qty, Decimal) and isinstance(price, Decimal) else row.get("amount")
        lines.append({"description": row.get("description"), "qty": row.get("qty"), "unit_price": row.get("unit_price"),
                      "amount": line_amount})
    fields["lines"] = lines
    return fields


def _version(doc) -> str:
    return str(doc.modified or "")


def _row(doc_id, doc) -> dict:
    result = json.loads(doc.result or "{}")
    face = card.face(_fields(doc, result))
    marks = card.check(face)
    return {"doc_id": doc_id, "direction": doc.direction, "version": _version(doc), "face": face, "marks": marks["marks"],
            "cells": marks["cells"], "blocking": marks["blocking"]}


def review(ctx, doc_ids) -> dict:
    """复核卡：每张一行票面与标记（标记不拦，``blocking`` 那几格写不进账本、这一张这次不写）。不是本人的 / 已提交的 / 识别没成的不列。"""
    from pearnly_erp_portal.integrations import intake as chat_intake

    rows = []
    for doc_id in _ids(doc_ids):
        row = _record(ctx, doc_id)
        if row is None or row.status != chat_intake.READY:
            continue
        rows.append(_row(doc_id, frappe.get_doc(chat_intake.DOCTYPE, row.name)))
    return {"rows": rows}


def edit(ctx, doc_id, field, value) -> dict:
    """就地改一格，写回收单记录的识别结果（锁住这一行、只认本人、只认还没提交的）。能改：单号、对方名、税号、分店、日期，
    明细的说明 / 数量 / 单价；合计、税额、明细金额由网页同一算法算，改了回 ``refused: computed``。回改后的这一行。"""
    from pearnly_erp_portal.integrations import intake as chat_intake

    row = _record(ctx, doc_id)
    if row is None:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    doc = chat_intake._owned(row.name, lock=True)
    if doc.status != chat_intake.READY:
        return {"refused": NOT_AVAILABLE}
    field = str(field or "")
    if field in card.MONEY_FIELDS:
        return {"refused": COMPUTED}
    keys = FACE_KEYS[doc.direction]
    result = json.loads(doc.result or "{}")
    if field in keys:
        face, _marks = card.edit(card.face(_fields(doc, result)), field, value)
        result[keys[field]] = face[field]
    else:
        head, _sep, rest = field.partition(".")
        index, _sep, column = rest.partition(".")
        lines = result.get("lines") or []
        if head != "lines" or column not in card.LINE_FIELDS or not index.isdigit() or int(index) >= len(lines):
            frappe.throw(_("Invalid request"))
        if column not in LINE_KEYS:
            return {"refused": COMPUTED}
        lines[int(index)][column] = card._text(value)
    doc.result = json.dumps(result, ensure_ascii=False, default=str)
    doc.save(ignore_permissions=True)
    return _row(str(doc_id), doc)


# ── 确定（直写）与结果 ────────────────────────────────────────────────────────


def confirm(ctx, doc_ids, consent) -> dict:
    """直写：逐张用网页同一个入账函数提交（提交即排直写，带上这段对话：写进本对话账套、过写入计划的门）。代录不经这里。
    一张提交不了（还有没定的行、付款没定、规则不过）这一张留下、原因原样，其余照常。``consent`` 由引擎存档。"""
    from pearnly_erp_portal.express import conversation
    from pearnly_erp_portal.integrations import intake as chat_intake

    docs = []
    with _conversation(ctx), conversation.for_session(str(ctx.get("session") or "")):
        for doc_id in _ids(doc_ids):
            row = _record(ctx, doc_id)
            if row is None:
                docs.append({"doc_id": doc_id, "status": HELD, "reason": "not_permitted"})
                continue
            problem = _not_ready(row)
            if problem:
                docs.append({"doc_id": doc_id, "status": HELD, "reason": problem})
                continue
            try:
                created = chat_intake.submit_chat(row.name)
            except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
                frappe.db.rollback()
                frappe.clear_messages()
                docs.append({"doc_id": doc_id, "status": HELD, "reason_text": str(error)[:300]})
                continue
            frappe.db.commit()  # 一张一提交：后面一张出错回滚不带走前面已入账的
            docs.append({"doc_id": doc_id, "status": QUEUED, "document": created.get("name")})
    return {"status": "done", "docs": docs}


UNDECIDED_LINES = "undecided_lines"
PAYMENT_UNDECIDED = "payment_undecided"


def _not_ready(row) -> str:
    """提交前还差人定的（同代录开录前 `operator._submit_intake` 的两条底线）：有没定的行、付款没定。付款在卡上点过，或对这个对方
    点过一次、记住了（卡上算已定）都算定了——记住的这里写回记录；只有票面判出的预选、没点过的，这张留下（卡上没点过的预选不生效）。"""
    from pearnly_erp_portal.express import choices as remembered
    from pearnly_erp_portal.express import operator
    from pearnly_erp_portal.integrations import intake as chat_intake

    record = frappe.db.get_value(chat_intake.DOCTYPE, row.name, ["line_choices", "result", "direction", "company"], as_dict=True)
    chosen = json.loads(record.line_choices or "null") or {}
    if any(line and not line.get("decided") for line in chosen.get("lines") or []):
        return UNDECIDED_LINES
    if chosen.get("payment_decided"):
        return ""
    subject = operator.payment_subject(record.direction, json.loads(record.result or "{}"))
    value = remembered.chosen(record.company, operator.PAYMENT_KIND, subject)
    if value == operator.UNPAID or value in operator._methods(record.direction):
        frappe.db.set_value(chat_intake.DOCTYPE, row.name, operator._payment_values(record.direction, value))
        return ""
    return PAYMENT_UNDECIDED


def result(ctx, doc_ids) -> dict:
    """每张一行：已入账（账本单号）/ 在写 / 留下（原因）/ 没成。还有在写的回 ``wait``（页面过一会儿再取）。"""
    from pearnly_erp_portal.express import cc_tools
    from pearnly_erp_portal.integrations import intake as chat_intake

    rows, wait = [], False
    for doc_id in _ids(doc_ids):
        row = _record(ctx, doc_id)
        if row is None:
            rows.append({"doc_id": doc_id, "status": FAILED, "reason": "not_permitted"})
            continue
        record = frappe.db.get_value(chat_intake.DOCTYPE, row.name, ["status", "direction", "purchase", "sales_document"],
                                     as_dict=True)
        document = record.purchase if record.direction == "purchase" else record.sales_document
        if record.status != chat_intake.CONFIRMED or not document:
            rows.append({"doc_id": doc_id, "status": HELD})
            continue
        found = cc_tools._outcome(cc_tools.KINDS[record.direction], document)
        status = {"entered": ENTERED, "queued": QUEUED}.get(found.get("status"), HELD if found.get("status") != "failed" else FAILED)
        wait = wait or status == QUEUED
        rows.append({"doc_id": doc_id, "status": status, "document": document,
                     **({"docnum": found["docnum"]} if found.get("docnum") else {}),
                     **({"reason_text": found["reason"]} if found.get("reason") else {})})
    return {"rows": rows, "wait": wait}


__all__ = ["COMPUTED", "ENTERED", "FAILED", "HELD", "NOT_AVAILABLE", "PAYMENT_UNDECIDED", "QUEUED", "UNDECIDED_LINES",
           "confirm", "edit", "intake", "result", "review", "uploads"]
