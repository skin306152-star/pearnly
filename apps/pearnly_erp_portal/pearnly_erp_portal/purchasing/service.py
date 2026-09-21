"""采购单据的生命周期：建单、列表、取数、提交、作废、付款。

权限：能力判据在 `access.guard`；取数一律 `frappe.get_list`，并按岗位收窄到本人
（老板看全店、员工只看自己录入的）。

刻意**不在本批做**的：过账到库存（表在库存批次）与生成会计凭证（会计不迁移）。
提交只把单据推进到 `submitted`；库存联动在库存批次接到这里，届时 `auto_stock_in`
才有效。现在这个开关默认关、并且这里不会假装已经扣过库存。
"""

from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import now, nowdate

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.purchasing import contracts, settings

DOCTYPE = "Purchase Document"
LINE_DOCTYPE = "Purchase Line"

NOT_FOUND = "Purchase document not found"
INVALID = "Invalid value"
DUPLICATE = "This document is already recorded: {0}"
LOCKED = "A submitted document cannot be changed"
VOIDED = "This document is void"
BAD_TRANSITION = "This status change is not allowed"
LINES_REQUIRED = "Provide at least one line item"

LIST_FIELDS = (
    "name", "doc_kind", "supplier", "supplier_name", "doc_no", "doc_date", "due_date",
    "status", "approval_status", "source", "currency", "grand_total", "net_payable",
    "paid_amount", "payment_status",
)


def _q2(value) -> Decimal:
    return contracts.q2(value)


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


def _dedupe_check(dedupe_key, dedupe_ref, exclude=None):
    """两把锁分开：外部幂等键（识别图片/调用方请求号）始终生效；"同一供应商的同一个
    单据号只允许一张"受采购设置的 `dedupe_block` 控制（有些门店确实会拆分记账）。
    """
    checks = [("dedupe_key", dedupe_key)]
    if settings.read_settings()["dedupe_block"]:
        checks.append(("dedupe_ref", dedupe_ref))
    for field, value in checks:
        if not value:
            continue
        filters = {field: value}
        if exclude:
            filters["name"] = ["!=", exclude]
        existing = frappe.db.get_value(DOCTYPE, filters, "name")
        if existing:
            frappe.throw(_(DUPLICATE).format(existing))


def _resolve_supplier(supplier, supplier_name):
    """把"手写供应商名"对齐到已有的供应商档案。

    为什么要对齐：去重判据是"供应商 + 单据号"。若一张单按档案编号、另一张按手写名字，
    同一个供应商会被算成两家，重复单据就拦不住了。所以按名字精确匹配到档案时自动关联；
    匹配不到就保留手写名字（旧线上也有没建档的供应商）。
    """
    if supplier:
        if not frappe.db.exists("ERP Supplier", supplier):
            frappe.throw(_("Supplier not found"))
        return supplier, frappe.db.get_value("ERP Supplier", supplier, "supplier_name")
    name = (supplier_name or "").strip()
    if not name:
        return None, supplier_name
    matched = frappe.db.get_value("ERP Supplier", {"supplier_name": name}, "name")
    return matched, name


