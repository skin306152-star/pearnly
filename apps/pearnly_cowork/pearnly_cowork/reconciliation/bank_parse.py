"""Deployed conversion of already-decoded pages into bank reconciliation rows.

Source: ec2e5c957e6b ``services/recon/bank_recon_pipeline.py``
(``statement_rows_from_entries`` plus the post-decode half of
``_parse_bank_stmt_via_pipeline`` and ``_parse_gl_via_pipeline``),
``services/recon/bank_stmt_legacy.py:gl_rows_from_pipeline_legacy`` and the
primitives ``_to_float``, ``_parse_date``, ``_BANK_SIGNATURES`` and ``_detect_bank``
from ``services/recon/bank_recon_utils.py``. The printed month names are the
deployed canonical table from ``core/thai_date.py``.

Reading a file, running OCR and calling a model stay in the legacy project: these
functions start from the decoded page dictionary that path produced and give back
the same rows the deployed reconcile consumed, balance chain included
(``bank_statement.finalize_rows``). Nothing here reads Frappe, a database, a file,
a clock, HTTP or a model.

Registered intentional differences against the deployed admission path:

* these rows reach native storage through ``bank_recon_contracts``, so a row with
  no description, a negative movement amount or a batch over ``MAX_ROWS`` is
  refused instead of matched; and
* a parsed row whose balance column was never read arrives as ``0.0`` (the
  deployed ``_to_float`` default) and is stored as a known zero, while the
  decoded-row contract can express the same row as an unknown balance.
"""

from datetime import date, datetime
import re
from typing import Any, Dict, List, Optional

from pearnly_cowork.reconciliation.bank_matching import GlRow
from pearnly_cowork.reconciliation.bank_statement import StatementRow, finalize_rows

# Identity of a task admitted from decoded pages rather than from decoded rows.
SOURCE_FORMAT = "pipeline_pages_v1"
PARSER_VERSION = "bank_recon_v2+pipeline_v1"

# Printed month names, canonical in the deployed core/thai_date.py.
THAI_MONTHS_FULL = (
	"มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
	"กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
)
THAI_MONTHS_ABBR_DOTTED = (
	"ม.ค", "ก.พ", "มี.ค", "เม.ย", "พ.ค", "มิ.ย",
	"ก.ค", "ส.ค", "ก.ย", "ต.ค", "พ.ย", "ธ.ค",
)
ENGLISH_MONTHS_ABBR3 = (
	"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
)

# The deployed lookup is ``printed_month_map(th_abbr="dotted_trailing", en_abbr=True)``:
# full Thai names first, then dotted trailing abbreviations, then English 3-letter
# abbreviations. ``parse_date`` scans it in insertion order.
PRINTED_MONTHS = {name: index for index, name in enumerate(THAI_MONTHS_FULL, 1)}
PRINTED_MONTHS.update({name + ".": index for index, name in enumerate(THAI_MONTHS_ABBR_DOTTED, 1)})
PRINTED_MONTHS.update({name: index for index, name in enumerate(ENGLISH_MONTHS_ABBR3, 1)})

# Bank detection keywords, in the deployed order.
BANK_SIGNATURES = {
	"kbank": ("กสิกรไทย", "kasikorn", "kasikornbank", "kbank", "k-bank", "k biz"),
	"bbl": ("กรุงเทพ", "bangkok bank", "bbl"),
	"kkp": ("เกียรตินาคิน", "kiatnakin", "kkp"),
	"ktb": ("กรุงไทย", "krungthai", "ktb"),
	"scb": ("ไทยพาณิชย์", "siam commercial", "scb"),
	"bay": ("กรุงศรี", "bank of ayudhya", "bay", "krungsri"),
	"ttb": ("ทหารไทย", "ธนชาต", "tmbthanachart", "tmb", "ttb"),
}


def to_float(value) -> float:
	"""Deployed amount reader: separators, parentheses and trailing minus, 2 places."""
	if value is None:
		return 0.0
	text = str(value).strip().replace(",", "").replace(" ", "").replace("\xa0", "")
	if not text or text in {"-", "–", "—"}:
		return 0.0
	negative = False
	if text.startswith("(") and text.endswith(")"):
		negative = True
		text = text[1:-1]
	if text.startswith("-"):
		negative = True
		text = text[1:]
	# Thai dot-as-thousands-separator: "115.586.50" → "115586.50"
	dot_count = text.count(".")
	if dot_count > 1:
		last_dot = text.rfind(".")
		text = text[:last_dot].replace(".", "") + text[last_dot:]
	try:
		number = round(float(text), 2)
		return -number if negative else number
	except Exception:
		return 0.0


