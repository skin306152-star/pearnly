from frappe.model.document import Document

from pearnly_cowork.folder_intake import access, live


class CoworkFolderIntakeFile(Document):
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

    def on_update(self):
        # 文件走到下一步（或结果表格换了版本）：开着的定时任务页重拉「处理中」（`folder_intake.live`）
        if self.has_value_changed("state") or self.has_value_changed("workbook_revision"):
            live.changed(self.rule, self.actor)

    def on_trash(self):
        access.deny()

    def before_rename(self, *args, **kwargs):
        access.deny()
