"""盘点差异看板：这份盘点每一行（商品 × 仓库 × 库位）的账面 / 实盘 / 差异。

**原生报表**（`report_type: Script Report`）：筛选、图表、导出都用框架自带的那一套，
不自己拼界面。图表在 `erp_stock_count_variance.js` 里给（框架的 `get_chart_data` 钩子），
按「看板按」这一项把行归到商品 / 仓库 / 库位上看。

列名与状态走**语言包按键取**，不用 `_()` —— 短词（Code / Location）会被框架自带的
译文抢走（"Code" 变"代码"、"Location" 变"地点"），与页面上的说法就对不上了。
"""

import frappe
from frappe.utils import flt

from pearnly_erp_portal.access import guard, rules
from pearnly_erp_portal.inventory import contracts as stock_contracts, count as counts

PRODUCT = "ERP Product"
BALANCE = "ERP Stock Balance"


def _label(key: str) -> str:
    from pearnly_core import browser_text

    lang = (frappe.local.lang or browser_text.FALLBACK_LANG)[:2]
    texts = browser_text.all_languages(key)
    return texts.get(lang) or texts.get("en") or key


def _columns() -> list:
    columns = [
        {"label": _label("stock.col.name"), "fieldname": "product_name",
         "fieldtype": "Data", "width": 220},
        {"label": _label("stock.col.code"), "fieldname": "code",
         "fieldtype": "Data", "width": 110},
        {"label": _label("stock.col.barcode"), "fieldname": "barcode",
         "fieldtype": "Data", "width": 140},
        {"label": _label("stock.col.warehouse"), "fieldname": "warehouse_name",
         "fieldtype": "Data", "width": 120},
        {"label": _label("stock.count.location"), "fieldname": "location",
         "fieldtype": "Data", "width": 100},
        # 这一行从哪来（空 = 账上快照）：盘点时补的、当场新建的、停用后又盘到的（用户 10-29）
        {"label": _label("stock.count.rowOrigin"), "fieldname": "row_origin",
         "fieldtype": "Data", "width": 100},
        {"label": _label("stock.col.unit"), "fieldname": "unit",
         "fieldtype": "Data", "width": 80},
        {"label": _label("stock.count.book"), "fieldname": "book_qty",
         "fieldtype": "Float", "precision": 3, "width": 110},
        {"label": _label("stock.count.counted"), "fieldname": "counted_qty",
         "fieldtype": "Float", "precision": 3, "width": 110},
        {"label": _label("stock.count.difference"), "fieldname": "difference",
         "fieldtype": "Float", "precision": 3, "width": 110},
    ]
    if guard.cost_visible():
        # 成本按岗位遮蔽（与库存页同一条规矩）：没权限的岗位这一列根本不出现。
        columns.append({"label": _label("stock.count.differenceValue"),
                        "fieldname": "difference_value", "fieldtype": "Currency",
                        "options": "THB", "width": 130})
    columns += [
        {"label": _label("stock.col.status"), "fieldname": "state",
         "fieldtype": "Data", "width": 110},
        {"label": _label("stock.count.countedBy"), "fieldname": "last_counted_by",
         "fieldtype": "Data", "width": 170},
        {"label": _label("stock.count.countedAt"), "fieldname": "last_counted_at",
         "fieldtype": "Datetime", "width": 170},
        # 按台管理的商品：这个仓位在库、这份盘点没扫到的台（没盘到的不自动改状态，人工处理，主会话 10-26）
        {"label": _label("serial.notCountedUnits"), "fieldname": "uncounted_units",
         "fieldtype": "Small Text", "width": 220},
    ]
    return columns


def _state(row) -> str:
    if not row["counted"]:
        return _label("stock.count.rowUncounted")
    return _label("stock.count.rowDifference" if flt(row["difference"], 3)
                  else "stock.count.rowMatch")


def _rows(name: str, only: str, warehouse="", location="") -> list:
    items = stock_contracts.count_rows(counts.list_items(count=name, limit=0), only=only, warehouse=warehouse,
                                       location=location)
    from pearnly_erp_portal.inventory import warehouses as warehouse_service

    warehouses = {row["name"]: warehouse_service.label(row) for row in frappe.get_list(  # 「编号 · 名称」（用户 10-29）
        "ERP Warehouse", fields=["name", "warehouse_name", "warehouse_code", "express_code"], limit_page_length=0)}
    uncounted = _uncounted_units(name, items)
    out = []
    for row in items:
        counted = bool(row["counted"])
        difference = flt(row["difference"], 3) if counted else None
        out.append({
            "product_name": row["product_name"], "code": row["code"],
            "barcode": row["barcode"], "unit": row["unit"],
            "warehouse_name": warehouses.get(row["warehouse"], row["warehouse"]),
            "location": row["location"],
            "row_origin": _label(f"stock.count.origin.{row['row_origin']}") if row.get("row_origin") else "",
            "book_qty": flt(row["book_qty"], 3),
            "counted_qty": flt(row["counted_qty"], 3) if counted else None,
            "difference": difference,
            # 账面/实盘是数量，金额要乘成本 —— 没成本（或没权限）时留空，不是 0。
            "difference_value": row.get("difference_value"),
            "counted": 1 if counted else 0,
            "state": _state(row),
            "last_counted_by": row["last_counted_by"],
            "last_counted_at": row["last_counted_at"],
            "uncounted_units": ", ".join(uncounted.get((row["product"], row["warehouse"], row["location"] or ""), [])),
        })
    return out


def _uncounted_units(name, items) -> dict:
    """按台管理的商品：``{(商品, 仓, 库位): [这份盘点没扫到的在库台的主号]}``。"""
    from pearnly_erp_portal.inventory import serials

    from pearnly_erp_portal.inventory import count as counts

    tracked = serials.tracked((row["product"] for row in items), counts.count_company(name))
    if not tracked:
        return {}
    scanned = {"count": name, "voided": 0, "serial": ["is", "set"]}
    seen = set(frappe.get_all("ERP Stock Count Entry", filters=scanned, pluck="serial", limit_page_length=0))  # bounded: 一份盘点扫到的台
    out = {}
    for row in items:
        if row["product"] not in tracked:
            continue
        units = frappe.get_all(serials.DOCTYPE, filters={"product": row["product"], "warehouse": row["warehouse"],
                                                        "status": serials.IN_STOCK},
                               fields=["name", "serial_no", "location"], order_by="serial_no asc",
                               limit_page_length=500)  # bounded: 一个仓一个车型的在库台
        out[(row["product"], row["warehouse"], row["location"] or "")] = [
            unit.serial_no for unit in units if unit.name not in seen and (unit.location or "") == (row["location"] or "")]
    return out


def _latest_count() -> str:
    """当前公司最新的一份盘点（盘点按仓分公司；以前不分公司，会给到别家公司的盘点，多仓评估 13.4）。"""
    from pearnly_erp_portal.inventory import warehouses
    from pearnly_erp_portal.masters import company as companies

    mine = warehouses.names_of(companies.active_company())
    return frappe.db.get_value("ERP Stock Count", {"warehouse": ["in", mine or [""]]}, "name",
                               order_by="creation desc") or ""


def execute(filters=None):
    filters = frappe._dict(filters or {})
    guard.require("stock.read")
    name = (filters.get("count") or "").strip()
    if not name:
        # 从"报表"列表直接点进来的情况：给最新的一份，不让页面空着。
        name = _latest_count()
    if not name or not frappe.db.exists("ERP Stock Count", name):
        return _columns(), []
    return _columns(), _rows(name, (filters.get("only") or "").strip(), filters.get("warehouse") or "",
                             filters.get("location") or "")
