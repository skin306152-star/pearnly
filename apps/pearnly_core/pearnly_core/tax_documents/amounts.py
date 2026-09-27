"""票面金额与税号的会计核对与修复（多产品共用，ADR-0012 / ADR-013）。

纯函数：只依赖标准库，不依赖 Frappe、HTTP 或任何产品 App。原在 ERP `recognition.checks`，2026-09-27 搬到共用层
给 Cowork 一起用（用户：「搬到共用层」）；数值与容差照抄旧线上 `pearnly-app@ec2e5c95` 的发票闸
（`services/ocr/direct_read.py`、`sanity.py`、`date_sanity.py`、`services/erp/invoice_amounts.py`）。

问题代码（`printed_issues` / `computed_issues` / `date_issues` 返回）给人看的话由各产品自己写：
total_missing、printed_math、computed_total、computed_vat、negative、same_tax_id、tax_id_checksum、vat_ratio、
line_sum、total_below_line、date_future、date_old。
"""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

TOLERANCE = Decimal("0.5")
VAT_RATE = Decimal("0.07")
RELATIVE = Decimal("0.02")
MAX_BACKDATE_YEARS = 5
MAX_FUTURE_DAYS = 1

# 折扣 / 优惠券印成一行（「ส่วนลด…」）：不是商品，并进整单折扣（采购、销售共用；语料实测：被记成正数商品多算，
# 或与整单折扣重复扣）。
DISCOUNT_LINE = re.compile(r"^\s*(ส่วนลด|ลดราคา|คูปอง|discount\b|disc\.|coupon\b)", re.IGNORECASE)


def is_discount_line(description, *amounts) -> bool:
    """这一行是折扣行：名称是折扣 / 优惠券，或金额 / 单价印成负数。"""
    if DISCOUNT_LINE.match(str(description or "")):
        return True
    return any((money(value) or 0) < 0 for value in amounts)


def money(value):
    """票面金额 → Decimal；会计写法的括号「(183.82)」是负数（贷项通知单常见，语料实测读不出合计）。"""
    if value in (None, ""):
        return None
    text = str(value).replace(",", "").replace("฿", "").strip()
    negative = text.startswith("(") and text.endswith(")")
    try:
        amount = Decimal(text.strip("()").strip())
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


# 泰国预扣税常用税率（%）：票面只印了扣税后的实付额时，按这几档反推（`withholding_rate`）。
WHT_RATES = (Decimal(1), Decimal("1.5"), Decimal(2), Decimal(3), Decimal(5), Decimal(10), Decimal(15))


def withholding_rate(base, vat, total, wht_amount=None):
    """票面「合计」是扣了预扣税后的实付额：基数 + VAT − 基数 × 税率 = 合计。印了预扣税额就按它算，
    否则只在某一档常用税率使等式正好成立时才认（语料实测：3% 服务费票没读出预扣税，合计对不上）。"""
    base, vat, total = money(base), money(vat) or Decimal(0), money(total)
    if base is None or total is None or base <= 0:
        return None
    if abs(base + vat - total) <= Decimal("0.01"):
        # 不扣预扣税就已经平了：不猜（语料 v2_006：5% 折扣与 5% 预扣算式凑巧相同）。
        return None
    printed = money(wht_amount)
    if printed and printed > 0:
        candidates = [rate for rate in WHT_RATES if abs(base * rate / 100 - printed) <= Decimal("0.01")]
    else:
        candidates = [rate for rate in WHT_RATES if abs(base + vat - base * rate / 100 - total) <= Decimal("0.01")]
    return candidates[0] if len(candidates) == 1 else None


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
        elif min(abs(sub + vat - total), abs(sub - discount + vat - total)) > TOLERANCE \
                and withholding_rate(sub - discount, vat, total) is None:
            # 合计是扣了预扣税后的实付额（基数 + VAT − 基数 × 常用税率）也是平的，不算票面错。
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


