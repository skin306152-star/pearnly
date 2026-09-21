# -*- coding: utf-8 -*-
"""班次。同一收银台同一时间只能有一个开班（唯一性在服务端用锁判断）。"""

from frappe.model.document import Document


class ERPShift(Document):
    pass
