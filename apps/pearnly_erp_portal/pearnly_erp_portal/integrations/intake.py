"""LINE 收单：按选好的选项识别 → 预览卡 → 确定入账 / 丢弃（用户 2026-09-25 定稿）。

链条：`line_flow` 问完公司、供应商/客户、已付/未付、付款方式、服务/库存（必要时仓库）→
用户发 PDF/图片 → 这里留底（私有 File）并落一条内部记录 → 交共用 OCR 队列识别 →
推一张预览卡（`line_cards`）→ 点「确定入账」走**网页端同一个**采购 / 销售入账函数
（供应商/客户匹配或新建、商品匹配或新建、付款、库存过账都在那里，收发存按现有功能关联）。

几条口径：
- **不做草稿**：这张内部记录网页端不显示；没点入账的，丢弃或过期就连文件一起清掉。
- 预览卡 10 分钟没有任何操作就失效；入账或丢弃之后这张卡也失效（卡上带一次性 nonce）。
- 重复单据允许入账，同一个文件也放行（用户 2026-09-25）；同一张卡只入一次（`line-intake:<记录名>`）。
- 服务类不动库存；库存类采购入库、销售出库（允许负库存，回执里写明）。
"""
import hashlib
import json
import secrets

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now, now_datetime

from pearnly_core import line, line_binding

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.integrations import line_cards, line_flow

DOCTYPE = "ERP LINE Intake"
QUEUED, READY, FAILED = "queued", "ready", "failed"
CONFIRMED, DISCARDED, EXPIRED = "confirmed", "discarded", "expired"
OCR_KIND = "erp.line_intake"
CARD_MINUTES = 10
MEDIA_TYPES = {"image": "image", "file": "pdf"}
DIRECTION_CAPABILITY = {"purchase": "purchase.write", "sales": "sales.write"}
MAX_BYTES = 20 * 1024 * 1024


# ── 收件 ────────────────────────────────────────────────────────────────────


def receive(*, line_user_id, sent_by, choices, media_type, content, filename,
            line_message_id="", reply_token="") -> dict:
    """留底并排队识别。调用方已把会话用户切成绑定的这个账号。"""
    direction = choices.get("direction")
    if direction not in DIRECTION_CAPABILITY or not line_flow.ready(choices):
        frappe.throw(_("Choose purchase or sales first"))
    if sent_by != frappe.session.user or line_binding.user_of(line_user_id) != sent_by:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    guard.require(DIRECTION_CAPABILITY[direction])
    if not content:
        frappe.throw(_("This document has no content"))
    if len(content) > MAX_BYTES:
        frappe.throw(_("This document is too large"))
    from pearnly_erp_portal.masters import company

    company.document_company(choices.get("company"), sent_by)  # still permitted?
    # 私有、归本人、先不挂任何单据：识别与入账都按「本人上传的未挂文件」来认它。
    file = frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1,
                           "content": content}).insert(ignore_permissions=True)
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "status": QUEUED, "direction": direction, "owner": sent_by,
        "sent_by": sent_by, "line_user_id": line_user_id,
        "media_type": MEDIA_TYPES.get(media_type, media_type or ""),
        "line_message_id": line_message_id, "file_hash": hashlib.sha256(content).hexdigest(),
        "source_file": file.file_url, "received_at": now(),
        "company": choices.get("company"), "party": choices.get("party") or "",
        "is_paid": int(bool(choices.get("is_paid"))), "payment_method": choices.get("payment_method") or "",
        "item_kind": choices.get("item_kind"), "warehouse": choices.get("warehouse") or None,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    from pearnly_core.execution.errors import Deferred, QueueFull
    from pearnly_core.execution.work import submit

    line.start_loading(line_user_id, 60)
    try:
        submit(OCR_KIND, doc.name, {"intake": doc.name, "line_user_id": line_user_id,
                                    "reply_token": reply_token}, sent_by)
    except (QueueFull, Deferred):
        _drop(doc, FAILED, "queue_busy")
        line.reply(reply_token, [{"type": "text", "text": _(
            "The recognition queue is busy. Please send the document again shortly.")}])
    return {"name": doc.name}


def _file_name(doc):
    return frappe.db.get_value("File", {"file_url": doc.source_file, "owner": doc.sent_by}, "name")


def _drop(doc, status, reason=""):
    """没入账就结束：状态落定，源文件删掉（不做草稿，不留件）。"""
    name = _file_name(doc)
    if name and not frappe.db.get_value("File", name, "attached_to_name"):
        frappe.delete_doc("File", name, ignore_permissions=True, force=True)
    frappe.db.set_value(DOCTYPE, doc.name, {"status": status, "decided_at": now(),
                                            "card_nonce": "", "error": reason[:500] or None})
    frappe.db.commit()


# ── 识别（共用 OCR 队列里跑）──────────────────────────────────────────────────


