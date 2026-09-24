"""期初库存。

期初**落成流水**（`movement_type=opening`），不单独留一份报表基数。为什么：旧线上
期初只进收发存报表、不进在库结存，于是"在库 30、收发存期末 40"两个数并存——同一个
事实两个答案。现在把期初写成流水，在库、收发存、成本全部同源，只有一个数。

`ERP Stock Opening` 保留为不可变的初始化凭据。完全相同的重放不加库存，已有凭据
或库存流水的仓位不能重新设置期初；后续变更走入库或盘点调整。
"""

import frappe

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import contracts, opening_batch, warehouses

DOCTYPE = "ERP Stock Opening"
PRODUCT = "ERP Product"
INVALID = "Invalid value"


def _name(product: str, warehouse: str, location="") -> str:
    return contracts.position_name("OPN", product, warehouse, location)


def upsert_opening(*, product, warehouse, as_of_date, qty, unit_cost=None, note=None, location="") -> dict:
    """Compatibility API: same report permission, shared immutable initialization."""
    return opening_batch.upsert_one(product=product, warehouse=warehouse, as_of_date=as_of_date,
                                    qty=qty, unit_cost=unit_cost, note=note, location=location)


def list_openings(*, product=None, warehouse=None, limit=200) -> list[dict]:
    guard.require("report.read")
    filters: dict = {}
    if product:
        filters["product"] = product
    if warehouse:
        filters["warehouse"] = warehouses.required(warehouse)
    else:
        filters.update(warehouses.scope_filter())
    return frappe.get_list(DOCTYPE, filters=filters,
                           fields=["name", "product", "warehouse", "location", "as_of_date", "qty",
                                   "unit_cost", "note"],
                           order_by="as_of_date desc", limit_page_length=int(limit))


def opening_for(product: str, warehouse: str, location=""):
    name = _name(product, warehouse, location)
    if not frappe.db.exists(DOCTYPE, name):
        return None
    return frappe.db.get_value(DOCTYPE, name, ["as_of_date", "qty", "unit_cost"], as_dict=True)
