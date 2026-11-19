"""Resolve the warehouse of business lines, and the stock count's location list.

Stock is kept per product and warehouse (11-19: locations dropped everywhere except
stock counts). A line's own warehouse wins; a line without one takes the document's
default warehouse, and only a company with one active warehouse needs neither.
Locations remain a record of where a counted item was found (`ERP Warehouse Location`).
Callers authorize their own business action.
"""

import frappe
from frappe import _

from pearnly_core.express_rules.contracts import codes as code_rules
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


CODE_LENGTH = 30  # 仓位编码（11-07）：同一仓库里不能重，可空；单据、流水里存的仍是仓位名称


def codes(warehouse) -> dict:
    """这个仓清单里各仓位的编码 ``{仓位名称: 编码}``（没写编码的不列）。"""
    if not warehouse:
        return {}
    rows = frappe.get_all("ERP Warehouse Location", filters={"parent": warehouse, "parenttype": "ERP Warehouse"},  # bounded: 一个仓的库位
                          fields=["location_name", "location_code"], limit_page_length=0)
    return {row.location_name: row.location_code.strip() for row in rows if (row.location_code or "").strip()}


def label(name, code="", description="") -> str:
    """「编码 · 名称 · 说明」（选仓位的下拉、LINE 按钮同一个写法）：没编码、没说明的那段不写。"""
    return " · ".join(part for part in ((code or "").strip(), (name or "").strip(), (description or "").strip()) if part)


def resolve(warehouse, value) -> str:
    """导入的表格、LINE 里打的字：先按这个仓清单里的编码认（不分大小写），认到换成那个仓位的名称；认不到照原样当名称。"""
    value = clean_location(value)
    if not value or not warehouse:
        return value
    wanted = value.casefold()
    found = [name for name, code in codes(warehouse).items() if code.casefold() == wanted]
    return found[0] if len(found) == 1 and value not in listed(warehouse) else value


def check(warehouse, location):
    """盘点记「实际仓位」时核（11-19 起仓位只在盘点里记）：仓库建了清单，仓位就只能是清单里在用的；空仓位照旧可以；
    没建清单的仓照旧手输。"""
    location = clean_location(location)
    if location and warehouse and has_list(warehouse):
        names = listed(warehouse)
        if not names:
            frappe.throw(_(NO_ACTIVE))  # 建过清单但全部停用 = 没有可用库位，先启用一个（主会话 10-26）
        location = code_rules.choose(location, list(names))  # 复述时空白变了的库位换回清单原样
        if location not in names:
            frappe.throw(_(NOT_LISTED) + f": {location}")
    return location


def header(warehouse=None, *, company=None) -> dict:
    """单头的默认仓（行上没写仓的用它）：要是这家公司启用的仓；没写回空。"""
    warehouse = warehouses.required(warehouse, company=company) if str(warehouse or "").strip() else None
    return {"warehouse": warehouse}


def prepare(line, warehouse=None, *, company=None, allow_inactive=False):
    """明细行定仓：行上的仓为准（要是这家公司启用的仓，别家公司的仓被拒），没写用 ``warehouse``（单头的默认仓），再没有只认
    「这家公司只有一个仓」，多个仓报错让人选。``allow_inactive``：停用的物料也认（盘点盘到停用物料，生成的调整单提交时才重新启用它）。"""
    result = dict(line)
    result.pop("location", None)
    result.pop("to_location", None)
    if result.get("warehouse"):
        result["warehouse"] = warehouses.required(result["warehouse"], company=company)
    if not result.get("product"):
        return result
    product = frappe.db.get_value("ERP Product", result["product"], ["is_active"], as_dict=True)
    if not product or not (product.is_active or allow_inactive):
        frappe.throw(_("Product not found"))
    if result.get("item_type") == "service":
        # 服务 / 费用行不进出库，不取仓（10-24 验收问题 22：多仓、没有收银台的公司开费用单，关联了服务物料就报
        # 「请选择仓库」，表单上又没有仓库栏）。行上写了仓照旧核过（上面）。
        return result
    # 仓库必须是这家公司真实存在的仓（仓库属于公司）。商品上那个"默认仓"不参与（用户 2026-09-24："进货就进卖货那个仓"）。
    # 行上没写、单据上也没写时只认「这家公司只有一个仓」，多个仓报错让人选（`warehouses.receiving`，多仓闭环 P1）。
    resolved = warehouses.receiving(result.get("warehouse") or warehouse, company=company)
    if not resolved:
        frappe.throw(_("Warehouse not found"))
    result["warehouse"] = resolved
    return result


def saved(line, warehouse=None, *, company=None):
    """Read the warehouse of an existing line (its own, else the document default)."""
    value = line.get("warehouse") or warehouse
    if value and company and warehouses.company_of(value) != company:
        frappe.throw(_("Warehouse not found"))
    if not value and line.get("item_type") == "service":
        return {"warehouse": None}   # 服务行没有仓（见 prepare）
    return {"warehouse": value or warehouses.required(company=company)}


def public(line, warehouse=None, *, company=None):
    place = saved(line, warehouse, company=company)
    return {**place, "product_name": frappe.db.get_value("ERP Product", line.get("product"), "product_name")
            if line.get("product") else None, "warehouse_name": frappe.db.get_value(
        "ERP Warehouse", place["warehouse"], "warehouse_name") or place["warehouse"]}
