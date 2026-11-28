# -*- coding: utf-8 -*-
"""每个 LINE 用户一条道：同一个人的事件一条一条按到达序处理（`pearnly_core.line_lane`）。

一行一个 LINE 用户。`holder` + `lease_until` 是租约：带条件的 UPDATE 影响 1 行才算拿到道，
拿不到的直接退出；持道的跑完一条续一次租，进程死了租约到点由每分钟的扫描接走。
"""

from frappe.model.document import Document


class PearnlyLINELane(Document):
    pass
