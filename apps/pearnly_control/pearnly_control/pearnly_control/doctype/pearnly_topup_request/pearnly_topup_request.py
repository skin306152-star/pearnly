# -*- coding: utf-8 -*-
"""充值申请：租户提交 → 管理员审核 → 通过才进余额。

状态机是单向的：`pending → approved | rejected`。**审核过的不许再改**
（改钱的动作必须一次性、可追溯），所以状态与金额在审核后都是只读的。

余额的加值不在这里做 —— 走 `pearnly_control.billing.grant()`，
它同时落台账。这里只管"这笔申请被同意了没有"。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class PearnlyTopupRequest(Document):
    def validate(self):
        from pearnly_control import billing_remote
        if billing_remote.enabled() and not self.flags.billing_remote_sync:
            frappe.throw(_("This is a read-only copy of the cloud billing request"))
        if self.status in ("approved", "rejected"):
            before = self.get_doc_before_save()
            if before and before.status in ("approved", "rejected"):
                frappe.throw(_("This top-up request was already reviewed; it cannot be handled twice"))
        amt = float(self.requested_thb or 0)
        if amt <= 0:
            frappe.throw(_("The requested amount must be greater than 0"))
        if amt > 500000:
            frappe.throw(_("A single top-up is capped at ฿500,000"))
