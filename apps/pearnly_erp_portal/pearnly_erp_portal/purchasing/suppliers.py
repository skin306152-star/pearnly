"""供应商名录。停用而不是删除；历史单据继续指向它。"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import tax_ids

DOCTYPE = "ERP Supplier"

NAME_REQUIRED = "A supplier needs a name"
NOT_FOUND = "Supplier not found"
INVALID = "Invalid value"

FIELDS = ("name", "supplier_name", "tax_id", "branch_type", "branch_no",
          "address", "phone", "note", "is_active", "express_code")


def _clean(value, limit=300):
    if value in (None, ""):
        return None
    value = str(value).strip()
    if not value:
        return None
    if len(value) > limit:
        frappe.throw(_(INVALID))
    return value


def create_supplier(*, supplier_name, tax_id=None, branch_type="head", branch_no=None,
                    address=None, phone=None, note=None):
    guard.require("suppliers.write")
    supplier_name = _clean(supplier_name)
    if not supplier_name:
        frappe.throw(_(NAME_REQUIRED))
    branch_type = "branch" if branch_type == "branch" else "head"
    tax_ids.require_valid(_clean(tax_id, 30), format_message="supplier.lookup.invalid_format")
    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "supplier_name": supplier_name,
        "tax_id": _clean(tax_id, 30),
        "branch_type": branch_type,
        "branch_no": _clean(branch_no, 20) if branch_type == "branch" else None,
        "address": _clean(address, 2000),
        "phone": _clean(phone, 50),
        "note": note,
        "is_active": 1,
    })
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def list_suppliers(*, q=None, include_inactive=False, limit=50) -> list[dict]:
    guard.require("suppliers.read")
    filters: dict = {}
    if not include_inactive:
        filters["is_active"] = 1
    if q:
        filters["supplier_name"] = ["like", f"%{q}%"]
    return frappe.get_list(
        DOCTYPE, filters=filters, fields=list(FIELDS), order_by="supplier_name asc",
        limit_page_length=int(limit),
    )


def update_supplier(name, **changes):
    guard.require("suppliers.write")
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    from pearnly_erp_portal.masters import access as master_access

    doc = master_access.require_master(frappe.get_doc(DOCTYPE, name))
    if "supplier_name" in changes:
        cleaned = _clean(changes["supplier_name"])
        if not cleaned:
            frappe.throw(_(NAME_REQUIRED))
        doc.supplier_name = cleaned
    for field, limit in (("tax_id", 30), ("branch_no", 20), ("address", 2000), ("phone", 50)):
        if field in changes:
            if field == "tax_id":
                tax_ids.require_valid(_clean(changes[field], limit), previous=doc.tax_id,
                                      format_message="supplier.lookup.invalid_format")
            doc.set(field, _clean(changes[field], limit))
    if "branch_type" in changes:
        doc.branch_type = "branch" if changes["branch_type"] == "branch" else "head"
    if "is_active" in changes:
        doc.is_active = 1 if changes["is_active"] in (True, 1, "1", "true") else 0
    if "note" in changes:
        doc.note = changes["note"]
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}


def set_supplier_active(name, active):
    return update_supplier(name, is_active=active)
