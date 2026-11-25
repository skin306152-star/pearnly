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
UNCONFIRMED = "unconfirmed"   # 写账结果不明：账本里可能有、还没核对（页面显示「结果不明」，不算失败也不算跳过）


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


# 一次取回的列：判据 + 复核票面与合计（`intake.build_payload` 读的那几列）+ 提交前的付款 / 行
RECORD_FIELDS = ["name", "source", "sent_by", "company", "direction", "status", "purchase", "sales_document", "modified",
                 "result", "line_choices", "is_paid", "payment_method", "warehouse", "item_kind", "party", "source_file_name"]


def _records(ctx, doc_ids) -> dict:
    """这批 ``I:<记录>`` 一次取回（同 `operator._intake` 的判据：本人、对话来的、这家公司的、有这类单据的开单权）：
    ``{doc_id: 记录}``；别的不在里面。"""
    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.integrations import intake as chat_intake

    names = {str(doc_id).partition(":")[2]: str(doc_id) for doc_id in _ids(doc_ids)
             if str(doc_id).partition(":")[0] == INTAKE and str(doc_id).partition(":")[2]}
    if not names:
        return {}
    allowed = {direction for direction, capability in chat_intake.DIRECTION_CAPABILITY.items() if guard.has(capability)}
    rows = frappe.get_all(chat_intake.DOCTYPE, filters={"name": ["in", list(names)], "source": chat_intake.SOURCE_CC,
                                                        "sent_by": frappe.session.user, "company": _company(ctx)},
                          fields=RECORD_FIELDS, limit_page_length=len(names))
    return {names[row.name]: row for row in rows if row.direction in allowed}


DOCUMENT_PREFIXES = {"P": "purchase", "S": "sales"}   # 已经是 ERP 单据的（待处理、代录待录清单里的 P: / S:）


def direction_of(doc_id) -> str | None:
    """这张单是采购还是销售（别的引导交单给 documents 源时定业务用，公开）：``P:`` / ``S:`` 按前缀，``I:`` 按收单记录；
    读不到回 None。"""
    prefix, _sep, name = str(doc_id or "").partition(":")
    if prefix in DOCUMENT_PREFIXES and name:
        return DOCUMENT_PREFIXES[prefix]
    if prefix == INTAKE and name:
        from pearnly_erp_portal.integrations import intake as chat_intake

        found = frappe.db.get_value(chat_intake.DOCTYPE, name, ["direction", "sent_by"], as_dict=True)
        return str(found.direction) if found and found.sent_by == frappe.session.user else None
    return None


def _document_row(doc_id):
    """``P:`` / ``S:`` → (种类, 单据)：单据页同一判据（`cc_tools._row`：查看权 / 公司可见 / 员工只看本人录入的）；看不到回 None。"""
    from pearnly_erp_portal.express import cc_tools

    kind, name = cc_tools._parse(doc_id)
    if kind is None or kind.code not in DOCUMENT_PREFIXES.values():
        return None
    row = cc_tools._row(kind, name)
    return (kind, frappe.get_doc(kind.doctype, name)) if row not in (None, cc_tools.NOT_PERMITTED) else None