def create_purchase(*, supplier_name, doc_date, lines, supplier=None, doc_kind="goods",
                    doc_no=None, due_date=None, currency="THB", fx_rate=1, has_vat=True,
                    discount_total=0, wht_amount=None, wht_rate=0, rounding=0,
                    supplier_tax_id=None, external_ref=None, dedupe_key=None,
                    image_sha256=None, notes=None, source=None):
    guard.require("purchase.write")
    if not isinstance(lines, list) or not lines:
        frappe.throw(_(LINES_REQUIRED))

    config = settings.read_settings()
    supplier, supplier_name = _resolve_supplier(supplier, supplier_name)
    dedupe_ref = contracts.duplicate_key(supplier or supplier_name, doc_no)
    _dedupe_check(dedupe_key, dedupe_ref)

    try:
        totals = contracts.compute_totals(
            lines=lines, has_vat=bool(has_vat), doc_kind=doc_kind,
            discount_total=discount_total, wht_rate=wht_rate, wht_amount=wht_amount,
            rounding=rounding,
        )
    except ValueError as exc:
        frappe.throw(f"{_(INVALID)}: {exc}")

    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "doc_kind": doc_kind,
        "supplier": supplier,
        "supplier_name": supplier_name,
        "supplier_tax_id": supplier_tax_id,
        "doc_no": doc_no,
        "doc_date": doc_date,
        "due_date": due_date,
        "status": contracts.DRAFT,
        "currency": currency,
        "fx_rate": fx_rate,
        "has_vat": 1 if has_vat else 0,
        "discount_total": totals["discount_total"],
        "wht_amount": totals["wht_amount"],
        "rounding": totals["rounding"],
        "external_ref": external_ref,
        "dedupe_key": dedupe_key,
        "dedupe_ref": dedupe_ref,
        "image_sha256": image_sha256,
        "notes": notes,
        "source": source,
        "lines": [
            {
                "line_no": index,
                "item_type": "service" if doc_kind == "service" else line.get("item_type", "goods"),
                "description": line["description"],
                "product_ref": line.get("product_ref"),
                "qty": line.get("qty", 1),
                "unit": line.get("unit"),
                "unit_price": line.get("unit_price", 0),
                "discount": line.get("discount", 0),
                "vat_rate": line.get("vat_rate", config["default_vat_rate"]),
            }
            for index, line in enumerate(lines, start=1)
        ],
    })
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def list_purchases(*, supplier=None, supplier_name=None, status=None, kind=None,
                   unpaid=False, q=None, date_from=None, date_to=None, limit=50):
    guard.require("purchase.read")
    filters: dict = dict(guard.scope_filters())
    if supplier:
        filters["supplier"] = supplier
    if supplier_name:
        filters["supplier_name"] = ["like", f"%{supplier_name}%"]
    if status:
        filters["status"] = status
    if kind:
        filters["doc_kind"] = kind
    if unpaid:
        filters["payment_status"] = ["in", [contracts.UNPAID, contracts.PARTIAL]]
    if date_from and date_to:
        filters["doc_date"] = ["between", [date_from, date_to]]
    elif date_from:
        filters["doc_date"] = [">=", date_from]
    elif date_to:
        filters["doc_date"] = ["<=", date_to]
    or_filters = None
    if q:
        or_filters = [["doc_no", "like", f"%{q}%"], ["supplier_name", "like", f"%{q}%"]]
    return frappe.get_list(
        DOCTYPE, filters=filters, or_filters=or_filters, fields=list(LIST_FIELDS),
        order_by="doc_date desc, creation desc", limit_page_length=int(limit),
    )


def get_purchase(name):
    guard.require("purchase.read")
    doc = _get(name)
    data = {field: doc.get(field) for field in LIST_FIELDS}
    for field in ("subtotal", "vat_amount", "wht_amount", "discount_total", "rounding",
                  "void_reason", "voided_at", "external_ref", "notes"):
        data[field] = doc.get(field)
    data["lines"] = [
        {"line_no": row.line_no, "item_type": row.item_type, "description": row.description,
         "product_ref": row.product_ref, "qty": row.qty, "unit": row.unit,
         "unit_price": row.unit_price, "discount": row.discount,
         "vat_rate": row.vat_rate, "line_total": row.line_total}
        for row in doc.get("lines") or []
    ]
    data["payments"] = [
        {"payment_date": row.payment_date, "amount": row.amount, "method": row.method,
         "note": row.note}
        for row in doc.get("payments") or []
    ]
    return data


def update_purchase(name, **changes):
    """改草稿。已提交或已作废的单据一律拒绝改动。"""
    guard.require("purchase.write")
    doc = _get(name)
    if doc.status == contracts.VOID:
        frappe.throw(_(VOIDED))
    if doc.status != contracts.DRAFT:
        frappe.throw(_(LOCKED))

    for field in ("doc_no", "doc_date", "due_date", "currency", "supplier",
                  "supplier_name", "supplier_tax_id", "external_ref", "notes", "source"):
        if field in changes:
            doc.set(field, changes[field])
    if "doc_kind" in changes:
        doc.doc_kind = changes["doc_kind"]
    if "has_vat" in changes:
        doc.has_vat = 1 if changes["has_vat"] in (True, 1, "1", "true") else 0
    if "lines" in changes:
        lines = changes["lines"]
        if not isinstance(lines, list) or not lines:
            frappe.throw(_(LINES_REQUIRED))
        doc.lines = []
        for index, line in enumerate(lines, start=1):
            doc.append("lines", {
                "line_no": index,
                "item_type": line.get("item_type", "goods"),
                "description": line["description"],
                "product_ref": line.get("product_ref"),
                "qty": line.get("qty", 1),
                "unit": line.get("unit"),
                "unit_price": line.get("unit_price", 0),
                "discount": line.get("discount", 0),
                "vat_rate": line.get("vat_rate", 7),
            })
    for field in ("discount_total", "wht_amount", "rounding", "fx_rate"):
        if field in changes:
            doc.set(field, changes[field])

    ref = contracts.duplicate_key(doc.supplier or doc.supplier_name, doc.doc_no)
    _dedupe_check(doc.dedupe_key, ref, exclude=doc.name)
    doc.dedupe_ref = ref
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}


