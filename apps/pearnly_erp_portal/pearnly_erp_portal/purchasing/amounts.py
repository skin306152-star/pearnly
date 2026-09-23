"""Purchase invoice amounts; fixed legacy contract, Decimal and half-even cents."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

CENT = Decimal("0.01")


def decimal(value, *, signed=False):
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid value") from None
    if not result.is_finite() or (not signed and result < 0):
        raise ValueError("Invalid value")
    return result


def money(value):
    return decimal(value, signed=True).quantize(CENT, rounding=ROUND_HALF_EVEN)


def calculate(lines, *, has_vat=True, price_mode="exclusive", discount_total=0,
              rounding=0, amount_override=None):
    if not isinstance(lines, list) or not lines:
        raise ValueError("Items are required")
    if price_mode not in ("exclusive", "inclusive"):
        raise ValueError("Invalid value")
    subtotal = vat = wht = Decimal(0)
    normalized = []
    for index, line in enumerate(lines, 1):
        qty, price, discount = (decimal(line.get(key)) for key in ("qty", "unit_price", "discount"))
        vat_rate = decimal(line.get("vat_rate", 7)) if has_vat else Decimal(0)
        wht_rate = decimal(line.get("wht_rate"))
        if qty <= 0 or vat_rate > 100 or wht_rate > 100:
            raise ValueError("Invalid value")
        factor = 1 + vat_rate / 100 if price_mode == "inclusive" else Decimal(1)
        net = money(qty * price / factor) - money(discount / factor)
        if net < 0:
            raise ValueError("Discount cannot exceed the amount")
        line_vat, line_wht = money(net * vat_rate / 100), money(net * wht_rate / 100)
        subtotal += net
        vat += line_vat
        wht += line_wht
        normalized.append({**line, "line_no": index, "qty": qty, "unit_price": price,
                           "discount": discount, "line_total": net, "vat_rate": vat_rate,
                           "wht_rate": wht_rate, "vat_amount": line_vat, "wht_amount": line_wht})
    discount = money(decimal(discount_total))
    adjustment = money(rounding)
    if discount > subtotal:
        raise ValueError("Discount cannot exceed the amount")
    grand = money(subtotal - discount + vat + adjustment)
    if amount_override:
        subtotal, discount, vat, grand = (money(decimal(amount_override[key])) for key in
                                         ("subtotal", "discount_total", "vat_amount", "grand_total"))
        adjustment = money(grand - (subtotal - discount + vat))
        if discount > subtotal or abs(adjustment) > CENT:
            raise ValueError("Totals do not match")
    payable = money(grand - wht)
    if payable < 0:
        raise ValueError("Invalid value")
    return {"subtotal": money(subtotal), "discount_total": discount, "vat_amount": money(vat),
            "wht_amount": money(wht), "rounding": adjustment, "grand_total": grand,
            "net_payable": payable, "lines": normalized}
