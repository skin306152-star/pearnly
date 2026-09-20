import frappe
from frappe import _
from frappe.model.document import Document
from pearnly_cowork.masters.api import require_manager
from pearnly_cowork.access import scope


class CoworkWorkspace(Document):
    def validate(self):
        require_manager()
        self.display_name = (self.display_name or "").strip()
        if not self.display_name:
            frappe.throw(_("A name is required."))
