"""收款规则码 → 给人看的一句话（LINE 收款与网页「我的收款」编辑弹窗共用；码见 `contracts` / `service`）。"""
from decimal import Decimal

from frappe import _

from pearnly_erp_portal.collection import contracts

CODES = {
    contracts.OVER_COLLECTED: "More than the amount due. Enter the amount again",
    contracts.AMOUNT_INVALID: "Enter a valid amount",
    contracts.AMOUNT_MISMATCH: "Total {0} does not match {1}",
    contracts.INVOICES_EMPTY: "Choose at least one invoice",
    contracts.INVOICE_NOT_FOUND: "This invoice is no longer open",
    contracts.INVOICE_DUPLICATE: "This invoice is no longer open",
    contracts.METHOD_INVALID: "This collection method is not available",
    contracts.METHOD_DUPLICATE: "This collection method is not available",
    contracts.CASH_ACCOUNT_MISSING: "Cash is not set up for this account set",
    contracts.DISCOUNT_ONLY: "Enter the amount received",
    contracts.VOUCHER_MISSING: "Send a photo of the transfer slip",
    contracts.CHANGED: "The collection changed. Check it again",
    contracts.NOT_DRAFT: "This collection cannot be changed",
    contracts.NOT_WITHDRAWABLE: "This collection cannot be withdrawn",
    contracts.BUSY: "CC Desktop is writing this collection",
    "collection.cc_update_needed": "CC Desktop needs an update",
    "collection.voucher_invalid": "Send a photo of the transfer slip",
    contracts.SALESMAN_INVALID: "This salesman is not available",
    contracts.DEPARTMENT_INVALID: "This department is not available",
    "express.masters_reading": "Reading from Express",
}


def money(value) -> str:
    return f"{Decimal(str(value or 0)):,.2f}"


def explain(error) -> str:
    code, detail = getattr(error, "code", ""), getattr(error, "detail", {}) or {}
    if code == contracts.AMOUNT_MISMATCH:
        return _(CODES[code]).format(money(detail.get("paid")), money(detail.get("invoices")))
    if code in CODES:
        return _(CODES[code])
    return _("Could not record this collection")
