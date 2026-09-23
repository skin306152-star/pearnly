"""Source purchase goods/expense and payment predicates, without draft creation."""
from __future__ import annotations
import re
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from pearnly_cowork.push.express.field_clean import to_decimal as _to_decimal
from pearnly_cowork.push.express.field_corrections import normalize_fields

_GENERIC_BUYER_RE = re.compile(
    r"^(?:customer|no\.?\s*customer|cash|walk[-\s]?in|guest|ลูกค้า|เงินสด|สด|take\s*away)[:\s\d-]*$",
    re.IGNORECASE,
)


def judge_direction(fields: dict, *, today: date) -> tuple[str, str]:
    """判 (kind, route)。fields = OCR 抽取(document_type/vat/...)。

    待归类已下线:识别完的票一律建草稿落采购列表(用户在列表改方向/删),不再判 inbox/销项分流。
    只有完整税票(tax_invoice)且有 VAT 才自动走进项票(可抵进项税·route=purchase)。
    简式税票 / receipt 即便印了 VAT,也先按费用处理,避免把不可抵扣票据自动计入进项税。
    银行转账/电商订单截图(payment_evidence)非正规税票 → 一律费用、不抵 VAT(用户可改)。
    销项分类(我是卖方)后期按上传前选业务类型单独做,届时再引入主体税号比对。
    """
    fields = normalize_fields(fields, today=today)
    dtype = (fields.get("document_type") or "").lower()
    if dtype in ("payment_evidence", "order_evidence"):
        return "expense", "expense"
    if (
        dtype == "tax_invoice"
        and _to_decimal(fields.get("vat")) > 0
        and _has_buyer_identity(fields)
    ):
        return "purchase_invoice", "purchase"
    return "expense", "expense"


def doc_type_payment_hint(doc_type: Optional[str]) -> Optional[bool]:
    """票种→付款态的会计判据(单一事实源 · 采购工作台与 ERP 推送共用)。

    收据/简式税票在场即已收/已付(True);仅完整税票/贷项凭证多为赊(False)。转账截图/
    订单证据等非正规税票方向证据不足,保守留 None——不赌,由调用方决定兜底。
    """
    dt = (doc_type or "").strip().lower()
    if dt in ("receipt", "simplified_tax_invoice"):
        return True
    if dt in ("tax_invoice", "credit_note"):
        return False
    return None


def _has_buyer_identity(fields: dict) -> bool:
    buyer_tax = (fields.get("buyer_tax") or "").strip()
    if re.fullmatch(r"\d{13}", buyer_tax):
        return True
    buyer_name = (fields.get("buyer_name") or "").strip()
    if not buyer_name or _GENERIC_BUYER_RE.match(buyer_name):
        return False
    return len(buyer_name) >= 3




SRC_MANUAL = "manual"


def item_verdict(fields: Dict[str, Any], *, today: date) -> Tuple[bool, str]:
    """(is_expense, src)。

    人工裁决(posting_item_type_manual · 复核屏显式点过)优先于自动判据——会计裁决,
    不是习惯裁决:有完整税票 = 可抵进项,票面法定证据不许被自动判据/档案习惯压过。
    无人工裁决 → judge_direction(与 Pearnly 自身单据同口径)兜底,src 带上判到的
    kind(purchase_invoice/expense)供排障——沿用两处调用点此前各自打的日志值域。
    """
    manual = str(fields.get("posting_item_type_manual") or "").strip().lower()
    if manual in ("expense", "goods"):
        return manual == "expense", SRC_MANUAL


    kind, _ = judge_direction(fields, today=today)
    return kind == "expense", f"judge_direction={kind}"


_VAT_INCL_NUM, _VAT_INCL_DEN = Decimal("7"), Decimal("107")

def vat_from_inclusive(total: Decimal) -> Decimal:
    """含税总额反推 VAT(泰国 7% 票面拆解):total × 7/107(未量化·调用方按需 quantize)。"""
    return total * _VAT_INCL_NUM / _VAT_INCL_DEN
