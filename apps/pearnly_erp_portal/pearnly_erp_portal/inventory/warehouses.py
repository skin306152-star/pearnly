"""仓库。第一个仓库自动成为默认仓库 —— 没有仓库就没法入出库，不能留一个空目录。"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard

DOCTYPE = "ERP Warehouse"
NOT_FOUND = "Warehouse not found"
NAME_REQUIRED = "A warehouse needs a name"

FIELDS = ("name", "warehouse_name", "is_default", "is_active", "note")


def ensure_default() -> str:
    """默认仓库：没有就建一个（旧线上入库/出库不带仓库时落默认仓）。"""
    existing = frappe.db.get_value(DOCTYPE, {"is_default": 1}, "name")
    if existing:
        return existing
    any_warehouse = frappe.db.get_value(DOCTYPE, {}, "name", order_by="creation asc")
    if any_warehouse:
        frappe.db.set_value(DOCTYPE, any_warehouse, "is_default", 1)
        return any_warehouse
    doc = frappe.get_doc({"doctype": DOCTYPE, "warehouse_name": "Main", "is_default": 1,
                          "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def create_warehouse(*, warehouse_name, is_default=False, note=None) -> dict:
    guard.require("stock.manage")
    name = (warehouse_name or "").strip()
    if not name:
        frappe.throw(_(NAME_REQUIRED))
    if frappe.db.exists(DOCTYPE, {"warehouse_name": name}):
        frappe.throw(_("This warehouse already exists"))
    doc = frappe.get_doc({"doctype": DOCTYPE, "warehouse_name": name,
                          "is_default": 1 if is_default else 0, "is_active": 1, "note": note})
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def list_warehouses(*, include_inactive=False, limit=50) -> list[dict]:
    guard.require("stock.read")
    filters = {} if include_inactive else {"is_active": 1}
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(FIELDS),
                           order_by="is_default desc, warehouse_name asc",
                           limit_page_length=int(limit))


def update_warehouse(name, **changes) -> dict:
    guard.require("stock.manage")
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    doc = frappe.get_doc(DOCTYPE, name)
    if "warehouse_name" in changes:
        cleaned = (changes["warehouse_name"] or "").strip()
        if not cleaned:
            frappe.throw(_(NAME_REQUIRED))
        doc.warehouse_name = cleaned
    if "is_default" in changes:
        doc.is_default = 1 if changes["is_default"] in (True, 1, "1", "true") else 0
    if "is_active" in changes:
        doc.is_active = 1 if changes["is_active"] in (True, 1, "1", "true") else 0
    if "note" in changes:
        doc.note = changes["note"]
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}
