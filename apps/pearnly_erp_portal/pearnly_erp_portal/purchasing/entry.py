"""Submit a purchase, settlement and stock receipt in one transaction."""
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import getdate, now, nowdate

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import positions, stock, warehouses
from pearnly_erp_portal.numbering import service as numbering
from pearnly_erp_portal.products import purchase as purchase_items
from pearnly_erp_portal.purchasing import access, amounts, contracts, files, settings

KINDS = {"purchase_invoice", "purchase_order", "expense", "goods", "service"}
METHODS = {"cash", "transfer", "promptpay", "bank", "cheque", "other"}
HEAD_FIELDS = ("doc_no", "due_date", "currency", "fx_rate", "supplier_tax_id", "supplier_branch_type",
               "supplier_branch_no", "supplier_branch_name", "supplier_address", "requester",
               "requester_user", "notes", "source", "external_ref", "image_sha256")
LINE_FIELDS = ("item_type", "product", "product_ref", "description", "code", "barcode", "qty", "unit",
               "unit_price", "discount", "vat_rate", "wht_rate", "category", "subcategory", "batch_no",
               "expiry_date", "warehouse", "location")


def flag(value, default=False):
    return default if value is None else value in (True, 1, "1", "true", "True")


def _supplier(payload):
    from pearnly_erp_portal.masters.selection import match_party

    key = payload.get("supplier")
    name = str(payload.get("supplier_name") or "").strip()
    if not key:
        key = match_party("supplier", name, tax_id=payload.get("supplier_tax_id"),
                          branch_type=payload.get("supplier_branch_type"), branch_no=payload.get("supplier_branch_no"))
    if key:
        if not frappe.db.exists("ERP Supplier", key):
            frappe.throw(_("Supplier not found. Please select the supplier again."))
        doc = frappe.get_doc("ERP Supplier", key)
        if not doc.is_active:
            frappe.throw(_("Supplier is disabled"))
        name = name or doc.supplier_name
        for target, field in (("supplier_tax_id", "tax_id"), ("supplier_branch_type", "branch_type"),
                              ("supplier_branch_no", "branch_no"), ("supplier_branch_name", "branch_name"),
                              ("supplier_address", "address")):
            if target not in payload:
                payload[target] = doc.get(field)
    if not name:
        frappe.throw(_("Supplier is required"))
    branch = payload.get("supplier_branch_type") or "none"
    if branch == "head":
        branch = "head_office"
    if branch not in ("head_office", "branch", "none"):
        frappe.throw(_("Invalid value"))
    if branch == "branch" and not str(payload.get("supplier_branch_no") or "").strip():
        frappe.throw(_("Branch No is required"))
    payload["supplier_branch_type"] = branch
    if not key:
        doc = frappe.get_doc({"doctype": "ERP Supplier", "supplier_name": name,
            "tax_id": payload.get("supplier_tax_id"), "branch_type": branch,
            "branch_no": payload.get("supplier_branch_no"), "branch_name": payload.get("supplier_branch_name"),
            "address": payload.get("supplier_address"), "is_active": 1}).insert(ignore_permissions=True)
        key = doc.name
    return key, name


def _lines(payload):
    rows = payload.get("lines")
    if not isinstance(rows, list) or not rows:
        frappe.throw(_("Items are required"))
    result = []
    for raw in rows:
        row = {key: raw[key] for key in LINE_FIELDS if key in raw}
        row["description"] = str(row.get("description") or "").strip()
        row["item_type"] = row.get("item_type") or "goods"
        if not row["description"] or row["item_type"] not in ("goods", "service"):
            frappe.throw(_("Item Name is required"))
        for key in ("category", "subcategory"):
            if row.get(key):
                category = frappe.get_doc("ERP Purchase Category", row[key])
                category.check_permission("read")
                if key == "subcategory" and category.parent_category != row.get("category"):
                    frappe.throw(_("Invalid value"))
                if key == "category" and category.parent_category:
                    frappe.throw(_("Invalid value"))
        result.append(row)
    return result


def preview(payload):
    guard.require("purchase.read")
    return _calculate(payload)


def _calculate(payload):
    override = None
    if flag(payload.get("amount_override")):
        override = {field: payload.get("override_" + key) for field, key in
                    (("subtotal", "subtotal"), ("discount_total", "discount"),
                     ("vat_amount", "vat"), ("grand_total", "grand"))}
    try:
        return amounts.calculate(payload.get("lines"), has_vat=flag(payload.get("has_vat"), True),
            price_mode=payload.get("price_mode") or "exclusive", discount_total=payload.get("discount_total", 0),
            rounding=payload.get("rounding", 0), amount_override=override)
    except (ValueError, KeyError, ArithmeticError) as exc:
        frappe.throw(_(str(exc)))


