"""Resolve free-text storage places and snapshot them on business lines.

Callers authorize their own business action. Missing location means product default;
an explicit empty location means the historical, unnamed position. Saved lines never
follow later edits to product defaults.
"""

import frappe
from frappe import _

from pearnly_erp_portal.inventory import warehouses


def clean_location(value):
    value = str(value or "").strip()
    if len(value) > 140:
        frappe.throw(_("Invalid value"))
    return value


def prepare(line, warehouse=None):
    result = dict(line)
    if not result.get("product"):
        return result
    product = frappe.db.get_value("ERP Product", result["product"],
                                  ["is_active", "default_location"],
                                  as_dict=True)
    if not product or not product.is_active:
        frappe.throw(_("Product not found"))
    # 仓库必须是这家公司真实存在的仓（仓库属于公司）。
    # **商品上那个"默认仓"不再参与**（用户 2026-09-24："进货就进卖货那个仓"）：它会让
    # 进货落在一个仓、收银从另一个仓卖，两边数量与金额永远对不上（实测：`水` 在卖货仓
    # 一路负库存、成本立不起来，收发存金额列一直是空的）。
    # 行上没写、单据上也没写时，落"这家公司卖货的那个仓"（`warehouses.receiving`，
    # 没有收银台才退回"唯一的仓 / 报错"）。
    wanted = result.get("warehouse") or warehouse
    resolved = warehouses.resolve(wanted, create=False) if wanted else warehouses.receiving()
    if not resolved:
        frappe.throw(_("Warehouse not found"))
    result["warehouse"] = resolved
    result["location"] = clean_location(
        result["location"] if "location" in result else product.default_location)
    return result


def saved(line, warehouse=None):
    """Read an existing line, including legacy blank positions, without product defaults."""
    return {"warehouse": line.get("warehouse") or warehouse or warehouses.required(),
            "location": clean_location(line.get("location"))}


def public(line, warehouse=None):
    place = saved(line, warehouse)
    return {**place, "product_name": frappe.db.get_value("ERP Product", line.get("product"), "product_name")
            if line.get("product") else None, "warehouse_name": frappe.db.get_value(
        "ERP Warehouse", place["warehouse"], "warehouse_name") or place["warehouse"]}
