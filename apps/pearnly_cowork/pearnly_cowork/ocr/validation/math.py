"""Decimal amount reconciliation from the deployed invoice trigger contract."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from functools import wraps

from pearnly_cowork.ocr.money import normalize_money


def _amounts(value):
    if isinstance(value, Decimal):
        if value.is_finite():
            yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _amounts(item)
    elif isinstance(value, dict):
        yield from _amounts(tuple(value.values()))
    elif hasattr(value, "subtotal"):
        for field in ("subtotal", "vat", "total_amount", "discount"):
            yield from _amounts(getattr(value, field, None))
        yield from _amounts(getattr(value, "items", None) or [])
        yield from _amounts(getattr(value, "additional_invoices", None) or [])
    else:
        amount = normalize_money(value)
        if amount is not None:
            yield amount


def exact_money(function):
    """Keep finite input precision local; never change process Decimal context."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        values = list(_amounts((args, kwargs)))
        integers = max((max(0, value.adjusted() + 1) for value in values), default=1)
        fraction = max((max(0, -value.as_tuple().exponent) for value in values), default=0)
        with localcontext() as context:
            context.prec = max(28, integers + fraction + len(str(len(values))) + 12)
            return function(*args, **kwargs)
    return wrapped


def _strict_money(value):
    if not value:
        return None
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("non-finite amount")
    return amount


def check_amount_math(invoice, *, tolerance=Decimal("0.5")) -> str | None:
    """Source's two discount interpretations, with strict core money parsing.

    Core amount commas/currency are invalid here, just as old float parsing;
    only discount uses the source's permissive normalize_money. Missing core
    fields skip this gate. Non-finite values now fail instead of slipping past
    float comparisons. All monetary arithmetic and formatting use Decimal.
    """
    tolerance = Decimal(str(tolerance))
    if not tolerance.is_finite() or tolerance < 0:
        raise ValueError("amount tolerance must be finite and nonnegative")
    try:
        sub = _strict_money(invoice.subtotal)
        vat = _strict_money(invoice.vat)
        total = _strict_money(invoice.total_amount)
        discount = normalize_money(getattr(invoice, "discount", None)) or Decimal("0")
        # 票面印了预扣税额时，合计可能是扣税后的实付额（ADR-013 共用会计规则，2026-09-27；旧线上没有这一式）。
        withheld = normalize_money(getattr(invoice, "wht_amount", None)) or Decimal("0")
    except (InvalidOperation, ValueError, TypeError):
        return "amount field not numeric (parse failed)"
    if sub is None or vat is None or total is None:
        return None
    return _reconcile(sub, vat, total, discount, tolerance, withheld)


@exact_money
def _reconcile(sub, vat, total, discount, tolerance, withheld=Decimal("0")):
    diff = min(abs(sub + vat - total), abs(sub - discount + vat - total))
    if withheld > 0:
        diff = min(diff, abs(sub - discount + vat - withheld - total))
    if diff > tolerance:
        return (
            f"amount math fail: subtotal {sub:.2f} - discount {discount:.2f} "
            f"+ vat {vat:.2f} != total {total:.2f} "
            f"(diff {diff:.2f} > {tolerance})"
        )
    return None
