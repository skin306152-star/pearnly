"""把共用会计规则（`pearnly_core.tax_documents.amounts`，与 ERP 同一套）用到 Cowork 发票结果上。

用户 2026-09-27：「搬到共用层」—— ERP 已有的确定性修正，Cowork 同样适用的这几条接进读票流程：

- 金额写法规范：「1,234.50」「(183.82)」「฿99」→ 纯数字（括号为负）。原先这类写法让直读闸判「金额不是数字」，
  白复核一次（语料：贷项通知单）。
- 预扣税：票面合计是扣了预扣税后的实付额（小计 − 折扣 + VAT − 小计 × 常用税率 = 合计），核对算平，并补上税率
  与税额（语料：3% 服务费税票每张都被判「金额对不上」、白复核一次）。
- 用 OCR 原文纠正（只在已经调过 Vision 的路线上）：税号校验位不过时取原文里两位以内唯一、且只差一位的合法税号
  （2026-09-28 压测：放宽到两位时换成了另一个能过校验位的错号）；金额对不上时，原文里只差一位、换上后能对平的那个数。
- 单号不纠正：原文与模型只差一个字符时两种写法都留给复核的人选（见 `invoice_number_alternative`）；
  只差 V/U、O/0、I/1 这类形近字时当作原文里有，保留模型读法、不交人选（见 `_CONFUSABLE`）。

「本公司是买方」那条不搬：Cowork 同一批可能是采购也可能是销售，客户本来就可能是卖方。
修正都会留说明（进 validation_warnings），改动票面数字的只采用能让核对通过的那一个。
"""
from __future__ import annotations

from decimal import Decimal
import re

from pearnly_core.tax_documents import amounts

from pearnly_cowork.ocr.validation.math import check_amount_math

AMOUNT_FIELDS = ("subtotal", "vat", "total_amount", "discount", "wht_amount")
WITHHOLDING_PREFIX = "withholding_inferred:"
TRANSCRIPT_PREFIX = "corrected_from_ocr_text:"
NUMBER_ALTERNATIVE_PREFIX = "invoice_number_alternative:"
NUMBER_UNSEEN_PREFIX = "invoice_number_unseen:"
NUMBER_RANGE_PREFIX = "invoice_number_range:"


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
        # 两位以内只有它一个、且只差一位才换：差两位的换法在压测里换成了另一个能过校验位的错号。
        near = amounts.nearest_tax_id(read, taxes, exclude={amounts.digits(getattr(invoice, other, ""))})
        if near and amounts.differing_digits(near, read) == 1:
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


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9/\\\-.#]*[A-Za-z0-9]|[A-Za-z0-9]")


def _norm(value) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


# 字形相近、Vision 原文常互相读错的字符（用户 2026-09-30：「IV / IU 这么清楚都看不明白吗」—— 其实是模型读对、
# 原文读错：erpcowork 13 张里 10 张被 V↔U 误报；9-28 压测按原文替换的 36 处有 35 处把对的改错）。
# 只差这类字符时不算"原文里没有"，保留模型的读法、不再交人选；5↔6 这种真读错的数字不在里面，照样交人选。
_CONFUSABLE = str.maketrans({"V": "U", "O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "S": "5",
                             "B": "8", "Z": "2"})


def _fold(value) -> str:
    return _norm(value).translate(_CONFUSABLE)


def invoice_number_alternative(invoice, text) -> str | None:
    """完整税票的单号对照 Vision 原文：原文里有就不管；没有、但原文里恰好一个同长度只差一个字符的 → 两种写法都记下，
    交复核的人选（不改票面值）。

    两边各有对错：2026-09-27 大语料里直读把 IN26-00658 读成 IN26-00668（原文对）；2026-09-28 Express 压测里
    原文把 IV69/06-017 读成 IU69/06-017，按原文替换的 36 处有 35 处把对的改错（模型对）。单号没有校验位，
    谁对只能看原件或这个对方的历史单号（`history.review`）。返回 `invoice_number_alternative: 模型|原文`。"""
    read = str(getattr(invoice, "invoice_number", "") or "").strip()
    wanted = _norm(read)
    if not wanted or not text or number_seen(read, text):
        return None
    tokens = _TOKEN.findall(str(text))
    pool = set(tokens) | {a + b for a, b in zip(tokens, tokens[1:])}
    near = {t for t in pool if len(_norm(t)) == len(wanted)
            and sum(x != y for x, y in zip(_norm(t), wanted)) == 1}
    if len({_norm(t) for t in near}) != 1:
        return None
    other = min(near, key=len)
    return f"{NUMBER_ALTERNATIVE_PREFIX} {read}|{other}"


def number_seen(read, text) -> bool:
    """单号在原文里作为完整的号码出现（一个词，或被空格 / 符号拆开的相邻两段）。

    不按「原文里某串字符的一部分」算：2026-09-28 压测里 `138051304940` 读成 `38051304940`、`922/46079`
    读成 `46079`，按子串比会被当成原文里有、放过去。"""
    wanted = _fold(read)
    if not wanted or not text:
        return False
    tokens = [_fold(token) for token in _TOKEN.findall(str(text))]
    pool = set(tokens) | {a + b for a, b in zip(tokens, tokens[1:])}
    return wanted in pool


def invoice_number_unseen(invoice, text) -> str | None:
    """完整税票的单号在原文里既找不到、也没有只差一个字符的写法 → 记下交复核（不改票面）。"""
    read = str(getattr(invoice, "invoice_number", "") or "").strip()
    if not read or not text or number_seen(read, text):
        return None
    return f"{NUMBER_UNSEEN_PREFIX} {read}"


_RANGE = re.compile(r"\s*(?:\bto\b|ถึง|~|–|—|\s-\s)\s*", re.IGNORECASE)


def _shape(value) -> str:
    return re.sub(r"[0-9]", "9", re.sub(r"[A-Za-z]", "A", str(value)))


def invoice_number_range(invoice) -> str | None:
    """单号写成一段号码范围（「IV69/06-026 to IV69/06-050」）：一张票不会这样写，这一页是清单 / 汇总表的续页。

    2026-09-28 复测：汇总表第 1 页认成非发票，第 2 页（第 26–50 行 + 合计）却被当成一张税票、单号写成范围。
    按用户口径汇总票不处理：两头是同一种写法的号码才算，返回 `invoice_number_range: 单号`，由引擎改判非发票。"""
    read = str(getattr(invoice, "invoice_number", "") or "").strip()
    parts = [part for part in _RANGE.split(read) if part.strip()]
    if len(parts) != 2 or not all(re.search(r"\d", part) for part in parts) or _shape(parts[0]) != _shape(parts[1]):
        return None
    return f"{NUMBER_RANGE_PREFIX} {read}"


def number_alternative(note) -> tuple[str, str] | None:
    """`invoice_number_alternative:` 说明 → (模型读数, 原文读数)；不是这种说明回 None。"""
    text = str(note or "")
    if not text.startswith(NUMBER_ALTERNATIVE_PREFIX):
        return None
    read, _, other = text[len(NUMBER_ALTERNATIVE_PREFIX):].strip().partition("|")
    return (read, other) if read and other else None
