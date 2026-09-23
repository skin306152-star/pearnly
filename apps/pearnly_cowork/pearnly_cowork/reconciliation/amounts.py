"""Deployed VAT amount rule: binary-float rounding with a one-satang tolerance.

Source: ec2e5c957e6b ``services/recon/reconciliation_matcher.py:_get_total`` and the
amount branches of ``services/recon/field_comparator.py:compare_field``.

The deployed matcher rounds with ``round(float(...), 2)`` and compares with
``abs(left - right) <= 0.01``. Binary floating point decides differently from a
Decimal policy on inputs such as ``"2.675"``: the float is ``2.67499999999999982``,
so the source keeps ``2.67`` while ``Decimal.quantize(0.01, ROUND_HALF_EVEN)``
yields ``2.68`` and would flip the pairing decision.

This module is the **registered compatibility contract** for imported invoice and
report amounts. It reproduces the deployed decision bit-for-bit so historical
reconciliation results do not change; it is deliberately not the Decimal money
rule for new native records. New Cowork amounts still use ``Decimal``.

Pure contract: no Frappe, database, file, clock, HTTP or legacy application import.
"""

from typing import Any, Mapping

AMOUNT_TOLERANCE = 0.01


def parse_amount(value: Any) -> float:
	"""Deployed amount coercion: drop visual thousands separators, then ``float()``."""
	return float(str(value).replace(",", ""))


def _rounded_total(value: Any) -> float:
	# Deployed `_get_total` swallows every parse failure as a zero amount.
	try:
		return round(parse_amount(value), 2)
	except Exception:
		return 0.0


def invoice_total(row: Mapping[str, Any]) -> float:
	"""Deployed ``_get_total(row, is_report=False)``; the pre-VAT amount wins."""
	return _rounded_total(row.get("amount_pre_vat") or row.get("total_amount") or 0)


def report_total(row: Mapping[str, Any]) -> float:
	"""Deployed ``_get_total(row, is_report=True)``; the report amount is pre-VAT."""
	return _rounded_total(row.get("report_amount") or row.get("amount_pre_vat") or 0)


def within_tolerance(left: float, right: float) -> bool:
	"""Deployed inclusive tolerance; a NaN difference is never within it."""
	return abs(left - right) <= AMOUNT_TOLERANCE


def field_comparison(matched: bool, delta: str = "", category: str = "") -> dict[str, Any]:
	"""Deployed ``_r`` result shape, shared by every compared field."""
	return {"matched": matched, "delta": delta, "category": category}


def compare_amount(invoice_value: Any, report_value: Any, *, category: str,
		parse_error: str) -> dict[str, Any]:
	"""Deployed unrounded amount comparison for one amount field.

	``category`` and ``parse_error`` keep the two deployed messages apart: the
	pre-VAT field reports "金额无法解析" and the VAT field "税额无法解析".
	"""
	try:
		left = parse_amount(invoice_value or 0)
		right = parse_amount(report_value or 0)
	except Exception:
		return field_comparison(False, parse_error, "amount_parse_error")
	if within_tolerance(left, right):
		return field_comparison(True)
	return field_comparison(False, f"差 {right - left:+.2f}", category)
