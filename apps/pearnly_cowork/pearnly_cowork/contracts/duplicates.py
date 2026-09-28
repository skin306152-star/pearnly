"""Invoice duplicate predicates from deployed ocr_history/queries.py (ec2e5c957e6b).

An exact invoice number wins over a date/seller/amount match. The second tier
also runs when a supplied number did not match, despite the legacy docstring.
Amounts use finite Decimal values, never floating point approximations.
"""
from decimal import Decimal, InvalidOperation

MATCH_FIELDS = {"exact": ["invoice_no"],
                "likely": ["invoice_date", "seller_name", "total_amount"],
                # 有意新增（2026-09-28 Express 压测：同一张油票在两三张照片里、单号读得不一样就查不出）：
                # 同一对方（有效 13 位税号）+ 同日期 + 同金额，不看单号和名称写法。
                "party": ["invoice_date", "party_tax", "total_amount"]}


def amount(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", ""))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def terms(summary):
    return {"invoice_no": (summary.get("invoice_no") or "").strip().lower(),
            "seller_name": (summary.get("seller_name") or "").strip().lower(),
            "invoice_date": str(summary.get("invoice_date") or ""),
            "total_amount": amount(summary.get("total_amount"))}


def eligible(current, level):
    if level == "exact":
        return bool(current["invoice_no"])
    return bool(current["invoice_date"] and current["seller_name"]
                and current["total_amount"] is not None)


def matches(current, previous, level):
    if not eligible(current, level):
        return False
    if level == "exact":
        return (previous.get("invoice_no") or "").lower() == current["invoice_no"]
    return (str(previous.get("invoice_date") or "") == current["invoice_date"]
            and (previous.get("seller_name") or "").lower() == current["seller_name"]
            and amount(previous.get("total_amount")) == current["total_amount"])


def _tax(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits if len(digits) == 13 else ""


def party_terms(summary, party_tax):
    """第三道：对方税号 + 日期 + 金额（与前两道并列，不改旧线上的两道判定）。"""
    return {"party_tax": _tax(party_tax), "invoice_date": str(summary.get("invoice_date") or ""),
            "total_amount": amount(summary.get("total_amount"))}


def party_matches(current, previous_tax, previous):
    return bool(current["party_tax"] and current["invoice_date"] and current["total_amount"] is not None
                and _tax(previous_tax) == current["party_tax"]
                and str(previous.get("invoice_date") or "") == current["invoice_date"]
                and amount(previous.get("total_amount")) == current["total_amount"])
