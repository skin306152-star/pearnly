"""对话里被停掉的半段回复（批 A ④，2026-11-09）：点了停止、引擎没出正式回复时，推送线程把已经逐字推出来的那一段记一行，
刷新、换设备后对话里还看得到（只给页面看，不交给模型）。只插入、不改；每段对话只留最近 `stopped.KEEP` 段，超出的由插入的地方
顺手删掉；删对话时一起删。只由 `pearnly_core.assistant.stopped` 读写。
"""
import frappe
from frappe.model.document import Document


class PearnlyAssistantStoppedReply(Document):
    pass


# 随 migrate 由 Frappe 原生的 on_doctype_update 建（已有同名索引就跳过）。
def on_doctype_update():
    frappe.db.add_index("Pearnly Assistant Stopped Reply", ["session_id", "creation"], "stopped_session_time")
