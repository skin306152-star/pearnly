"""Resolve/create an item for a sale recorded from LINE. No commit.

网页端录销售不替物料岗位建档（见 `sales.entry._resolve_item`）；LINE 收单是用户
2026-09-25 另定的口径：销售明细匹配不到商品就按票面**自动新建**，再按负库存出库。
所以这条只给 LINE 收单用，能力判据是销售录入岗位。
"""
from decimal import Decimal

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.products import contracts, service


def resolve(line, *, create=True, company=None):
    guard.require("sales.write")
    name = line.get("product")
    if not name:
        from pearnly_erp_portal.masters.selection import match_product

        name, found = match_product(line["description"], company)  # 这家公司该用的商品里认（别家 Express 的不认）
        if found > 1:
            frappe.throw(_("Please select an Item"))
    if not name and create:
        item = frappe.get_doc({"doctype": "ERP Product",
            "product_name": contracts.require_name(line["description"]),
            "code": service._allocate_code(), "unit": line.get("unit"), "base_unit": line.get("unit"),
            "unit_price": Decimal(str(line.get("unit_price") or 0)), "price_set": 1,
            "default_cost": 0, "cost_set": 0, "vat_applicable": 1, "is_active": 1})
        # Deliberately limited to the sale's own item; ERP Sales keeps read-only access
        # to the general item master.
        item.insert(ignore_permissions=True)
        name = item.name
    if name:
        item = frappe.get_doc("ERP Product", name)
        if not item.is_active:
            frappe.throw(_("Item is disabled"))
        line.update(product=name, unit=line.get("unit") or item.base_unit or item.unit)
    return line
