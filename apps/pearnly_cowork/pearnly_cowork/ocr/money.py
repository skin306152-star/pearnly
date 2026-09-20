"""Pure OCR amount/Thai tax ID parsing from deployed ec2e5c957e6b.

The legacy OCR cleaner intentionally strips currency text and commas; it is
not the strict parser for invoice math or reconciliation. Values remain exact
Decimal here instead of the source's binary float. Do not silently substitute
zero for a missing/unreadable amount.
"""
from decimal import Decimal, InvalidOperation
import re
from typing import Any

_RE_NON_MONEY = re.compile(r"[^\d.\-]")
_RE_NON_DIGIT = re.compile(r"\D")


def normalize_money(value: Any) -> Decimal | None:
    """Keep the deployed OCR character cleaning, without binary rounding.

    Scientific notation and parenthesized negatives are not reinterpreted:
    the legacy cleaner turns ``1e3`` into ``13`` and ``(100)`` into ``100``.
    Callers needing strict numeric input must use a separate strict parser.
    """
    if value is None:
        return None
    text = _RE_NON_MONEY.sub("", str(value).replace(",", "").strip())
    if not text or text in ("-", ".", "-."):
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def normalize_id(value: Any) -> str:
    """Retain the legacy digit-only tax/document identifier normalization."""
    return _RE_NON_DIGIT.sub("", str(value or ""))


def valid_thai_tax_id(value: Any) -> bool:
    """Thai 13-digit MOD-11 checksum; no master lookup or external authority."""
    normalized = normalize_id(value)
    if len(normalized) != 13:
        return False
    digits = [int(char) for char in normalized]
    weighted = sum(digits[index] * (13 - index) for index in range(12))
    return digits[12] == (11 - weighted % 11) % 10
