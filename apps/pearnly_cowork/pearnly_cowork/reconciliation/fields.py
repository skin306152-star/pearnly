"""Deployed per-field comparison for the VAT reconciliation worksheet.

Source: ec2e5c957e6b ``services/recon/field_comparator.py:compare_field`` and
``compare_all_fields``.

Every branch keeps the deployed order, text and category: the two distinct amount
parse-error messages, the rejected unparsable date, the +/-1 day band and the
``> 20`` day "cross-period" label. Amount branches delegate to ``amounts``, the
registered binary-float contract, instead of the platform Decimal policy.

Comparison output is evidence for the accountant; it never decides a pairing and
never writes to ERP.

Pure contract: no Frappe, database, file, clock or HTTP access.
"""

from typing import Any, Mapping

from pearnly_cowork.reconciliation import amounts
from pearnly_cowork.reconciliation.normalization import (
	normalize_branch,
	normalize_invoice_no,
	normalize_name,
	normalize_str,
	normalize_tax_id,
	parse_date,
)

DATE_FIELDS = ("date", "document_date", "invoice_date")
INVOICE_NUMBER_FIELDS = ("invoice_no", "document_no", "doc_no")
BUYER_NAME_FIELDS = ("buyer_name", "customer_name")
BUYER_TAX_FIELDS = ("buyer_tax_id", "customer_tax_id")
BUYER_BRANCH_FIELDS = ("buyer_branch", "customer_branch")
PRE_VAT_FIELDS = ("amount_pre_vat", "net_amount")
VAT_FIELDS = ("vat_amount", "tax_amount", "output_vat")

# Field name -> (invoice key, report key); the deployed comparison order.
FIELD_MAP = (
	("date", "invoice_date", "report_date"),
	("invoice_no", "invoice_no", "report_invoice_no"),
	("buyer_name", "buyer_name", "report_buyer_name"),
	("buyer_tax_id", "buyer_tax_id", "report_buyer_tax_id"),
	("buyer_branch", "buyer_branch", "report_buyer_branch"),
	("amount_pre_vat", "amount_pre_vat", "report_amount_pre_vat"),
	("vat_amount", "vat_amount", "report_vat_amount"),
)

# Deployed 61-VAT rule: individual buyers skip fields 6 and 7.
BUYER_FIELDS = ("buyer_tax_id", "buyer_branch")


def compare_field(field_name: str, invoice_value: Any, report_value: Any) -> dict[str, Any]:
	"""Compare one field and return the deployed ``{matched, delta, category}``."""
	field = field_name.lower()

	if field in DATE_FIELDS:
		left, right = parse_date(invoice_value), parse_date(report_value)
		if left is None or right is None:
			return amounts.field_comparison(False, "日期无法解析", "date_parse_error")
		delta = (right - left).days
		if delta == 0:
			return amounts.field_comparison(True)
		if abs(delta) <= 1:
			return amounts.field_comparison(False, f"差 {delta:+d} 天", "date_diff")
		if abs(delta) > 20:
			return amounts.field_comparison(False, f"差 {delta:+d} 天(跨期)", "date_period_mismatch")
		return amounts.field_comparison(False, f"差 {delta:+d} 天", "date_diff")

	if field in INVOICE_NUMBER_FIELDS:
		left = normalize_invoice_no(invoice_value)
		right = normalize_invoice_no(report_value)
		if left == right and left:
			return amounts.field_comparison(True)
		return amounts.field_comparison(
			False, f"{invoice_value} ≠ {report_value}", "invoice_no_mismatch")

	if field in BUYER_NAME_FIELDS:
		left = normalize_name(invoice_value)
		right = normalize_name(report_value)
		if left == right and left:
			return amounts.field_comparison(True)
		return amounts.field_comparison(False, f"{invoice_value} ≠ {report_value}", "name_mismatch")

	if field in BUYER_TAX_FIELDS:
		if not invoice_value and not report_value:
			return amounts.field_comparison(True)
		left = normalize_tax_id(str(invoice_value or ""))
		right = normalize_tax_id(str(report_value or ""))
		if left == right:
			return amounts.field_comparison(True)
		return amounts.field_comparison(
			False, f"{invoice_value} ≠ {report_value}", "tax_id_mismatch")

	if field in BUYER_BRANCH_FIELDS:
		if not invoice_value and not report_value:
			return amounts.field_comparison(True)
		left = normalize_branch(str(invoice_value or ""))
		right = normalize_branch(str(report_value or ""))
		if left == right:
			return amounts.field_comparison(True)
		return amounts.field_comparison(False, f"{left} ≠ {right}", "branch_mismatch")

	if field in PRE_VAT_FIELDS:
		return amounts.compare_amount(invoice_value, report_value,
			category="amount_diff", parse_error="金额无法解析")

	if field in VAT_FIELDS:
		return amounts.compare_amount(invoice_value, report_value,
			category="vat_diff", parse_error="税额无法解析")

	left, right = normalize_str(invoice_value), normalize_str(report_value)
	return amounts.field_comparison(
		left == right, "" if left == right else f"{invoice_value} ≠ {report_value}")


def compare_all_fields(invoice_row: Mapping[str, Any], report_row: Mapping[str, Any],
		skip_buyer: bool = False) -> dict[str, Any]:
	"""Run every deployed field over one already-paired row.

	``skip_buyer`` drops the tax-ID and branch fields for individual buyers. The
	result lists categories in sorted order and flags any unmatched field.
	"""
	fields: dict[str, Any] = {}
	categories = set()
	for field_name, invoice_key, report_key in FIELD_MAP:
		if skip_buyer and field_name in BUYER_FIELDS:
			continue
		result = compare_field(field_name, invoice_row.get(invoice_key), report_row.get(report_key))
		fields[field_name] = result
		if not result["matched"] and result["category"]:
			categories.add(result["category"])
	return {
		"fields": fields,
		"categories": sorted(categories),
		"has_diff": any(not value["matched"] for value in fields.values()),
	}
