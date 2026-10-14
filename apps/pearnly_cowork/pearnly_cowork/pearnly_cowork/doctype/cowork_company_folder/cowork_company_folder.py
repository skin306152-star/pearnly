from frappe.model.document import Document

from pearnly_cowork.folder_intake import access


class CoworkCompanyFolder(Document):
    """公司主档单据文件夹（ADR-020）的私有表：只经 `folder_intake.company_folders` 写；原生接口的建、改、删、改名一律拒绝。"""

    def check_permission(self, *args, **kwargs):
        access.guard(self)

    def before_validate(self):
        access.guard(self)

    validate = before_validate
    before_insert = before_validate

    def on_trash(self):
        access.deny()

    def before_rename(self, *args, **kwargs):
        access.deny()
