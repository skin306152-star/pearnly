from frappe.model.document import Document

from pearnly_cowork.pos_import import layouts


class CoworkPOSLayout(Document):
    """POS 导出版式只经 `pos_import/layouts.py` 写；原生接口的建、改、删、改名一律拒绝。"""

    def check_permission(self, *args, **kwargs):
        layouts.guard(self)

    def autoname(self):
        layouts.guard(self)
        self.name = self.layout_key

    def before_validate(self):
        layouts.guard(self)

    validate = before_validate
    before_insert = before_validate

    def on_trash(self):
        layouts.guard_delete(self)

    def before_rename(self, *args, **kwargs):
        layouts.deny()
