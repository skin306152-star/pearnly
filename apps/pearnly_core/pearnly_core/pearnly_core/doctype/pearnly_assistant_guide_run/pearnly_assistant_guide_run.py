from frappe.model.document import Document


class PearnlyAssistantGuideRun(Document):
    """一次引导的服务端记录（多产品共用，`pearnly_core.assistant.guide.run`）：哪段对话、哪项业务、当时的公司 / 账套 / 月份、
    每步答案、走到哪一步。随对话删。"""
