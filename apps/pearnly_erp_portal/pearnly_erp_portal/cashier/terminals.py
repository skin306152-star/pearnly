"""收银台。收银台挂仓库、仓库属于公司 —— 所以"默认收银台"是**每家公司**各一个
（一租户多公司：收银员只在自己公司的收银台上落单）。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import warehouses
from pearnly_erp_portal.masters import company as companies

DOCTYPE = "ERP POS Terminal"
NOT_FOUND = "Terminal not found"
NAME_REQUIRED = "A terminal needs a name"
FIELDS = ("name", "terminal_name", "warehouse", "is_default", "is_active")


def ensure_default(company=None) -> str:
    company = company or companies.active_company()
    names = warehouses.names_of(company)
    in_company = names or [""]
    existing = frappe.db.get_value(DOCTYPE, {"warehouse": ["in", in_company], "is_default": 1}, "name")
    if existing:
        return existing
    any_terminal = frappe.db.get_value(DOCTYPE, {"warehouse": ["in", in_company]}, "name",
                                       order_by="creation asc")
    if any_terminal:
        frappe.db.set_value(DOCTYPE, any_terminal, "is_default", 1)
        return any_terminal
    doc = frappe.get_doc({"doctype": DOCTYPE, "terminal_name": "Counter 1",
                          "warehouse": warehouses.ensure_default(company),
                          "is_default": 1, "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def resolve(terminal=None, company=None) -> str:
    if terminal:
        if not frappe.db.exists(DOCTYPE, terminal):
            frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
        return terminal
    return ensure_default(company)


def list_terminals(*, include_inactive=False, limit=50, company=None) -> list[dict]:
    guard.require("cashier.read")
    filters = dict(warehouses.scope_filter(company))
    if not include_inactive:
        filters["is_active"] = 1
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(FIELDS),
                           order_by="is_default desc, terminal_name asc",
                           limit_page_length=int(limit))


def create_terminal(*, terminal_name, warehouse=None, is_default=False) -> dict:
    guard.require("cashier.manage")
    name = (terminal_name or "").strip()
    if not name:
        frappe.throw(_(NAME_REQUIRED))
    doc = frappe.get_doc({"doctype": DOCTYPE, "terminal_name": name,
                          "warehouse": warehouses.resolve(warehouse),
                          "is_default": 1 if is_default else 0, "is_active": 1})
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def names_in_company(company=None) -> list:
    """某家公司名下的收银台（收银台挂仓库、仓库属于公司）。班次表上没有仓库列，
    所以班次的作用域靠这一份名单来收（`terminal in (...)`）。"""
    names = warehouses.names_of(company)
    if not names:
        return []
    return frappe.get_all(DOCTYPE, {"warehouse": ["in", names]}, pluck="name",
                          order_by="creation asc")


def scope_filter(company=None) -> dict:
    """列表用的"这家公司的收银台"过滤；一个都没有时给一个匹配不到任何行的哨兵。"""
    names = names_in_company(company)
    return {"terminal": ["in", names]} if names else {"terminal": "__missing__"}


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
