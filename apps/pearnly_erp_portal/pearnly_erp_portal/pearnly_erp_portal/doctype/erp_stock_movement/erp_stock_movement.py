# -*- coding: utf-8 -*-
"""库存流水。**不可变**：只允许创建与读取；改结存靠追加流水。

作废单据不删流水，而是追加一笔反向流水，历史仍然看得见。金额与单价在"成本从未
确立"时为空，这是有意的，不是缺省值。
"""

import frappe
from frappe.model.document import Document


class ERPStockMovement(Document):
    pass


# 收发存与库存卡按「仓库 + 商品」分组（11-19 取消仓位；旧的带仓位的覆盖索引留在已有站点上，不再用）、按日期截止读流水（`inventory/report.py`）；单品明细按商品读。
# 没有索引时每次整表扫（验收 10-25：30 万行流水收发存默认页 6.3 秒、按数量排序 13.5 秒，裸 SQL 分组 2.2 秒）。
# 随 migrate 由 Frappe 原生的 on_doctype_update 建（已有同名索引就跳过）；已有站点由补丁
# `v1_13_stock_movement_indexes` 补建。
# 第一条是覆盖索引：收发存的分组求和（`report._position_sums`）要的列全在里面，不回表（验收实测：30 万行
# 全在条件内时普通索引用不上、整表扫，加上数量 / 类型 / 来源后 EXPLAIN 为 Using index，一次分组 0.95 秒）。
INDEXES = {
    "warehouse_product_date_cover_index": ["warehouse", "product", "posting_date", "qty_base", "movement_type", "ref_type"],
    "product_warehouse_date_index": ["product", "warehouse", "posting_date"],
}


def on_doctype_update():
    for index_name, fields in INDEXES.items():
        frappe.db.add_index("ERP Stock Movement", fields, index_name)
