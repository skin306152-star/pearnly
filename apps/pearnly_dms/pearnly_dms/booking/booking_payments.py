# -*- coding: utf-8 -*-
"""LINE 订车付款资料的**解析**、编辑校验与确认卡摘要。零 IO。

## 这一层是干什么的

用户在 LINE 里是一行一行写资料的（"支票号 12345 | 簿号 678"这种），
而 DMS 要的是**各自独立的原生槽位**（`cheque_no` / `cheque_book_no` / `card_type` /
`dst_bank_id`…）。把自由文本拆成原生槽位就是这里的职责 —— 也是**唯一**一处。
（`dms_payments` 只认原生槽位名，不负责拆文本；这条边界是本批次特意理清的。）

## 报错为什么是"码"而不是话

`PaymentValidationError.code` 是稳定的机器码（`dms_booking.invalid_bank` 之类），
**文案不在这一层**：老产品把它交给前端包按语义键渲染
（`static/dms-booking-edit/dms-booking-i18n.js`：`dms_booking.invalid_bank` →
`masterChanged` → 各语言文案）。照原样保留码，译文在哪渲染是上一层的事。

照搬老产品 `services/line_dms/booking_payments.py`（线上基准 `501589f0`），逻辑未改。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from pearnly_dms.masters.company_banks import (
    PAYMENT_CHANNEL_BANKS,
    company_bank_payment_extra,
    resolve_bank_identity,
)
from pearnly_dms.contracts.payments import (
    MANUAL_BANK_FLAG,
    missing_transfer_fields,
)
from pearnly_dms.booking.conversation.qa_util import (
    CHANNEL_EXTRA_SHAPE,
    find_row,
    parse_amount,
)
from pearnly_dms.contracts.text_fields import split_fields


class PaymentValidationError(ValueError):
    """付款资料校验失败。`code` 是稳定的机器码（译文由上一层渲染）。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_DETAIL_KEYS = {
    "cheque": ("cheque_no", "cheque_book_no"),
    "cashier_cheque": ("cashier_no", "cashier_book_no"),
    "card": ("bank_name", "card_type"),
}


def _two_parts(value: str) -> Optional[Tuple[str, str]]:
    """支票/本票编号与簿号：先用明确分隔符，再按首个空格拆分。"""
    parts = split_fields(value, 2)
    if parts is not None:
        return parts[0], parts[1]
    normalized = value.replace("｜", "|")
    if "|" in normalized:
        left, right = (part.strip() for part in normalized.split("|", 1))
        return (left, right) if left and right else None
    words = normalized.split()
    if len(words) < 2:
        return None
    return words[0], " ".join(words[1:])


def parse_payment_detail(
    channel: str, text: Optional[str], *, manual_bank: bool = False
) -> Optional[Dict[str, str]]:
    """把 LINE 的**一行**付款补充资料拆成 DMS 的独立字段。

    分段用共用分隔符规则（`dms_text_fields`）：`| ｜ , ， 、 / ／ ·` 都行，点号另有保守规则。

    `manual_bank` = 该渠道银行目录权威为空：多出一段手工银行名称
    （支票/本票在**末段**，卡在**首段**）。
    """
    value = str(text or "").strip()
    if not value:
        return None
    if channel == "card" and manual_bank:
        parts = split_fields(value, 2)
        if parts is not None and all(part != "-" for part in parts):
            return {"bank_name": parts[0], "card_type": parts[1]}
        return None
    if channel in {"card", "other"}:
        return {"card_type" if channel == "card" else "detail": value} if value != "-" else None
    keys = _DETAIL_KEYS.get(channel)
    if keys and manual_bank and channel != "card":
        parts = split_fields(value, 3)
        if parts is None or any(part == "-" for part in parts):
            return None
        return {keys[0]: parts[0], keys[1]: parts[1], "bank_name": parts[2]}
    parts = _two_parts(value)
    if parts is None:
        return None
    left, right = parts
    if keys:
        return {keys[0]: left, keys[1]: right}
    if channel == "other":
        return {"detail": value}
    return None


