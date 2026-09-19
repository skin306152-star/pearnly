# -*- coding: utf-8 -*-
"""证件号 ↔ DMS 客户的本地对照。

设计原则（来自旧系统界面明写的规则）：**证件号相同默认视为同一客户**，避免重复建档。
客户主数据在外部 MR.ERP DMS 里，本地只记"这个证件号对应 DMS 的哪个客户"，
以及最后一次核对时间。

⚠️ 证件号属于个人敏感信息：不进列表视图，也不写进日志。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class DMSCustomerLink(Document):
    def validate(self):
        self.id_number = (self.id_number or "").strip()
        if not self.id_number:
            frappe.throw(_("ID number is required"))
        if not self.external_customer_ref:
            frappe.throw(_("A DMS customer reference is required; otherwise this link means nothing"))
