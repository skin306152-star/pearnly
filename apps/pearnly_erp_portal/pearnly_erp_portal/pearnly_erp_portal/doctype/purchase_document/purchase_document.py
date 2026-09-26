"""Purchase records, validated amounts and immutable posted documents."""

from decimal import Decimal

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_erp_portal.purchasing import contracts


class PurchaseDocument(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if ((self.calculation_version or 0) >= 2 or (previous and (previous.calculation_version or 0) >= 2)) and not self.flags.get("purchase_action"):
            if self.is_new() or (previous and previous.status != "draft") or self.status != "draft":
                frappe.throw(_("Document has already been submitted"))

        if (self.fx_rate or 0) <= 0:
            frappe.throw(_("The FX rate must be greater than 0"))
        self._recompute()

    def on_trash(self):
        if (self.calculation_version or 0) >= 2 and self.status != "draft":
            frappe.throw(_("Only Draft documents can be deleted"))

    def _recompute(self):
        """金额一律重算，不接受前端传进来的合计——避免对不上账。

        规则本体在 `purchasing/contracts.py`（纯逻辑、可离线验证），这里只负责把它
        接到单据上：这样网页、LINE、识别入库三条路径用的是同一份金额口径。
        """
        if (self.get("calculation_version") or 0) >= 2:
            from pearnly_erp_portal.purchasing import amounts

            override = None
            if self.amount_override:
                override = {key: self.get("override_" + slot) for key, slot in
                            (("subtotal", "subtotal"), ("discount_total", "discount"),
                             ("vat_amount", "vat"), ("grand_total", "grand"))}
            try:
                totals = amounts.calculate([row.as_dict() for row in self.lines],
                    has_vat=bool(self.has_vat), price_mode=self.price_mode or "exclusive",
                    discount_total=self.discount_total, rounding=self.rounding,
                    amount_override=override,
                    # 旧版本的单据保持原算法（保存时金额不变）：版本 3 起整单折扣先于 VAT，
                    # 版本 4 起含税票的 VAT 从含税合计反算。
                    discount_before_vat=self.calculation_version >= amounts.DISCOUNT_BEFORE_VAT,
                    vat_from_total=self.calculation_version >= amounts.INCLUSIVE_VAT_FROM_TOTAL)
            except (ValueError, KeyError) as exc:
                frappe.throw(_(str(exc)))
            for row, result in zip(self.lines, totals.pop("lines")):
                for field in ("line_no", "line_total", "vat_amount", "wht_amount"):
                    row.set(field, result[field])
            for key, value in totals.items():
                self.set(key, value)
            self.payment_status = contracts.payment_status(self.paid_amount, self.net_payable)
            return

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
