from frappe.model.document import Document

from pearnly_cowork.companies import access


class CoworkCompanyChoice(Document):
    """公司规则（第 2 期统一客户规则）的私有表：只经 `companies.rules` 写；原生接口的建、改、删、改名一律拒绝。"""

    def check_permission(self, *args, **kwargs):
        access.guard(self)

    def autoname(self):
        access.guard(self)
        self.name = self.input_key

    def before_validate(self):
        access.guard(self)

    validate = before_validate
    before_insert = before_validate

    def on_trash(self):
        access.deny()

    def before_rename(self, *args, **kwargs):
        access.deny()
