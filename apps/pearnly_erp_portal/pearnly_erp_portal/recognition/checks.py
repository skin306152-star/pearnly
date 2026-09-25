"""识别结果的确定性检查（纯函数，不读库、不联网）。

照旧线上 `pearnly-app@ec2e5c95` 发票直读的两层闸（`services/ocr/direct_read.py:429-445`
`_invoice_hard_gates`、`services/ocr/triggers.py:75-100`、`services/ocr/sanity.py:36-280`、
`services/ocr/date_sanity.py:20-46`），数值与容差照抄：

- **硬闸**（不过就换升级模型重读，仍不过则标「需复核」）：票面合计缺失；小计 − 折扣 + VAT
  ≠ 合计；负数；买卖同税号；合计小于单行；VAT 不是 7%；13 位税号 MOD-11 不过；明细和大于小计
  （含税明细的小票豁免）。
- **软标注**（只标「需复核」）：单据日期晚于今天 1 天以上或早于 5 年。

另加一条旧线上在入账确认时才做的（`services/erp/invoice_amounts.py:28-58`，不平返回 422）：
**按识别出的明细算出的合计必须等于票面合计**。这里提前到识别阶段，因为新流程的卡片
直接给出合计（09-26 真机：20% 折扣被丢、只有金额没有单价，这两次都该在这里被拦下）。
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

TOLERANCE = Decimal("0.5")
VAT_RATE = Decimal("0.07")
RELATIVE = Decimal("0.02")
MAX_BACKDATE_YEARS = 5
MAX_FUTURE_DAYS = 1

# 代码 → 给人看的话（翻译在 i18n/messages.tsv）。
MESSAGES = {
    "total_missing": "The total printed on the document was not found",
    "printed_math": "Subtotal, discount and VAT do not add up to the printed total",
    "computed_total": "The items do not add up to the printed total",
    "negative": "A negative amount was read",
    "same_tax_id": "Seller and buyer have the same tax ID",
    "tax_id_checksum": "A tax ID failed its check digit",
    "vat_ratio": "VAT is not 7% of the amount before VAT",
    "line_sum": "The items add up to more than the subtotal",
    "total_below_line": "The total is less than one of the items",
    "date_future": "The document date is in the future",
    "date_old": "The document date is more than 5 years old",
}
SOFT = frozenset({"date_future", "date_old"})


def money(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("฿", "").strip())
    except (InvalidOperation, ValueError):
        return None


def digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def valid_tax_id(value) -> bool:
    """泰国 13 位税号的 MOD-11 校验位。"""
    raw = digits(value)
    if len(raw) != 13:
        return True  # 残缺/空由别的规则管，这里只判 13 位读错一位
    total = sum(int(raw[i]) * (13 - i) for i in range(12))
    return (11 - total % 11) % 10 == int(raw[12])


def _vat_ratio_ok(sub, vat, discount) -> bool:
    if vat is None or vat <= 0 or sub is None or sub <= 0:
        return True
    gross = sub * VAT_RATE
    net_sub = sub - (discount or 0)
    net = net_sub * VAT_RATE if net_sub > 0 else gross
    return min(abs(vat - gross), abs(vat - net)) <= max(TOLERANCE, min(gross, net) * RELATIVE)


def _vat_inclusive_lines(line_sum, sub, vat, total, discount) -> bool:
    if vat is None or vat <= 0 or total is None or total <= 0:
        return False
    tol = max(TOLERANCE, total * RELATIVE)
    return (abs(line_sum - total) <= tol and abs(sub - (discount or 0) + vat - total) <= tol
            and _vat_ratio_ok(sub, vat, discount))


def printed_issues(printed: dict, line_amounts: list, *, seller_tax=None, buyer_tax=None) -> list:
    """票面自身是否自洽（旧线上的硬闸）。`printed` 是票面印的 subtotal/discount/vat/total。"""
    sub, vat, total = money(printed.get("subtotal")), money(printed.get("vat")), money(printed.get("total"))
    discount = money(printed.get("discount")) or Decimal(0)
    issues = []
    if total is None:
        issues.append("total_missing")
    if any(value is not None and value < 0 for value in (sub, vat, total)):
        issues.append("negative")
    seller, buyer = digits(seller_tax), digits(buyer_tax)
    if seller and buyer and len(seller) >= 10 and seller == buyer:
        issues.append("same_tax_id")
    lines = [amount for amount in line_amounts if amount is not None]
    if total is not None and total > 0 and lines and not discount and total < max(lines) - TOLERANCE:
        issues.append("total_below_line")
    # 模型有时把票面的含税金额（如「รวมเป็นเงิน」）当成小计：小计 ≈ 合计、VAT ≈ 合计 × 7/107
    # 就是含税票面，本身是平的（09-26 实测两张采购票都这样误报过）。
    inclusive = (sub is not None and total is not None and vat is not None and total > 0
                 and abs(sub - discount - total) <= TOLERANCE
                 and abs(vat - total * VAT_RATE / (1 + VAT_RATE)) <= max(TOLERANCE, total * VAT_RATE * RELATIVE))
    if sub is not None and total is not None and sub > 0 and not inclusive:
        if vat is None:
            net = sub - discount
            expected = net * VAT_RATE
            if abs(total - net) > TOLERANCE and abs(total - net - expected) > max(TOLERANCE, expected * RELATIVE):
                issues.append("printed_math")
        elif min(abs(sub + vat - total), abs(sub - discount + vat - total)) > TOLERANCE:
            issues.append("printed_math")
    if not inclusive and not _vat_ratio_ok(sub, vat, discount):
        issues.append("vat_ratio")
    if any(not valid_tax_id(value) for value in (seller, buyer) if value):
        issues.append("tax_id_checksum")
    if sub is not None and sub > 0 and len(lines) >= 2 and not inclusive:
        line_sum, tol = sum(lines), max(TOLERANCE, sub * RELATIVE)
        after = line_sum - discount
        discounted_ok = discount and (abs(after - sub) <= tol or (total is not None and abs(after - total) <= max(tol, total * RELATIVE)))
        if line_sum > sub + tol and not discounted_ok and not _vat_inclusive_lines(line_sum, sub, vat, total, discount):
            issues.append("line_sum")
    return issues


def computed_issues(printed: dict, computed_total) -> list:
    """按识别出的明细算出的合计 ≠ 票面合计（旧线上入账时的 422，这里提前）。"""
    total, computed = money(printed.get("total")), money(computed_total)
    if total is None or computed is None:
        return []
    return ["computed_total"] if abs(total - computed) > TOLERANCE else []


def date_issues(value, today=None) -> list:
    try:
        year, month, day = (int(part) for part in str(value or "")[:10].split("-"))
        parsed = date(year, month, day)
    except (ValueError, TypeError):
        return []
    anchor = today or date.today()
    if parsed > anchor + timedelta(days=MAX_FUTURE_DAYS):
        return ["date_future"]
    if parsed < anchor - timedelta(days=365 * MAX_BACKDATE_YEARS):
        return ["date_old"]
    return []


def hard(issues) -> list:
    return [code for code in issues if code not in SOFT]
