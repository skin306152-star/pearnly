from frappe.model.document import Document

from pearnly_cowork.readback import access


class CoworkExpressReadback(Document):
    """Express 回读的私有表：只经 `readback.service` 写、删；原生接口的读、建、改、删、改名一律拒绝。"""

    def check_permission(self, *args, **kwargs):
        access.guard(self)

    def autoname(self):
        access.guard(self)
        self.name = self.readback_id

    def before_validate(self):
        access.guard(self)

    validate = before_validate
    before_insert = before_validate

    def on_trash(self):
        access.deny()

    def before_rename(self, *args, **kwargs):
        access.deny()