def _document_review(doc_id, kind, doc) -> dict:
    """已经是 ERP 单据的一行复核票面（改要回单据页走网页的修改，这里只看：``editable`` 为空）。"""
    party = ("supplier_name", "supplier_tax_id", "supplier_branch_no") if kind.code == "purchase" else \
        ("buyer_name", "buyer_tax_id", "buyer_branch_no")
    fields = {"doc_no": doc.get("doc_no") or doc.get("system_no"), "party_name": doc.get(party[0]), "party_tax": doc.get(party[1]),
              "party_branch": doc.get(party[2]), "date": doc.get("doc_date"), "subtotal": doc.get("subtotal"),
              "vat": doc.get("vat_amount"), "total": doc.get("grand_total"),
              "lines": [{"description": row.get("description"), "qty": row.get("qty"), "unit_price": row.get("unit_price"),
                         "amount": row.get("amount")} for row in doc.get("lines") or ()]}
    face = card.face(fields)
    marks = card.check(face)
    return {"doc_id": doc_id, "direction": kind.code, "version": str(doc.get("modified") or ""), "face": face,
            "marks": marks["marks"], "cells": marks["cells"], "blocking": marks["blocking"], "editable": []}


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
            filename, content = files.attachment(file_id)  # loop-query: 卡上一次交的文件，最多 FILES_MAX 份
            # 每份文件一条收单记录交识别（同代录放文件）
            found = chat_intake.receive_chat(company=_company(ctx), direction=direction, filename=filename,  # loop-query: 最多 FILES_MAX 份
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
    editable = [*FACE_KEYS[doc.direction], *(f"lines.{index}.{column}" for index in range(len(face["lines"])) for column in LINE_KEYS)]
    return {"doc_id": doc_id, "direction": doc.direction, "version": _version(doc), "face": face, "marks": marks["marks"],
            "cells": marks["cells"], "blocking": marks["blocking"], "editable": editable}


def review(ctx, doc_ids) -> dict:
    """复核卡：每张一行票面与标记（标记不拦，``blocking`` 那几格写不进账本、这一张这次不写）。不是本人的 / 已提交的 / 识别没成的不列。"""
    from pearnly_erp_portal.integrations import intake as chat_intake

    found = _records(ctx, doc_ids)
    rows = []
    for doc_id in _ids(doc_ids):
        if doc_id.partition(":")[0] in DOCUMENT_PREFIXES:
            got = _document_row(doc_id)  # loop-query: 已经是 ERP 单据的按单据页同一判据逐张读，一次引导最多 DOCS_MAX 张
            if got:
                rows.append(_document_review(doc_id, *got))
            continue
        row = found.get(doc_id)
        if row is None or row.status != chat_intake.READY:
            continue
        rows.append(_row(doc_id, row))  # loop-query: 合计按网页同一个算法逐张算（intake.totals），一次引导最多 DOCS_MAX 张
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
    found = _records(ctx, doc_ids)
    with _conversation(ctx), conversation.for_session(str(ctx.get("session") or "")):
        for doc_id in _ids(doc_ids):
            if doc_id.partition(":")[0] in DOCUMENT_PREFIXES:
                docs.append(_push_document(ctx, doc_id))  # loop-query: 已经是 ERP 单据的逐张推（对话推送同一条），最多 DOCS_MAX 张
                continue
            row = found.get(doc_id)
            if row is None:
                docs.append({"doc_id": doc_id, "status": HELD, "reason": "not_permitted"})
                continue
            problem = _not_ready(row)  # loop-query: 付款按对方记住的要查记忆（每张一次），一次引导最多 DOCS_MAX 张
            if problem:
                frappe.db.commit()  # 记住的付款写回了记录也留着
                docs.append({"doc_id": doc_id, "status": HELD, "reason": problem, "reason_text": _held_text(row)})
                continue
            try:
                # 逐张用网页同一个入账函数提交、一张一事务（同代录开录 `operator._submit_intake`）
                created = chat_intake.submit_chat(row.name)  # loop-query: 一张一事务提交，最多 DOCS_MAX 张
            except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
                frappe.db.rollback()
                frappe.clear_messages()
                docs.append({"doc_id": doc_id, "status": HELD, "reason_text": str(error)[:300]})
                continue
            frappe.db.commit()  # 一张一提交：后面一张出错回滚不带走前面已入账的
            docs.append({"doc_id": doc_id, "status": QUEUED, "document": created.get("name")})
    return {"status": "done", "docs": docs}


def _push_document(ctx, doc_id) -> dict:
    """已经是 ERP 单据的（待处理里停下的、代录待录里的）：对话推送同一条（`cc_tools._push_one`：写入计划的门、本对话账套、
    结果不明不再推、交给代录的不接）。"""
    from pearnly_erp_portal.express import cc_tools

    found = cc_tools._push_one(doc_id, {"company": str(ctx.get("company") or "")})
    status = str(found.get("status") or "")
    if status in ("sent", "already_success", "already_pending", "queued", "entered", "waiting_for_cc"):
        return {"doc_id": doc_id, "status": QUEUED}
    return {"doc_id": doc_id, "status": HELD, **({"reason_text": str(found["reason"])} if found.get("reason") else {}),
            **({"reason": str(found["reason_code"])} if found.get("reason_code") else {})}


UNDECIDED_LINES = "undecided_lines"
PAYMENT_UNDECIDED = "payment_undecided"


def _choices(row) -> dict:
    return json.loads(row.line_choices or "null") or {}


def _lines_open(row) -> bool:
    return any(line and not line.get("decided") for line in _choices(row).get("lines") or [])


def _not_ready(row) -> str:
    """提交前还差人定的（同代录开录前 `operator._submit_intake` 的两条底线）：有没定的行、付款没定（`operator.settle_payment`：
    卡上点过的，或对这个对方点过一次记住了的才算；只是预选、没点过的这一张留下）。"""
    from pearnly_erp_portal.express import operator

    if _lines_open(row):
        return UNDECIDED_LINES
    return "" if operator.settle_payment(row.company, row.name, row) else PAYMENT_UNDECIDED


def _held_text(row) -> str:
    """没提交的这一张为什么留下（结果行那一句，沿用代录开录同一组词条）。"""
    from pearnly_erp_portal.express import operator

    if _lines_open(row):
        return _(operator.UNDECIDED_LINES_TEXT)
    return "" if _choices(row).get("payment_decided") else _(operator.NO_PAYMENT_TEXT)


def result(ctx, doc_ids) -> dict:
    """每张一行：已入账（账本单号）/ 在写 / 留下（原因）/ 没成。还有在写的回 ``wait``（页面过一会儿再取）。"""
    from pearnly_erp_portal.express import cc_tools
    from pearnly_erp_portal.integrations import intake as chat_intake

    found = _records(ctx, doc_ids)
    rows, wait, ledgerless = [], False, {}
    for doc_id in _ids(doc_ids):
        prefix, _sep, plain = doc_id.partition(":")
        record = found.get(doc_id)
        if prefix in DOCUMENT_PREFIXES:
            record = frappe._dict(status=chat_intake.CONFIRMED, direction=DOCUMENT_PREFIXES[prefix],
                                  purchase=plain if prefix == "P" else None, sales_document=plain if prefix == "S" else None)
        if record is None:
            rows.append({"doc_id": doc_id, "status": FAILED, "reason": "not_permitted"})
            continue
        document = record.purchase if record.direction == "purchase" else record.sales_document
        if record.status != chat_intake.CONFIRMED or not document:
            text = _held_text(record) if record.status == chat_intake.READY else ""
            rows.append({"doc_id": doc_id, "status": HELD, **({"reason_text": text} if text else {})})
            continue
        bare = prefix not in DOCUMENT_PREFIXES and _ledgerless(record.company, ledgerless)  # loop-query: 按公司只判一次
        if bare:
            # 这家公司没接任何账本：提交进 ERP 就办完了（同 LINE，主会话 11-24 定），不排写账；原来按写账状态判成「未完成」
            row = frappe.db.get_value(_DOCTYPES[record.direction], document, ["ledger_state", "system_no"],  # loop-query: 逐张读单号，最多 DOCS_MAX 张
                                      as_dict=True) or {}
            if not row.get("ledger_state"):
                rows.append({"doc_id": doc_id, "status": ENTERED, "document": document,
                             **({"docnum": row["system_no"]} if row.get("system_no") else {})})
                continue
        # 每张单的写账状态同对话推送结果逐张判（`cc_tools._outcome`）
        outcome = cc_tools._outcome(cc_tools.KINDS[record.direction], document)  # loop-query: 逐张判写账状态，最多 DOCS_MAX 张
        status = {"entered": ENTERED, "queued": QUEUED, "unconfirmed": UNCONFIRMED}.get(
            outcome.get("status"), FAILED if outcome.get("status") == "failed" else HELD)
        wait = wait or status == QUEUED
        rows.append({"doc_id": doc_id, "status": status, "document": document,
                     **({"docnum": outcome["docnum"]} if outcome.get("docnum") else {}),
                     **({"reason_text": outcome["reason"]} if outcome.get("reason") else {})})
    return {"rows": rows, "wait": wait}


_DOCTYPES = {"purchase": "Purchase Document", "sales": "ERP Sales Document"}


def _ledgerless(company, seen) -> bool:
    """这家公司没接任何账本（没接 Express、账本也不是 MR.ERP）；``seen`` 是本次结果里按公司记的结论（一次引导一家公司）。"""
    if company not in seen:
        from pearnly_erp_portal.express import push
        from pearnly_erp_portal.mrerp import push as mrerp_push

        seen[company] = not push.enabled(company) and not mrerp_push.handles(company)
    return seen[company]


__all__ = ["DOCUMENT_PREFIXES", "direction_of", "COMPUTED", "ENTERED", "FAILED", "HELD", "NOT_AVAILABLE", "PAYMENT_UNDECIDED", "QUEUED", "UNDECIDED_LINES",
           "confirm", "edit", "intake", "result", "review", "uploads"]
