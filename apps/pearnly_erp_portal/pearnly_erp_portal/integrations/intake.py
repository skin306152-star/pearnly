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

识别与确认由采购记录页面承接；只有确认操作才在一个事务内落采购、付款与库存。
"""

import hashlib

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.purchasing import entry as purchasing
from pearnly_erp_portal.sales import contracts as sales_contracts
from pearnly_erp_portal.sales import entry as sales

DOCTYPE = "ERP LINE Intake"
STATUS_RECEIVED = "received"
STATUS_CONFIRMED = "confirmed"
STATUS_DISCARDED = "discarded"

MEDIA_TYPES = {"image": "image", "file": "pdf"}

# 两个方向：收进来的可能是进项单据（采购）也可能是销项单据（销售）。
# 各自要求对应岗位 —— 不能借"上传"把没有采购岗位的人变成能建采购单的人。
DIRECTIONS = ("purchase", "sales")
DIRECTION_CAPABILITY = {"purchase": "purchase.write", "sales": "sales.write"}
MAX_BYTES = 20 * 1024 * 1024

NOT_FOUND = "Draft not found"
ALREADY_SETTLED = "This draft was already confirmed or discarded"
EMPTY_FILE = "This document has no content"
TOO_LARGE = "This document is too large"

LIST_FIELDS = ("name", "status", "direction", "sent_by", "line_user_id", "media_type",
               "file_hash", "source_file", "received_at", "decided_at", "purchase",
               "sales_document", "discard_reason", "note")


def _get(name, *, write=False, lock=False):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    if lock:
        frappe.db.sql("select name from `tabERP LINE Intake` where name=%s for update", (name,))
    doc = frappe.get_doc(DOCTYPE, name)
    direction = doc.direction or "purchase"
    guard.require(direction + (".write" if write else ".read"))
    if guard.scope_filters() and doc.owner != frappe.session.user:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    doc.check_permission("write" if write else "read")
    return doc


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
    if direction not in DIRECTIONS:
        frappe.throw(_("Unknown direction"))
    guard.require(DIRECTION_CAPABILITY[direction])
    if not content:
        frappe.throw(_(EMPTY_FILE))
    if len(content) > MAX_BYTES:
        frappe.throw(_(TOO_LARGE))
    digest = hashlib.sha256(content).hexdigest()
    existing = frappe.db.get_value(DOCTYPE, {"line_user_id": line_user_id,
                                             "file_hash": digest, "direction": direction,
                                             "status": ["in", [STATUS_RECEIVED, STATUS_CONFIRMED]]}, "name")
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


def list_intakes(*, status=STATUS_RECEIVED, direction=None, limit=50) -> list[dict]:
    """列草稿。两个方向都会出现在这里，按岗位过滤由调用方决定看哪一边。"""
    guard.require("purchase.read")
    filters = {**guard.scope_filters(), **({"status": status} if status else {})}
    if direction:
        filters["direction"] = direction
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="received_at desc", limit_page_length=int(limit))


def get_intake(name) -> dict:
    guard.require("purchase.read")
    doc = _get(name)
    return {field: doc.get(field) for field in LIST_FIELDS}


def confirm(name, *, lines, doc_date=None, **fields) -> dict:
    """确认草稿：进项建采购记录、销项建销售单据。重复确认返回同一张单。"""
    doc = _get(name, write=True, lock=True)
    direction = doc.direction or "purchase"
    guard.require(DIRECTION_CAPABILITY[direction])
    if doc.status == STATUS_CONFIRMED:
        return {"name": doc.purchase or doc.sales_document, "intake": doc.name,
                "duplicate": True}
    if doc.status != STATUS_RECEIVED:
        frappe.throw(_(ALREADY_SETTLED))
    if direction == "purchase":
        def finish(created):
            purchase = frappe.get_doc("Purchase Document", created["name"])
            file_name = frappe.db.get_value("File", {"file_url": doc.source_file,
                "attached_to_doctype": DOCTYPE, "attached_to_name": doc.name}, "name")
            original = frappe.get_doc("File", file_name)
            original.check_permission("read")
            copied = frappe.get_doc({"doctype": "File", "file_name": original.file_name,
                "content": original.get_content(), "is_private": 1,
                "attached_to_doctype": purchase.doctype, "attached_to_name": purchase.name}).insert(ignore_permissions=True)
            purchase.append("attachments", {"file": copied.name, "file_url": copied.file_url,
                "file_name": copied.file_name, "kind": "bill"})
            purchase.flags.purchase_action = True
            purchase.save()
            doc.purchase, doc.status, doc.decided_at = created["name"], STATUS_CONFIRMED, frappe.utils.now()
            doc.save(ignore_permissions=True)

        payload = {**fields, "supplier_name": fields.get("supplier_name") or doc.note or "",
            "doc_date": doc_date or str(frappe.utils.nowdate()), "lines": lines,
            "image_sha256": doc.file_hash, "source": "line"}
        created = purchasing.post(payload, request_id=f"line-intake:{doc.name}", finalize=finish)
        return {"name": created["name"], "intake": doc.name, "duplicate": created.get("duplicate", False)}
    else:
        def finish(created):
            doc.sales_document, doc.status, doc.decided_at = created["name"], STATUS_CONFIRMED, frappe.utils.now()
            doc.save(ignore_permissions=True)

        payload = {**fields, "doc_kind": fields.pop("doc_kind", None) or sales_contracts.RECEIPT,
            "doc_date": doc_date or str(frappe.utils.nowdate()),
            "buyer_name": fields.pop("buyer_name", None) or _("Unknown buyer"),
            "lines": lines, "source": "line", "image_sha256": doc.file_hash,
            "source_file": doc.source_file}
        created = sales.post(payload, request_id=f"line-intake:{doc.name}", finalize=finish)
        return {"name": created["name"], "intake": doc.name,
                "duplicate": created.get("duplicate", False)}
    doc.status = STATUS_CONFIRMED
    doc.decided_at = frappe.utils.now()
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": created["name"], "intake": doc.name, "duplicate": False}


def discard(name, reason=None) -> dict:
    """丢掉这条草稿。**不删文件** —— 事后还要能查"当时传的是什么"。"""
    doc = _get(name, write=True, lock=True)
    guard.require(DIRECTION_CAPABILITY[doc.direction or "purchase"])
    if doc.status != STATUS_RECEIVED:
        frappe.throw(_(ALREADY_SETTLED))
    doc.status = STATUS_DISCARDED
    doc.decided_at = frappe.utils.now()
    doc.discard_reason = (reason or "")[:500] or None
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}


def has_permission(doc, user=None, permission_type=None):
    user = user or frappe.session.user
    if not guard.has((doc.direction or "purchase") + ".read", user):
        return False
    return not guard.scope_filters(user) or doc.owner == user


def permission_query(user=None):
    user = user or frappe.session.user
    conditions = []
    if guard.scope_filters(user):
        conditions.append("`tabERP LINE Intake`.owner=" + frappe.db.escape(user))
    directions = [direction for direction in DIRECTIONS if guard.has(direction + ".read", user)]
    if not directions:
        return "1=0"
    conditions.append("`tabERP LINE Intake`.direction in (" + ",".join(frappe.db.escape(d) for d in directions) + ")")
    return " and ".join(conditions)
