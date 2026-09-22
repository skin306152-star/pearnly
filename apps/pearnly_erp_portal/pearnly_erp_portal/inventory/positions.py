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
    result["warehouse"] = warehouses.resolve(
        result.get("warehouse") or warehouse or product.default_warehouse)
    result["location"] = clean_location(
        result["location"] if "location" in result else product.default_location)
    return result


def saved(line, warehouse=None):
    """Read an existing line, including legacy blank positions, without product defaults."""
    return {"warehouse": line.get("warehouse") or warehouse or warehouses.ensure_default(),
            "location": clean_location(line.get("location"))}


def public(line, warehouse=None):
    place = saved(line, warehouse)
    return {**place, "product_name": frappe.db.get_value("ERP Product", line.get("product"), "product_name")
            if line.get("product") else None, "warehouse_name": frappe.db.get_value(
        "ERP Warehouse", place["warehouse"], "warehouse_name") or place["warehouse"]}
