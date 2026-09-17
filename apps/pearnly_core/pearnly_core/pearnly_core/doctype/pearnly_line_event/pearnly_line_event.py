# -*- coding: utf-8 -*-
"""LINE webhook 事件登记：幂等占坑 + 审计。

`event_id` 唯一约束本身就是幂等机制：LINE 重投同一条事件时，插入会失败，
于是重复事件不会被处理第二遍。
"""

from frappe.model.document import Document


class PearnlyLINEEvent(Document):
    pass
