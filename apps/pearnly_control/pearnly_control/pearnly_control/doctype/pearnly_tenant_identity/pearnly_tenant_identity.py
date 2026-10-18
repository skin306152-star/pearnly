import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_control.account.member_service import _CAPABILITY


class PearnlyTenantIdentity(Document):
    """开户时登记的租户管理员身份。只由管理通道写入（`register`），不在界面上改。"""

    def validate(self):
        if self.flags.member_identity_capability is not _CAPABILITY or not self.is_new():
            frappe.throw(_("Not permitted"), frappe.PermissionError)
