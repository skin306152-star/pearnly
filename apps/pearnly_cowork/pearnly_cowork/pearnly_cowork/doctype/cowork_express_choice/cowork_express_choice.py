from frappe.model.document import Document

from pearnly_cowork.push import choices


class CoworkExpressChoice(Document):
    """账套选择只经推送服务写（`push/choices.py`）；原生接口的建、改、删、改名一律拒绝。"""

    def check_permission(self, *args, **kwargs):
        choices.guard(self)

    def autoname(self):
        choices.guard(self)
        self.name = self.input_key

    def before_validate(self):
        choices.guard(self)

    validate = before_validate
    before_insert = before_validate

    def on_trash(self):
        choices.deny()

    def before_rename(self, *args, **kwargs):
        choices.deny()
