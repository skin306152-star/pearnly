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
from pearnly_erp_portal.inventory import count as counts

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
    ]
    return columns


def _state(row) -> str:
    if not row["counted"]:
        return _label("stock.count.rowUncounted")
    return _label("stock.count.rowDifference" if flt(row["difference"], 3)
                  else "stock.count.rowMatch")


def _rows(name: str, only: str) -> list:
    items = counts.list_items(count=name, limit=0)
    if only == "counted":
        items = [row for row in items if row["counted"]]
    elif only == "uncounted":
        items = [row for row in items if not row["counted"]]
    elif only == "differences":
        items = [row for row in items if row["counted"] and flt(row["difference"], 3)]
    warehouses = {row["name"]: row["warehouse_name"] for row in frappe.get_list(
        "ERP Warehouse", fields=["name", "warehouse_name"], limit_page_length=0)}
    out = []
    for row in items:
        counted = bool(row["counted"])
        difference = flt(row["difference"], 3) if counted else None
        out.append({
            "product_name": row["product_name"], "code": row["code"],
            "barcode": row["barcode"], "unit": row["unit"],
            "warehouse_name": warehouses.get(row["warehouse"], row["warehouse"]),
            "location": row["location"],
            "book_qty": flt(row["book_qty"], 3),
            "counted_qty": flt(row["counted_qty"], 3) if counted else None,
            "difference": difference,
            # 账面/实盘是数量，金额要乘成本 —— 没成本（或没权限）时留空，不是 0。
            "difference_value": row.get("difference_value"),
            "counted": 1 if counted else 0,
            "state": _state(row),
            "last_counted_by": row["last_counted_by"],
            "last_counted_at": row["last_counted_at"],
        })
    return out


def _latest_count() -> str:
    return frappe.db.get_value("ERP Stock Count", {}, "name", order_by="creation desc") or ""


def execute(filters=None):
    filters = frappe._dict(filters or {})
    guard.require("stock.read")
    name = (filters.get("count") or "").strip()
    if not name:
        # 从"报表"列表直接点进来的情况：给最新的一份，不让页面空着。
        name = _latest_count()
    if not name or not frappe.db.exists("ERP Stock Count", name):
        return _columns(), []
    return _columns(), _rows(name, (filters.get("only") or "").strip())
