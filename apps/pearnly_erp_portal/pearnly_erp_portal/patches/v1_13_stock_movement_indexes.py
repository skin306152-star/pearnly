"""库存流水的分组 / 截止日期索引（``erp_stock_movement.INDEXES``），已有站点迁移时补建。

``on_doctype_update`` 只在 DocType 定义有变化或新装站点时被 Frappe 调用；这两个索引是在 DocType JSON 不变的情况下加的
（同 Cowork `v0_0_25_push_queue_index`）。这里在迁移时调一次，已有同名索引就跳过，重跑无变化。
"""
from pearnly_erp_portal.pearnly_erp_portal.doctype.erp_stock_movement.erp_stock_movement import on_doctype_update


def execute():
    on_doctype_update()
