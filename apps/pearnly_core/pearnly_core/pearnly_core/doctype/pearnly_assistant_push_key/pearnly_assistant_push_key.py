"""浏览器推送的 VAPID 密钥（ADR-019 送达通道 2）：每个站点一对，第一次用到时生成；私钥是密码字段（加密存放）。
只由 `pearnly_core.assistant.push` 读写。"""
from frappe.model.document import Document


class PearnlyAssistantPushKey(Document):
    pass
