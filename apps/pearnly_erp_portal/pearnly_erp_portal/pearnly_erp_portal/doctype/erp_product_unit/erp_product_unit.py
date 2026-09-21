# -*- coding: utf-8 -*-
"""商品的销售单位。换算系数必须大于 0（旧线上同样约束）。"""

from frappe.model.document import Document
from frappe.utils import flt


class ERPProductUnit(Document):
    def validate(self):
        if flt(self.factor_to_base) <= 0:
            self.factor_to_base = 1
