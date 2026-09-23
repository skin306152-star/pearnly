"""期初库存。

期初**落成流水**（`movement_type=opening`），不单独留一份报表基数。为什么：旧线上
期初只进收发存报表、不进在库结存，于是"在库 30、收发存期末 40"两个数并存——同一个
事实两个答案。现在把期初写成流水，在库、收发存、成本全部同源，只有一个数。

`ERP Stock Opening` 保留为**录入凭据**：它是用户填过的期初，改期初时按差额补一笔
调整流水（流水仍然不可变，改动留下痕迹）。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import contracts, positions, warehouses, stock

DOCTYPE = "ERP Stock Opening"
PRODUCT = "ERP Product"
INVALID = "Invalid value"


def _name(product: str, warehouse: str, location="") -> str:
    return contracts.position_name("OPN", product, warehouse, location)


def upsert_opening(*, product, warehouse, as_of_date, qty, unit_cost=0, note=None, location="") -> dict:
    guard.require("stock.write")
    if not frappe.db.exists(PRODUCT, product):
        frappe.throw(_("Product not found"), frappe.DoesNotExistError)
    warehouse = warehouses.required(warehouse)
    location = positions.clean_location(location)
    stock.lock_products([product])
    try:
        quantity = contracts.d(qty)
        cost = contracts.d(unit_cost)
        if quantity < 0 or cost < 0:
            raise ValueError("negative")
    except ValueError:
        frappe.throw(_(INVALID))

    name = _name(product, warehouse, location)
    if frappe.db.exists(DOCTYPE, name):
        doc = frappe.get_doc(DOCTYPE, name)
    else:
        doc = frappe.get_doc({"doctype": DOCTYPE, "product": product, "warehouse": warehouse, "location": location})

    # 期初按差额补一笔流水：录入凭据可以改，流水仍然是只增不改的账。
    #
    # `posted_qty` 记住"已经推给流水多少"，因此重复提交同一份期初不会重复记账，改期初
    # 只补差额；幂等锚带上"改动前"的数量，每次修订都是不同的一笔，重放不会撞车。
    recorded = contracts.d(0) if doc.is_new() else contracts.d(doc.posted_qty or 0)
    delta = quantity - recorded
    if delta != 0:
        movement_type = contracts.OPENING if recorded == 0 else contracts.ADJUST
        stock.apply_movement(
            product=product, warehouse=warehouse, location=location, movement_type=movement_type, qty=delta,
            unit_cost=unit_cost, posting_date=as_of_date, ref_type="opening", ref_id=name,
            reason="opening stock" if movement_type == contracts.OPENING else "opening revised",
            client_uuid=f"opening:{frappe.generate_hash(length=24)}",
        )
        doc.posted_qty = quantity
    doc.as_of_date = as_of_date
    doc.qty = quantity
    doc.unit_cost = cost
    doc.note = note
    doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "qty": float(doc.qty), "unit_cost": float(doc.unit_cost or 0)}


def list_openings(*, product=None, warehouse=None, limit=200) -> list[dict]:
    guard.require("stock.read")
    filters: dict = {}
    if product:
        filters["product"] = product
    if warehouse:
        filters["warehouse"] = warehouse
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
