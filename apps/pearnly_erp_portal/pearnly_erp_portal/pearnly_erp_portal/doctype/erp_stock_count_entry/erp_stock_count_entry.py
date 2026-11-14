"""Native stock-count write guards; business writes use the inventory service."""
from frappe.model.document import Document
from pearnly_erp_portal.inventory import count_state


class ERPStockCountEntry(Document):
    def validate(self):
        count_state.validate_document(self)

    def on_trash(self):
        count_state.before_delete(self)


# 盘点记录按盘点单（及盘点行）取；没有索引时整表扫（性能体检 11-14）。已有站点由补丁 `v1_29_stock_count_indexes` 补建。
INDEXES = {"count_item_index": ["count", "item"]}


def on_doctype_update():
    import frappe

    for index_name, fields in INDEXES.items():
        frappe.db.add_index("ERP Stock Count Entry", fields, index_name)
