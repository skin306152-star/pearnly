import re

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_cowork.ocr.feedback import access


class CoworkOCRCorrection(Document):
    def check_permission(self, permtype="read", permlevel=None):
        # Native get_doc/check_permission short-circuits hooks for Administrator
        # and trusts ignore_permissions flags. This internal document grants
        # only its explicit create capability even through those entry points.
        if permtype == "create":
            access.guard_insert(self)
            return
        frappe.throw(_("Not Permitted"), frappe.PermissionError)

    def before_insert(self):
        access.guard_insert(self)

    def before_validate(self):
        access.guard_insert(self)

    def validate(self):
        access.guard_insert(self)

    def autoname(self):
        if not re.fullmatch(r"[0-9a-f]{64}", self.event_key or ""):
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
        self.name = "CWFC-" + self.event_key

    def on_trash(self):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)

    def before_rename(self, *args, **kwargs):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
