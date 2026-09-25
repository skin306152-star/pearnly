"""LINE 收单：按选好的选项识别 → 一张只有「编辑」的卡 → 网页端原生采购 / 销售页提交入账。

链条（用户 2026-09-25 定稿）：`line_flow` 问完公司、供应商/客户、已付/未付、付款方式、
服务/库存（必要时仓库）→ 用户发 PDF/图片 → 这里留底（私有 File）并落一条内部记录 →
交共用 OCR 队列识别 → 推一张卡，**只有「编辑」**（不过期、不回执，省官方号消息额度）→
点「编辑」在 LINE 里打开网页端**原生**的采购 / 销售新建页，识别结果与选择已填好 →
点「提交」才入账过账（与手工录入同一个入账函数，收发存按现有功能关联）→ 之后再点
「编辑」打开的是那张单的查看页，不能再改。

- 不是草稿：这条内部记录网页端列表里看不到，只能从卡片进来。
- 重复单据放行，同一个文件也放行（用户 2026-09-25）；同一张卡只入一次（`line-intake:<记录名>`）。
- 服务类不动库存；库存类采购入库、销售出库（销售匹配不到商品自动新建，允许负库存）。
"""
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import add_to_date, now, now_datetime

from pearnly_core import line, line_binding

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.integrations import line_cards, line_flow

DOCTYPE = "ERP LINE Intake"
QUEUED, READY, FAILED, CONFIRMED = "queued", "ready", "failed", "confirmed"
OCR_KIND = "erp.line_intake"
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
    # 所以记录上的源文件字段是 Data 不是 Attach —— Attach 会让 Frappe 保存时把这份文件
    # 自动挂到收单记录上，识别就会被判「不是本人未挂出的上传」而拒绝（2026-09-26 真机）。
    file = frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1,
                           "content": content}).insert(ignore_permissions=True)
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "status": QUEUED, "direction": direction, "owner": sent_by,
        "sent_by": sent_by, "line_user_id": line_user_id,
        "media_type": MEDIA_TYPES.get(media_type, media_type or ""),
        "line_message_id": line_message_id, "file_hash": hashlib.sha256(content).hexdigest(),
        "source_file": file.file_url, "source_file_name": file.name, "received_at": now(),
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
    """这张单自己那份 File。按记录编号取，不按地址找：同一张照片再发一次时，Frappe 会让新旧
    两份 File 共用同一个文件地址，按地址找可能拿到另一张单的那份。"""
    return doc.get("source_file_name") or ""


def _drop(doc, status, reason=""):
    """识别没成：状态落定，源文件删掉（不留件）。"""
    name = _file_name(doc)
    if name and not frappe.db.get_value("File", name, "attached_to_name"):
        frappe.delete_doc("File", name, ignore_permissions=True, force=True)
    frappe.db.set_value(DOCTYPE, doc.name, {"status": status, "decided_at": now(),
                                            "error": reason[:500] or None})
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
    previous, previous_lang = frappe.session.user, frappe.local.lang
    frappe.set_user(doc.sent_by)
    # LINE 里的话一律用官方号的语言（站点系统语言，与 webhook 回复同一口径），
    # 不随这个账号个人的界面语言走 —— 否则同一段对话里前面泰文、卡片突然变中文。
    frappe.local.lang = channel_language()
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
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        line.reply_or_push(line_user_id, [line_cards.preview(doc, result, totals(doc))], reply_token)
        return {}
    finally:
        frappe.set_user(previous)
        frappe.local.lang = previous_lang


def channel_language():
    return frappe.db.get_single_value("System Settings", "language") or "th"


def ocr_failed(arguments, code):
    """队列本身失败（排队超时、结果不确定）：告诉本人，记录落定。"""
    doc = frappe.db.get_value(DOCTYPE, arguments.get("intake"), ["name", "status", "source_file_name"],
                              as_dict=True)
    if not doc or doc.status != QUEUED:
        return
    _drop(doc, FAILED, code)
    frappe.local.lang = channel_language()
    text = (_("The recognition result needs checking. Please do not resend this document yet.")
            if code == "provider_outcome_unknown" else _("Could not read this document. Please send it again."))
    line.push(arguments.get("line_user_id"), [{"type": "text", "text": text}])


# ── 预览卡 → 入账 ────────────────────────────────────────────────────────────


def _fallback_name(direction):
    """「一般供应商 / 散客」落进单据和主档的名字用官方号语言（站点系统语言），不随打开网页的
    那个人的界面语言变 —— 否则同一个兜底供应商会按不同语言各建一个（09-26 实测记成了中文「散客」）。"""
    return _(line_flow.FALLBACK_LABELS[direction], lang=channel_language())


