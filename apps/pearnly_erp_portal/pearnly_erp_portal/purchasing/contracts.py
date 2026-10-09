"""采购金额与流转的纯规则：不依赖 Frappe、IO 或上层流程。

金额一律 Decimal，四舍五入到分；付款状态由实付与净应付的差决定，不接受前端传来
的矛盾组合（旧线上同样口径）。
"""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TWO = Decimal("0.01")

DRAFT = "draft"
SUBMITTED = "submitted"
VOID = "void"

UNPAID = "unpaid"
PARTIAL = "partial"
PAID = "paid"

# 允许的流转：草稿→提交、草稿→作废、提交→作废。提交后不能改回草稿。
ALLOWED_TRANSITIONS = {
    DRAFT: (SUBMITTED, VOID),
    SUBMITTED: (VOID,),
    VOID: (),
}


def q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(TWO, rounding=ROUND_HALF_UP)


def amount(value, *, field: str = "amount", allow_zero: bool = True) -> Decimal:
    if value in (None, ""):
        if allow_zero:
            return Decimal("0")
        raise ValueError(f"{field}: required")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field}: invalid") from None
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field}: invalid")
    return q2(result)


def compute_totals(*, lines, has_vat, doc_kind, discount_total=0, wht_rate=0,
                   wht_amount=None, rounding=0):
    """把明细算成单据金额。

    - 行小计 = 数量 × 单价 − 行折扣；
    - 有 VAT 时按行税率分别算，再合计；
    - 净应付 = 含税合计 − WHT − 单据折扣 + 取整；
    - WHT 可以按服务类默认税率算，也可以直接给金额（旧线上两者都支持）。
    """
    if not lines:
        raise ValueError("lines: required")

    subtotal = Decimal("0")
    vat_total = Decimal("0")
    for index, line in enumerate(lines, start=1):
        qty = amount(line.get("qty"), field=f"line {index} qty")
        price = amount(line.get("unit_price"), field=f"line {index} price")
        line_discount = amount(line.get("discount"), field=f"line {index} discount")
        if qty <= 0:
            # 旧线上行数量必须大于 0：0 数量的行只会让合计对不上。
            raise ValueError(f"line {index}: quantity must be greater than 0")
        gross = qty * price
        line_total = q2(gross) - q2(line_discount)
        if line_total < 0:
            raise ValueError(f"line {index}: discount exceeds the line")
        subtotal += line_total
        if has_vat:
            rate = amount(line.get("vat_rate", 7), field=f"line {index} vat")
            vat_total += q2(line_total * rate / Decimal("100"))

    subtotal = q2(subtotal)
    vat_total = q2(vat_total) if has_vat else Decimal("0")
    grand_total = q2(subtotal + vat_total)

    if wht_amount in (None, ""):
        wht = q2(subtotal * amount(wht_rate) / Decimal("100")) if wht_rate else Decimal("0")
    else:
        wht = amount(wht_amount, field="wht_amount")

    discount = amount(discount_total, field="discount_total")
    adjust = amount(rounding, field="rounding")
    net_payable = q2(grand_total - wht - discount + adjust)
    if net_payable < 0:
        raise ValueError("net_payable: negative")

    return {
        "doc_kind": doc_kind,
        "subtotal": subtotal,
        "vat_amount": vat_total,
        "grand_total": grand_total,
        "wht_amount": wht,
        "discount_total": discount,
        "rounding": adjust,
        "net_payable": net_payable,
    }


def payment_status(paid, net_payable) -> str:
    """付款状态跟着实付金额走，不让人手填出一个矛盾的组合。"""
    paid = q2(paid)
    net_payable = q2(net_payable)
    if paid <= 0:
        return UNPAID
    if paid < net_payable:
        return PARTIAL
    return PAID


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, ())


def duplicate_key(supplier: str, doc_no: str):
    """去重判据：同一供应商 + 同一单据号视为同一张单。

    旧线上是"同一账套 + 供应商 + 单据号"，**不看单据类型** —— 同一家供应商的同一个
    单据号不会因为记成"货物"和"服务"就变成两张单。没有单据号时不去重（很多手写单据
    确实没号）。
    """
    supplier = (supplier or "").strip().lower()
    doc_no = (doc_no or "").strip().lower()
    if not doc_no or not supplier:
        return None
    return f"{supplier}:{doc_no}"


def detail_key(error):
    """`compute_totals` 的校验句 → 给人看的词条 `(键, 参数)`（`erp.detail.*`，2026-10-09）；认不出的回 `(None, ())`，
    调用方只报「值无效」，不把英文句子拼给用户。"""
    text = str(error or "")
    line = re.fullmatch(r"line (\d+): quantity must be greater than 0", text)
    if line:
        return "erp.detail.line_qty", (line.group(1),)
    if re.fullmatch(r"line \d+: discount exceeds the line", text):
        return "erp.detail.line_discount", ()
    return {"lines: required": ("erp.detail.lines", ()),
            "net_payable: negative": ("erp.detail.net_payable", ())}.get(text, (None, ()))
