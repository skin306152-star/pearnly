import frappe
from frappe import _
from frappe.model.document import Document
from pearnly_cowork.masters.api import require_manager
from pearnly_cowork.access import scope


class CoworkClient(Document):
    def validate(self):
        require_manager()
        self.display_name = (self.display_name or "").strip()
        if not self.display_name:
            frappe.throw(_("A name is required."))
        scope.require_scope(self.workspace)
        before = self.get_doc_before_save()
        if before and before.workspace != self.workspace:
            frappe.throw(_("A client cannot be moved to another company."))