def _recogniser(direction):
    if direction == "purchase":
        from pearnly_erp_portal.purchasing import recognition
    else:
        from pearnly_erp_portal.sales import recognition
    return recognition


def run(intake, line_user_id, reply_token=""):
    """登记在 `pearnly_ocr_handlers` 的识别入口。换绑 / 停用时不识别、不扣费。"""
    doc = frappe.get_doc(DOCTYPE, intake)
    if doc.status != QUEUED:
        return {"cancelled": "not_queued"}
    if line_binding.user_of(line_user_id) != doc.sent_by or doc.line_user_id != line_user_id:
        _drop(doc, FAILED, "binding_changed")
        return {"cancelled": "binding_changed"}
    previous = frappe.session.user
    frappe.set_user(doc.sent_by)
    try:
        try:
            result = _recogniser(doc.direction).recognise([{"file": _file_name(doc), "kind": "bill"}])
        except frappe.ValidationError as exc:
            _drop(doc, FAILED, str(exc))
            line.reply_or_push(line_user_id, [{"type": "text", "text": _(
                "Could not read this document. Please send it again.")}], reply_token)
            return {"failed": "recognition_failed", "notified": True}
        doc.reload()
        doc.result = json.dumps(result, ensure_ascii=False, default=str)
        doc.status = READY
        doc.card_nonce = secrets.token_urlsafe(12)
        doc.card_expires_at = add_to_date(now_datetime(), minutes=CARD_MINUTES)
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        line.reply_or_push(line_user_id, [line_cards.preview(doc, result, totals(doc))], reply_token)
        return {}
    finally:
        frappe.set_user(previous)


def ocr_failed(arguments, code):
    """队列本身失败（排队超时、结果不确定）：告诉本人，记录落定。"""
    doc = frappe.db.get_value(DOCTYPE, arguments.get("intake"), ["name", "status", "source_file",
                              "sent_by"], as_dict=True)
    if not doc or doc.status != QUEUED:
        return
    _drop(doc, FAILED, code)
    text = (_("The recognition result needs checking. Please do not resend this document yet.")
            if code == "provider_outcome_unknown" else _("Could not read this document. Please send it again."))
    line.push(arguments.get("line_user_id"), [{"type": "text", "text": text}])


# ── 预览卡 → 入账 ────────────────────────────────────────────────────────────


def build_payload(doc, result=None) -> dict:
    """把「选好的主档 + 识别字段」拼成网页端入账函数要的入参（同一个形状）。"""
    result = result if result is not None else json.loads(doc.result or "{}")
    goods = doc.item_kind == "goods"
    attachments = [{"file": _file_name(doc), "kind": "bill"}]
    party = doc.party if doc.party and doc.party != line_flow.FROM_DOCUMENT else None
    if doc.direction == "purchase":
        from pearnly_erp_portal.purchasing import recognition

        payload = {key: result[key] for key in recognition.HEADER_FIELDS
                   if result.get(key) not in (None, "")}
        lines = []
        for row in result.get("lines") or []:
            row = {**row, "item_type": "goods" if goods else "service"}
            if goods and doc.warehouse:
                row["warehouse"] = doc.warehouse
            lines.append(row)
        payload.update(company=doc.company, lines=lines, is_paid=doc.is_paid,
                       payment_method=doc.payment_method or "cash", update_stock=int(goods),
                       auto_create_product=int(goods), source="line", attachments=attachments)
        if party:
            payload["supplier"] = party
        return payload
    payload = {key: result.get(key) for key in ("doc_kind", "doc_date", "buyer_name", "party_type",
                                                  "address", "has_vat", "vat_rate")}
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    lines = []
    for row in result.get("lines") or []:
        row = {key: row.get(key) for key in ("product", "description", "qty", "unit", "unit_price",
                                             "discount") if row.get(key) not in (None, "")}
        if goods and doc.warehouse:
            row["warehouse"] = doc.warehouse
        lines.append(row)
    payload.update(company=doc.company, lines=lines, is_paid=doc.is_paid,
                   payment_method=doc.payment_method or None, source="line", attachments=attachments)
    if not doc.is_paid:
        payload["paid_amount"] = 0
    if party:
        payload["customer"] = party
    elif not payload.get("buyer_name"):
        payload["buyer_name"] = _("Unknown buyer")
    return payload


def totals(doc, result=None) -> dict:
    """卡上显示的金额用网页端同一套算法，算不出来（字段缺）就不显示金额。"""
    try:
        payload = build_payload(doc, result)
        if doc.direction == "purchase":
            from pearnly_erp_portal.purchasing import entry
        else:
            from pearnly_erp_portal.sales import entry
        return entry.preview(payload)
    except Exception:
        frappe.clear_messages()
        return {}


