"""One record per product and warehouse (11-19: locations are only kept on stock counts)."""

from frappe.model.document import Document

from pearnly_erp_portal.inventory.contracts import position_name


class ERPStockOpening(Document):
    def autoname(self):
        self.name = position_name("OPN", self.product, self.warehouse)
