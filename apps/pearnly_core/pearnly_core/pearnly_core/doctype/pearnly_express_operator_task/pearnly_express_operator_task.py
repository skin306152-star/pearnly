"""实操任务（主会话 10-27）：一个人在一家公司（同一产品内）一次实操的任务——标题、置顶、状态、执行电脑与包含的单据，跨设备
看、改都以它为准；CC 本机只缓存执行中的进度。只由 `pearnly_core.express_operator.tasks` 读写（原生读写一律拒，见 hooks）。

可见范围先照对话：只给创建人本人看和改。以后若要给公司管理员看团队任务，另议。
"""
import frappe
from frappe.model.document import Document


class PearnlyExpressOperatorTask(Document):
    pass


# 随 migrate 由 Frappe 原生的 on_doctype_update 建（已有同名索引就跳过）。
INDEXES = {
    "operator_task_owner_modified": ["owner", "modified"],  # 本人的任务按改动时间分页（`tasks.listing`）
}


def on_doctype_update():
    for index_name, fields in INDEXES.items():
        frappe.db.add_index("Pearnly Express Operator Task", fields, index_name)
