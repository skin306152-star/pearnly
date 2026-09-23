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
                                  ["is_active", "default_warehouse", "default_location"],
                                  as_dict=True)
    if not product or not product.is_active:
        frappe.throw(_("Product not found"))
    # 仓库必须是这家公司真实存在的仓（仓库属于公司）。商品上那个默认仓可能是别家公司
    # 或者已经停用的，那种情况要**报错**而不是把货悄悄记到"没有仓库"上。
    wanted = result.get("warehouse") or warehouse or product.default_warehouse
    # 一个都没有时不再往"默认仓"上落：`required()` 只认"这家公司只有一个仓"，
    # 多个仓直接报错让调用方明确指定（见 `warehouses` 抬头）。
    resolved = warehouses.resolve(wanted, create=False) if wanted else warehouses.required()
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
