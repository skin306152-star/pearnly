# -*- coding: utf-8 -*-
"""LINE 多轮会话的中间状态。

一个 LINE 用户在一个租户里**同时只会走一条流程**（发身份证建档、或者订车逐问），
所以一份会话一行：`line_user_id` 就是主键。

和绑定表一样：这张表只存在于某个租户自己的站点里，没有 tenant 字段 ——
跨租户串会话在结构上就不可能发生。
"""

from frappe.model.document import Document


class PearnlyLINESession(Document):
    pass