def _required(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text == "-" or len(text) > 160:
        raise PaymentValidationError("dms_booking.payment_detail_required")
    return text


def normalize_editor_payments(rows: list, masters: dict) -> list[dict]:
    """转账只接受**收款银行选择**（账户资料来自主档）；其他渠道校验各自字段。

    返回的 `extra` 一律是**原生槽位**，可以直接交给 `dms_payments.payment_form_fields`。
    """
    if not rows:
        raise PaymentValidationError("dms_booking.payment_required")
    banks = masters.get("company_banks") or []
    clean = []
    seen = set()
    for item in rows[:12]:
        channel = str(item.get("channel") or "")
        if channel not in CHANNEL_EXTRA_SHAPE:
            raise PaymentValidationError("dms_booking.invalid_payment")
        if channel in seen:
            raise PaymentValidationError("dms_booking.duplicate_payment")
        seen.add(channel)
        amount = parse_amount(str(item.get("amount") or ""))
        if amount is None:
            raise PaymentValidationError("dms_booking.invalid_amount")
        extra = dict(item.get("extra") or {})
        if channel == "transfer":
            bank = find_row(banks, str(extra.get("dst_id") or ""))
            if bank is None:
                raise PaymentValidationError("dms_booking.invalid_bank")
            extra = company_bank_payment_extra(bank)
            if missing_transfer_fields(extra):
                raise PaymentValidationError("dms_booking.payment_detail_required")
        elif channel in PAYMENT_CHANNEL_BANKS:
            key = PAYMENT_CHANNEL_BANKS[channel]
            identity = resolve_bank_identity(
                masters.get(key), key, extra.get("bank_id"), extra.get("bank_name")
            )
            if identity is None:
                raise PaymentValidationError("dms_booking.invalid_bank")
            keys = {
                "cheque": ("cheque_no", "cheque_book_no"),
                "cashier_cheque": ("cashier_no", "cashier_book_no"),
                "card": ("card_type",),
            }[channel]
            extra = {
                "bank_id": identity["id"],
                "bank_name": identity["name"],
                **{key: _required(extra.get(key)) for key in keys},
                **({MANUAL_BANK_FLAG: "1"} if identity["manual"] else {}),
            }
        elif channel == "other":
            extra = {"detail": _required(extra.get("detail"))}
        else:
            extra = {}
        clean.append({"channel": channel, "amount": f"{Decimal(amount):.2f}", "extra": extra})
    return clean


def payment_preview_detail(payment: dict) -> str:
    """确认卡的一行付款摘要 —— **展示的数据与 DMS 字段保持同源**（不给用户看两套说法）。"""
    value = str(payment.get("amount") or "")
    extra = payment.get("extra") or {}
    channel = payment.get("channel")
    if channel == "transfer":
        source = " ".join(
            part
            for part in (
                str(extra.get("src_account_name") or ""),
                str(extra.get("src_bank_name") or ""),
                str(extra.get("src_account_no") or ""),
                str(extra.get("src_branch_name") or ""),
            )
            if part
        )
        route = " → ".join(
            part
            for part in (
                source,
                " ".join(
                    str(extra.get(key) or "")
                    for key in (
                        "dst_business_name",
                        "dst_bank_name",
                        "dst_account_no",
                        "dst_branch_name",
                    )
                ).strip()
                or str(extra.get("dst") or ""),
            )
            if part
        )
        return " · ".join(part for part in (value, route) if part)
    if channel in {"cheque", "cashier_cheque"}:
        ref_key = "cheque_no" if channel == "cheque" else "cashier_no"
        ref = " · ".join(
            part
            for part in (
                str(extra.get(ref_key) or ""),
                str(
                    extra.get("cheque_book_no" if channel == "cheque" else "cashier_book_no") or ""
                ),
                str(extra.get("bank_name") or ""),
            )
            if part
        )
        return " · ".join(part for part in (value, ref) if part)
    if channel == "card":
        ref = " · ".join(
            part
            for part in (str(extra.get("bank_name") or ""), str(extra.get("card_type") or ""))
            if part
        )
        return " · ".join(part for part in (value, ref) if part)
    return " · ".join(part for part in (value, str(extra.get("detail") or "")) if part)
