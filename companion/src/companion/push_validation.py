"""Express 推送前本地守门。

主项目后端应该先拦掉不可推的任务;这里是 Companion 在真正点击/输入
Express 之前的最后一道保护,防止缺映射或坏数据时乱操作用户账套。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


ERR_MAPPING_MISSING_CUSTOMER = "MAPPING_MISSING_CUSTOMER"
ERR_MAPPING_MISSING_ITEM = "MAPPING_MISSING_ITEM"
ERR_INVALID_DATE = "INVALID_INVOICE_DATE"
ERR_INVALID_QUANTITY = "INVALID_QUANTITY"
ERR_EMPTY_ITEMS = "EMPTY_ITEMS"


@dataclass
class PayloadValidationResult:
    ok: bool
    error_code: str | None = None
    detail: str = ""


def validate_express_payload(payload: dict) -> PayloadValidationResult:
    """在任何鼠标/键盘自动化开始前返回阻断错误。"""
    customer_code = str(payload.get("customer_code") or "").strip()
    if not customer_code:
        return PayloadValidationResult(
            False,
            ERR_MAPPING_MISSING_CUSTOMER,
            "missing Express customer_code mapping",
        )

    invoice_date = str(payload.get("invoice_date") or "").strip()
    if not _looks_like_express_date(invoice_date):
        return PayloadValidationResult(
            False,
            ERR_INVALID_DATE,
            "invoice_date must be DD/MM/YY or DD/MM/YYYY",
        )

    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        return PayloadValidationResult(False, ERR_EMPTY_ITEMS, "payload.items empty")

    first_item = items[0] or {}
    item_code = str(first_item.get("code") or "").strip()
    if not item_code:
        return PayloadValidationResult(
            False,
            ERR_MAPPING_MISSING_ITEM,
            "missing Express item code mapping",
        )

    quantity = str(first_item.get("quantity") or "").strip()
    if not _positive_decimal(quantity):
        return PayloadValidationResult(
            False,
            ERR_INVALID_QUANTITY,
            "item quantity must be a positive number",
        )

    return PayloadValidationResult(True)


def _looks_like_express_date(value: str) -> bool:
    parts = value.split("/")
    if len(parts) != 3:
        return False
    day, month, year = parts
    if not (day.isdigit() and month.isdigit() and year.isdigit()):
        return False
    if len(day) not in (1, 2) or len(month) not in (1, 2) or len(year) not in (2, 4):
        return False
    d = int(day)
    m = int(month)
    return 1 <= d <= 31 and 1 <= m <= 12


def _positive_decimal(value: str) -> bool:
    try:
        return Decimal(value.replace(",", "")) > 0
    except (InvalidOperation, AttributeError):
        return False
