"""明细行合计与票面金额对不对得上：推送前的明细闸与识别后的复核提示同一个判定（单一来源）。

纯计算，不依赖框架、不做 IO。容差 1.00 吸收 OCR 逐行四舍五入；行合计等于税前额，或（泰国零售常见的逐行含税）
等于含税合计，都算对得上。
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

TOLERANCE = Decimal("1.00")
_CENT = Decimal("0.01")


def _money(value):
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def matched_target(line_sum: Decimal, base: Decimal, total: Decimal | None = None) -> str | None:
    """行合计对上了哪一个：'base'（税前额）/ 'total'（含税合计，且合计大于税前额）/ None（都对不上）。"""
    if abs(_cents(line_sum) - _cents(base)) <= TOLERANCE:
        return "base"
    if total is not None and _cents(total) > _cents(base) and abs(_cents(line_sum) - _cents(total)) <= TOLERANCE:
        return "total"
    return None


def review_status(fields: dict) -> str | None:
    """识别结果的明细对账（复核提示用）：'mismatch' / 'incomplete'；没有明细、没有金额或对得上回 None。

    与推送的 `extract_line_items` 同口径：金额缺了用数量 × 单价补，品名或金额都没有就算不完整。
    """
    raw = fields.get("items") if isinstance(fields, dict) else None
    if not isinstance(raw, list) or not raw:
        return None
    subtotal = _money(fields.get("subtotal"))
    total = _money(fields.get("total_amount"))
    discount = _money(fields.get("discount")) or Decimal(0)
    base = subtotal - discount if subtotal is not None else None
    if base is None and total is not None:
        vat = _money(fields.get("vat")) or Decimal(0)
        base = total - vat
    if base is None:
        return None
    line_sum = Decimal(0)
    for item in raw:
        if not isinstance(item, dict):
            return "incomplete"
        amount = _money(item.get("subtotal") if item.get("subtotal") not in (None, "") else item.get("amount"))
        if amount is None:
            qty, price = _money(item.get("qty")), _money(item.get("price") or item.get("unit_price"))
            amount = qty * price if qty is not None and price is not None else None
        if not str(item.get("name") or "").strip() or amount is None:
            return "incomplete"
        line_sum += amount
    # 票面折扣行常被读成负数明细：行合计对上未扣折扣的小计也算对得上；小计印的是折后数时，行合计 − 折扣 ≈ 小计
    # 同样对得上（与推送的明细闸同口径，`push/express/common.extract_line_items`）。
    if matched_target(line_sum, base, total) or (subtotal is not None and matched_target(line_sum, subtotal)) or (
            subtotal is not None and discount and matched_target(line_sum - discount, subtotal)):
        return None
    return "mismatch"


__all__ = ["TOLERANCE", "matched_target", "review_status"]
