"""Anthropic 用量与费用账本（2026-11-11，`pearnly_core.ai_usage`）：每笔调用（标题、代录、识别）一行；CC 对话每段远端会话每天一行
（名字 `cc-<远端会话>-<日期>`，存当天增量、反复覆盖）。只由 `pearnly_core.ai_usage.ledger` 写，`ai_usage.report` 汇总；
没有菜单和页面，只给 System Manager 读。删对话不删账（成本账要留），只存数字与编号、不存对话内容。
"""
import frappe
from frappe.model.document import Document


class PearnlyAIUsage(Document):
    pass


# 随 migrate 由 Frappe 原生的 on_doctype_update 建（已有同名索引就跳过）。
def on_doctype_update():
    frappe.db.add_index("Pearnly AI Usage", ["day", "purpose"], "ai_usage_day_purpose")
    frappe.db.add_index("Pearnly AI Usage", ["segment", "day"], "ai_usage_segment_day")
