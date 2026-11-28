# -*- coding: utf-8 -*-
"""LINE webhook 事件登记：幂等占坑 + 按用户排队 + 审计。

`event_id` 唯一约束本身就是幂等机制：LINE 重投同一条事件时，插入会失败，
于是重复事件不会被处理第二遍。一对一的事件先落一行 `queued`，由这个 LINE 用户的道
（`pearnly_core.line_lane`）按到达序逐条处理；群组等没有用户的事件照旧当场处理。
"""

import frappe
from frappe.model.document import Document

INDEXES = {
    # 道取下一条：本用户、排队中、按 LINE 时间排
    "line_event_user_status_time": ["line_user_id", "status", "line_timestamp"],
    # 扫描「有排队却没人持道」与按状态清理
    "line_event_status_arrived": ["status", "arrived_at"],
}


class PearnlyLINEEvent(Document):
    pass


def on_doctype_update():
    for index_name, fields in INDEXES.items():
        frappe.db.add_index("Pearnly LINE Event", fields, index_name)
