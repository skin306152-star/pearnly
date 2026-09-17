# -*- coding: utf-8 -*-
"""识别记录（Cowork 录入工作台的核心业务对象）。

对应旧系统界面上的：录入工作台 → 识别记录 → 推送日志。

状态机是这条链路的骨架，**不允许跳步**：

    draft ──识别完成──> recognized ──人工确认──> confirmed ──排队──> queued
                                                                    │
                                          pushed <──推送成功────────┘
                                             └──> failed / rejected

金额一律服务端重算（与采购单同一条规则）；识别原始输出单独存一份用于留证，
它**不是**业务字段，确认后的字段才是。
"""

from decimal import Decimal, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document

TWO = Decimal("0.01")

PUSH_CHANNELS = ("none", "companion", "cloud")


def q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(TWO, rounding=ROUND_HALF_UP)


class IntakeRecord(Document):
    def validate(self):
        self._validate_direction()
        self._recompute()
        self._guard_status()

    def _validate_direction(self):
        if self.direction not in ("purchase", "sales"):
            frappe.throw(_("方向只能是进项或销项"))
        # 注意：两边都为空时不能判成"相等"——那会把没填税号的正常单据全拦下来。
        # 只有两边都填了且相同才是真的填错。
        mine = (self.company_tax_id or "").strip()
        theirs = (self.counterparty_tax_id or "").strip()
        if self.direction == "purchase" and mine and theirs and mine == theirs:
            frappe.throw(_("进项的对方税号不能等于自己"))

    def _recompute(self):
        net = Decimal(str(self.net_amount or 0))
        vat = Decimal(str(self.vat_amount or 0))
        self.grand_total = q2(net + vat)

    def _guard_status(self):
        """状态只能沿链路往前走；推送必须有确认过的单据。"""
        if self.is_new():
            if self.status not in ("draft", "recognized"):
                frappe.throw(_("新记录只能从 draft 或 recognized 开始"))
            return

        before = frappe.db.get_value(self.doctype, self.name, "status")
        order = ["draft", "recognized", "confirmed", "queued", "pushed"]
        if before in order and self.status in order:
            if order.index(self.status) < order.index(before):
                frappe.throw(_("状态不能回退：{0} → {1}").format(before, self.status))

        if self.status in ("queued", "pushed") and before not in ("confirmed", "queued", "pushed"):
            frappe.throw(_("还没确认的单据不能推送"))

        if self.push_channel not in PUSH_CHANNELS:
            frappe.throw(_("推送渠道不合法"))
