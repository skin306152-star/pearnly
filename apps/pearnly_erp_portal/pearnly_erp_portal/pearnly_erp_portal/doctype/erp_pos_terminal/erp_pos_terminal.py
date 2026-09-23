# -*- coding: utf-8 -*-
"""收银台：属于公司、卖这家公司某个仓库里的货（官方 POS 配置也是 `company` + `warehouse`）。

每家公司各自有一个默认收银台（收银员不带收银台时落到自己公司的那个）。
"""

import frappe
from frappe.model.document import Document


class ERPPOSTerminal(Document):
    def validate(self):
        if self.warehouse:
            owner = frappe.db.get_value("ERP Warehouse", self.warehouse, "company")
            if owner and self.company and owner != self.company:
                frappe.throw(frappe._("That warehouse belongs to another company"))
        if self.is_default:
            for other in frappe.get_all("ERP POS Terminal", filters={
                    "company": self.company, "is_default": 1,
                    "name": ["!=", self.name or ""]}, pluck="name"):
                frappe.db.set_value("ERP POS Terminal", other, "is_default", 0)
