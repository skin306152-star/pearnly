# -*- coding: utf-8 -*-
"""收银台：属于公司、卖这家公司某个仓库里的货（官方 POS 配置也是 `company` + `warehouse`）。

每家公司各自有一个默认收银台（收银员不带收银台时落到自己公司的那个）。
"""

import frappe
from frappe.model.document import Document


class ERPPOSTerminal(Document):
    def validate(self):
        if self.warehouse:
            owner, active = frappe.db.get_value("ERP Warehouse", self.warehouse, ["company", "is_active"]) or (None, 0)
            if owner and self.company and owner != self.company:
                frappe.throw(frappe._("That warehouse belongs to another company"))
            # 收银台只能绑启用着的仓（停用的仓卖货时会被拒，G16）；只在新建或换仓时核，仓后来停用的旧收银台改名照样能存
            if not active and (self.is_new() or self.has_value_changed("warehouse")):
                from pearnly_erp_portal.inventory import warehouses

                frappe.throw(frappe._(warehouses.INACTIVE))
        if self.is_default:
            for other in frappe.get_all("ERP POS Terminal", filters={
                    "company": self.company, "is_default": 1,
                    "name": ["!=", self.name or ""]}, pluck="name"):
                frappe.db.set_value("ERP POS Terminal", other, "is_default", 0)
