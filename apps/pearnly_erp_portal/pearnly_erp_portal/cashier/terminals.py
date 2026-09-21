"""收银台。默认收银台保证收银员不带收银台时也有地方落单。"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard

DOCTYPE = "ERP POS Terminal"
NOT_FOUND = "Terminal not found"
NAME_REQUIRED = "A terminal needs a name"
FIELDS = ("name", "terminal_name", "warehouse", "is_default", "is_active")


def ensure_default() -> str:
    existing = frappe.db.get_value(DOCTYPE, {"is_default": 1}, "name")
    if existing:
        return existing
    any_terminal = frappe.db.get_value(DOCTYPE, {}, "name", order_by="creation asc")
    if any_terminal:
        frappe.db.set_value(DOCTYPE, any_terminal, "is_default", 1)
        return any_terminal
    doc = frappe.get_doc({"doctype": DOCTYPE, "terminal_name": "Counter 1",
                          "is_default": 1, "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def resolve(terminal=None) -> str:
    if terminal:
        if not frappe.db.exists(DOCTYPE, terminal):
            frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
        return terminal
    return ensure_default()


def list_terminals(*, include_inactive=False, limit=50) -> list[dict]:
    guard.require("cashier.read")
    filters = {} if include_inactive else {"is_active": 1}
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(FIELDS),
                           order_by="is_default desc, terminal_name asc",
                           limit_page_length=int(limit))


def create_terminal(*, terminal_name, warehouse=None, is_default=False) -> dict:
    guard.require("cashier.manage")
    name = (terminal_name or "").strip()
    if not name:
        frappe.throw(_(NAME_REQUIRED))
    doc = frappe.get_doc({"doctype": DOCTYPE, "terminal_name": name, "warehouse": warehouse,
                          "is_default": 1 if is_default else 0, "is_active": 1})
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def update_terminal(name, **changes) -> dict:
    guard.require("cashier.manage")
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    doc = frappe.get_doc(DOCTYPE, name)
    for field in ("terminal_name", "warehouse"):
        if field in changes:
            doc.set(field, changes[field])
    for field in ("is_default", "is_active"):
        if field in changes:
            doc.set(field, 1 if changes[field] in (True, 1, "1", "true") else 0)
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}
