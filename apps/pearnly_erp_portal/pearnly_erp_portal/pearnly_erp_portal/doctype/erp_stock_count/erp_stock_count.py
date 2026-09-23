"""Native stock-count write guards; business writes use the inventory service."""
from frappe.model.document import Document
from pearnly_erp_portal.inventory import count_state


class ERPStockCount(Document):
    def validate(self):
        count_state.validate_document(self)

    def on_trash(self):
        count_state.before_delete(self)