def set_status(name, target, reason=None):
    """流转：草稿→提交、草稿/提交→作废。非法流转一律拒绝。"""
    guard.require("purchase.write")
    doc = _get(name)
    if not contracts.can_transition(doc.status, target):
        frappe.throw(_(BAD_TRANSITION))
    if target == contracts.VOID and doc.paid_amount and _q2(doc.paid_amount) > 0:
        # 已收付款的单据不能直接作废：先把付款冲掉，否则账上会留下无主的钱。
        frappe.throw(_("Void the payments before voiding the document"))
    doc.status = target
    if target == contracts.VOID:
        doc.void_reason = reason
        doc.voided_at = now()
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}


def pay(name, *, amount, method=None, date=None, note=None):
    """登记一笔付款。多笔累加；超过净应付的付款被拒。"""
    guard.require("purchase.write")
    doc = _get(name)
    if doc.status == contracts.VOID:
        frappe.throw(_(VOIDED))
    if doc.status != contracts.SUBMITTED:
        frappe.throw(_("Confirm the document before recording a payment"))
    try:
        paid_now = contracts.amount(amount, field="amount", allow_zero=False)
    except ValueError:
        frappe.throw(_(INVALID))
    net = _q2(doc.net_payable)
    total = _q2(doc.paid_amount) + paid_now
    if total > net:
        frappe.throw(_("The payment is more than the net payable"))
    doc.append("payments", {
        "payment_date": date or nowdate(),
        "amount": paid_now,
        "method": method,
        "note": note,
    })
    doc.paid_amount = total
    doc.payment_status = contracts.payment_status(total, net)
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "paid_amount": doc.paid_amount, "payment_status": doc.payment_status}


def set_payment_status(name, status):
    """直接把单据标成未付/已付（旧线上有这条快捷路径，用于线下已结清的单据）。"""
    guard.require("purchase.write")
    doc = _get(name)
    if status not in (contracts.UNPAID, contracts.PARTIAL, contracts.PAID):
        frappe.throw(_(INVALID))
    if status == contracts.PAID:
        doc.paid_amount = doc.net_payable
    elif status == contracts.UNPAID:
        doc.paid_amount = 0
    doc.payment_status = status
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "paid_amount": doc.paid_amount, "payment_status": doc.payment_status}


def summary(*, date_from=None, date_to=None):
    """期间汇总。金额求和用 Decimal，避免浮点误差累积。"""
    guard.require("purchase.read")
    filters: dict = {"status": ["!=", contracts.VOID], **guard.scope_filters()}
    if date_from and date_to:
        filters["doc_date"] = ["between", [date_from, date_to]]
    rows = frappe.get_list(
        DOCTYPE, filters=filters,
        fields=["grand_total", "vat_amount", "net_payable", "paid_amount"],
        limit_page_length=0,
    )

    def field(row, key):
        return row.get(key) if isinstance(row, dict) else getattr(row, key, None)

    total = sum((_q2(field(r, "grand_total")) for r in rows), Decimal("0"))
    vat = sum((_q2(field(r, "vat_amount")) for r in rows), Decimal("0"))
    payable = sum((_q2(field(r, "net_payable")) for r in rows), Decimal("0"))
    paid = sum((_q2(field(r, "paid_amount")) for r in rows), Decimal("0"))
    return {
        "count": len(rows),
        "grand_total": str(total),
        "vat_amount": str(vat),
        "net_payable": str(payable),
        "paid_amount": str(paid),
        "outstanding": str(payable - paid),
    }
