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


NOT_LISTED = "This location is not in the location list of this warehouse"
NO_ACTIVE = "This warehouse has no active location"


def has_list(warehouse) -> bool:
    """这个仓建了库位清单没有（停用的也算建了：全部停用 = 一个都不能用，验收 10-26）。"""
    return bool(warehouse) and bool(frappe.db.exists("ERP Warehouse Location",
                                                     {"parent": warehouse, "parenttype": "ERP Warehouse"}))


def listed(warehouse) -> list:
    """这个仓的库位清单（在用的）；没建清单回空（库位照旧手输）。"""
    if not warehouse:
        return []
    return frappe.get_all("ERP Warehouse Location", filters={"parent": warehouse, "parenttype": "ERP Warehouse",  # bounded: 一个仓的库位
                                                            "is_active": 1}, pluck="location_name",
                          order_by="idx asc", limit_page_length=0)


def check(warehouse, location):
    """库位清单（第 5 项）：仓库建了清单，库位就只能是清单里在用的；空库位（不分库位）照旧可以；没建清单的仓照旧手输。
    只在货**进**库位时核（库存流水 `stock.apply_movement`、盘点记到别处）：从清单之前的旧库位出货照常。"""
    location = clean_location(location)
    if location and warehouse and has_list(warehouse):
        names = listed(warehouse)
        if not names:
            frappe.throw(_(NO_ACTIVE))  # 建过清单但全部停用 = 没有可用库位，先启用一个（主会话 10-26）
        if location not in names:
            frappe.throw(_(NOT_LISTED) + f": {location}")
    return location


def prepare(line, warehouse=None, *, company=None):
    result = dict(line)
    if result.get("warehouse"):
        result["warehouse"] = warehouses.required(result["warehouse"], company=company)
    if not result.get("product"):
        return result
    product = frappe.db.get_value("ERP Product", result["product"],
                                  ["is_active", "default_location"],
                                  as_dict=True)
    if not product or not product.is_active:
        frappe.throw(_("Product not found"))
    if result.get("item_type") == "service":
        # 服务 / 费用行不进出库，不取仓（10-24 验收问题 22：多仓、没有收银台的公司开费用单，关联了服务物料就报
        # 「请选择仓库」，表单上又没有仓库栏）。行上写了仓照旧核过（上面）。
        return result
    # 仓库必须是这家公司真实存在的仓（仓库属于公司）。
    # **商品上那个"默认仓"不再参与**（用户 2026-09-24："进货就进卖货那个仓"）：它会让
    # 进货落在一个仓、收银从另一个仓卖，两边数量与金额永远对不上（实测：`水` 在卖货仓
    # 一路负库存、成本立不起来，收发存金额列一直是空的）。
    # 行上没写、单据上也没写时只认「这家公司只有一个仓」，多个仓报错让人选（`warehouses.receiving`，多仓闭环 P1）。
    wanted = result.get("warehouse") or warehouse
    resolved = warehouses.receiving(wanted, company=company)
    if not resolved:
        frappe.throw(_("Warehouse not found"))
    result["warehouse"] = resolved
    location = clean_location(result["location"] if "location" in result else product.default_location)
    # 商品上的建议库位不在这个仓的清单里时不硬塞（留空让人选）。单据上明写的不在这里核：这一行是进是出这里不知道，
    # 进库位的核在库存流水（`stock.apply_movement`），从旧库位出货照常（验收 10-26）
    if "location" not in result and location and has_list(resolved) and location not in listed(resolved):
        location = ""
    result["location"] = location
    return result


def saved(line, warehouse=None, *, company=None):
    """Read an existing line, including legacy blank positions, without product defaults."""
    value = line.get("warehouse") or warehouse
    if value and company and warehouses.company_of(value) != company:
        frappe.throw(_("Warehouse not found"))
    if not value and line.get("item_type") == "service":
        return {"warehouse": None, "location": clean_location(line.get("location"))}   # 服务行没有仓（见 prepare）
    return {"warehouse": value or warehouses.required(company=company),
            "location": clean_location(line.get("location"))}


def public(line, warehouse=None, *, company=None):
    place = saved(line, warehouse, company=company)
    return {**place, "product_name": frappe.db.get_value("ERP Product", line.get("product"), "product_name")
            if line.get("product") else None, "warehouse_name": frappe.db.get_value(
        "ERP Warehouse", place["warehouse"], "warehouse_name") or place["warehouse"]}
