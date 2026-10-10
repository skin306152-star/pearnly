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
            frappe.throw(_("Company name is required"))
        from pearnly_control.onboarding import assistant

        assistant.validate(self)

    def on_update(self):
        # 智能体设置在租户管理后台填 / 改 / 清：推到这个租户的每个产品站点（ADR-019）。
        if self.flags.assistant_changed:
            from pearnly_control import provisioning

            provisioning.queue_assistant_config(self.name)
