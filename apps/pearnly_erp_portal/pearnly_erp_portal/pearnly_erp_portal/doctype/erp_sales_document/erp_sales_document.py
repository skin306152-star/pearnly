# -*- coding: utf-8 -*-
"""销售单据（记录，不是税务发票）。金额一律由纯规则层算，服务端与前端不各算一份。

金额在这张单落库前重算一次：直连 REST 的、以后换前端的，都得过这一关。
金额规则本身在 `pearnly_erp_portal/sales/contracts.py`（不连库、可离线断言）。
"""

import frappe
from frappe.model.document import Document

from pearnly_erp_portal.sales import contracts


class ERPSalesDocument(Document):
    def validate(self):
        if self.is_new() or self.status in contracts.EDITABLE_STATUSES:
            self._apply_totals()

    def _apply_totals(self):
        totals = contracts.compute_totals(
            [{
                "description": line.description,
                "product": line.product,
                "qty": line.qty,
                "unit": line.unit,
                "unit_price": line.unit_price,
                "discount": line.discount,
                "discount_pct": line.discount_pct,
                "vat_applicable": bool(line.vat_applicable),
            } for line in self.lines],
            vat_rate=self.vat_rate if self.has_vat else 0,
            header_discount_amount=self.header_discount,
            price_includes_vat=bool(self.get("price_includes_vat")),
        )
        for row, line in zip(self.lines, totals["lines"]):
            row.line_total = line["line_total"]
            row.discount = line["discount"]
            row.line_no = line["line_no"]
        self.subtotal = totals["subtotal"]
        self.discount_total = totals["discount_total"]
        self.vat_amount = totals["vat_amount"]
        self.grand_total = totals["grand_total"]
