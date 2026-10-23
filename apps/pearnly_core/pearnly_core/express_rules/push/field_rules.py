"""Source purchase goods/expense and payment predicates, without draft creation."""
from __future__ import annotations
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from pearnly_core.express_rules.push.field_clean import to_decimal as _to_decimal
from pearnly_core.express_rules.push.field_corrections import normalize_fields
from pearnly_core.tax_documents import rules as tax_rules


def judge_direction(fields: dict, *, today: date) -> tuple[str, str]:
    """判 (kind, route)。fields = OCR 抽取(document_type/vat/...)。

    口径在全平台共用的决策表 `pearnly_core.tax_documents.rules.purchase_treatment`（ADR-0012），
    这里只把 Cowork 的字段名接上去:只有完整税票(tax_invoice)、印了 VAT、写明买方才走进项票
    (可抵进项税·route=purchase);简式税票 / receipt / 转账截图 / 订单截图即便印了 VAT 也按费用处理。
    与旧线上 `services/purchase/intake.py:38-57` 逐项等价(`scripts/cowork-express-source-parity.py` 对照)。
    """
    fields = normalize_fields(fields, today=today)
    treatment = tax_rules.purchase_treatment(fields.get("document_type"), vat=_to_decimal(fields.get("vat")),
                                             buyer_tax_id=fields.get("buyer_tax"),
                                             buyer_name=fields.get("buyer_name"))
    return ("purchase_invoice", "purchase") if treatment.claims_input_vat else ("expense", "expense")


def doc_type_payment_hint(doc_type: Optional[str]) -> Optional[bool]:
    """票种→付款态的会计判据(单一事实源 `pearnly_core.tax_documents.rules.paid_hint` · 采购工作台与 ERP 推送共用)。

    收据/简式税票在场即已收/已付(True);仅完整税票/贷项凭证多为赊(False)。转账截图/
    订单证据等非正规税票方向证据不足,保守留 None——不赌,由调用方决定兜底。
    """
    return tax_rules.paid_hint(doc_type)


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


VAT_CHOICES = ("claim", "expense")


def lane_verdict(fields: Dict[str, Any], *, today: date, vat_choice: Any = None) -> Tuple[bool, str]:
    """(is_expense, src)：进项映射器实际走的车道（`item_verdict`，再按这家选过的「小票 / 简易税票抵不抵进项」覆盖，
    ``vat_choice`` = `choices.resolve` 的 ``applied["vat"]``）。映射器与「商品单入账方式」的交人判据
    （`goods_posting.blocked`）共用这一个，两边不会一个说交人、一个说不交（审查处理 10-16）。"""
    if vat_choice in VAT_CHOICES:
        return vat_choice == "expense", "choice"
    return item_verdict(fields, today=today)


_VAT_INCL_NUM, _VAT_INCL_DEN = Decimal("7"), Decimal("107")

def vat_from_inclusive(total: Decimal) -> Decimal:
    """含税总额反推 VAT(泰国 7% 票面拆解):total × 7/107(未量化·调用方按需 quantize)。"""
    return total * _VAT_INCL_NUM / _VAT_INCL_DEN
