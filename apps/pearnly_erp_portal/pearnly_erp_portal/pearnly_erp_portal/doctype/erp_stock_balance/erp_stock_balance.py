"""One record per product, warehouse and free-text location."""

from frappe.model.document import Document

from pearnly_erp_portal.inventory.contracts import position_name


class ERPStockBalance(Document):
    def autoname(self):
        self.location = str(self.location or "").strip()
        self.name = position_name("BAL", self.product, self.warehouse, self.location)


# 按物料查结存（盘点的库存令牌、估值、物料详情）与按仓库查结存（建盘点快照、库存一览）。没有索引时每次整表扫：
# 11-14 线上 testerp 结存 2 万行，盘点明细 251 行就整表扫 251 次，服务端 6–7 秒（性能体检，docs/work/2026-11-14-page-performance.md）。
# 随 migrate 由 Frappe 原生的 on_doctype_update 建（已有同名索引就跳过）；已有站点由补丁 `v1_29_stock_count_indexes` 补建。
INDEXES = {
    "product_warehouse_index": ["product", "warehouse"],
    "warehouse_product_index": ["warehouse", "product"],
}


def on_doctype_update():
    import frappe

    for index_name, fields in INDEXES.items():
        frappe.db.add_index("ERP Stock Balance", fields, index_name)
