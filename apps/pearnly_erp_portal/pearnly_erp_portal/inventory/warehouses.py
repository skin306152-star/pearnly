"""仓库。**仓库属于公司**（官方也是这么分的：`Warehouse.company`），库存因此天然按公司分开 ——
`ERP Stock Balance` / `ERP Stock Movement` 都按仓库记账，不用再加一列。

每家公司的第一个仓库自动成为**这家公司**的默认仓库；没有仓库就没法入出库，所以不留空目录。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company as companies

DOCTYPE = "ERP Warehouse"
NOT_FOUND = "Warehouse not found"
NAME_REQUIRED = "A warehouse needs a name"

FIELDS = ("name", "warehouse_name", "company", "is_default", "is_active", "note")


def resolve(value=None, *, create=False, company=None):
    """按名字取一个**已存在**的仓库（官方原生口径：仓库是主数据，不靠自由填写凭空长出来）。

    只有显式传 `create=True` 的调用方才允许按名字新建（开通/实验室造数据那些路径）。
    留空 = 这家公司的默认仓库。
    """
    company = company or companies.active_company()
    value = str(value or "").strip()
    if not value:
        return ensure_default(company) if create else None
    if len(value) > 140:
        frappe.throw(_("Invalid value"))
    name = frappe.db.exists(DOCTYPE, value) or frappe.db.get_value(
        DOCTYPE, {"warehouse_name": value, "company": company}, "name")
    if name:
        row = frappe.db.get_value(DOCTYPE, name, ["is_active", "company"], as_dict=True)
        if not row or not row.is_active or row.company != company:
            # 别家公司的仓库、或已停用：只读的调用方当"没这个仓库"处理（页面给空结果），
            # 要建单的调用方按业务规则报错。
            if not create:
                return None
            frappe.throw(_(NOT_FOUND))
        return name
    if not create:
        return None
    # Serialize catalog creation, then re-read after a competing transaction commits.
    frappe.db.sql("select name from `tabDocType` where name=%s for update", (DOCTYPE,))
    name = frappe.db.get_value(DOCTYPE, {"warehouse_name": value, "company": company}, "name")
    if name:
        return name
    doc = frappe.get_doc({"doctype": DOCTYPE, "warehouse_name": value, "company": company,
                          "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def ensure_default(company=None) -> str:
    """某家公司的默认仓库：没有就建一个（入库/出库不带仓库时落自己公司的默认仓）。"""
    company = company or companies.active_company()
    existing = frappe.db.get_value(DOCTYPE, {"company": company, "is_default": 1}, "name")
    if existing:
        return existing
    any_warehouse = frappe.db.get_value(DOCTYPE, {"company": company}, "name",
                                        order_by="creation asc")
    if any_warehouse:
        frappe.db.set_value(DOCTYPE, any_warehouse, "is_default", 1)
        return any_warehouse
    doc = frappe.get_doc({"doctype": DOCTYPE, "warehouse_name": "Main", "company": company,
                          "is_default": 1, "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def create_warehouse(*, warehouse_name, is_default=False, note=None, company=None) -> dict:
    guard.require("stock.manage")
    company = companies.document_company(company)
    name = (warehouse_name or "").strip()
    if not name:
        frappe.throw(_(NAME_REQUIRED))
    if frappe.db.exists(DOCTYPE, {"warehouse_name": name, "company": company}):
        frappe.throw(_("This warehouse already exists"))
    doc = frappe.get_doc({"doctype": DOCTYPE, "warehouse_name": name, "company": company,
                          "is_default": 1 if is_default else 0, "is_active": 1, "note": note})
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def list_warehouses(*, include_inactive=False, limit=50, company=None) -> list[dict]:
    guard.require("stock.read")
    filters = {"company": companies.document_company(company)}
    if not include_inactive:
        filters["is_active"] = 1
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(FIELDS),
                           order_by="is_default desc, warehouse_name asc",
                           limit_page_length=int(limit))


def names_of(company=None, *, include_inactive=True) -> list:
    """某家公司名下的仓库名。库存/出入库/盘点都按仓库记账，所以"看这家公司的库存"
    就是"看这些仓库"；单据上没有 company 列的那些表靠它过滤（`warehouse in (...)`）。"""
    company = company or companies.active_company()
    filters = {"company": company}
    if not include_inactive:
        filters["is_active"] = 1
    return frappe.get_all(DOCTYPE, filters=filters, pluck="name", order_by="creation asc")


def scope_filter(company=None) -> dict:
    """列表用的"这家公司的仓库"过滤；一家仓库都没有时给一个匹配不到任何行的哨兵。"""
    names = names_of(company)
    return {"warehouse": ["in", names]} if names else {"warehouse": "__missing__"}


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
