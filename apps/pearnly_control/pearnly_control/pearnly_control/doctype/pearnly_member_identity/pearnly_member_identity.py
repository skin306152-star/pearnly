import frappe
from frappe.model.document import Document

from pearnly_control.account.member_service import _CAPABILITY


class PearnlyMemberIdentity(Document):
    def validate(self):
        if self.flags.member_identity_capability is not _CAPABILITY or not self.is_new():
            frappe.throw("Not permitted", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Not permitted", frappe.PermissionError)
