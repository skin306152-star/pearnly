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
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from pearnly_core.tax_documents import rules as tax_rules

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
    "computed_vat": "The VAT worked out from the items does not match the printed VAT",
    "negative": "A negative amount was read",
    "same_tax_id": "Seller and buyer have the same tax ID",
    "tax_id_checksum": "A tax ID failed its check digit",
    "vat_ratio": "VAT is not 7% of the amount before VAT",
    "line_sum": "The items add up to more than the subtotal",
    "total_below_line": "The total is less than one of the items",
    "date_future": "The document date is in the future",
    "date_old": "The document date is more than 5 years old",
    # 共用决策表（`pearnly_core.tax_documents.rules`，ADR-0012）要人看的原因：只标注、不换模型重读。
    tax_rules.REVIEW_UNKNOWN_TYPE: "The document type was not recognised, so input VAT is not claimed",
    tax_rules.REVIEW_TAX_INVOICE_WITHOUT_VAT: "This tax invoice shows no VAT, so input VAT is not claimed",
    tax_rules.REVIEW_TAX_INVOICE_WITHOUT_BUYER: "This tax invoice does not name the buyer, so input VAT is not claimed",
    tax_rules.REVIEW_CREDIT_NOTE: "This is a credit note: check the amounts and the original invoice",
}
SOFT = frozenset({"date_future", "date_old", tax_rules.REVIEW_UNKNOWN_TYPE, tax_rules.REVIEW_TAX_INVOICE_WITHOUT_VAT,
                  tax_rules.REVIEW_TAX_INVOICE_WITHOUT_BUYER, tax_rules.REVIEW_CREDIT_NOTE})


# 折扣 / 优惠券印成一行（「ส่วนลด…」）：不是商品，并进整单折扣（采购、销售共用；语料实测：被记成正数商品多算，
# 或与整单折扣重复扣）。
DISCOUNT_LINE = re.compile(r"^\s*(ส่วนลด|ลดราคา|คูปอง|discount\b|disc\.|coupon\b)", re.IGNORECASE)


# 提示词片段（采购、销售共用）：语料实测 7-Eleven 小票把门店 / POS 码当成单号；SM 小票的 3% 刷卡费没算进明细。
POS_RULES = ("On a shop or POS slip doc_no is the receipt running number (printed after R#, Rcpt, No. or เลขที่),\n"
             "never the branch, store, POS, terminal or cashier code. Discount and coupon rows (ส่วนลด ...) are not\n"
             "items: add them to discount_total. A service charge or card fee printed below the items\n"
             "(Service Charge 10%, Credit Card 3%) is an extra line with its printed amount.")


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


def hard(issues) -> list:
    return [code for code in issues if code not in SOFT]


# 值得再读一遍的只有「金额 / 结构对不上」。税号校验位不过：语料实测 3.8 重读后多半还是错，改由 OCR 原文
# 按校验位核对（`purchasing.recognition._anchor`）；贷项通知单本来就不录；票种 / 进项原因、日期软标注只标红。
NOT_REREAD = frozenset({"tax_id_checksum", "negative", "same_tax_id"})


def reread(issues) -> list:
    """值得让模型再读的问题（ADR-013）。"""
    return [code for code in hard(issues) if code not in NOT_REREAD]


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
