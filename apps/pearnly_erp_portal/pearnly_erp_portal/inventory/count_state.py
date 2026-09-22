"""Stock-count locking, inventory revisions and native-document write boundaries."""
import hashlib
import json

import frappe
from frappe import _


def lock_task(name, *, active=True):
    rows = frappe.db.sql("select name, status from `tabERP Stock Count` where name=%s for update",
                         (name,), as_dict=True)
    if not rows:
        frappe.throw(_("Stock count not found"), frappe.DoesNotExistError)
    if active and rows[0].status != "Active":
        frappe.throw(_("This stock count is closed"))
    return rows[0]


def stock_token(product):
    # Count + maximum creation detect even an issue followed by an equal receipt.
    movement = frappe.db.sql(
        "select count(*), max(creation), max(name) from `tabERP Stock Movement` where product=%s",
        (product,))
    balances = frappe.db.sql(
        "select name, qty_on_hand, value, unit_cost, cost_known from `tabERP Stock Balance` where product=%s order by name",
        (product,))
    units = frappe.db.get_value("ERP Product", product, ["base_unit", "unit"])
    return hashlib.sha256(json.dumps([movement, balances, units], default=str).encode()).hexdigest()


def validate_document(doc):
    """Generic Desk/RPC writes must not bypass totals, locks or closed-task immutability."""
    if doc.doctype == "ERP Stock Count":
        if not doc.is_new():
            lock_task(doc.name)
        if doc.status != "Active":
            frappe.throw(_("Use the stock count workbench"))
        return
    lock_task(doc.get("count"))
    if not getattr(frappe.local, "stock_count_service_write", False):
        frappe.throw(_("Use the stock count workbench"))
    if not doc.is_new():
        previous = frappe.db.get_value(doc.doctype, doc.name, "count")
        if previous != doc.get("count"):
            frappe.throw(_("That row is not part of this stock count"))


def before_delete(doc):
    lock_task(doc.name if doc.doctype == "ERP Stock Count" else doc.get("count"))
    if not getattr(frappe.local, "stock_count_service_write", False):
        frappe.throw(_("Use the stock count workbench"))


def write_document(doc, method):
    """Service context is not serializable in a Desk/RPC document's flags."""
    previous = getattr(frappe.local, "stock_count_service_write", False)
    frappe.local.stock_count_service_write = True
    try:
        return getattr(doc, method)(ignore_permissions=True)
    finally:
        frappe.local.stock_count_service_write = previous
