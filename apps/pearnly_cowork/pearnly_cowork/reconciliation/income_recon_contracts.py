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

# The per-file diagnostics stored beside a task: which file each side came from,
# which reader served it and what it kept. The deployed run kept one file name per
# side and showed the reader's own counters, so the same names are stored here.
MAX_PARSE_FILES = 20
MAX_ACCOUNTS = 100
MAX_WARNINGS = 50
MAX_PHOTO_PAGES = 300
MAX_PAGE_REFS = 200
MAX_FILE_NAME = 140
PARSE_INFO_SECTIONS = {
	"gl": ("file", "method", "rows", "parser_version", "accounts_seen", "candidate_data_lines",
		"tables_seen", "header_found", "sheets_seen", "rows_kept"),
	# tax_id：税报表头印的本公司税号（对账预设按公司记，2026-09-30 加；旧任务没有）。
	# photo_pages：照片 / 扫描件识别出的税报每页有哪些单号（结果页提示同一页多行对不上多半是读错；2026-09-30 加）。
	"vat": ("file", "method", "rows", "parser_version", "warnings", "tax_id", "photo_pages"),
}


def gl_row(raw):
	"""Validate one decoded ledger row into its exact stored representation."""
	raw = _exact_keys(raw, GL_KEYS, "income_gl")
	doc_no = _text(raw.get("doc_no"), "income_gl.doc_no")
	given = _text(raw.get("norm_doc_no"), "income_gl.norm_doc_no")
	# 总账里写成负数（「(1,000.00)」「-1,000.00」）的是冲销 / 红字：负的贷方记成借方、负的借方记成贷方，
	# 借贷净额不变。原来整份对账因此失败（2026-09-28 录制发现）。
	debit = _amount(raw.get("debit"), "income_gl.debit", nonnegative=False) or Decimal(0)
	credit = _amount(raw.get("credit"), "income_gl.credit", nonnegative=False) or Decimal(0)
	debit, credit = max(debit, Decimal(0)) + max(-credit, Decimal(0)), max(credit, Decimal(0)) + max(-debit, Decimal(0))
	return {
		"doc_no": doc_no,
		# The deployed parser derived the normalized number from the printed one.
		"norm_doc_no": given or normalize_doc_no(doc_no),
		"date": _text(raw.get("date"), "income_gl.date", limit=40),
		"account_code": _text(raw.get("account_code"), "income_gl.account_code"),
		"description": _text(raw.get("description"), "income_gl.description"),
		"debit": debit,
		"credit": credit,
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


def file_name(value, field):
	"""Validate one stored file name; the deployed run kept the printed names."""
	return _text(value, field, limit=MAX_FILE_NAME)


def parse_info(value):
	"""Validate the optional per-file diagnostics stored with one task.

	Only the two deployed sections with their deployed record keys are kept: which
	file each side came from, which reader served it, how many rows it kept and the
	counters that reader reported. An unknown section or record key is refused
	instead of stored and silently ignored.
	"""
	if value in (None, ""):
		return {}
	if not isinstance(value, dict) or set(value) - set(PARSE_INFO_SECTIONS):
		raise ReconciliationContractError("reconciliation.parse_info_invalid")
	result = {}
	for section, keys in PARSE_INFO_SECTIONS.items():
		records = value.get(section)
		if records in (None, ""):
			result[section] = []
			continue
		if not isinstance(records, list) or len(records) > MAX_PARSE_FILES:
			raise ReconciliationContractError(f"reconciliation.parse_info_too_many:{section}")
		parsed = []
		for record in records:
			if not isinstance(record, dict) or set(record) - set(keys):
				raise ReconciliationContractError(f"reconciliation.parse_info_invalid:{section}")
			rows = record.get("rows") or 0
			if isinstance(rows, bool) or not isinstance(rows, int) or not 0 <= rows <= MAX_ROWS:
				raise ReconciliationContractError(f"reconciliation.parse_info_rows:{section}")
			item = {
				"file": _text(record.get("file"), f"{section}.file"),
				"method": _text(record.get("method"), f"{section}.method", limit=40),
				"rows": rows,
				"parser_version": _text(record.get("parser_version"),
					f"{section}.parser_version", limit=40),
			}
			if section == "gl":
				names = record.get("accounts_seen") or []
				if (not isinstance(names, list) or len(names) > MAX_ACCOUNTS
						or any(not isinstance(name, str) for name in names)):
					raise ReconciliationContractError(
						f"reconciliation.parse_info_invalid:{section}")
				item["accounts_seen"] = [_text(name, f"{section}.accounts_seen")
					for name in names]
				for key in ("candidate_data_lines", "tables_seen", "sheets_seen", "rows_kept"):
					if key not in record:
						continue
					count = record.get(key)
					if isinstance(count, bool) or not isinstance(count, int) or count < 0:
						raise ReconciliationContractError(
							f"reconciliation.parse_info_invalid:{section}")
					item[key] = count
				if "header_found" in record:
					item["header_found"] = bool(record.get("header_found"))
			else:
				warnings = record.get("warnings") or []
				if (not isinstance(warnings, list) or len(warnings) > MAX_WARNINGS
						or any(not isinstance(warning, str) for warning in warnings)):
					raise ReconciliationContractError(
						f"reconciliation.parse_info_invalid:{section}")
				item["warnings"] = [_text(warning, f"{section}.warnings")
					for warning in warnings]
				if record.get("tax_id"):
					item["tax_id"] = _text(record.get("tax_id"), f"{section}.tax_id", limit=13)
				pages = record.get("photo_pages") or []
				if pages:
					if (not isinstance(pages, list) or len(pages) > MAX_PHOTO_PAGES
							or any(not isinstance(page, list) or len(page) != 2 or not isinstance(page[1], list)
								or len(page[1]) > MAX_PAGE_REFS for page in pages)):
						raise ReconciliationContractError(f"reconciliation.parse_info_invalid:{section}")
					item["photo_pages"] = [[_text(label, f"{section}.photo_pages"),
						[_text(ref, f"{section}.photo_pages") for ref in refs]] for label, refs in pages]
			parsed.append(item)
		result[section] = parsed
	return result


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
