# -*- coding: utf-8 -*-
"""Pearnly Identity：把外部身份 (issuer, subject) 绑定到本地账号。

这张表是"谁是谁"的唯一依据：**不按邮箱匹配，不自动开户**。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class PearnlyIdentity(Document):
    def validate(self):
        if not self.issuer or not self.subject:
            frappe.throw(_("Both issuer and subject are required"))

        # 复合唯一：(issuer, subject) 只能对应一个本地账号。
        # Frappe 的字段级 unique 不做复合约束，所以在这里显式拦。
        duplicate = frappe.db.get_value(
            "Pearnly Identity",
            {"issuer": self.issuer, "subject": self.subject, "name": ("!=", self.name)},
            ["name", "user"],
            as_dict=True,
        )
        if duplicate:
            frappe.throw(
                _("This identity is already bound to account {0} (record {1})").format(duplicate.user, duplicate.name)
            )

        if not frappe.db.exists("User", self.user):
            frappe.throw(_("No such local account: {0}").format(self.user))
