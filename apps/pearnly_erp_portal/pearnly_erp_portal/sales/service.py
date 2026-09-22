"""销售单据服务：建单、确认（确认即出库）、作废（回冲）、上传识别。

三条口径：

1. **草稿只是草稿。** 建单给一个我方单号（`system_no`），但草稿不动货、也不代表成交；
   确认才落定。**本仓不做税务发票**（用户 2026-09-23），所以没有税票连号那一套。
2. **只有已确认的销售凭证动货**（`contracts.should_post_stock`）：过账走出库流水，
   与采购入库同源；幂等锚钉在单据名上，重复确认不会扣两次。
3. **出库不需要库存岗位**：录单的人已经过了 `sales.write` 这道闸，库存那边走
   `internal=True` 通道 —— 一个用户动作只过一道闸（与收银卖货同一口径）。

上传单据的识别入口与复核也在这个模块；它和手工录入落在同一张表上，只是来源不同。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import positions, stock, warehouses
from pearnly_erp_portal.masters import company
from pearnly_erp_portal.sales import contracts, ocr

DOCTYPE = "ERP Sales Document"

NOT_FOUND = "Document not found"
NOT_EDITABLE = "A confirmed document cannot be edited"
ALREADY_CONFIRMED = "This document was already confirmed"
BLOCKED = "This document cannot be confirmed yet"
POINTLESS = "Only a confirmed document can be voided"

LIST_FIELDS = ("name", "status", "doc_kind", "source", "system_no", "doc_date", "customer",
               "buyer_name", "party_type", "address",
               "has_vat", "vat_rate", "header_discount", "subtotal",
               "discount_total", "vat_amount", "grand_total",
               "payment_status", "payment_method", "payment_date", "stock_posted_at",
               "source_file", "note")


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


# ── 建单 / 改单 ─────────────────────────────────────────────────────────────


def create_document(*, doc_kind, doc_date, buyer_name, lines, source=contracts.SOURCE_MANUAL,
                    customer=None, has_vat=True, vat_rate=7, header_discount=0,
                    party_type=None, address=None, note=None, source_file=None,
                    image_sha256=None, payment_status="unpaid", payment_method=None,
                    payment_date=None) -> dict:
    guard.require("sales.write")
    if doc_kind not in contracts.DOC_KINDS:
        frappe.throw(_("Unknown document kind"))
    from pearnly_erp_portal.numbering import service as numbering

    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "status": contracts.DRAFT,
        "doc_kind": doc_kind,
        "source": source,
        # 我方单据号：系统发，建单就占号。这是**记录编号**，不是税票号（本仓没有税票号）。
        "system_no": numbering.allocate(
            direction="sales", on=doc_date,
            exists=lambda value: bool(frappe.db.exists(DOCTYPE, {"system_no": value}))),
        "doc_date": doc_date,
        "customer": customer,
        "buyer_name": buyer_name,
        "party_type": party_type or "company",
        "address": address,
        "has_vat": 1 if has_vat in (True, 1, "1", "true") else 0,
        "vat_rate": vat_rate,
        "header_discount": header_discount,
        "note": note,
        "source_file": source_file,
        "image_sha256": image_sha256,
        "payment_status": payment_status,
        "payment_method": payment_method,
        "payment_date": payment_date,
        "lines": [positions.prepare(line) for line in lines],
    })
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name, "system_no": doc.system_no, "grand_total": str(doc.grand_total)}


def update_document(name, *, lines=None, **changes) -> dict:
    guard.require("sales.write")
    doc = _get(name)
    if doc.status not in contracts.EDITABLE_STATUSES:
        frappe.throw(_(NOT_EDITABLE))
    if lines is not None:
        doc.set("lines", [])
        for line in lines:
            doc.append("lines", positions.prepare(line))
    for field in ("doc_date", "buyer_name", "customer", "party_type",
                  "address", "has_vat", "vat_rate", "header_discount", "note",
                  "source_file", "image_sha256",
                  "payment_status", "payment_method", "payment_date"):
        if field in changes:
            doc.set(field, changes[field])
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "grand_total": str(doc.grand_total)}


def list_documents(*, status=None, doc_kind=None, limit=50, start=0) -> list[dict]:
    guard.require("sales.read")
    filters = {}
    if status:
        filters["status"] = status
    if doc_kind:
        filters["doc_kind"] = doc_kind
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="doc_date desc, creation desc",
                           limit_start=int(start), limit_page_length=int(limit))


def get_document(name) -> dict:
    guard.require("sales.read")
    doc = _get(name)
    row = {field: doc.get(field) for field in LIST_FIELDS}
    row["lines"] = [{"line_no": line.line_no, "product": line.product,
                     "description": line.description, "qty": line.qty, "unit": line.unit,
                     "unit_price": line.unit_price, "discount": line.discount,
                     "line_total": line.line_total, **positions.public(line),
                     "vat_applicable": line.vat_applicable} for line in doc.lines]
    return row


# ── 确认前的闸 ──────────────────────────────────────────────────────────────


def _gate(doc) -> None:
    """确认前的全部拒因一起报，让人一次看清差什么。"""
    reasons = contracts.confirm_gates({
        "doc_kind": doc.doc_kind,
        "grand_total": doc.grand_total,
        "payment_status": doc.payment_status,
        "payment_method": doc.payment_method,
        "payment_date": doc.payment_date,
    })
    if reasons:
        frappe.throw(f"{_(BLOCKED)}: {', '.join(reasons)}")


def _post_stock(doc) -> dict:
    """确认即出库。幂等锚 = 单据名；已经有留痕就跳过。"""
    if doc.stock_posted_at or not contracts.should_post_stock(doc.doc_kind, doc.status):
        return {"posted": False}
    lines = [{"product": line.product, "qty": line.qty, "unit": line.unit,
              **positions.saved(line)}
             for line in doc.lines if line.product]
    if not lines:
        return {"posted": False}
    # 单据上没有仓库字段（多数租户只有一个仓库），落默认仓库。
    stock.apply_lines(warehouse=warehouses.ensure_default(), movement_type="sale_out",
                      lines=lines,
                      posting_date=doc.doc_date, ref_type="ERP Sales Document",
                      ref_id=doc.name, doc_no=doc.system_no, internal=True,
                      client_uuid=f"sales-doc:{doc.name}")
    frappe.db.set_value(DOCTYPE, doc.name, "stock_posted_at", frappe.utils.now(),
                        update_modified=False)
    return {"posted": True, "lines": len(lines)}


# ── 流转 ────────────────────────────────────────────────────────────────────


def confirm_document(name) -> dict:
    """确认：闸 → 落定 → 过账出库。闸不过就不落定，也不动货。"""
    guard.require("sales.write")
    frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", (name,))
    doc = _get(name)
    if doc.status == contracts.SUBMITTED:
        frappe.throw(_(ALREADY_CONFIRMED))
    if not contracts.can(doc.status, contracts.CONFIRM):
        frappe.throw(_(NOT_EDITABLE))
    _gate(doc)
    doc.status = contracts.SUBMITTED
    doc.save()
    posted = _post_stock(doc)
    frappe.db.commit()
    return {"name": doc.name, "stock": posted}


def void_document(name, reason=None) -> dict:
    """作废：把出库退回来（反向流水）。"""
    guard.require("sales.write")
    frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", (name,))
    doc = _get(name)
    if doc.status != contracts.SUBMITTED:
        frappe.throw(_(POINTLESS))
    _reverse_stock(doc, reason)
    doc.status = contracts.VOID
    doc.note = (f"{doc.note or ''}\nvoid: {reason or ''}").strip()
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}


def _reverse_stock(doc, reason=None) -> None:
    if not doc.stock_posted_at:
        return
    stock.reverse_movements(ref_type="ERP Sales Document", ref_id=doc.name,
                            client_uuid=f"sales-doc-void:{doc.name}", reason=reason)
    frappe.db.set_value(DOCTYPE, doc.name, "stock_posted_at", None, update_modified=False)


# ── 上传单据：识别 → 填进草稿 ───────────────────────────────────────────────
#
# 三条来源都落在同一张表上：手工录入直接调 `create_document`；上传走这里；
# POS 收银走收银域（已交付）。下游只有商品/库存/收发存 —— 确认即出库。


def create_from_upload(*, content, content_type, filename, doc_date=None, manual=False) -> dict:
    """上传一张单据：先识别，再把归一后的字段填进一张 `source=ocr` 的草稿。

    `manual=True` 表示人不想（或不能）识别：那就只留底、开一张空草稿，
    字段自己填 —— 上传不等于必须花钱识别。
    """
    guard.require("sales.write")
    payload = {} if manual else ocr.recognise(content, content_type)
    mapped = contracts.map_recognised(payload)
    mapped["doc_date"] = mapped.get("doc_date") or doc_date or str(frappe.utils.nowdate())
    if not mapped["lines"]:
        frappe.throw(_("Nothing was recognised; enter the lines by hand"))
    mapped["source_file"] = _store_source(content, filename) or None
    mapped["image_sha256"] = _sha256(content)
    created = create_document(**mapped, source=contracts.SOURCE_OCR)
    return {**created, "recognised": not manual, "source_file": mapped["source_file"]}


def attach_recognition(name, *, payload) -> dict:
    """人复核识别结果：把（可能改过的）识别字段填回草稿。只有草稿能改。"""
    guard.require("sales.write")
    doc = _get(name)
    if doc.status not in contracts.EDITABLE_STATUSES:
        frappe.throw(_(NOT_EDITABLE))
    mapped = contracts.map_recognised(payload)
    for field in ("doc_date", "buyer_name", "party_type", "address", "has_vat",
                  "vat_rate"):
        if mapped.get(field) is not None:
            doc.set(field, mapped[field])
    doc.set("lines", [])
    for line in mapped["lines"]:
        doc.append("lines", line)
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "grand_total": str(doc.grand_total)}


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _store_source(content: bytes, filename: str) -> str:
    """给单据留底（私有 File）。留底失败不拦业务，但要留痕。"""
    if not content:
        return ""
    try:
        file_doc = frappe.get_doc({
            "doctype": "File", "file_name": filename or "sales-source.bin",
            "is_private": 1, "content": content,
        })
        file_doc.insert(ignore_permissions=True)
        return file_doc.file_url
    except Exception:
        frappe.log_error(title="Sales source file storage failed",
                         message=frappe.get_traceback()[-1200:])
        return ""
