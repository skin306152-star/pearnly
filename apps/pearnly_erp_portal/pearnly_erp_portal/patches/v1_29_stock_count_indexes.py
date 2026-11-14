"""盘点 / 结存的过滤索引（各 DocType 模块的 ``INDEXES``），已有站点迁移时补建（性能体检 11-14）。

``on_doctype_update`` 只在 DocType 定义有变化或新装站点时被 Frappe 调用；这些索引是在 DocType JSON 不变的情况下加的
（同 ``v1_13_stock_movement_indexes``）。已有同名索引就跳过，重跑无变化。
"""
from pearnly_erp_portal.pearnly_erp_portal.doctype.erp_stock_balance import erp_stock_balance
from pearnly_erp_portal.pearnly_erp_portal.doctype.erp_stock_count_entry import erp_stock_count_entry
from pearnly_erp_portal.pearnly_erp_portal.doctype.erp_stock_count_item import erp_stock_count_item


def execute():
    for module in (erp_stock_balance, erp_stock_count_item, erp_stock_count_entry):
        module.on_doctype_update()
