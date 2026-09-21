# -*- coding: utf-8 -*-
"""公司主档。默认公司只有一个，公司资料页看的就是它。

新架构里"公司 × 产品 = 一个 Site"，所以多数租户这张表只有一行；保留列表是因为
一个法人主体确实可能在这里登记多条，默认那条用于开单与资料页。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ERPCompany(Document):
    def validate(self):
        month = int(self.fiscal_year_start_month or 1)
        if not 1 <= month <= 12:
            frappe.throw(_("The fiscal year start month must be between 1 and 12"))
        if self.is_default:
            for other in frappe.get_all("ERP Company", filters={
                    "is_default": 1, "name": ["!=", self.name or ""]}, pluck="name"):
                frappe.db.set_value("ERP Company", other, "is_default", 0)
