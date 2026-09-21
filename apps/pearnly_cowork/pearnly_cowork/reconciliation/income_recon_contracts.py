"""Decoded income-reconciliation rows and their native storage contract.

The pairing rule lives in ``income_vat``; this module validates the two decoded
sides a caller hands over: ledger rows and the rows of the output-VAT report. The
deployed dictionary keys the core reads (``report_ref_no`` and its fallbacks) are
kept verbatim, so nothing is translated on the way in.

Amounts are validated as ``Decimal`` at this boundary and stored as the deployed
binary floats, exactly like the bank store: the decimal rule guards the entry, the
stored numbers and the matcher stay what the deployed code produced. Parsing the
ledger and VAT files, and the workbook export, are later slices of this family.

Pure contract: no Frappe, database, file, clock, HTTP or model access.
"""

from decimal import Decimal

from pearnly_cowork.reconciliation.income_vat import GlRow, normalize_doc_no
from pearnly_cowork.reconciliation.recon_contracts import (
	MAX_ROWS,
	MAX_TEXT,
	ReconciliationContractError,
	amount as _amount,
	canonical_digest,
	date_value as _date,
	json_dumps,
	json_loads,
	only_keys as _exact_keys,
	text as _text,
	title_text,
)

SOURCE_FORMAT = "decoded_vat_rows_v1"
AMOUNT_TOLERANCE = Decimal("0.01")
MAX_AMOUNT_TOLERANCE = Decimal("1000")
GL_KEYS = frozenset(("doc_no", "norm_doc_no", "date", "account_code", "description", "debit",
	"credit"))
VAT_KEYS = frozenset(("report_ref_no", "report_reference_no", "report_invoice_no", "report_date",
	"report_buyer_name", "report_amount_pre_vat"))
HEADER_KEYS = ("amount_tolerance", "gl_account_code")


def gl_row(raw):
	"""Validate one decoded ledger row into its exact stored representation."""
	raw = _exact_keys(raw, GL_KEYS, "income_gl")
	doc_no = _text(raw.get("doc_no"), "income_gl.doc_no")
	given = _text(raw.get("norm_doc_no"), "income_gl.norm_doc_no")
	return {
		"doc_no": doc_no,
		# The deployed parser derived the normalized number from the printed one.
		"norm_doc_no": given or normalize_doc_no(doc_no),
		"date": _text(raw.get("date"), "income_gl.date", limit=40),
		"account_code": _text(raw.get("account_code"), "income_gl.account_code"),
		"description": _text(raw.get("description"), "income_gl.description"),
		"debit": _amount(raw.get("debit"), "income_gl.debit"),
		"credit": _amount(raw.get("credit"), "income_gl.credit"),
	}


def vat_row(raw):
	"""Validate one decoded output-VAT report row, keeping the deployed keys."""
	raw = _exact_keys(raw, VAT_KEYS, "income_vat")
	return {
		"report_ref_no": _text(raw.get("report_ref_no"), "income_vat.report_ref_no"),
		"report_reference_no": _text(raw.get("report_reference_no"),
			"income_vat.report_reference_no"),
		"report_invoice_no": _text(raw.get("report_invoice_no"), "income_vat.report_invoice_no"),
		"report_date": _text(raw.get("report_date"), "income_vat.report_date", limit=40),
		"report_buyer_name": _text(raw.get("report_buyer_name"), "income_vat.report_buyer_name",
			limit=MAX_TEXT),
		# A negative report amount is a credit note, not a refused value; a row
		# without an amount is the deployed zero, not an unknown.
		"report_amount_pre_vat": _amount(raw.get("report_amount_pre_vat") or 0,
			"income_vat.report_amount_pre_vat", nonnegative=False),
	}


def header(value):
	"""Validate the optional header; unknown keys are refused, not stored."""
	if value in (None, ""):
		value = {}
	if not isinstance(value, dict) or set(value) - set(HEADER_KEYS):
		raise ReconciliationContractError("reconciliation.header_invalid")
	tolerance = _amount(value.get("amount_tolerance") or AMOUNT_TOLERANCE,
		"header.amount_tolerance")
	if tolerance > MAX_AMOUNT_TOLERANCE:
		raise ReconciliationContractError("reconciliation.amount_tolerance_invalid")
	gl_account_code = _text(value.get("gl_account_code"), "header.gl_account_code", limit=40)
	return {"amount_tolerance": tolerance, "gl_account_code": gl_account_code}


def decode_rows(gl_rows, vat_rows):
	"""Validate both sides and refuse an empty or oversized batch."""
	if not isinstance(gl_rows, list) or not isinstance(vat_rows, list):
		raise ReconciliationContractError("reconciliation.rows_invalid")
	if len(gl_rows) + len(vat_rows) > MAX_ROWS:
		raise ReconciliationContractError("reconciliation.rows_too_many")
	return ([gl_row(row) for row in gl_rows], [vat_row(row) for row in vat_rows])


def digest(header_value, gl_rows, vat_rows):
	"""Fingerprint of one request, used for exact replay detection."""
	payload = {
		"header": {key: str(value) if isinstance(value, Decimal) else value
			for key, value in header_value.items()},
		"gl": [{key: str(value) if isinstance(value, Decimal) else value
			for key, value in row.items()} for row in gl_rows],
		"vat": [{key: str(value) if isinstance(value, Decimal) else value
			for key, value in row.items()} for row in vat_rows],
	}
	return canonical_digest(payload)


def matching_gl_rows(rows):
	"""Ledger rows in the deployed core's shape (binary floats)."""
	return [GlRow(doc_no=row["doc_no"], norm_doc_no=row["norm_doc_no"], date=row["date"],
		account_code=row["account_code"], description=row["description"],
		debit=float(row["debit"]), credit=float(row["credit"])) for row in rows]


def matching_vat_rows(rows):
	"""VAT rows in the deployed core's shape (the keys it reads, binary floats)."""
	return [{**row, "report_amount_pre_vat": float(row["report_amount_pre_vat"])}
		for row in rows]
