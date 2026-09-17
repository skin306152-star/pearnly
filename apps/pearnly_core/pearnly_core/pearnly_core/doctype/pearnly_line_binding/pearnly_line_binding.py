# -*- coding: utf-8 -*-
"""LINE 用户与本租户账号的绑定。

这张表只存在于某个租户自己的站点里，所以"哪个租户的 LINE"由所在站点决定，
不需要 tenant 字段 —— 跨租户串号在结构上就不可能发生。
"""

from frappe.model.document import Document


class PearnlyLINEBinding(Document):
    pass
