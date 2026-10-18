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
        # 识别引擎（ADR-022）：只认谷歌 / Claude，空当 Claude（用户 2026-10-18：新开户默认 Claude）。
        if (self.ocr_engine or "Claude") not in ("Google", "Claude"):
            frappe.throw(_("Recognition engine must be Google or Claude"))
        self.ocr_engine = self.ocr_engine or "Claude"

    def on_update(self):
        # 识别引擎改了：重排每个产品站点的 set_ocr，写入站点配置 pearnly_ocr_engine（ADR-022），下一份单据起生效。
        if self.has_value_changed("ocr_engine") and not self.is_new():
            from pearnly_control import provisioning

            provisioning.queue_ocr_engine(self.name)
