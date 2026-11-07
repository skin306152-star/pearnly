"""仓库下的库位清单（第 5 项，车辆按台补充设计第 8 节，主会话 10-26 审定）。

- 清单是仓库的子表（``ERP Warehouse.locations``，读写跟着仓库走）；没建清单的仓，库位照旧手输，行为不变。
- 建了清单：货进到哪个库位要在清单里（``positions.check``，单据行、库存流水、盘点记录统一核）；从旧库位出货、作废冲回照常。
- 选库位：页面上的库位格是原生 Autocomplete，选项按所选仓库从这里按输入取（:func:`api_location_options`）。
- 停用：还有库存的库位不能停用；删除：从没被用过的才能删（用过的停用）。
- 建清单时可以把这个仓现在有库存的库位一键带进来（只登记，不改数据）。
Express 只有仓库没有库位，库位一直是 ERP 自有，不涉及 CC。
"""
import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import positions, warehouses

CHILD = "ERP Warehouse Location"
READ_CAPABILITIES = ("stock.read", "inventory.read", "purchase.read", "sales.read", "cashier.read", "count.read")
HAS_STOCK = "A location with stock cannot be turned off"
USED = "A location that has been used cannot be removed"
DUPLICATE = "Duplicate location"
DUPLICATE_CODE = "Duplicate location code"
OFF_LIST = "stock.location.offList"


def _stock_at(warehouse) -> dict:
    """这个仓各库位的结存数量：``{库位: 数量}``（只列不为 0 的）。"""
    out = {}
    for row in frappe.get_all("ERP Stock Balance", filters={"warehouse": warehouse},  # bounded: 一个仓按库位合计
                              fields=["location", {"SUM": "qty_on_hand", "as": "qty"}], group_by="location",
                              order_by="", limit_page_length=0):
        if abs(frappe.utils.flt(row.qty)) > 1e-9:
            out[row.location or ""] = frappe.utils.flt(row.qty)
    return out


def _used(warehouse, location) -> bool:
    return bool(frappe.db.exists("ERP Stock Movement", {"warehouse": warehouse, "location": location}))


@frappe.whitelist()
def api_location_options(txt="", warehouse=None, **kwargs):
    """库位格的下拉选项：这个仓清单里在用的库位，按输入搜（最多 50 个）。没建清单回空（照旧手输）。"""
    if not any(guard.has(capability) for capability in READ_CAPABILITIES):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if not warehouse:
        return []
    # 仓按它自己所属的公司核（仍要在本人可用范围里），不按当前公司：手机盘点取的是盘点那家公司的仓（10-30）
    warehouse = warehouses.required(warehouse, company=warehouses.company_of(warehouse))
    filters = {"parent": warehouse, "parenttype": "ERP Warehouse", "is_active": 1}
    text = positions.clean_location(txt)
    # 按仓位编码或名称搜（11-07）
    or_filters = {"location_name": ["like", f"%{text}%"], "location_code": ["like", f"%{text}%"]} if text else None
    listed = frappe.get_all(CHILD, filters=filters, or_filters=or_filters,
                            fields=["location_name", "location_code", "description"], order_by="idx asc",
                            limit_page_length=50)
    names = [row.location_name for row in listed]
    if not positions.has_list(warehouse):
        return names
    # 清单外、还有库存的旧库位照常能出库、能盘，也列出来，标「不在清单」（不能再往里入库，服务端核，主会话 10-26）
    old = [name for name in _stock_at(warehouse) if name and name not in names and (not text or text in name)]
    # 选项显示「编码 · 名称 · 说明」（11-07 主会话定；没编码、没说明的那段不写，10-29 批复 #4 的说明保留），值仍是仓位名称
    return [{"value": row.location_name, "label": positions.label(row.location_name, row.location_code, row.description)}
            for row in listed] + \
        [{"value": name, "label": f"{name} · {_(OFF_LIST)}"} for name in sorted(old)[:50]]


@frappe.whitelist()
def api_get_locations(warehouse=None):
    """维护清单用：清单各行（带这个库位现在的库存）与这个仓有库存、还没进清单的库位（可一键带入）。"""
    guard.require("warehouses.manage")
    warehouse = warehouses.required(warehouse)
    stock = _stock_at(warehouse)
    rows = frappe.get_all(CHILD, filters={"parent": warehouse, "parenttype": "ERP Warehouse"},  # bounded: 一个仓的库位
                          fields=["location_code", "location_name", "description", "is_active"], order_by="idx asc",
                          limit_page_length=0)
    names = {row.location_name for row in rows}
    return {"rows": [{**row, "qty": stock.get(row.location_name, 0)} for row in rows],
            "in_stock": [{"location_name": name, "qty": qty} for name, qty in sorted(stock.items()) if name and name not in names]}


@frappe.whitelist(methods=["POST"])
def api_save_locations(warehouse=None, rows=None):
    """保存清单（整份）：名称去空白、不能重；还有库存的不能停用，用过的不能删（停用）。"""
    guard.require("warehouses.manage")
    warehouse = warehouses.required(warehouse)
    rows = frappe.parse_json(rows) if isinstance(rows, str) else (rows or [])
    doc = frappe.get_doc("ERP Warehouse", warehouse)
    stock = _stock_at(warehouse)
    before = {row.location_name: int(row.is_active or 0) for row in doc.get("locations") or []}
    cleaned, seen, seen_codes = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = positions.clean_location(row.get("location_name"))
        if not name:
            continue
        if name in seen:
            frappe.throw(_(DUPLICATE) + f": {name}")
        seen.add(name)
        active = 1 if row.get("is_active") in (True, 1, "1", "true", None) else 0
        if not active and stock.get(name):
            frappe.throw(_(HAS_STOCK) + f": {name}")
        code = code_of(row)
        if code and code.casefold() in seen_codes:
            frappe.throw(_(DUPLICATE_CODE) + f": {code}")
        if code:
            seen_codes.add(code.casefold())
        cleaned.append({"location_code": code, "location_name": name, "is_active": active,
                        "description": str(row.get("description") or "").strip()[:140]})  # 库位说明（用户 10-29）
    for name in before:
        if name not in seen and (stock.get(name) or _used(warehouse, name)):
            frappe.throw(_(USED) + f": {name}")
    doc.set("locations", cleaned)
    doc.save(ignore_permissions=True)  # 上面已核 warehouses.manage 与这家公司的仓
    frappe.db.commit()
    return api_get_locations(warehouse)


def code_of(row) -> str:
    """一行仓位的编码：去空白，最长 `positions.CODE_LENGTH`；可空。"""
    code = str((row or {}).get("location_code") or "").strip()
    if len(code) > positions.CODE_LENGTH:
        frappe.throw(_("Invalid value"))
    return code


def duplicate_code(rows) -> str:
    """同一仓库里重复的仓位编码（不分大小写，空编码不算）；没有回空。保存仓库时控制器也核（任何保存路径都拦）。"""
    seen = set()
    for row in rows or []:
        code = str((row.get("location_code") if isinstance(row, dict) else getattr(row, "location_code", "")) or "").strip()
        if code and code.casefold() in seen:
            return code
        if code:
            seen.add(code.casefold())
    return ""


__all__ = ["api_get_locations", "api_location_options", "api_save_locations"]
