"""Internal read model. Only the scoped service may mutate it."""
import frappe
from frappe.model.document import Document


class DMSReadSnapshot(Document):
    def validate(self):
        frappe.throw("DMS snapshots are managed by the synchronizer", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("DMS snapshots are managed by the synchronizer", frappe.PermissionError)

    def before_rename(self, *args, **kwargs):
        frappe.throw("DMS snapshots cannot be renamed", frappe.PermissionError)
