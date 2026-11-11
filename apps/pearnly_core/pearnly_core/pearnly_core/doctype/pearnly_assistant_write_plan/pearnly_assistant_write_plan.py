"""CC 写入计划（一句话下令开始，A 线 2026-11-11）：CC 写账前服务端算出的这次写哪几张、跳过哪几张，用户在对话里同意后才开始。
只由 `pearnly_core.assistant.write_plan` 读写；删对话时一起删。
"""
import frappe
from frappe.model.document import Document


class PearnlyAssistantWritePlan(Document):
    pass


# 随 migrate 由 Frappe 原生的 on_doctype_update 建（已有同名索引就跳过）。
def on_doctype_update():
    frappe.db.add_index("Pearnly Assistant Write Plan", ["session_id", "status"], "write_plan_session_status")