def build_payload(doc, result=None) -> dict:
    """把「选好的主档 + 识别字段」拼成网页端入账函数要的入参（同一个形状）。"""
    result = result if result is not None else json.loads(doc.result or "{}")
    goods = doc.item_kind == "goods"
    attachments = [{"file": _file_name(doc), "kind": "bill"}]
    party = doc.party if doc.party not in ("", None, line_flow.FROM_DOCUMENT, line_flow.FALLBACK) else None
    fallback = doc.party == line_flow.FALLBACK
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
        elif fallback:
            # 一般供应商：票面上的供应商信息不用，统一记到同名的那一个（第一次按名字新建，之后复用）。
            for key in ("supplier_tax_id", "supplier_branch_type", "supplier_branch_no",
                        "supplier_branch_name", "supplier_address"):
                payload.pop(key, None)
            payload["supplier_name"] = _fallback_name("purchase")
        return payload
    payload = {key: result.get(key) for key in ("doc_kind", "doc_date", "buyer_name", "party_type",
                                                  "address", "has_vat", "vat_rate", "price_includes_vat")}
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
    elif payload.get("doc_date"):
        # 定·09-25：已付的付款日期取单据日期（网页端表单默认是今天，这里预先填好）。
        payload["payment_date"] = payload["doc_date"]
    if party:
        payload["customer"] = party
    elif fallback:
        # 散客：不建客户档案，不用票面买方信息。
        payload.pop("address", None)
        payload.update(buyer_name=_fallback_name("sales"), party_type="anonymous")
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


# ── 网页端：从卡片打开 → 提交 ────────────────────────────────────────────────


def _owned(name, *, lock=False):
    """只有发这张单据的本人能打开 / 提交（服务端判，不靠页面）。"""
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)
    if lock:
        frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", name)
    doc = frappe.get_doc(DOCTYPE, name)
    if doc.sent_by != frappe.session.user:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    guard.require(DIRECTION_CAPABILITY[doc.direction])
    return doc


def open_for_edit(name) -> dict:
    """卡片「编辑」打开的数据：已入账就给单号（页面转去查看页），否则给预填好的表单数据。"""
    doc = _owned(name)
    recorded = doc.purchase if doc.direction == "purchase" else doc.sales_document
    if doc.status == CONFIRMED and recorded:
        return {"direction": doc.direction, "recorded": recorded}
    if doc.status != READY:
        frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)
    from pearnly_erp_portal.masters import company

    # 这张单在 LINE 里选的是哪家公司，页面就按哪家打开（仓库、商品选择都跟当前公司走）。
    switched = company.active_company() != doc.company
    if switched:
        company.set_active_company(doc.company)
    form = build_payload(doc)
    file = frappe.db.get_value("File", _file_name(doc), ["name", "file_name", "file_url"], as_dict=True)
    form["attachments"] = [{"file": file.name, "file_name": file.file_name, "file_url": file.file_url,
                            "kind": "bill", "intake": True}] if file else []
    if doc.direction == "purchase" and form.get("supplier"):
        form["supplier_name"] = frappe.db.get_value("ERP Supplier", form["supplier"], "supplier_name")
    if doc.direction == "sales" and form.get("customer"):
        form["buyer_name"] = frappe.db.get_value("ERP Customer", form["customer"], "customer_name")
    form.pop("company", None)
    return {"direction": doc.direction, "intake": doc.name, "switched_company": switched, "doc": form}


def submit(name, payload) -> dict:
    """页面点「提交」：用页面上的内容入账（公司与源文件以这张卡为准），不回 LINE 回执。"""
    doc = _owned(name, lock=True)
    recorded = doc.purchase if doc.direction == "purchase" else doc.sales_document
    if doc.status == CONFIRMED and recorded:
        return {"name": recorded, "duplicate": True}
    if doc.status != READY:
        frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)
    payload = dict(payload or {})
    payload["company"] = doc.company
    payload["source"] = "line"
    source = {"file": _file_name(doc), "kind": "bill"}
    others = [row for row in payload.get("attachments") or [] if row.get("file") != source["file"]]
    payload["attachments"] = [source, *others]

    def finish(created):
        link = "purchase" if doc.direction == "purchase" else "sales_document"
        frappe.db.set_value(DOCTYPE, doc.name, {link: created["name"], "status": CONFIRMED,
                                                "decided_at": now()})

    if doc.direction == "purchase":
        from pearnly_erp_portal.purchasing import entry
    else:
        from pearnly_erp_portal.products import sales as sale_items
        from pearnly_erp_portal.sales import entry

        if doc.item_kind == "goods":
            payload["lines"] = [sale_items.resolve(dict(row)) for row in payload.get("lines") or []]
    return entry.post(payload, request_id="line-intake:" + doc.name, finalize=finish, allow_duplicate=True)


# ── 清理 ────────────────────────────────────────────────────────────────────


def expire_stale():
    """每小时：卡在排队里超过 6 小时的记录落为失败并清掉源文件（卡片本身不过期）。"""
    cutoff = add_to_date(now_datetime(), hours=-6)
    for row in frappe.get_all(DOCTYPE, filters={"status": QUEUED, "received_at": ["<", cutoff]},
                              fields=["name", "source_file_name"], limit_page_length=500):
        _drop(row, FAILED, "stale")
