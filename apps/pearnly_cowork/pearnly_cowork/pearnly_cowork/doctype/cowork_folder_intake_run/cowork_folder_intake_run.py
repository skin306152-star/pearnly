from frappe.model.document import Document

from pearnly_cowork.folder_intake import access


class CoworkFolderIntakeRun(Document):
    """文件夹自动录入的私有表：只经 `folder_intake` 域写；原生接口的建、改、删、改名一律拒绝。"""

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
