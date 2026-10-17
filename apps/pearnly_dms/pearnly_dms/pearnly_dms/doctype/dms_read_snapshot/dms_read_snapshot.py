"""Internal read model. Only the scoped service may mutate it (DMS snapshots are managed by the synchronizer)."""
import frappe
from frappe import _
from frappe.model.document import Document


class DMSReadSnapshot(Document):
    def validate(self):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    def on_trash(self):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    def before_rename(self, *args, **kwargs):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