def _card(name, nonce, line_user_id, *, lock=False):
    """点卡片按钮：只认本人、当前这张、没过期的卡。"""
    if lock:
        frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", name)
    doc = frappe.get_doc(DOCTYPE, name) if name and frappe.db.exists(DOCTYPE, name) else None
    if not doc or doc.line_user_id != line_user_id or line_binding.user_of(line_user_id) != doc.sent_by:
        return None, _("This card is no longer valid")
    if doc.status == CONFIRMED:
        return None, _("This document was already recorded")
    if doc.status != READY or not nonce or nonce != doc.card_nonce:
        return None, _("This card is no longer valid")
    if doc.card_expires_at and get_datetime(doc.card_expires_at) < now_datetime():
        _drop(doc, EXPIRED)
        return None, _("This card has expired. Please upload the document again.")
    return doc, ""


def confirm(name, nonce, line_user_id, reply_token="", result=None) -> dict:
    """确定入账：按选项录入，成功回一段文字回执；失败说清原因，卡片仍有效。"""
    doc, problem = _card(name, nonce, line_user_id, lock=True)
    if not doc:
        _reply(line_user_id, reply_token, problem)
        return {"ok": False, "message": problem}
    previous = frappe.session.user
    frappe.set_user(doc.sent_by)
    try:
        guard.require(DIRECTION_CAPABILITY[doc.direction])
        if result is not None:
            doc.result = json.dumps(result, ensure_ascii=False, default=str)
        payload = build_payload(doc)

        def finish(created):
            link = "purchase" if doc.direction == "purchase" else "sales_document"
            frappe.db.set_value(DOCTYPE, doc.name, {link: created["name"], "status": CONFIRMED,
                                "decided_at": now(), "card_nonce": "", "result": doc.result})

        try:
            if doc.direction == "purchase":
                from pearnly_erp_portal.purchasing import entry
            else:
                from pearnly_erp_portal.products import sales as sale_items
                from pearnly_erp_portal.sales import entry

                if doc.item_kind == "goods":
                    payload["lines"] = [sale_items.resolve(dict(row)) for row in payload["lines"]]
            created = entry.post(payload, request_id="line-intake:" + doc.name, finalize=finish,
                                 allow_duplicate=True)
        except frappe.ValidationError as exc:
            frappe.db.rollback()
            frappe.clear_messages()
            message = _("Could not record: {0}").format(frappe.utils.strip_html(str(exc)))
            _reply(line_user_id, reply_token, message)
            return {"ok": False, "message": message}
        except Exception:
            frappe.db.rollback()
            frappe.clear_messages()
            frappe.log_error(title="ERP LINE record failed")
            message = _("Could not record this document. Please try again.")
            _reply(line_user_id, reply_token, message)
            return {"ok": False, "message": message}
        text = line_cards.receipt(doc, created, negatives=_negatives(doc, created))
        _reply(line_user_id, reply_token, text)
        return {"ok": True, "name": created["name"], "message": text}
    finally:
        frappe.set_user(previous)


def discard(name, nonce, line_user_id, reply_token=""):
    doc, problem = _card(name, nonce, line_user_id, lock=True)
    if not doc:
        return _reply(line_user_id, reply_token, problem)
    _drop(doc, DISCARDED)
    _reply(line_user_id, reply_token, _("Discarded"))


def _reply(line_user_id, reply_token, text):
    line.reply_or_push(line_user_id, [{"type": "text", "text": text}], reply_token)


def _negatives(doc, created) -> list:
    """销售出库后变成负数的商品（回执里写明）。"""
    if doc.direction != "sales" or doc.item_kind != "goods":
        return []
    from pearnly_erp_portal.inventory import stock

    lines = frappe.get_all("ERP Sales Document Line", filters={"parent": created["name"]},
                           fields=["product", "warehouse", "location"])
    result = []
    for row in lines:
        if not row.product:
            continue
        balance = stock.current_balance(row.product, row.warehouse or doc.warehouse, row.location or "")
        if balance.qty < 0:
            result.append((frappe.db.get_value("ERP Product", row.product, "product_name"), balance.qty))
    return result


# ── 清理 ────────────────────────────────────────────────────────────────────


def expire_stale():
    """每小时：过期没点的卡、卡住的排队记录，连源文件一起清掉（不留草稿）。"""
    now_at = now_datetime()
    for row in frappe.get_all(DOCTYPE, filters={"status": READY, "card_expires_at": ["<", now_at]},
                              fields=["name", "source_file", "sent_by"], limit_page_length=500):
        _drop(row, EXPIRED)
    for row in frappe.get_all(DOCTYPE, filters={"status": QUEUED,
                              "received_at": ["<", add_to_date(now_at, hours=-6)]},
                              fields=["name", "source_file", "sent_by"], limit_page_length=500):
        _drop(row, FAILED, "stale")
