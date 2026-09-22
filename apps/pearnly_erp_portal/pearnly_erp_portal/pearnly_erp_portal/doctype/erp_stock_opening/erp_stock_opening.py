"""One record per product, warehouse and free-text location."""

from frappe.model.document import Document

from pearnly_erp_portal.inventory.contracts import position_name


class ERPStockOpening(Document):
    def autoname(self):
        self.location = str(self.location or "").strip()
        self.name = position_name("OPN", self.product, self.warehouse, self.location)
