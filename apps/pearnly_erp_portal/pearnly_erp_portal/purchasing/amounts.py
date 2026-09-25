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


# 计算版本 3（2026-09-26 起新建的单据）：整单折扣先扣、再按折后金额算 VAT，VAT 整单一次取整 —— 照 MR.ERP
# （用户给的泰国 ERP 源码 `mrerp-source` `src/appe/component/javascript/allform.js:650-721`：
# `beforevat = grandtotal − discount`，`vat = beforevat × rate`；采购、销售同一函数）与本仓销售。
# 版本 2 是「先算 VAT、后扣折扣」（旧线上手工录入口径），已有单据按它继续算，保存时金额不变。
DISCOUNT_BEFORE_VAT = 3


def _allocate(total, weights):
    """按权重分摊一笔钱，尾差落在最后一行（每行两位小数）。"""
    base = sum(weights, Decimal(0))
    shares, remaining = [], total
    for index, weight in enumerate(weights):
        share = remaining if index == len(weights) - 1 else (
            money(total * weight / base) if base else Decimal(0))
        share = min(share, remaining) if index < len(weights) - 1 else share
        shares.append(share)
        remaining -= share
    return shares


def calculate(lines, *, has_vat=True, price_mode="exclusive", discount_total=0,
              rounding=0, amount_override=None, discount_before_vat=False):
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
    exclusive_discount = discount
    if discount_before_vat and normalized:
        # 整单折扣按票面口径输入、原样存（含税票就是含税的折扣，与行折扣同一口径）；按各行金额
        # 分摊、换成不含税后从 VAT 基数里扣掉。存的是输入值，所以反复保存重算结果不变。
        factors = [1 + row["vat_rate"] / 100 if price_mode == "inclusive" else Decimal(1) for row in normalized]
        shares = _allocate(discount, [row["line_total"] * factor for row, factor in zip(normalized, factors)])
        exclusive_discount, bases = Decimal(0), []
        for row, share, factor in zip(normalized, shares, factors):
            exclusive = money(share / factor) if discount else Decimal(0)
            if exclusive > row["line_total"]:
                raise ValueError("Discount cannot exceed the amount")
            exclusive_discount += exclusive
            bases.append(row["line_total"] - exclusive)
        # VAT 按整单（同税率一组）算一次再取整，与票面一致 —— 进项税要与供应商税票分毫不差；
        # 逐行取整再相加，明细多时会差几分（MR.ERP 不含税票也是整单一次，`allform.js:718`）。
        # 各行的 VAT 按基数分摊，尾差落在该税率最后一行。
        vat = Decimal(0)
        for rate in {row["vat_rate"] for row in normalized}:
            group = [(row, base) for row, base in zip(normalized, bases) if row["vat_rate"] == rate]
            total_vat = money(sum((base for _row, base in group), Decimal(0)) * rate / 100)
            for (row, _base), share in zip(group, _allocate(total_vat, [base for _row, base in group])):
                row["vat_amount"] = share
            vat += total_vat
    if exclusive_discount > subtotal:
        raise ValueError("Discount cannot exceed the amount")
    grand = money(subtotal - exclusive_discount + vat + adjustment)
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
