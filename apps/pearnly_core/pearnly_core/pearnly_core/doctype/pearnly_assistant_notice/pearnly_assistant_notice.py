"""对话提醒（ADR-019）：产品发给某个用户的提醒，挂在对话上就在对话里出卡片。只由 `pearnly_core.assistant.notices` 读写。

去重键唯一：同一时段的提醒只记一条、只推一次。删对话时只把 `session_id` 置空，不删行（去重键要留着）。
"""
import frappe
from frappe.model.document import Document


class PearnlyAssistantNotice(Document):
    pass


# 随 migrate 由 Frappe 原生的 on_doctype_update 建（已有同名索引就跳过）。
INDEXES = {
    "notice_reference": ["reference_doctype", "reference_name"],    # 按产品记录找提醒
    "notice_reference_time": ["reference_doctype", "creation"],     # 小助手同步：某类记录某时刻之后的提醒（`for_references`）
}


def on_doctype_update():
    for index_name, fields in INDEXES.items():
        frappe.db.add_index("Pearnly Assistant Notice", fields, index_name)
