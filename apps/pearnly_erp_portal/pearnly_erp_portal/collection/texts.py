"""收款规则码 → 给人看的一句话（LINE、CC 与网页「收款」页弹窗共用；码见 `contracts` / `service`）。"""
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
    # 账套、公司、客户（服务层与选账套规则的码；11-12 前这几种只说「Could not record this collection」）
    "express.account_set_several": "Choose the account set",
    "ledger_needed": "Choose the account set",
    "express.company_tax_missing": "The company has no 13-digit tax ID",
    "express.account_set_missing": "No Express account set covers this date",
    "collection.company_not_linked": "This company does not use Express",
    "collection.customer_not_found": "Customer not found",
    contracts.INVOICES_TOO_MANY: "At most {0} invoices",
    contracts.METHODS_TOO_MANY: "At most {0} collection methods",
    # 账本是 MR.ERP 的公司（`collection.mrerp`）
    "collection.mrerp_reading": "Reading from MR.ERP",
    "collection.mrerp_one_method": "Choose one collection method",
    "collection.mrerp_menu_needed": "Choose the MR.ERP collection menu",
}
# 只有写进 MR.ERP 这条会记进收款原因的几个码（Express 那条的原因照旧原样给，一字不变）
MRERP_CODES = ("collection.mrerp_reading", "collection.mrerp_one_method", "collection.mrerp_menu_needed")
LIMITS = {contracts.INVOICES_TOO_MANY: contracts.MAX_INVOICES, contracts.METHODS_TOO_MANY: contracts.MAX_METHODS}


def money(value) -> str:
    return f"{Decimal(str(value or 0)):,.2f}"


def no_book(company, day) -> str:
    """「没有账套」：说出日期与已连接各本的期间（同 ERP 单据，`express.target.company_no_book_text`）。"""
    from pearnly_erp_portal.express import target

    return target.company_no_book_text(company, day)


def periods_text(rows, shown=str) -> str:
    """可录收款的日期段：「会计期间：2024-01-01–2025-12-31 TEST2、…」（:func:`service.periods`）；``shown`` 换日期写法（LINE 用佛历）。"""
    import frappe

    joiner = "、" if str(getattr(frappe.local, "lang", "") or "")[:2] in ("zh", "ja") else ", "
    listed = joiner.join(f"{shown(row['start'])}–{shown(row['end'])} {row.get('short') or row.get('label') or ''}".strip() for row in rows or ())
    return _("Accounting period: {0}").format(listed)


def reason(value) -> str:
    """回执里的原因给人看：电脑回的码有现成说法的换成它（会计期间已在 Express 关账），其余去掉码头照原文。"""
    import re

    from pearnly_core.cc_desktop.wire import ack_rules

    if ack_rules.error_code(value) == "PERIOD_LOCKED":
        return _("express.reason.periodLocked")
    text = str(value or "")
    if text in MRERP_CODES:
        return _(CODES[text])
    if text.startswith(("mrerp.", "ERR_MRERP", "ERR_AUTH", "ERR_TECHNICAL", "ERR_BUSINESS", "ERR_UNEXPECTED",
                        "express.voided_after_write")):
        # 写进 MR.ERP 的结论（`collection.mrerp.settle`）：同 ERP 单据待处理的说法（`express.pending.reason_label`）
        from pearnly_erp_portal.express import pending

        return pending.reason_label("failed", text)
    return re.sub(r"^\[[A-Za-z0-9_]+\]\s*", "", str(value or ""))


def explain(error) -> str:
    code, detail = getattr(error, "code", ""), getattr(error, "detail", {}) or {}
    if code == "collection.date_needed":
        return " · ".join(filter(None, (_("Collection Date"), periods_text(detail.get("periods")) if detail.get("periods")
                                        else "")))
    if code == "express.account_set_missing" and isinstance(detail, dict) and detail.get("company") and detail.get("day"):
        if detail.get("ledger") == "mrerp":
            from pearnly_erp_portal.collection import mrerp

            return mrerp.no_book_text(detail["company"], detail["day"])
        return no_book(detail["company"], detail["day"])
    if code == contracts.AMOUNT_MISMATCH:
        return _(CODES[code]).format(money(detail.get("paid")), money(detail.get("invoices")))
    if code in LIMITS:
        return _(CODES[code]).format(LIMITS[code])
    if code in CODES:
        return _(CODES[code])
    return _("Could not record this collection")
