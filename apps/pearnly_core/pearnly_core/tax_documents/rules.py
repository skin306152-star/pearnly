"""票种判定与业务处理的决策表（ADR-0012）。纯函数：不依赖 Frappe、不做 IO，同样输入永远同样输出。

三层分工：
1. 识别只抄票面事实（标题原文、买方税号 / 名称、票面 VAT）——模型给的票种只作参考；
2. `classify`：票面标题能认出法定票种就以标题为准，认不出才用模型给的，都没有就是 `OTHER`；
3. `purchase_treatment`：票种 × 采购场景 → 是否抵进项、记采购还是费用、默认付款、要人看的原因。

采购口径照旧线上 `pearnly-app@ec2e5c95` `services/purchase/intake.py:38-72`（`judge_direction`、
`doc_type_payment_hint`、`_has_buyer_identity`，Cowork `push/express/field_rules.py` 原样搬来的那份）：
只有完整税票、印了 VAT、写明买方的，才记可抵扣进项税；其余即使印了 VAT 也按含税全额记费用。
销项不走这张表：我方开出的简式税票同样要交销项税，按票面金额记。
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

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
