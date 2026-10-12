"""浏览器推送订阅（ADR-019 送达通道 2）：一台设备的浏览器一条，按推送地址的 sha256 唯一，属于最后认领它的登录人。
只由 `pearnly_core.assistant.push` 读写。"""
from frappe.model.document import Document


class PearnlyAssistantPushSubscription(Document):
    pass
