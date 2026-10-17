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
        # 识别引擎（ADR-022）：只认谷歌 / Claude，空当谷歌。
        if (self.ocr_engine or "Google") not in ("Google", "Claude"):
            frappe.throw(_("Recognition engine must be Google or Claude"))
        self.ocr_engine = self.ocr_engine or "Google"

    def on_update(self):
        # 智能体设置在租户管理后台填 / 改 / 清：推到这个租户的每个产品站点（ADR-019）。
        if self.flags.assistant_changed:
            from pearnly_control import provisioning

            provisioning.queue_assistant_config(self.name)
        # 识别引擎改了：重排每个产品站点的 set_ocr，写入站点配置 pearnly_ocr_engine（ADR-022），下一份单据起生效。
        if self.has_value_changed("ocr_engine") and not self.is_new():
            from pearnly_control import provisioning

            provisioning.queue_ocr_engine(self.name)
