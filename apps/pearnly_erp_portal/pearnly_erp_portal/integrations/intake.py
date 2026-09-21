"""LINE 收单据 → 落成 ERP 采购记录（服务端）。

链条：LINE 发来图片/PDF → 下载媒体 → 留底（Frappe `File`，私有）→ 落一条
`ERP LINE Intake` 草稿 → 人在 ERP 页面看草稿、补齐字段后确认 → 调采购域建单。

三处口径（都照旧线上，但换了落点）：

1. **同一份文件重复发只落一条草稿**：按内容 sha256 + 发件 LINE 用户去重。旧线上用的是
   识别记录的指纹；新平台的单据还没识别，就用文件本身。
2. **确认才建单，且只能建一次**：`dedupe_key` 钉在草稿名上，重复确认返回同一张单，
   不会出现两张一样的采购记录。
3. **谁可以发**：要有采购岗位（`purchase.write`）——收进来的就是采购单据。销售方向
   （旧线上 `direction=sales`）等销售单据流那一批，本批不假装支持。

还没做：识别（OCR）与字段预填、以及供人确认的那个页面。**这里不假装有**：
草稿落下时只有文件与来源，字段由确认时传入；页面在收尾批。
"""

import hashlib

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.purchasing import service as purchasing

DOCTYPE = "ERP LINE Intake"
STATUS_RECEIVED = "received"
STATUS_CONFIRMED = "confirmed"
STATUS_DISCARDED = "discarded"

MEDIA_TYPES = {"image": "image", "file": "pdf"}
MAX_BYTES = 20 * 1024 * 1024

NOT_FOUND = "Draft not found"
ALREADY_SETTLED = "This draft was already confirmed or discarded"
EMPTY_FILE = "This document has no content"
TOO_LARGE = "This document is too large"

LIST_FIELDS = ("name", "status", "direction", "sent_by", "line_user_id", "media_type",
               "file_hash", "source_file", "received_at", "decided_at", "purchase",
               "discard_reason", "note")


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


def _store_file(intake, content: bytes, filename: str) -> None:
    """把源文件挂在这条草稿上（私有）。返回的 file_url 写回草稿。"""
    if not content:
        frappe.throw(_(EMPTY_FILE))
    if len(content) > MAX_BYTES:
        frappe.throw(_(TOO_LARGE))
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": filename,
        "is_private": 1,
        "attached_to_doctype": DOCTYPE,
        "attached_to_name": intake.name,
        "content": content,
    })
    file_doc.insert(ignore_permissions=True)
    intake.source_file = file_doc.file_url


def receive(*, line_user_id, sent_by, media_type, content, filename,
            line_message_id="", direction="purchase") -> dict:
    """LINE 来了一件单据：留底并落草稿。重复内容返回已有草稿，不新建。"""
    guard.require("purchase.write")
    if direction != "purchase":
        frappe.throw(_("Only purchase documents can be sent in for now"))
    if not content:
        frappe.throw(_(EMPTY_FILE))
    if len(content) > MAX_BYTES:
        frappe.throw(_(TOO_LARGE))
    digest = hashlib.sha256(content).hexdigest()
    existing = frappe.db.get_value(DOCTYPE, {"line_user_id": line_user_id,
                                             "file_hash": digest,
                                             "status": STATUS_RECEIVED}, "name")
    if existing:
        return {"name": existing, "duplicate": True}

    intake = frappe.get_doc({
        "doctype": DOCTYPE,
        "status": STATUS_RECEIVED,
        "direction": direction,
        "sent_by": sent_by,
        "line_user_id": line_user_id,
        "media_type": MEDIA_TYPES.get(media_type, media_type or ""),
        "line_message_id": line_message_id,
        "file_hash": digest,
        "received_at": frappe.utils.now(),
    })
    intake.insert(ignore_permissions=True)
    _store_file(intake, content, filename or f"{intake.name}.bin")
    intake.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": intake.name, "duplicate": False}


def list_intakes(*, status=STATUS_RECEIVED, limit=50) -> list[dict]:
    guard.require("purchase.read")
    filters = {"status": status} if status else {}
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="received_at desc", limit_page_length=int(limit))


def get_intake(name) -> dict:
    guard.require("purchase.read")
    doc = _get(name)
    return {field: doc.get(field) for field in LIST_FIELDS}


def confirm(name, *, supplier_name, doc_date, lines, **fields) -> dict:
    """确认草稿 → 建 ERP 采购草稿。重复确认返回同一张单。"""
    guard.require("purchase.write")
    doc = _get(name)
    if doc.status == STATUS_CONFIRMED and doc.purchase:
        return {"name": doc.purchase, "intake": doc.name, "duplicate": True}
    if doc.status != STATUS_RECEIVED:
        frappe.throw(_(ALREADY_SETTLED))
    created = purchasing.create_purchase(
        supplier_name=supplier_name, doc_date=doc_date, lines=lines,
        dedupe_key=f"line-intake:{doc.name}", image_sha256=doc.file_hash,
        source="line", **fields)
    doc.status = STATUS_CONFIRMED
    doc.purchase = created["name"]
    doc.decided_at = frappe.utils.now()
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": created["name"], "intake": doc.name, "duplicate": False}


def discard(name, reason=None) -> dict:
    """丢掉这条草稿。**不删文件** —— 事后还要能查"当时传的是什么"。"""
    guard.require("purchase.write")
    doc = _get(name)
    if doc.status != STATUS_RECEIVED:
        frappe.throw(_(ALREADY_SETTLED))
    doc.status = STATUS_DISCARDED
    doc.decided_at = frappe.utils.now()
    doc.discard_reason = (reason or "")[:500] or None
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}
