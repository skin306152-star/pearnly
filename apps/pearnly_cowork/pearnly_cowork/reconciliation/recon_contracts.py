"""Shared pure primitives of the native reconciliation row contracts.

Bank and income reconciliation accept rows a caller already decoded; both need the
same amount, text, date and unknown-field rules, the same canonical JSON and the
same request fingerprint. This module holds those primitives once, so each domain
contract keeps only its own row shape.

Pure contract: no Frappe, database, file, clock, HTTP or model access.
"""

from datetime import date as date_type
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

MAX_ROWS = 20000
MAX_TEXT = 300
MAX_DECIMAL_DIGITS = 24
DECIMAL_PLACES = 6


class ReconciliationContractError(ValueError):
	"""Raised for a row or header the native entry point refuses."""


def amount(value, field, *, nonnegative=True):
	"""Validate one decimal amount into an exact ``Decimal`` (or ``None``)."""
	if value is None or value == "":
		return Decimal(0) if nonnegative else None
	try:
		number = Decimal(str(value).strip().replace(",", ""))
	except (InvalidOperation, ArithmeticError):
		raise ReconciliationContractError(f"reconciliation.amount_invalid:{field}") from None
	if not number.is_finite() or len(number.as_tuple().digits) > MAX_DECIMAL_DIGITS:
		raise ReconciliationContractError(f"reconciliation.amount_invalid:{field}")
	# A Decimal keeps far larger exponents than the deployed float matcher can
	# compare, so an amount outside the storable range is refused, not rounded.
	if number and (number.adjusted() > MAX_DECIMAL_DIGITS or not math.isfinite(float(number))):
		raise ReconciliationContractError(f"reconciliation.amount_invalid:{field}")
	if nonnegative and number < 0:
		raise ReconciliationContractError(f"reconciliation.amount_negative:{field}")
	if -number.as_tuple().exponent > DECIMAL_PLACES:
		raise ReconciliationContractError(f"reconciliation.amount_precision:{field}")
	return number


def text(value, field, *, required=False, limit=MAX_TEXT):
	text_value = "" if value is None else str(value).strip()
	if required and not text_value:
		raise ReconciliationContractError(f"reconciliation.text_required:{field}")
	if len(text_value) > limit:
		raise ReconciliationContractError(f"reconciliation.text_too_long:{field}")
	return text_value


def date_value(value, field):
	"""Validate one ISO date into a ``date``; an empty value stays unknown."""
	text_value = text(value, field, limit=10)
	if not text_value:
		return None
	try:
		return date_type.fromisoformat(text_value)
	except ValueError:
		raise ReconciliationContractError(f"reconciliation.date_invalid:{field}") from None


def only_keys(raw, allowed, field):
	"""Refuse a row that is not a mapping or carries a field the contract lacks."""
	if not isinstance(raw, dict):
		raise ReconciliationContractError(f"reconciliation.row_invalid:{field}")
	if set(raw) - set(allowed):
		raise ReconciliationContractError(f"reconciliation.row_unknown_field:{field}")
	return raw


def json_dumps(value):
	"""Canonical JSON for stored payloads; keys sorted, no insignificant space."""
	return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def json_loads(value):
	return json.loads(value or "{}")


def _plain(value):
	if isinstance(value, Decimal):
		return str(value)
	if isinstance(value, date_type):
		return value.isoformat()
	if isinstance(value, dict):
		return {key: _plain(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [_plain(item) for item in value]
	return value


def inputs_json(**sides):
	"""对账任务的原始输入（按文件原顺序的解码行，含 Decimal / 日期）→ 存进 `inputs_json` 的 JSON。

	结果页打开与重算从它出发，不再从排好序的结果行倒推（2026-09-29 排查）。没给的一侧不存。"""
	return json_dumps({key: _plain(rows) for key, rows in sides.items() if rows is not None})


def title_text(value):
	"""Validate a caller-supplied task title."""
	return text(value, "title", required=True, limit=140)


def canonical_digest(payload):
	"""Fingerprint of one request, used for exact replay detection."""
	return hashlib.sha256(json_dumps(payload).encode()).hexdigest()
