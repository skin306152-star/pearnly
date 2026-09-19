# -*- coding: utf-8 -*-
"""采购单。

这是 ERP 产品的第一条纵向切片：把旧系统 purchase_docs / purchase_lines 的
**业务含义**（单据类型、外部单据号、币种与汇率、税额分列、审批与付款状态）
映射过来，而不是照搬每一列。

刻意**没有**搬过来的：`ocr_raw`（OCR 原始报文，属于流转中间态）、
`requester_user_id`（内部外键，用链接表达更清楚）、`amount_override`（阈值开关，
等有真实需求再补）。这些在旧系统里是流程痕迹，不是业务字段。

金额一律用 Decimal 计算后写入；日期用 Date/Datetime，时间戳存 UTC。
"""

from decimal import Decimal, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

TWO = Decimal("0.01")


def q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(TWO, rounding=ROUND_HALF_UP)


class PurchaseDocument(Document):
    def validate(self):
        self._validate_amounts()
        self._recompute()

    def _validate_amounts(self):
        if not self.lines:
            frappe.throw(_("A purchase document needs at least one line"))
        if flt(self.fx_rate) <= 0:
            frappe.throw(_("The FX rate must be greater than 0"))
        for line in self.lines:
            if flt(line.qty) <= 0:
                frappe.throw(_("Row {0}: quantity must be greater than 0").format(line.idx))
            if flt(line.unit_price) < 0:
                frappe.throw(_("Row {0}: unit price cannot be negative").format(line.idx))

    def _recompute(self):
        """金额一律重算，不接受前端传进来的合计——避免对不上账。"""
        subtotal = Decimal("0")
        vat_total = Decimal("0")

        for line in self.lines:
            gross = Decimal(str(line.qty)) * Decimal(str(line.unit_price))
            line_total = q2(gross) - q2(line.discount)
            line.line_total = line_total
            subtotal += line_total
            if self.has_vat:
                vat_total += q2(line_total * Decimal(str(line.vat_rate)) / Decimal("100"))

        self.subtotal = q2(subtotal)
        self.vat_amount = q2(vat_total) if self.has_vat else Decimal("0")
        self.grand_total = q2(Decimal(str(self.subtotal)) + Decimal(str(self.vat_amount)))
        self.net_payable = q2(
            Decimal(str(self.grand_total))
            - Decimal(str(self.wht_amount or 0))
            - Decimal(str(self.discount_total or 0))
            + Decimal(str(self.rounding or 0))
        )

        # 付款状态跟着实付金额走，不让人手填出一个矛盾的组合
        paid = q2(self.paid_amount)
        if paid <= 0:
            self.payment_status = "unpaid"
        elif paid < self.net_payable:
            self.payment_status = "partial"
        else:
            self.payment_status = "paid"
