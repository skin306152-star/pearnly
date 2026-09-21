"""收银的纯规则：付款结算与找零、销售金额、班次现金。

照旧线上 `services/pos/payment_settlement.py` 与 `services/pos/shift.py` 的口径搬过来：

- 支付方式限定额度内；`qr` 归一到 `promptpay`；
- 金额必须是正的、最多两位小数；
- 实收不得少于应收；**找零只能出自现金**（非现金多付要么是错，要么得改单）；
- 零元单不收任何付款；
- 交班：应有现金 = 备用金 + 现金实收 − 找零（找零只发现金）。
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")

CASH = "cash"
PROMPTPAY = "promptpay"
CARD = "card"
TRANSFER = "transfer"

# 「qr」是旧客户端的写法，落库统一成 promptpay，避免同一件事两种口径。
PAYMENT_METHODS = (CASH, PROMPTPAY, CARD, TRANSFER)
METHOD_ALIASES = {"qr": PROMPTPAY}

SALE = "sale"
REFUND = "refund"

OPEN = "open"
CLOSED = "closed"

UNPAID = "unpaid"
PAID = "paid"


class SettlementError(ValueError):
    """付款不合规。`detail` 说明是哪一条，便于界面指到具体字段。"""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def d(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise SettlementError("amount") from None
    if not result.is_finite():
        raise SettlementError("amount")
    return result


def q2(value) -> Decimal:
    return d(value).quantize(CENT, rounding=ROUND_HALF_UP)


def too_many_decimals(value) -> bool:
    return d(value).as_tuple().exponent < -2


def normalize_method(method) -> str:
    name = (method or CASH).strip().lower()
    name = METHOD_ALIASES.get(name, name)
    if name not in PAYMENT_METHODS:
        raise SettlementError("method")
    return name


def settle(payments, grand_total) -> tuple[list, Decimal, Decimal]:
    """校验并归一化付款，返回 (付款明细, 实收, 找零)。

    规则与旧线上一致，一条都不放宽：
    - 应收不能是负的或非有限；
    - 零元单**不收任何付款**（不然等于白送找零）；
    - 每笔金额 > 0、最多两位小数、方式在允许集合内；
    - 实收 < 应收 → underpaid；
    - 找零 > 现金实收 → noncash_overpay（非现金多付不找零）。
    """
    total = q2(grand_total)
    if total < 0:
        raise SettlementError("grand_total")
    if total == 0:
        if payments:
            raise SettlementError("zero_total")
        return [], Decimal("0"), Decimal("0")

    normalized = []
    paid_total = Decimal("0")
    cash_total = Decimal("0")
    for payment in payments or []:
        if not isinstance(payment, dict):
            raise SettlementError("payment")
        method = normalize_method(payment.get("method"))
        amount = d(payment.get("amount"))
        if amount <= 0 or too_many_decimals(amount):
            raise SettlementError("amount")
        paid_total += amount
        if method == CASH:
            cash_total += amount
        normalized.append({"method": method, "amount": q2(amount), "ref": payment.get("ref")})

    paid_total = q2(paid_total)
    if paid_total < total:
        raise SettlementError("underpaid")
    change = q2(paid_total - total)
    if change > q2(cash_total):
        raise SettlementError("noncash_overpay")
    return normalized, paid_total, change


def line_total(qty, unit_price, discount=0) -> Decimal:
    """行净额 = 数量 × 单价 − 行折扣。折扣大于行金额要报错，不能出现负行。"""
    gross = d(qty) * d(unit_price)
    net = q2(gross) - q2(discount)
    if net < 0:
        raise SettlementError("line_discount")
    return net


def sale_totals(lines, *, discount_total=0, has_vat=True, vat_rate=7):
    """销售金额：小计 → 单据折扣 → VAT → 应收。行必须有正数量。"""
    if not lines:
        raise SettlementError("lines")
    subtotal = Decimal("0")
    for index, line in enumerate(lines, start=1):
        if d(line.get("qty")) <= 0:
            raise SettlementError(f"line_{index}_qty")
        subtotal += line_total(line.get("qty"), line.get("unit_price"), line.get("discount", 0))
    subtotal = q2(subtotal)
    discount = q2(discount_total)
    if discount > subtotal:
        raise SettlementError("discount")
    taxable = q2(subtotal - discount)
    vat = q2(taxable * d(vat_rate) / Decimal("100")) if has_vat else Decimal("0")
    return {
        "subtotal": subtotal,
        "discount_total": discount,
        "vat_amount": vat,
        "grand_total": q2(taxable + vat),
    }


def shift_expected(opening_float, cash_in, change_out) -> Decimal:
    """应有现金 = 备用金 + 现金实收 − 找零。找零只发现金，所以只减现金那一份。"""
    return q2(d(opening_float) + d(cash_in) - d(change_out))


def shift_difference(counted_cash, expected_cash) -> Decimal:
    """实点 − 应有：正数多钱、负数短钱。"""
    return q2(d(counted_cash) - d(expected_cash))


def payment_status(paid_total, grand_total) -> str:
    return PAID if q2(paid_total) >= q2(grand_total) and q2(grand_total) > 0 else UNPAID
