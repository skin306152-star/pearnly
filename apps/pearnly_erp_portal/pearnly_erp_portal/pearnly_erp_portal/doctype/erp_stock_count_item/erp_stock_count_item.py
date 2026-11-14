"""Native stock-count write guards; business writes use the inventory service."""
from frappe.model.document import Document
from pearnly_erp_portal.inventory import count_state


class ERPStockCountItem(Document):
    def validate(self):
        count_state.validate_document(self)

    def on_trash(self):
        count_state.before_delete(self)


# 盘点行都按盘点单取（`count = ?`）；没有索引时整表扫，所有盘点单的行越多越慢（性能体检 11-14）。
# 随 migrate 由 on_doctype_update 建；已有站点由补丁 `v1_29_stock_count_indexes` 补建。
INDEXES = {"count_product_index": ["count", "product"]}


def on_doctype_update():
    import frappe

    for index_name, fields in INDEXES.items():
        frappe.db.add_index("ERP Stock Count Item", fields, index_name)
