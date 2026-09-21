# -*- coding: utf-8 -*-
"""收银台。默认收银台只有一个，收银员不带收银台时落到它。"""

import frappe
from frappe.model.document import Document


class ERPPOSTerminal(Document):
    def validate(self):
        if self.is_default:
            for other in frappe.get_all("ERP POS Terminal", filters={
                    "is_default": 1, "name": ["!=", self.name or ""]}, pluck="name"):
                frappe.db.set_value("ERP POS Terminal", other, "is_default", 0)
