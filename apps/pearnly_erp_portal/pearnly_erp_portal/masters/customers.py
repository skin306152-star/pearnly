"""客户（销售买方）。停用而不是删除；历史单据继续指向它。"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard, listing
from pearnly_erp_portal.masters import contracts, tax_ids

DOCTYPE = "ERP Customer"
NAME_REQUIRED = "A customer needs a name"
NOT_FOUND = "Customer not found"
INVALID = "Invalid value"

LIST_FIELDS = ("name", "customer_name", "short_name", "party_type", "tax_id", "branch_type",
               "branch_no", "address", "contact_person", "contact_phone", "contact_email",
               "promptpay_id", "color", "is_active", "express_code")


def _get(name):
    from pearnly_erp_portal.masters import access as master_access

    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return master_access.require_master(frappe.get_doc(DOCTYPE, name))


def create_customer(**fields) -> dict:
    guard.require("customers.manage")
    try:
        clean = contracts.normalize_buyer(fields)
    except ValueError:
        frappe.throw(_(INVALID))
    if not clean["customer_name"]:
        frappe.throw(_(NAME_REQUIRED))
    tax_ids.require_valid(clean["tax_id"], format_message="client.lookup.invalid_format")
    doc = frappe.get_doc({"doctype": DOCTYPE, **clean, "is_active": 1,
                          "note": contracts.clean(fields.get("note"), contracts.MAX_NOTE)})
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


# 列表按页取（接了 Express 的公司客户动辄几万，一次取全会把页面卡死，10-25 testerp 7.4 万）
SEARCH_FIELDS = ("customer_name", "short_name", "tax_id", "express_code", "contact_phone")
SORT_FIELDS = {field: field for field in ("customer_name", "party_type", "tax_id", "contact_phone", "address", "is_active")}


def _filters(q, include_inactive):
    return ([] if include_inactive else [["is_active", "=", 1]]), listing.search_or(q, SEARCH_FIELDS)


def list_customers(*, q=None, include_inactive=False, limit=50, start=0, sort_by=None, sort_order=None) -> list[dict]:
    guard.require("customers.read")
    filters, or_filters = _filters(q, include_inactive)
    return frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters, fields=list(LIST_FIELDS),
                           order_by=listing.order_by(sort_by, sort_order, SORT_FIELDS, "customer_name asc"),
                           limit_start=int(start or 0), limit_page_length=int(limit))


def count_customers(*, q=None, include_inactive=False) -> int:
    """列表总数（页脚与「加载更多」用）：跟着权限（按公司隔离的权限钩子）走，同商品列表的计数。"""
    guard.require("customers.read")
    filters, or_filters = _filters(q, include_inactive)
    return len(frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters, pluck="name", limit_page_length=0))


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
    tax_ids.require_valid(clean["tax_id"], previous=doc.get("tax_id"), format_message="client.lookup.invalid_format")
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

