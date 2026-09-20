from frappe.model.document import Document
from pearnly_cowork.history.storage import validate_document, guard_write
import frappe
from frappe import _


class CoworkOCRHistory(Document):
    def before_insert(self):
        guard_write(self)

    def before_validate(self):
        guard_write(self)

    def autoname(self):
        self.name = "CWH-" + self.request_key[:48]

    def validate(self):
        validate_document(self)

    def on_trash(self):
        if self.source_file:
            frappe.throw(_("Original evidence and ownership cannot be changed."), frappe.PermissionError)
