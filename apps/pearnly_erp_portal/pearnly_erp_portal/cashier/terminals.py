"""收银台：**属于公司、卖这家公司某个仓库里的货**（官方 POS 配置也是这两样：
`company` + `warehouse`）。

所以一句话：收银台 = 某家公司的一台柜台，货从它指定的仓库出。班次、小票、库存因此都能
一路追到"哪家公司、哪个仓"。每家公司的收银台各自有一个默认（`is_default`）。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import warehouses
from pearnly_erp_portal.masters import company as companies

DOCTYPE = "ERP POS Terminal"
NOT_FOUND = "Terminal not found"
NAME_REQUIRED = "A terminal needs a name"
FIELDS = ("name", "terminal_name", "company", "warehouse", "is_default", "is_active")


def ensure_default(company=None) -> str:
    company = company or companies.active_company()
    existing = frappe.db.get_value(DOCTYPE, {"company": company, "is_default": 1}, "name")
    if existing:
        return existing
    any_terminal = frappe.db.get_value(DOCTYPE, {"company": company}, "name",
                                       order_by="creation asc")
    if any_terminal:
        frappe.db.set_value(DOCTYPE, any_terminal, "is_default", 1)
        return any_terminal
    doc = frappe.get_doc({"doctype": DOCTYPE, "terminal_name": "Counter 1",
                          "company": company,
                          "warehouse": warehouses.ensure_default(company),
                          "is_default": 1, "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def resolve(terminal=None, company=None) -> str:
    if terminal:
        row = frappe.db.get_value(DOCTYPE, terminal, ["company", "is_active"], as_dict=True)
        if not row or not row.is_active:
            frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
        # 别家公司的收银台：不能拿来落单（货会记到别人账上）。
        if row.company != (company or companies.active_company()):
            frappe.throw(_("Not permitted"), frappe.PermissionError)
        return terminal
    return ensure_default(company)


def list_terminals(*, include_inactive=False, limit=50, company=None) -> list[dict]:
    guard.require("cashier.read")
    filters = {"company": companies.document_company(company)}
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
    company = companies.active_company()
    doc = frappe.get_doc({"doctype": DOCTYPE, "terminal_name": name,
                          "company": company,
                          "warehouse": warehouses.required(warehouse, company=company),
                          "is_default": 1 if is_default else 0, "is_active": 1})
    doc.insert()
    if doc.is_default:
        for other in frappe.get_all(DOCTYPE, filters={"company": company, "is_default": 1,
                                                      "name": ["!=", doc.name]}, pluck="name"):
            frappe.db.set_value(DOCTYPE, other, "is_default", 0)
    frappe.db.commit()
    return {"name": doc.name}


def names_in_company(company=None) -> list:
    """某家公司的收银台。班次表上没有公司/仓库列，所以班次的作用域靠这一份名单来收
    （`terminal in (...)`）。"""
    return frappe.get_all(DOCTYPE, {"company": companies.document_company(company)},
                          pluck="name", order_by="creation asc")


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