def post(payload, *, request_id, name=None, amended_from=None, finalize=None):
    guard.require("purchase.write")
    if not isinstance(payload, dict) or not request_id or len(str(request_id)) > 140:
        frappe.throw(_("Invalid value"))
    payload = dict(payload)
    fingerprint = hashlib.sha256(json.dumps([payload, name, amended_from], sort_keys=True,
                                           ensure_ascii=False, default=str).encode()).hexdigest()
    # Serialize auto-item matching until commit. Unique request_id remains the durable replay guard.
    with frappe.cache.lock("erp-purchase-post", timeout=120, blocking_timeout=20):
        try:
            existing = frappe.db.get_value("Purchase Document", {"request_id": request_id}, "name")
            if existing:
                doc = access.get_document(existing)
                if doc.request_hash != fingerprint:
                    frappe.throw(_("Request ID already exists"))
                return {"name": doc.name, "status": doc.status, "duplicate": True}
            result = _post(payload, request_id, fingerprint, name, amended_from)
            if finalize:
                finalize(result)
            frappe.db.commit()
            return result
        except Exception:
            frappe.db.rollback()
            raise


def _post(payload, request_id, fingerprint, name, amended_from):
    kind = payload.get("doc_kind") or "purchase_invoice"
    if kind not in KINDS:
        frappe.throw(_("Invalid value"))
    day = str(getdate(payload.get("doc_date") or nowdate()))
    payload["lines"] = _lines(payload)
    totals = _calculate(payload)
    if payload.get("currency", "THB") not in ("THB", "USD", "EUR", "CNY", "JPY", "SGD"):
        frappe.throw(_("Invalid Currency"))
    if payload.get("currency", "THB") != settings.read_settings()["base_currency"] and not payload.get("fx_rate"):
        frappe.throw(_("Exchange Rate is required"))
    rate = amounts.decimal(payload.get("fx_rate", 1))
    if rate <= 0:
        frappe.throw(_("Exchange Rate must be greater than zero"))
    has_vat = flag(payload.get("has_vat"), True)
    if has_vat and not str(payload.get("doc_no") or "").strip():
        frappe.throw(_("Supplier Invoice No is required"))
    doc = access.get_document(name, write=True, lock=True) if name else frappe.new_doc("Purchase Document")
    if name and doc.status != "draft":
        frappe.throw(_("Document has already been submitted"))
    original = access.get_document(amended_from, write=True, lock=True) if amended_from else None
    if original and original.status != "submitted":
        frappe.throw(_("Document has already been cancelled"))
    if payload.get("image_sha256"):
        duplicates = frappe.get_all("Purchase Document", filters={"image_sha256": payload["image_sha256"],
            "status": ["!=", "void"]}, pluck="name")
        if any(key not in (name, amended_from) for key in duplicates):
            frappe.throw(_("Document already exists"))
    supplier, supplier_name = _supplier(payload)
    reference = contracts.duplicate_key(supplier, payload.get("doc_no"))
    if reference and settings.read_settings()["dedupe_block"]:
        duplicates = frappe.get_all("Purchase Document", filters={"dedupe_ref": reference,
                    "status": ["!=", "void"]}, pluck="name")
        if any(key not in (name, amended_from) for key in duplicates):
            frappe.throw(_("Supplier Invoice No already exists"))
    rows = []
    for row in totals["lines"]:
        row = purchase_items.resolve(dict(row), create=flag(payload.get("auto_create_product"), True),
                                     currency_rate=rate)
        if flag(payload.get("update_stock"), True) and row["item_type"] == "goods" and not row.get("product"):
            frappe.throw(_("Please select an Item"))
        if row.get("warehouse"):
            row["warehouse"] = warehouses.required(row["warehouse"])
        rows.append(positions.prepare(row))
    if original:
        _cancel(original, _("Amended"))
    doc.update({key: payload.get(key) for key in HEAD_FIELDS})
    from pearnly_erp_portal.masters import company as companies

    doc.company = companies.document_company(doc.get("company") if name else payload.get("company"))
    doc.update({"doc_kind": kind, "doc_date": day, "supplier": supplier, "supplier_name": supplier_name,
        "currency": payload.get("currency") or "THB", "fx_rate": rate,
        "system_no": doc.get("system_no") or numbering.allocate(direction="purchase", on=day,
              exists=lambda value: bool(frappe.db.exists("Purchase Document", {"system_no": value}))),
        "calculation_version": 2, "status": "submitted", "has_vat": int(has_vat),
        "request_id": request_id, "request_hash": fingerprint, "dedupe_ref": reference,
        "corrected_from": amended_from, "price_mode": payload.get("price_mode") or "exclusive",
        "update_stock": int(flag(payload.get("update_stock"), True)),
        "auto_create_product": int(flag(payload.get("auto_create_product"), True)),
        "is_paid": int(flag(payload.get("is_paid"), True)),
        "payment_method": payload.get("payment_method") or "cash",
        "amount_override": int(flag(payload.get("amount_override"))),
        "discount_total": totals["discount_total"], "rounding": totals["rounding"],
        "summary": ", ".join(row["description"] for row in rows), "lines": rows})
    for key in ("subtotal", "discount", "vat", "grand"):
        doc.set("override_" + key, payload.get("override_" + key, 0))
    if doc.payment_method not in METHODS:
        frappe.throw(_("Invalid value"))
    doc.paid_amount = totals["net_payable"] if doc.is_paid else 0
    doc.payments = []
    if doc.is_paid and doc.paid_amount:
        doc.append("payments", {"payment_date": day, "amount": doc.paid_amount, "method": doc.payment_method})
    doc.flags.purchase_action = True
    doc.save()
    if doc.update_stock:
        goods = [{"product": row.product, "qty": row.qty, "unit": row.unit,
                  "unit_cost": amounts.decimal(row.line_total) / amounts.decimal(row.qty) * rate,
                  **positions.saved(row)} for row in doc.lines if row.item_type == "goods" and row.product]
        if goods:
            # 入库到**这张采购单上明确的仓**：明细行给了仓就用它（采购表单有那一格），
            # 行上也没写时落**这家公司卖货的那个仓**（用户 2026-09-24："进货就进卖货那个仓"），
            # 没有收银台才退回"这家公司只有一个仓"，多个仓直接报错让你去选。
            stock.apply_lines(warehouse=warehouses.receiving(goods[0].get("warehouse")),
                              movement_type="purchase_in", lines=goods,
                posting_date=day, ref_type="purchase", ref_id=doc.name, doc_no=doc.system_no,
                client_uuid="purchase-in:" + doc.name, internal=True)
            doc.stock_posted_at = now()
    files.bind(doc, payload.get("attachments"))
    if original:
        files.copy_evidence(original, doc)
    digest = files.evidence_hash(doc)
    if digest:
        duplicates = frappe.get_all("Purchase Document", filters={"image_sha256": digest,
            "status": ["!=", "void"]}, pluck="name")
        if any(key not in (doc.name, amended_from) for key in duplicates):
            frappe.throw(_("Document already exists"))
        doc.image_sha256 = digest
    doc.flags.purchase_action = True
    doc.save()
    return {"name": doc.name, "status": doc.status, "system_no": doc.system_no}


