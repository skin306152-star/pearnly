"""Decoded bank-reconciliation rows and their native storage contract.

The new Cowork entry point accepts rows that were already decoded by a caller;
file parsing and model extraction stay in the legacy project. Numbers arrive as
decimal strings and are validated here with ``Decimal`` so a stored row keeps an
exact value, while matching still runs on the deployed binary-float contract
(``bank_matching``), because that is what decides historical results.

Pure contract: no Frappe, database, file, clock, HTTP or model access.
"""

from datetime import date
from decimal import Decimal

from pearnly_cowork.reconciliation.recon_contracts import (
	DECIMAL_PLACES,
	MAX_DECIMAL_DIGITS,
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

SOURCE_FORMAT = "decoded_rows_v1"

STATEMENT_KEYS = frozenset(("date", "description", "withdrawal", "deposit", "balance",
	"confidence", "balance_ok", "autocorrected"))
GL_KEYS = frozenset(("date", "doc_no", "account_code", "description", "debit", "credit",
	"balance"))
HEADER_AMOUNTS = ("stmt_opening", "gl_opening", "stmt_closing", "gl_closing")
MAX_PARSE_FILES = 200
MAX_ACCOUNTS = 100
MAX_DIAGNOSTIC = 1000
MAX_WARNINGS = 20
MAX_WARNING_TEXT = 500

# The four anchor amounts the deployed run snapshots and lets a user override; the
# workbook shows the OCR value beside the one actually used.
ANCHOR_KEYS = ("stmt_opening", "gl_opening", "gl_closing", "stmt_closing")
# The deployed run saved `_parse_info` with these two sections and record keys so
# the workbook's File Info section could show real per-file numbers.
PARSE_INFO_SECTIONS = {
	# recognized：这份账单是识别读出的（照片 / 扫描件；2026-09-30 加，旧任务没有）。
	"stmt_files": ("file", "rows", "ok", "error", "error_code", "bank_code", "recognized"),
	"gl_files": ("file", "rows", "ok", "error", "error_code", "accounts"),
}
CONFIDENCE_LEVELS = ("high", "medium", "low")
BALANCE_STATES = ("ok", "mismatch")


def statement_row(raw):
	"""Validate one decoded statement row into an exact stored representation."""
	raw = _exact_keys(raw, STATEMENT_KEYS, "statement")
	confidence = _text(raw.get("confidence"), "statement.confidence", limit=10) or "high"
	if confidence not in CONFIDENCE_LEVELS:
		raise ReconciliationContractError("reconciliation.confidence_invalid")
	balancestate = _text(raw.get("balance_ok"), "statement.balance_ok", limit=10)
	if balancestate and balancestate not in BALANCE_STATES:
		raise ReconciliationContractError("reconciliation.balance_state_invalid")
	balance = _amount(raw.get("balance"), "statement.balance", nonnegative=False)
	return {
		"date": _date(raw.get("date"), "statement.date"),
		# 摘要可以空（利息、手续费行常没有；旧版同样按空串收）：一行没摘要不该让整份对账失败（2026-09-28）。
		"description": _text(raw.get("description"), "statement.description"),
		"withdrawal": _amount(raw.get("withdrawal"), "statement.withdrawal"),
		"deposit": _amount(raw.get("deposit"), "statement.deposit"),
		"balance": balance,
		"confidence": confidence,
		"balance_ok": balancestate,
		"autocorrected": bool(raw.get("autocorrected")),
	}


def gl_row(raw):
	"""Validate one decoded general-ledger row into an exact stored representation."""
	raw = _exact_keys(raw, GL_KEYS, "gl")
	# 总账里写成负数的是冲销 / 红字：负的借方记成贷方、负的贷方记成借方，净额不变（与收入对账同一口径，
	# 2026-09-28 收入先修；2026-09-29 排查银行这边：「(500.00)」被当成借方 500，余额差 1,000）。
	debit = _amount(raw.get("debit"), "gl.debit", nonnegative=False) or Decimal(0)
	credit = _amount(raw.get("credit"), "gl.credit", nonnegative=False) or Decimal(0)
	debit, credit = max(debit, Decimal(0)) + max(-credit, Decimal(0)), max(credit, Decimal(0)) + max(-debit, Decimal(0))
	return {
		"date": _date(raw.get("date"), "gl.date"),
		"doc_no": _text(raw.get("doc_no"), "gl.doc_no"),
		"account_code": _text(raw.get("account_code"), "gl.account_code"),
		"description": _text(raw.get("description"), "gl.description"),
		"debit": debit,
		"credit": credit,
		"balance": _amount(raw.get("balance"), "gl.balance", nonnegative=False),
	}


def header(value):
	"""Validate the optional header amounts; unknown keys are refused, not stored."""
	if value in (None, ""):
		value = {}
	if not isinstance(value, dict) or set(value) - set(HEADER_AMOUNTS) - {"bank_code", "gl_account_code"}:
		raise ReconciliationContractError("reconciliation.header_invalid")
	result = {key: _amount(value.get(key), f"header.{key}", nonnegative=False) or Decimal(0)
		for key in HEADER_AMOUNTS}
	result["bank_code"] = _text(value.get("bank_code"), "header.bank_code", limit=40)
	result["gl_account_code"] = _text(value.get("gl_account_code"), "header.gl_account_code", limit=40)
	return result


def parse_info(value):
	"""Validate the optional per-file parse diagnostics stored with one task.

	Only the two deployed sections with their deployed record keys are kept; the
	workbook's File Info section reads these names, so an unknown section or record
	key is refused instead of stored and silently ignored.
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
			count = record.get("rows") or 0
			if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_ROWS:
				raise ReconciliationContractError(f"reconciliation.parse_info_rows:{section}")
			accounts = record.get("accounts") or []
			if (not isinstance(accounts, list) or len(accounts) > MAX_ACCOUNTS
					or any(not isinstance(account, str) for account in accounts)):
				raise ReconciliationContractError(f"reconciliation.parse_info_invalid:{section}")
			item = {
				"file": _text(record.get("file"), f"{section}.file"),
				"rows": count,
				"ok": bool(record.get("ok")),
				"error": _text(record.get("error"), f"{section}.error", limit=MAX_DIAGNOSTIC) or None,
				"error_code": _text(record.get("error_code"), f"{section}.error_code") or None,
			}
			if section == "stmt_files":
				item["bank_code"] = _text(record.get("bank_code"), f"{section}.bank_code", limit=40)
				if record.get("recognized"):
					item["recognized"] = True
			else:
				item["accounts"] = [_text(account, f"{section}.accounts") for account in accounts]
			parsed.append(item)
		result[section] = parsed
	return result


def decode_rows(statement_rows, gl_rows):
	"""Validate both sides and refuse an empty or oversized batch."""
	if not isinstance(statement_rows, list) or not isinstance(gl_rows, list):
		raise ReconciliationContractError("reconciliation.rows_invalid")
	if len(statement_rows) + len(gl_rows) > MAX_ROWS:
		raise ReconciliationContractError("reconciliation.rows_too_many")
	return ([statement_row(row) for row in statement_rows], [gl_row(row) for row in gl_rows])


def run_info(value):
	"""Validate the optional run warnings and anchor records stored with one task.

	The three names are the deployed ones: ``warnings`` holds the strings the run
	would have shown, ``anchor_ocr`` the four amounts the reader saw and
	``anchor_overrides`` those the user replaced, each as ``{"ocr": …, "user": …}``.
	An unknown key, a non-string warning, an unknown anchor or a non-numeric amount
	is refused instead of stored and silently ignored.
	"""
	if value in (None, ""):
		return {}
	if not isinstance(value, dict) or set(value) - {"warnings", "anchor_ocr", "anchor_overrides"}:
		raise ReconciliationContractError("reconciliation.run_info_invalid")
	result = {}
	warnings = value.get("warnings")
	if warnings not in (None, ""):
		if (not isinstance(warnings, list) or len(warnings) > MAX_WARNINGS
				or any(not isinstance(warning, str) or len(warning) > MAX_WARNING_TEXT
					for warning in warnings)):
			raise ReconciliationContractError("reconciliation.run_info_invalid:warnings")
		# A run with nothing to warn about stores no section at all.
		if warnings:
			result["warnings"] = list(warnings)
	ocr = value.get("anchor_ocr")
	if ocr not in (None, ""):
		if not isinstance(ocr, dict) or set(ocr) - set(ANCHOR_KEYS):
			raise ReconciliationContractError("reconciliation.run_info_invalid:anchor_ocr")
		stored = {}
		for key in ANCHOR_KEYS:
			if key not in ocr:
				continue
			amount = ocr.get(key)
			if amount is None:
				stored[key] = None
				continue
			try:
				stored[key] = float(str(amount).replace(",", "").strip())
			except (TypeError, ValueError):
				raise ReconciliationContractError(
					f"reconciliation.run_info_invalid:anchor_ocr.{key}") from None
		if stored:
			result["anchor_ocr"] = stored
	overrides = value.get("anchor_overrides")
	if overrides not in (None, ""):
		if not isinstance(overrides, dict) or set(overrides) - set(ANCHOR_KEYS):
			raise ReconciliationContractError("reconciliation.run_info_invalid:anchor_overrides")
		stored = {}
		for key, record in overrides.items():
			if not isinstance(record, dict) or set(record) - {"ocr", "user"} or "user" not in record:
				raise ReconciliationContractError(
					f"reconciliation.run_info_invalid:anchor_overrides.{key}")
			try:
				user = float(str(record["user"]).replace(",", "").strip())
			except (TypeError, ValueError):
				raise ReconciliationContractError(
					f"reconciliation.run_info_invalid:anchor_overrides.{key}") from None
			original = record.get("ocr")
			if original is not None:
				try:
					original = float(str(original).replace(",", "").strip())
				except (TypeError, ValueError):
					raise ReconciliationContractError(
						f"reconciliation.run_info_invalid:anchor_overrides.{key}") from None
			stored[key] = {"ocr": original, "user": user}
		if stored:
			result["anchor_overrides"] = stored
	return result


def digest(header_value, statement_rows, gl_rows):
	"""Canonical fingerprint of one request, used for exact replay detection."""
	payload = {"header": {key: str(value) if isinstance(value, Decimal) else value
						for key, value in header_value.items()},
		"statement": [{key: value.isoformat() if isinstance(value, date) else str(value)
						for key, value in row.items()} for row in statement_rows],
		"gl": [{key: value.isoformat() if isinstance(value, date) else str(value)
				for key, value in row.items()} for row in gl_rows]}
	return canonical_digest(payload)


def matching_statement_rows(rows):
	"""Convert stored rows to the deployed matching contract.

	Storage keeps exact decimals; the deployed matcher compares binary floats,
	so this is the one place the documented float contract is re-entered.
	"""
	from pearnly_cowork.reconciliation.bank_statement import StatementRow
	result = []
	for row in rows:
		# The deployed row formats its balance into the fingerprint, so an unread
		# balance column is assigned after construction.
		item = StatementRow(row["date"], row["description"], float(row["withdrawal"]),
			float(row["deposit"]), 0.0 if row["balance"] is None else float(row["balance"]),
			confidence=row["confidence"],
			balance_ok=None if not row["balance_ok"] else row["balance_ok"] == "ok",
			amount_autocorrected=row["autocorrected"], direction_autocorrected=row["autocorrected"])
		if row["balance"] is None:
			item.balance = None
		result.append(item)
	return result


def matching_gl_rows(rows):
	from pearnly_cowork.reconciliation.bank_matching import GlRow
	return [GlRow(row["date"], row["doc_no"], row["account_code"], row["description"],
		float(row["debit"]), float(row["credit"]),
		balance=0.0 if row["balance"] is None else float(row["balance"])) for row in rows]
