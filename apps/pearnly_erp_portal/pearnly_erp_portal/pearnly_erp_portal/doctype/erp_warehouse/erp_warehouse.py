# -*- coding: utf-8 -*-
"""仓库。同一时间只有一个默认仓库，入库／出库不带仓库时落到它。"""

import frappe
from frappe.model.document import Document


class ERPWarehouse(Document):
    def validate(self):
        if self.is_default:
            others = frappe.get_all(
                "ERP Warehouse",
                filters={"is_default": 1, "name": ["!=", self.name or ""]},
                pluck="name",
            )
            for other in others:
                frappe.db.set_value("ERP Warehouse", other, "is_default", 0)
