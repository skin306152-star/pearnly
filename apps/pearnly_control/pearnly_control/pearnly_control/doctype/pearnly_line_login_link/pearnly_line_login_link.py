import frappe
from frappe.model.document import Document

from pearnly_control.account.member_service import _CAPABILITY


class PearnlyLINELoginLink(Document):
    """某个站点（租户 × 产品）上某身份当前关联的 LINE。只由建号服务写入。"""

    def validate(self):
        if self.flags.member_identity_capability is not _CAPABILITY:
            frappe.throw("Not permitted", frappe.PermissionError)
