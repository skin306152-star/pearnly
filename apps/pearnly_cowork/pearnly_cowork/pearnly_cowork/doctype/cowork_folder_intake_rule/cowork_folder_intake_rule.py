from frappe.model.document import Document

from pearnly_cowork.folder_intake import access, live


class CoworkFolderIntakeRule(Document):
    """文件夹自动录入的私有表：只经 `folder_intake` 域写；原生接口的建、改、删、改名一律拒绝。"""

    def check_permission(self, *args, **kwargs):
        access.guard(self)

    def before_validate(self):
        access.guard(self)

    validate = before_validate
    before_insert = before_validate

    def on_update(self):
        # 任务建、改、启停、立即运行（含在 CC 里做的）：开着的定时任务页重拉（`folder_intake.live`）；换了负责人原负责人也刷
        before = self.get_doc_before_save()
        live.changed(self.name, before.rule_owner if before else None)

    def on_trash(self):
        access.deny()

    def before_rename(self, *args, **kwargs):
        access.deny()
