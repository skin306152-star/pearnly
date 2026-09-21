# -*- coding: utf-8 -*-
"""客户（销售买单方）。

买方类型决定哪些字段有意义（旧线上同样口径）：匿名买家没有税号；只有公司才有分店。
归一化在服务层做，落库前就把不该有的值清掉，避免"匿名客户带税号"这种脏数据。
"""

import frappe
from frappe.model.document import Document


class ERPCustomer(Document):
    def validate(self):
        if self.party_type != "company":
            self.tax_id = None
            self.branch_type = None
            self.branch_no = None
        elif self.branch_type != "branch":
            self.branch_no = None
        if self.party_type == "anonymous":
            self.tax_id = None