def _reverse_payments(doc):
    amount = amounts.money(doc.paid_amount)
    if amount <= 0:
        return
    posted = sum((amounts.money(row.amount) for row in doc.payments), amounts.money(0))
    if posted != amount:
        doc.append("payments", {"payment_date": str(doc.doc_date), "amount": amount - posted,
                   "method": doc.payment_method or "other", "reference": _("Opening Balance")})
    doc.append("payments", {"payment_date": nowdate(), "amount": -amount,
               "method": doc.payment_method or "other", "is_reversal": 1,
               "reference": doc.system_no or doc.name})
    doc.paid_amount = 0
    doc.is_paid = 0


def _cancel(doc, reason=None):
    if doc.status == "void":
        return
    _reverse_payments(doc)
    if doc.stock_posted_at:
        stock.reverse_movements(ref_type="purchase", ref_id=doc.name,
                                client_uuid="purchase-void:" + doc.name, reason=reason)
    doc.status, doc.void_reason, doc.voided_at = "void", reason, now()
    doc.flags.purchase_action = True
    doc.save()


def cancel(name, reason=None):
    doc = access.get_document(name, write=True, lock=True)
    _cancel(doc, reason)
    return {"name": doc.name, "status": doc.status}


def payment(name, *, amount=None, method="cash", date=None, note=None, paid=None, request_id=None):
    doc = access.get_document(name, write=True, lock=True)
    if doc.status != "submitted":
        frappe.throw(_("Document must be submitted"))
    fingerprint = hashlib.sha256(json.dumps([amount, method, date, note, paid], default=str).encode()).hexdigest()
    if request_id:
        previous = next((row for row in doc.payments if row.request_id == request_id), None)
        if previous:
            if previous.request_hash != fingerprint:
                frappe.throw(_("Request ID already exists"))
            return {"name": doc.name, "payment_status": doc.payment_status, "duplicate": True}
    if paid is not None and flag(paid) and amounts.money(doc.paid_amount) == amounts.money(doc.net_payable):
        return {"name": doc.name, "payment_status": doc.payment_status, "duplicate": True}
    previous_count = len(doc.payments)
    if paid is not None and not flag(paid):
        _reverse_payments(doc)
    else:
        remaining = amounts.money(doc.net_payable) - amounts.money(doc.paid_amount)
        value = remaining if paid is not None else amounts.money(amounts.decimal(amount))
        if value <= 0 or value > remaining or method not in METHODS:
            frappe.throw(_("Invalid payment amount"))
        doc.append("payments", {"payment_date": str(getdate(date or nowdate())), "amount": value,
                                "method": method, "note": note})
        doc.paid_amount = amounts.money(doc.paid_amount) + value
        doc.is_paid = int(doc.paid_amount == amounts.money(doc.net_payable))
    if request_id and len(doc.payments) > previous_count:
        doc.payments[-1].request_id, doc.payments[-1].request_hash = request_id, fingerprint
    doc.flags.purchase_action = True
    doc.save()
    return {"name": doc.name, "payment_status": doc.payment_status}


def delete_drafts(names):
    docs = [access.get_document(name, write=True, lock=True) for name in sorted(set(names))]
    if any(doc.status != "draft" for doc in docs):
        frappe.throw(_("Only Draft documents can be deleted"))
    for doc in docs:
        frappe.delete_doc("Purchase Document", doc.name)
    return {"deleted": len(docs)}
