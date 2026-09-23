# -*- coding: utf-8 -*-
"""收银台取数：在架商品 + 售价 + **这个收银台的可用结存**。

用户 2026-09-23 口径："收银台也要做，用官方原生的，数据关联商品，库存，收发存"。
所以收银台的商品区一次给全三样：

· 商品：编码 / 名称 / 单位（商品主数据，`ERP Product`，只要在架的）；
· 售价：商品主数据里的售价（收银台卖的就是货架标价，含税口径见 `cashier/sales.py`）；
· 库存：**这个收银台所在仓库**的结存，读 `ERP Stock Balance` —— 那张表由收发存的流水
  维护（`inventory/stock.py`），所以卖出去的那一笔会当场进收发存，不是另算一份。

仓库按**这台收银台自己配的那个仓**解析（`terminals.place_of`），与 `sales.create_sale`
落单时同一口径 —— 否则界面看到的库存和实际扣的仓库会不是一个地方。
**没有"默认仓"可以兜**（用户 2026-09-24 删掉了那个概念）：没配仓、而这家公司又有多个仓时，
这里直接报错让人去把收银台的仓配好。
"""

from __future__ import annotations

import frappe

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.cashier import terminals
from pearnly_erp_portal.inventory import warehouses
from pearnly_erp_portal.products import service as products

BALANCE = "ERP Stock Balance"


def warehouse_for(terminal=None) -> str:
    """这个收银台动的是哪个仓库：**它自己配的那个**（没有"默认仓"了）。"""
    name = terminals.resolve(terminal)
    row = frappe.db.get_value("ERP POS Terminal", name, ["company", "warehouse"], as_dict=True)
    return terminals.place_of(row)


def _on_hand(warehouse: str, names: list) -> dict:
    """一次取回这批商品在该仓库的结存（同一商品多仓位就加起来）。"""
    if not names:
        return {}
    rows = frappe.get_all(BALANCE, filters={"warehouse": warehouse, "product": ["in", names]},
                          fields=["product", "qty_on_hand"], limit_page_length=0)
    totals: dict = {}
    for row in rows:
        totals[row["product"]] = totals.get(row["product"], 0.0) + float(row["qty_on_hand"] or 0)
    return totals


def products_page(*, terminal=None, search=None, limit=50, start=0) -> dict:
    """收银台的商品区一页：在架商品 + 售价 + 该收银台仓库的结存。

    返回 `{items, total}` 的形状（与销售记录那条 `records.page` 一致）：原生列表要
    `total` 才画得出"第 N 项 / 共 M 项"，只给数组的话页脚会显示成 `0 / undefined`。
    """
    guard.require("cashier.read")
    warehouse = warehouse_for(terminal)
    rows = products.list_products(search=search, limit=limit, start=start)
    totals = _on_hand(warehouse, [row["name"] for row in rows])
    for row in rows:
        row["warehouse"] = warehouse
        row["qty_on_hand"] = round(totals.get(row["name"], 0.0), 6)
    return {"items": rows, "total": products.count_products(search=search)}


def lookup(value, *, terminal=None) -> dict:
    """扫到（或手输）一个码 → 商品 + 该收银台的结存；查不到回空字典。

    查四处（编码/条码/二维码/售卖单位上的条码）与权限走商品域现有那条
    `products.find_product` —— 收银台不另写一套找品规则。
    """
    guard.require("cashier.read")
    found = products.find_product(value)
    if not found:
        return {}
    warehouse = warehouse_for(terminal)
    return {**found, "warehouse": warehouse,
            "qty_on_hand": round(_on_hand(warehouse, [found["name"]]).get(found["name"], 0.0), 6)}
