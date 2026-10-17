import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_control.account.member_service import _CAPABILITY


class PearnlyMemberOperation(Document):
    def validate(self):
        if self.flags.member_identity_capability is not _CAPABILITY or not self.is_new():
            frappe.throw(_("Not permitted"), frappe.PermissionError)

    def on_trash(self):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