def parse_date(raw) -> Optional[date]:
	"""Deployed statement/ledger date reader, Buddhist-era conversion included."""
	if raw is None:
		return None
	# A datetime/date cell converts directly (Buddhist year → Gregorian, -543).
	if isinstance(raw, datetime):
		year = raw.year
		if year >= 2500:
			year -= 543
		try:
			return date(year, raw.month, raw.day)
		except ValueError:
			return None
	if isinstance(raw, date):
		year = raw.year
		if year >= 2500:
			year -= 543
		try:
			return date(year, raw.month, raw.day)
		except ValueError:
			return None
	raw = str(raw).strip()
	if not raw:
		return None

	# An ISO datetime string drops its time part so the 3-part branch below holds.
	if " " in raw or "T" in raw:
		raw = raw.split(" ")[0].split("T")[0]

	clean = raw.replace("/", "-").replace(".", "-").strip()

	for th_name, month_num in PRINTED_MONTHS.items():
		if th_name in raw:
			# e.g. "15 มกราคม 2567" or "15 ม.ค. 67"
			numbers = re.findall(r"\d+", raw)
			if len(numbers) >= 2:
				day = int(numbers[0])
				year_raw = int(numbers[-1])
				if year_raw >= 2500:
					year_raw -= 543
				elif year_raw < 100:
					# Thai short Buddhist year: 68 → BE 2568 → CE 2025
					year_raw += 1957 if year_raw >= 43 else 2000
				try:
					return date(year_raw, month_num, day)
				except ValueError:
					pass  # off-calendar combination, try the next rule

	parts = re.split(r"[-/\s]", clean)
	if len(parts) == 3:
		first, second, third = parts
		try:
			if len(first) == 4 and int(first) > 1900:
				year, month, day = int(first), int(second), int(third)
				if year >= 2500:
					year -= 543
			elif len(third) == 4 or len(third) == 2:
				day, month = int(first), int(second)
				year = int(third)
				if year >= 2500:
					year -= 543
				elif year < 100:
					year += 1957 if year >= 43 else 2000
			else:
				return None
			return date(year, month, day)
		except (ValueError, TypeError):
			pass

	return None


def detect_bank(text: str) -> str:
	"""Bank code from content or a printed bank name; ``generic`` when unknown."""
	lowered = text.lower()
	for bank_code, keywords in BANK_SIGNATURES.items():
		if any(keyword.lower() in lowered for keyword in keywords):
			return bank_code
	return "generic"


def statement_rows_from_entries(entries: List[Dict[str, Any]], filename: str) -> List[StatementRow]:
	"""Decoded statement entries → rows; the single deployed conversion of both paths.

	A row with no movement on either side is skipped; the printed date text wins
	over the normalized date, which is split back into a calendar date.
	"""
	rows: List[StatementRow] = []
	for entry in entries:
		deposit = to_float(entry.get("deposit"))
		withdrawal = to_float(entry.get("withdrawal"))
		balance = to_float(entry.get("balance"))
		if deposit == 0.0 and withdrawal == 0.0:
			continue
		tx_date = parse_date(entry.get("transaction_date_raw") or "")
		if tx_date is None and entry.get("transaction_date"):
			try:
				year, month, day = entry["transaction_date"].split("-")
				tx_date = date(int(year), int(month), int(day))
			except (ValueError, AttributeError):
				tx_date = None
		rows.append(
			StatementRow(
				date=tx_date,
				description=entry.get("description") or "",
				withdrawal=withdrawal,
				deposit=deposit,
				balance=balance,
				source_file=filename,
			)
		)
	return rows


