# -*- coding: utf-8 -*-
"""租户（商业客户）。

租户是"一家公司"，不等于它的域名、也不等于它的登录账号——
公司改名不迁移、不换身份。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class PearnlyTenant(Document):
    def validate(self):
        self.tenant_name = (self.tenant_name or "").strip()
        if not self.tenant_name:
            frappe.throw(_("租户名称必填"))
