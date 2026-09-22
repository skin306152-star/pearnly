"""One record per product, warehouse and free-text location."""

from frappe.model.document import Document

from pearnly_erp_portal.inventory.contracts import position_name


class ERPStockBalance(Document):
    def autoname(self):
        self.location = str(self.location or "").strip()
        self.name = position_name("BAL", self.product, self.warehouse, self.location)
