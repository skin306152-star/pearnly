"""智能体会话归属（ADR-019）：哪个会话属于本站哪个用户。只由 `pearnly_core.assistant.sessions` 读写。"""
from frappe.model.document import Document


class PearnlyAssistantSession(Document):
    pass
