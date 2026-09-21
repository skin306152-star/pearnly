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

from decimal import Decimal

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_erp_portal.purchasing import contracts


class PurchaseDocument(Document):
    def validate(self):
        if (self.fx_rate or 0) <= 0:
            frappe.throw(_("The FX rate must be greater than 0"))
        self._recompute()

    def _recompute(self):
        """金额一律重算，不接受前端传进来的合计——避免对不上账。

        规则本体在 `purchasing/contracts.py`（纯逻辑、可离线验证），这里只负责把它
        接到单据上：这样网页、LINE、识别入库三条路径用的是同一份金额口径。
        """
        if not self.lines:
            frappe.throw(_("A purchase document needs at least one line"))

        try:
            totals = contracts.compute_totals(
                lines=[
                    {"qty": line.qty, "unit_price": line.unit_price,
                     "discount": line.discount, "vat_rate": line.vat_rate}
                    for line in self.lines
                ],
                has_vat=bool(self.has_vat),
                doc_kind=self.doc_kind,
                discount_total=self.discount_total,
                wht_amount=self.wht_amount,
                rounding=self.rounding,
            )
        except ValueError as exc:
            frappe.throw(str(exc))

        # 行小计回填，明细与合计对得上。
        for line in self.lines:
            line.line_total = contracts.q2(
                Decimal(str(line.qty or 0)) * Decimal(str(line.unit_price or 0))
            ) - contracts.q2(line.discount)

        self.subtotal = totals["subtotal"]
        self.vat_amount = totals["vat_amount"]
        self.grand_total = totals["grand_total"]
        self.net_payable = totals["net_payable"]
        self.payment_status = contracts.payment_status(self.paid_amount, self.net_payable)
