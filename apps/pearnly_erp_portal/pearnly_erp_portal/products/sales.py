"""Resolve/create an item for a sale's stock-out line. No commit.

销售明细勾了「出库」、认不到物料就按票面**自动新建**，再按负库存出库（LINE 收单用户 2026-09-25 定；11-06 按行选物料与库存
评估 12 节 #2 起网页同一口径，`sales.entry._resolve_item`）。能力判据是销售录入岗位。
"""
from decimal import Decimal

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.products import contracts, service
from pearnly_erp_portal.masters import access as master_access


def resolve(line, *, create=True, company=None, created=None):
    """``line["new_item"]``：人在 LINE 里选了「新建」——不按品名认。``created``：随单新建的物料名记进这个列表（回填来由）。"""
    guard.require("sales.write")
    fresh = bool(line.pop("new_item", None))
    name = line.get("product")
    auto = not name  # 按品名 / 编码自动认的：单位跟商品走（`selection.own_unit`）
    if not name and not fresh:
        from pearnly_erp_portal.masters.selection import match_product

        name, found = match_product(line["description"], company)  # 这家公司该用的商品里认（别家 Express 的不认）
        if found > 1:
            frappe.throw(_("Please select an Item"))
    if not name and (create or fresh):
        item = frappe.get_doc({"doctype": "ERP Product",
            "product_name": contracts.require_name(line["description"]),
            "code": service._allocate_code(), "unit": line.get("unit"), "base_unit": line.get("unit"),
            "unit_price": Decimal(str(line.get("unit_price") or 0)), "price_set": 1,
            "default_cost": 0, "cost_set": 0, "vat_applicable": 1, "is_active": 1, "origin": "sales"})
        # Deliberately limited to the sale's own item; ERP Sales keeps read-only access
        # to the general item master.
        item.insert(ignore_permissions=True)
        name = item.name
        if created is not None:
            created.append(name)
    if name:
        item = frappe.get_doc("ERP Product", name)
        if not master_access.usable(getattr(item, master_access.OWNER, None), company):
            frappe.throw(_("Product not found"))  # 别家公司 Express 的商品不能用在这家的单上（10-24 验收）
        if not item.is_active:
            frappe.throw(_("Item is disabled"))
        line.update(product=name, unit=line.get("unit") or item.base_unit or item.unit)
        if auto:
            from pearnly_erp_portal.masters.selection import own_unit

            line["unit"] = own_unit(item, line["unit"])
    return line
