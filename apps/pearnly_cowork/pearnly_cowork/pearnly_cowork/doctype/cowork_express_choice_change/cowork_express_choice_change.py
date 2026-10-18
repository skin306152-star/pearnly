import frappe
from frappe.model.document import Document

from pearnly_cowork.push import choices


class CoworkExpressChoiceChange(Document):
    """账套记忆被人改值的记录（数字会计员第 3 期 R1）只经推送服务写（`push/choices.py`）；原生接口的建、改、删、改名一律拒绝。"""

    def check_permission(self, *args, **kwargs):
        choices.guard(self)

    def before_validate(self):
        choices.guard(self)

    validate = before_validate
    before_insert = before_validate

    def on_trash(self):
        choices.deny()

    def before_rename(self, *args, **kwargs):
        choices.deny()


# 判档时按「连接 + 类 + 改的时间」取近 60 天的（`push.choices.overrides`）；随 migrate 由 Frappe 原生的 on_doctype_update 建
# （新表建表时调，已有同名索引就跳过）。
INDEXES = {"connection_kind_changed_index": ["connection", "kind", "changed_at"]}


def on_doctype_update():
    for index_name, fields in INDEXES.items():
        frappe.db.add_index(choices.OVERRIDE_DOCTYPE, fields, index_name)
