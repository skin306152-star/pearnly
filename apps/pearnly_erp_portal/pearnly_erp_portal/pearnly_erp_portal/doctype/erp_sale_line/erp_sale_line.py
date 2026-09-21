# -*- coding: utf-8 -*-
"""销售明细行。数量必须为正；退货用整单 sale_type=refund 表达，不用负数行。"""

from frappe.model.document import Document


class ERPSaleLine(Document):
    pass
