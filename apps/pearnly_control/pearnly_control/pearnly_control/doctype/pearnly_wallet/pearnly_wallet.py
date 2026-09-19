# -*- coding: utf-8 -*-
"""租户钱包：**余额的唯一存放处**。

三套产品（Cowork / ERP / DMS）共用这一份余额 —— 它们各自是独立站点，
只有控制面是所有租户、所有产品都认识的地方，所以钱只能记在这。

表按 tenant 唯一（`autoname: field:tenant`）：一个租户一行，不会有第二本账。

⚠️ **余额只被 `pearnly_control.billing` 改**。别处直接 `doc.balance_thb = x`
会让台账与余额对不上（老产品的铁律 #26：钱写入必须同时落余额与流水）。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class PearnlyWallet(Document):
    def validate(self):
        if not self.tenant:
            frappe.throw(_("A wallet must belong to a tenant"))
