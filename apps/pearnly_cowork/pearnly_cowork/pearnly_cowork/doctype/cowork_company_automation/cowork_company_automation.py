from frappe.model.document import Document

from pearnly_cowork.companies import access


class CoworkCompanyAutomation(Document):
    """公司的自动推送档位（数字会计员第 3 期第 9 条）的私有表：只经 `companies.automation` 写；原生接口的建、改、删、改名一律拒绝。"""

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
