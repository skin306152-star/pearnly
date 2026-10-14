"""票种判定与业务处理的决策表（ADR-0012）。纯函数：不依赖 Frappe、不做 IO，同样输入永远同样输出。

三层分工：
1. 识别只抄票面事实（标题原文、买方税号 / 名称、票面 VAT）——模型给的票种只作参考；
2. `classify`：票面标题能认出法定票种就以标题为准，认不出才用模型给的，都没有就是 `OTHER`；
3. `purchase_treatment`：票种 × 采购场景 → 是否抵进项、记采购还是费用、默认付款、要人看的原因。

采购口径照旧线上 `pearnly-app@ec2e5c95` `services/purchase/intake.py:38-72`（`judge_direction`、
`doc_type_payment_hint`、`_has_buyer_identity`，Cowork `push/express/field_rules.py` 原样搬来的那份）：
只有完整税票、印了 VAT、写明买方的，才记可抵扣进项税；其余即使印了 VAT 也按含税全额记费用。
销项不走这张表：我方开出的简式税票同样要交销项税，按票面金额记。

另有 `included_vat`：简式税票的票面价依法含 VAT（ม.86/6）。识别出的税额是 0 或没写、税前额就是总额时，按 7/107
拆出税额（销项、进项一样拆；进项拆出后抵不抵仍由各产品自己的流程定，本函数不管）。
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Optional, Tuple

from pearnly_core.tax_documents.amounts import VAT_RATE, money
from pearnly_core.tax_documents.codes import (CREDIT_NOTE, DOCUMENT_TYPES, OTHER, RECEIPT,
                                              SIMPLIFIED_TAX_INVOICE, TAX_INVOICE)

# 标题认票种：按顺序匹配，先窄后宽（「ใบกำกับภาษีอย่างย่อ」也含「ใบกำกับภาษี」）。泰文去掉空白再比，
# 英文转大写再比。增项通知单本表不建模，标题认出来就明确归 OTHER 交人工。
_TITLE_RULES = (
    (("อย่างย่อ",), ("ABB", "ABBREVIATED", "SIMPLIFIED TAX INVOICE"), SIMPLIFIED_TAX_INVOICE),
    (("ใบลดหนี้",), ("CREDIT NOTE",), CREDIT_NOTE),
    (("ใบเพิ่มหนี้",), ("DEBIT NOTE",), OTHER),
    (("ใบกำกับภาษี",), ("TAX INVOICE",), TAX_INVOICE),
    (("ใบเสร็จรับเงิน", "ใบรับ"), ("RECEIPT",), RECEIPT),
)
_ENGLISH_WORD = re.compile(r"[^A-Z]+")

_GENERIC_BUYER_RE = re.compile(
    r"^(?:customer|no\.?\s*customer|cash|walk[-\s]?in|guest|ลูกค้า|เงินสด|สด|take\s*away)[:\s\d-]*$",
    re.IGNORECASE,
)

# 要人看的原因（产品自己翻成界面文案）。简式税票 / 收据不抵进项是正常口径，不算要人看。
REVIEW_UNKNOWN_TYPE = "document_type_unknown"
REVIEW_TAX_INVOICE_WITHOUT_VAT = "tax_invoice_without_vat"
REVIEW_TAX_INVOICE_WITHOUT_BUYER = "tax_invoice_without_buyer"
REVIEW_CREDIT_NOTE = "credit_note"


def normalize(document_type) -> Optional[str]:
    """模型给的票种 → 码表里的码；认不出返回 None（空值与未知值不当成完整税票）。"""
    value = str(document_type or "").strip().lower()
    return value if value in DOCUMENT_TYPES else None


def from_title(title) -> Optional[str]:
    """票面标题原文 → 票种；标题里没有法定票种名称返回 None。"""
    text = str(title or "")
    thai = re.sub(r"\s+", "", text)
    english = " " + _ENGLISH_WORD.sub(" ", text.upper()).strip() + " "
    for thai_names, english_names, kind in _TITLE_RULES:
        if any(name in thai for name in thai_names) or any(f" {name} " in english for name in english_names):
            return kind
    return None


def classify(model_type=None, title=None) -> Tuple[str, str]:
    """(票种, 依据)。依据是 `title` / `model` / `unknown`：标题优先，模型次之，都没有就是 OTHER。"""
    by_title = from_title(title)
    if by_title:
        return by_title, "title"
    by_model = normalize(model_type)
    if by_model:
        return by_model, "model"
    return OTHER, "unknown"


def has_buyer_identity(buyer_tax_id=None, buyer_name=None) -> bool:
    """票面写明了买方：13 位税号，或不是「现金 / 散客」这类泛称的名称。"""
    if re.fullmatch(r"\d{13}", str(buyer_tax_id or "").strip()):
        return True
    name = str(buyer_name or "").strip()
    if not name or _GENERIC_BUYER_RE.match(name):
        return False
    return len(name) >= 3


def _amount(value) -> Decimal:
    try:
        result = Decimal(str(value).replace(",", "").strip()) if value not in (None, "") else Decimal(0)
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return result if result.is_finite() else Decimal(0)


def paid_hint(document_type) -> Optional[bool]:
    """票种 → 默认付款态：收据 / 简式税票当场已付，完整税票 / 减项通知单多为赊，其余不猜（None）。"""
    kind = normalize(document_type)
    if kind in (RECEIPT, SIMPLIFIED_TAX_INVOICE):
        return True
    if kind in (TAX_INVOICE, CREDIT_NOTE):
        return False
    return None


@dataclass(frozen=True)
class PurchaseTreatment:
    document_type: str
    claims_input_vat: bool      # 记可抵扣进项税；False = 票面含税全额记成本 / 费用
    route: str                  # "purchase"（进项票）或 "expense"（费用）
    paid_hint: Optional[bool]
    review: Tuple[str, ...]     # 要人看的原因码；空 = 按表处理即可


def purchase_treatment(document_type, *, vat=None, buyer_tax_id=None, buyer_name=None) -> PurchaseTreatment:
    """采购场景的决策表。`document_type` 应先经 `classify`；`vat` 是票面印的 VAT。

    | 票种 | 抵进项 | 记为 | 要人看 |
    | --- | --- | --- | --- |
    | 完整税票 + 印了 VAT + 写明买方 | 是 | 采购 | — |
    | 完整税票，缺 VAT 或缺买方 | 否 | 费用 | 缺哪项 |
    | 简式税票 / 收据 | 否 | 费用 | — |
    | 转账凭证 / 订单截图 | 否 | 费用 | — |
    | 减项通知单 | 否 | 费用 | 减项通知单（本表不自动冲减） |
    | 其他 / 认不出 | 否 | 费用 | 票种不明 |
    """
    kind = normalize(document_type) or OTHER
    review = []
    claims = False
    if kind == TAX_INVOICE:
        printed_vat, buyer = _amount(vat) > 0, has_buyer_identity(buyer_tax_id, buyer_name)
        claims = printed_vat and buyer
        if not printed_vat:
            review.append(REVIEW_TAX_INVOICE_WITHOUT_VAT)
        if not buyer:
            review.append(REVIEW_TAX_INVOICE_WITHOUT_BUYER)
    elif kind == CREDIT_NOTE:
        review.append(REVIEW_CREDIT_NOTE)
    elif kind == OTHER:
        review.append(REVIEW_UNKNOWN_TYPE)
    return PurchaseTreatment(document_type=kind, claims_input_vat=claims,
                             route="purchase" if claims else "expense",
                             paid_hint=paid_hint(kind), review=tuple(review))


# ── 简式税票含税价拆税（ม.86/6）─────────────────────────────────────────────────────────────────────
# 简式税票只能由增值税登记人开具，票面价依法是含税价（ประมวลรัษฎากร ม.86/6），税率 7%。识别有时把这类票的税额读成 0
# （2026-10-13 testcowork：手写 ABB「รวมเงิน (รวมภาษีมูลค่าเพิ่มแล้ว) 727.00」读成税前 727.00、税额 0，销项少记 47.56；
# 同一本另三张模型自己拆对了）。模型输出不稳，用这条确定性规则兜住：只把数字规整对，不加提示、不加确认步骤。

INCLUDED_VAT_TYPES = (SIMPLIFIED_TAX_INVOICE,)  # 票面价依法含 VAT 的票种
SPLIT = "split"
# 不拆的原因：只给排查与离线回放看，不给用户看。
NOT_INCLUDED_TYPE = "not_included_type"
FOREIGN_CURRENCY = "foreign_currency"
TOTAL_MISSING = "total_missing"
VAT_PRESENT = "vat_present"
VAT_UNREADABLE = "vat_unreadable"
SUBTOTAL_DIFFERS = "subtotal_differs"
WITHHOLDING = "withholding"
NO_VAT_MARKED = "no_vat_marked"

_CENT = Decimal("0.01")
_BAHT = frozenset({"", "THB", "BAHT", "บาท", "฿"})
# 票面写明免税 / 零税率 / 不含税的字样：这类票的税额本来就是 0，不按含税价拆。只认写明的，不猜。
# 英文词前后只排除英文字母（泰文与英文常连写，`\b` 在泰文字母旁不成立）。
_NO_VAT_MARKS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"ยกเว้น",                                                                  # ยกเว้นภาษี / สินค้ายกเว้น VAT
    r"อัตรา\s*ศูนย์|อัตรา\s*(?:ภาษี\S*\s*)?(?:ร้อยละ\s*)?0(?:\.0+)?(?![\d.,])",   # อัตราศูนย์ / อัตราร้อยละ 0
    r"(?:(?<![a-z])vat(?![a-z])|ภาษี\s*มูลค่า\s*เพิ่ม)\W{0,6}0(?:\.0+)?(?![\d.,])",  # VAT 0% / ภาษีมูลค่าเพิ่ม 0.00
    r"(?<![\d.,])0(?:\.0+)?\s*%\s*(?:vat(?![a-z])|ภาษี)",                        # 0% VAT
    r"ไม่\s*(?:มี|คิด|เสีย|รวม)\s*(?:ภาษี|vat(?![a-z]))",                          # ไม่มีภาษี / ไม่คิด VAT / ไม่รวม VAT
    r"(?<![a-z])(?:non[\s-]*vat|exempt|zero[\s-]*rated?(?![a-z])|no\s+vat(?![a-z])|vat[\s-]*free(?![a-z]))",
))


@dataclass(frozen=True)
class IncludedVat:
    reason: str                         # SPLIT 或不拆的原因
    subtotal: Optional[Decimal] = None  # 拆出的税前额（两位小数）
    vat: Optional[Decimal] = None       # 拆出的税额（两位小数）

    @property
    def applies(self) -> bool:
        return self.reason == SPLIT


def no_vat_marked(texts: Iterable) -> bool:
    """票面文字（备注、品名、金额栏原文…）里写明了免税 / 零税率 / 不含税。"""
    return any(pattern.search(str(text)) for text in texts or () if text not in (None, "")
               for pattern in _NO_VAT_MARKS)


def _finite(value):
    amount = money(value)
    return amount if amount is not None and amount.is_finite() else None


def included_vat(document_type, total, *, subtotal=None, vat=None, wht_amount=None, currency=None,
                 texts: Iterable = ()) -> IncludedVat:
    """简式税票识别出税额 0 / 没写、税前额就是总额（或没写）→ 按含税价拆：税额 = 总额 × 7/107（四舍五入到分），
    税前额 = 总额 − 税额。`texts` 是票面上的文字（备注、品名、金额栏原文），写明免税 / 零税率的不拆。

    其余一律不动，返回不拆的原因：别的票种、外币、总额缺失或不为正、已有税额、税额读不成数、税前额与总额不同
    （或读不成数）、带预扣税、写明免税 / 零税率。`document_type` 是码表里的码（经 `classify` 或模型给的）。
    """
    if normalize(document_type) not in INCLUDED_VAT_TYPES:
        return IncludedVat(NOT_INCLUDED_TYPE)
    if str(currency or "").strip().upper() not in _BAHT:
        return IncludedVat(FOREIGN_CURRENCY)
    gross = _finite(total)
    if gross is None or gross <= 0:
        return IncludedVat(TOTAL_MISSING)
    if str(vat if vat is not None else "").strip():
        read = _finite(vat)
        if read is None:
            return IncludedVat(VAT_UNREADABLE)
        if read != 0:
            return IncludedVat(VAT_PRESENT)
    if str(subtotal if subtotal is not None else "").strip():
        base = _finite(subtotal)
        if base is None or abs(base - gross) > _CENT:
            return IncludedVat(SUBTOTAL_DIFFERS)
    withheld = _finite(wht_amount)
    if withheld is not None and withheld > 0:
        return IncludedVat(WITHHOLDING)
    if no_vat_marked(texts):
        return IncludedVat(NO_VAT_MARKED)
    tax = (gross * VAT_RATE / (1 + VAT_RATE)).quantize(_CENT, rounding=ROUND_HALF_UP)
    return IncludedVat(SPLIT, (gross - tax).quantize(_CENT, rounding=ROUND_HALF_UP), tax)