def gl_rows_from_pages(pages_legacy: Dict[str, Any]) -> List[GlRow]:
	"""Decoded ledger pages → GL rows; a row with no debit and no credit is skipped."""
	rows: List[GlRow] = []
	for page in pages_legacy.get("pages") or []:
		document = (page or {}).get("document") or {}
		for entry in document.get("entries") or []:
			debit = to_float(entry.get("debit"))
			credit = to_float(entry.get("credit"))
			if debit == 0.0 and credit == 0.0:
				continue
			date_text = entry.get("transaction_date") or ""
			tx_date = None
			if date_text:
				try:
					year, month, day = date_text.split("-")
					tx_date = date(int(year), int(month), int(day))
				except (ValueError, AttributeError):
					tx_date = parse_date(entry.get("transaction_date_raw") or date_text)
			rows.append(
				GlRow(
					date=tx_date,
					doc_no=entry.get("voucher_no") or "",
					account_code=entry.get("account_code") or "",
					description=entry.get("description") or "",
					debit=debit,
					credit=credit,
					source_file="",
				)
			)
	return rows


def statement_from_pipeline(pages_legacy: Dict[str, Any], filename: str) -> Dict[str, Any]:
	"""Deployed statement adapter result for one already-decoded pipeline dictionary.

	The file-format dispatch, the OCR call and the extracted-page gate stay in the
	legacy project; everything after the decode is here, including the balance
	chain run in the deployed order over the document-level opening balance.
	"""
	pages = (pages_legacy or {}).get("pages") or []
	if not pages:
		return {"ok": False, "rows": [], "row_count": 0, "bank_code": "generic",
			"error": "no pages parsed"}
	document = (pages[0] or {}).get("document") or {}
	bank_code = detect_bank(document.get("bank_name") or "")
	rows = statement_rows_from_entries(document.get("entries") or [], filename)
	# The balance chain runs with the document opening because the rows carry no
	# opening row of their own once the no-movement rows were skipped.
	opening = to_float(document.get("opening_balance"))
	closing = to_float(document.get("closing_balance"))
	rows = finalize_rows(rows, opening)
	balance_warn_count = sum(1 for row in rows if row.balance_ok is False)
	# A repaired row has already flipped its own flag, so it is counted apart.
	amount_fixed_count = sum(1 for row in rows if getattr(row, "amount_autocorrected", False))
	if not closing:
		closing = next((row.balance for row in reversed(rows) if row.balance), 0.0)
	return {
		"ok": True,
		"rows": rows,
		"row_count": len(rows),
		"opening": opening,
		"closing": closing,
		"bank_code": bank_code,
		"balance_warn_count": balance_warn_count,
		"amount_fixed_count": amount_fixed_count,
		"parser_version": PARSER_VERSION,
		"needs_review": bool(
			(pages_legacy or {}).get("_needs_review", False)
			or balance_warn_count
			or amount_fixed_count
		),
	}


def gl_from_pipeline(pages_legacy: Dict[str, Any], account_code: str = "") -> Dict[str, Any]:
	"""Deployed ledger adapter result for one already-decoded pipeline dictionary."""
	rows = gl_rows_from_pages(pages_legacy or {})
	if account_code:
		rows = [row for row in rows if row.account_code == account_code]
	accounts = sorted({row.account_code for row in rows if row.account_code})
	return {
		"ok": True,
		"rows": rows,
		"row_count": len(rows),
		"accounts": accounts,
		"parser_version": PARSER_VERSION,
		"needs_review": (pages_legacy or {}).get("_needs_review", False),
	}


def decoded_statement_rows(rows: List[StatementRow]) -> List[Dict[str, Any]]:
	"""Statement rows in the native decoded-row contract (``bank_recon_contracts``)."""
	return [
		{
			"date": row.date.isoformat() if row.date else "",
			"description": row.description,
			"withdrawal": row.withdrawal,
			"deposit": row.deposit,
			"balance": row.balance,
			"confidence": row.confidence,
			"balance_ok": "" if row.balance_ok is None else ("ok" if row.balance_ok else "mismatch"),
			# The deployed result row reports both auto-corrections through one flag.
			"autocorrected": bool(row.amount_autocorrected or row.direction_autocorrected),
		}
		for row in rows
	]


def decoded_gl_rows(rows: List[GlRow]) -> List[Dict[str, Any]]:
	"""Ledger rows in the native decoded-row contract (``bank_recon_contracts``)."""
	return [
		{
			"date": row.date.isoformat() if row.date else "",
			"doc_no": row.doc_no,
			"account_code": row.account_code,
			"description": row.description,
			"debit": row.debit,
			"credit": row.credit,
			"balance": row.balance,
		}
		for row in rows
	]
