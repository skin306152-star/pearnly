# -*- coding: utf-8 -*-
"""身份证客户录入（DMS 的核心业务对象）。

对应旧系统界面上的四步：上传证件 → 客户匹配 → 资料确认 → 推送 DMS。

状态机与 Cowork 录入同一套纪律：不能跳步、不能回退、没确认不能推送。

    uploaded → recognized → matched → confirmed → pushed / failed

推送状态的真值在 `erp_push_logs`（trigger='id_card'），这里只存引用——
与旧系统"推送状态唯一源"的约定一致，不另建第二套。
"""

import frappe
from frappe import _
from frappe.model.document import Document

ORDER = ["uploaded", "recognized", "matched", "confirmed", "pushed"]


class DMSIDCardIntake(Document):
    def validate(self):
        self._validate_fields()
        self._guard_status()

    def _validate_fields(self):
        if not self.external_ref and self.status in ("confirmed", "pushed"):
            frappe.throw(_("A DMS customer reference is required before confirming or pushing"))
        if self.match_result not in ("unknown", "existing", "new"):
            frappe.throw(_("Match result must be unmatched / existing customer / new customer"))

    def _guard_status(self):
        if self.is_new():
            if self.status not in ("uploaded", "recognized"):
                frappe.throw(_("A new record can only start as uploaded or recognized"))
            return

        before = frappe.db.get_value(self.doctype, self.name, "status")
        if before in ORDER and self.status in ORDER:
            if ORDER.index(self.status) < ORDER.index(before):
                frappe.throw(_("Status cannot go backwards: {0} → {1}").format(before, self.status))

        # 与 Cowork 同一条纪律：没人工确认过的资料不能推送
        if self.status == "pushed" and before not in ("confirmed", "pushed"):
            frappe.throw(_("The details are not confirmed yet, so it cannot be pushed to DMS"))

        if self.status == "confirmed" and not self.external_ref:
            frappe.throw(_("Customer matching is required before confirming"))