def computed_issues(printed: dict, computed_total, computed_vat=None, net_payable=None) -> list:
    """按识别出的明细算出的合计 / VAT ≠ 票面（旧线上入账时的 422，这里提前）。

    VAT 也要比：09-26 实测一张含税票被读成「无 VAT」，合计照样对得上，入账却会少记 490.65 的税。"""
    issues = []
    total, computed, payable = money(printed.get("total")), money(computed_total), money(net_payable)
    # 票面「合计」可能印的是扣了预扣税后的实付额：和实付额对上也算对（语料实测：预扣税票每张都误报并白升级一次）。
    if total is not None and computed is not None and abs(total - computed) > TOLERANCE \
            and (payable is None or abs(total - payable) > TOLERANCE):
        issues.append("computed_total")
    vat, worked = money(printed.get("vat")), money(computed_vat)
    if vat is not None and worked is not None and abs(vat - worked) > max(TOLERANCE, vat * RELATIVE):
        issues.append("computed_vat")
    return issues


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


def vat_mode(printed: dict, line_sum, discount=0):
    """票面是含税价还是不含税价：旧线上入账时的判法（`services/erp/invoice_amounts.py:28-58`）——
    明细合计 − 折扣 等于票面合计就是含税，等于税前金额（合计 − VAT）就是不含税；都不等返回 None。

    09-26 实测：一张含税采购票两个模型都读成「无 VAT」，合计照样对，税却会少记。模型给的
    has_vat / price_mode 不可靠时，用票面自己的数字定。"""
    total, vat, lines = money(printed.get("total")), money(printed.get("vat")), money(line_sum)
    if total is None or lines is None or vat is None or vat <= 0:
        return None
    net = lines - (money(discount) or 0)
    if abs(net - total) <= TOLERANCE:
        return "inclusive"
    if abs(net - (total - vat)) <= TOLERANCE or withholding_rate(net, vat, total) is not None:
        return "exclusive"
    return None


def inferred_bill_discount(total, vat, subtotal, gross):
    """明细加起来（`gross`）比票面合计多出一截、又没读到整单折扣时，多出的就是整单折扣 —— 但只在票面 VAT
    能独立印证折后金额时才认（含税：VAT = 合计 × 7/107；不含税：VAT = 小计 × 7% 且小计 + VAT = 合计），
    不是硬凑合计。返回折扣额或 None。语料实测：Giffarine 税票把折扣 3,162 读成 0。"""
    total, vat, sub, gross = money(total), money(vat), money(subtotal), money(gross)
    if total is None or not vat or vat <= 0 or gross is None or gross <= total + TOLERANCE:
        return None
    if abs(vat - total * VAT_RATE / (1 + VAT_RATE)) <= TOLERANCE:
        return (gross - total).quantize(Decimal("0.01"))
    if sub is not None and gross > sub + TOLERANCE and abs(sub + vat - total) <= TOLERANCE \
            and abs(vat - sub * VAT_RATE) <= TOLERANCE:
        return (gross - sub).quantize(Decimal("0.01"))
    return None


# ── OCR 原文里的税号与金额：用来核对模型读数（ADR-013 第 4 条，每一处改动都要让检查问题变少才采用）──────────

TAX_IN_TEXT = re.compile(r"(?<!\d)(?:\d[\s-]?){12}\d(?!\d)")
AMOUNT_IN_TEXT = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)|(?<![\d.,])\d+\.\d{2}(?!\d)")


def tax_ids_in_text(text) -> set:
    """原文里校验位合法的 13 位税号。"""
    return {t for t in (digits(m) for m in TAX_IN_TEXT.findall(str(text or ""))) if valid_tax_id(t)}


def amounts_in_text(text) -> set:
    """原文里带两位小数的金额。"""
    return {money(a) for a in AMOUNT_IN_TEXT.findall(str(text or ""))} - {None}


def differing_digits(a, b) -> int:
    """两个等长号码 / 金额有几位不同（长度不同视为很多位）。"""
    a, b = str(a), str(b)
    return sum(x != y for x, y in zip(a, b)) if len(a) == len(b) else max(len(a), len(b))


def nearest_tax_id(read, candidates, *, exclude=(), max_diff=2):
    """读到的税号校验位不过时，原文里只差 `max_diff` 位以内、且唯一的那个合法税号；找不到或不唯一返回 None。"""
    read = digits(read)
    near = [t for t in candidates if t not in exclude and differing_digits(t, read) <= max_diff]
    return near[0] if len(near) == 1 else None


def one_digit_amounts(value, amounts) -> list:
    """原文里与读数只差一位数字的金额（读错一位的候选）。"""
    value = money(value)
    if value is None:
        return []
    return [amount for amount in amounts if differing_digits(f"{amount:.2f}", f"{value:.2f}") == 1]
