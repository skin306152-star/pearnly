"""把共用会计规则（`pearnly_core.tax_documents.amounts`，与 ERP 同一套）用到 Cowork 发票结果上。

用户 2026-09-27：「搬到共用层」—— ERP 已有的确定性修正，Cowork 同样适用的这几条接进读票流程：

- 金额写法规范：「1,234.50」「(183.82)」「฿99」→ 纯数字（括号为负）。原先这类写法让直读闸判「金额不是数字」，
  白复核一次（语料：贷项通知单）。
- 预扣税：票面合计是扣了预扣税后的实付额（小计 − 折扣 + VAT − 小计 × 常用税率 = 合计），核对算平，并补上税率
  与税额（语料：3% 服务费税票每张都被判「金额对不上」、白复核一次）。
- 用 OCR 原文纠正（只在已经调过 Vision 的路线上）：税号校验位不过时取原文里只差两位以内、唯一的合法税号；
  金额对不上时，原文里只差一位、换上后能对平的那个数。

「本公司是买方」那条不搬：Cowork 同一批可能是采购也可能是销售，客户本来就可能是卖方。
修正都会留说明（进 validation_warnings），改动票面数字的只采用能让核对通过的那一个。
"""
from __future__ import annotations

from decimal import Decimal

from pearnly_core.tax_documents import amounts

from pearnly_cowork.ocr.validation.math import check_amount_math

AMOUNT_FIELDS = ("subtotal", "vat", "total_amount", "discount", "wht_amount")
WITHHOLDING_PREFIX = "withholding_inferred:"
TRANSCRIPT_PREFIX = "corrected_from_ocr_text:"


def normalize_amounts(invoice) -> list[str]:
    """把能读成金额的票面字段规范成纯数字字符串；读不成的原样留给核对去报。返回改过的字段。"""
    changed = []
    for name in AMOUNT_FIELDS:
        raw = getattr(invoice, name, None)
        if raw in (None, ""):
            continue
        text = str(raw).strip()
        value = amounts.money(text)
        if value is None or not value.is_finite():
            continue
        plain = format(value, "f")
        if plain != text:
            setattr(invoice, name, plain)
            changed.append(name)
    return changed


def infer_withholding(invoice) -> str | None:
    """合计是扣了预扣税后的实付额时补上税率 / 税额，返回说明；不是这种情况返回 None。"""
    if getattr(invoice, "is_not_invoice", False):
        return None
    sub, total = amounts.money(invoice.subtotal), amounts.money(invoice.total_amount)
    if sub is None or total is None:
        return None
    base = sub - (amounts.money(invoice.discount) or Decimal(0))
    rate = amounts.withholding_rate(base, amounts.money(invoice.vat) or Decimal(0), total, invoice.wht_amount)
    if rate is None:
        return None
    withheld = (base * rate / 100).quantize(Decimal("0.01"))
    if not str(invoice.wht_rate or "").strip():
        invoice.wht_rate = format(rate.normalize(), "f")
    if not str(invoice.wht_amount or "").strip():
        invoice.wht_amount = f"{withheld:.2f}"
    return f"{WITHHOLDING_PREFIX} total {total:.2f} is net of {rate.normalize()}% withholding tax ({withheld:.2f})"


def repair_from_transcript(invoice, text, *, tolerance=Decimal("0.5")) -> list[str]:
    """用 Vision 原文核对：税号校验位、读错一位的金额。返回说明（没改返回空）。"""
    notes = []
    if not text or getattr(invoice, "is_not_invoice", False):
        return notes
    taxes = amounts.tax_ids_in_text(text)
    for field, other in (("seller_tax", "buyer_tax"), ("buyer_tax", "seller_tax")):
        read = amounts.digits(getattr(invoice, field, ""))
        if len(read) != 13 or amounts.valid_tax_id(read):
            continue
        near = amounts.nearest_tax_id(read, taxes, exclude={amounts.digits(getattr(invoice, other, ""))})
        if near:
            setattr(invoice, field, near)
            notes.append(f"{TRANSCRIPT_PREFIX} {field} {read}→{near}")
    if not check_amount_math(invoice, tolerance=tolerance):
        return notes
    found = amounts.amounts_in_text(text)
    for field in ("total_amount", "vat", "subtotal"):
        value = amounts.money(getattr(invoice, field, None))
        if value is None or value < 0 or value in found:
            continue
        before = getattr(invoice, field)
        for candidate in amounts.one_digit_amounts(value, found):
            setattr(invoice, field, f"{candidate:.2f}")
            if not check_amount_math(invoice, tolerance=tolerance):
                notes.append(f"{TRANSCRIPT_PREFIX} {field} {before}→{candidate:.2f}")
                return notes
            setattr(invoice, field, before)
    return notes
