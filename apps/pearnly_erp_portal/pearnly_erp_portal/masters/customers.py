"""客户（销售买方）。停用而不是删除；历史单据继续指向它。"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import contracts

DOCTYPE = "ERP Customer"
NAME_REQUIRED = "A customer needs a name"
NOT_FOUND = "Customer not found"
INVALID = "Invalid value"

LIST_FIELDS = ("name", "customer_name", "short_name", "party_type", "tax_id", "branch_type",
               "branch_no", "address", "contact_person", "contact_phone", "contact_email",
               "promptpay_id", "color", "is_active")


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


def create_customer(**fields) -> dict:
    guard.require("customers.manage")
    try:
        clean = contracts.normalize_buyer(fields)
    except ValueError:
        frappe.throw(_(INVALID))
    if not clean["customer_name"]:
        frappe.throw(_(NAME_REQUIRED))
    doc = frappe.get_doc({"doctype": DOCTYPE, **clean, "is_active": 1,
                          "note": contracts.clean(fields.get("note"), contracts.MAX_NOTE)})
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def list_customers(*, q=None, include_inactive=False, limit=50) -> list[dict]:
    guard.require("customers.read")
    filters: dict = {}
    if not include_inactive:
        filters["is_active"] = 1
    if q:
        filters["customer_name"] = ["like", f"%{q}%"]
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="customer_name asc", limit_page_length=int(limit))


def get_customer(name) -> dict:
    guard.require("customers.read")
    doc = _get(name)
    return {field: doc.get(field) for field in LIST_FIELDS}


def update_customer(name, **fields) -> dict:
    guard.require("customers.manage")
    doc = _get(name)
    merged = {field: doc.get(field) for field in
              ("customer_name", "short_name", "party_type", "tax_id", "branch_type", "branch_no",
               "address", "contact_person", "contact_phone", "contact_email", "promptpay_id",
               "color")}
    merged.update({k: v for k, v in fields.items() if k in merged})
    try:
        clean = contracts.normalize_buyer(merged)
    except ValueError:
        frappe.throw(_(INVALID))
    if not clean["customer_name"]:
        frappe.throw(_(NAME_REQUIRED))
    for field, value in clean.items():
        doc.set(field, value)
    if "note" in fields:
        doc.note = contracts.clean(fields["note"], contracts.MAX_NOTE)
    if "is_active" in fields:
        doc.is_active = 1 if fields["is_active"] in (True, 1, "1", "true") else 0
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}


def set_active(name, active) -> dict:
    return update_customer(name, is_active=active)


def required_for_invoice(name) -> dict:
    """开票前检查买方还缺什么（供开票侧调用）。"""
    guard.require("customers.read")
    doc = _get(name)
    missing = contracts.missing_for_tax_invoice({field: doc.get(field) for field in
                                                 ("customer_name", "address", "party_type",
                                                  "tax_id", "branch_type", "branch_no")})
    return {"name": doc.name, "missing": missing}
