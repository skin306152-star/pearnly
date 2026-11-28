"""Native stock-count write guards; business writes use the inventory service."""
from frappe.model.document import Document

from pearnly_erp_portal.express import ledger_guard
from pearnly_erp_portal.inventory import count_state


class ERPStockCount(Document):
    def validate(self):
        ledger_guard.keep_current(self)  # 账本栏只由写账链路写：旧副本保存不盖回（11-28 B ③）
        count_state.validate_document(self)

    def on_trash(self):
        count_state.before_delete(self)
