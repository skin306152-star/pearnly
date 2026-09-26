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
        # 只有删除服务（`history/removal.py`）核过权限与推送后才放行；别的入口删不掉带原件的记录。
        from pearnly_cowork.history.removal import DELETE
        if self.source_file and self.flags.cowork_history_delete is not DELETE:
            frappe.throw(_("Original evidence and ownership cannot be changed."), frappe.PermissionError)
