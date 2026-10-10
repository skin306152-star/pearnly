"""智能体工具调用的执行记录（ADR-019）：唯一的 tool_use_id 保证同一次调用只执行一遍，也留作审计。"""
from frappe.model.document import Document


class PearnlyAssistantToolRun(Document):
    pass
