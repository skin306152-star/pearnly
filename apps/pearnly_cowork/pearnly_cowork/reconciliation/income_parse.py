"""Deployed conversion of already-extracted GL content into ledger rows.

Source: ec2e5c957e6b ``services/recon/gl_vat_parse_common.py`` (the leaf helpers and
the column-name constants), ``services/recon/gl_vat_parse_excel.py``
(``parse_gl_excel`` and ``_gl_rows_from_pipeline_legacy``) and
``services/recon/gl_vat_parse_pdf.py`` (``parse_gl_pdf``'s table path and
``_parse_gl_text_lines``).

Opening the workbook or the PDF stays in the legacy project: these functions start
from the cell rows a sheet produced, the per-page text and tables a PDF reader
extracted, or the decoded entries the OCR pipeline normalized, and give back the
same ``GlRow`` rows the deployed reconcile consumed, including the deployed quirks:

* a row is a section header only while no account column was found, and only when
  it carries no amount in the columns already known;
* the Excel path converts a date cell to an ISO string and shifts a Buddhist year,
  while the PDF table path keeps the raw cell text;
* the text-line path accepts both the three-number tail and the two-number tail
  with the direction decided by the running balance, inherits the date of the
  previous line, and treats the return wording as a debit;
* the table path falls back to the text lines when it kept no row at all.

The output rows reach native storage through ``income_recon_contracts``, so a row
the decoded-row contract refuses (a value with more than six decimals, a text over
the stored limit, an oversized batch) is an error here instead of a silent drop.

The ``error`` field of a refused source carries the deployed one-line wording
(``services/ocr/error_format.short_error``), not the raw exception text: the field
is what the accountant reads, and a pydantic dump is not a sentence.

Pure logic: no Frappe, database, file, clock, HTTP, model or export access.
"""

from datetime import date, datetime
import re
from typing import Any, Dict, List, Optional, Sequence

from pearnly_cowork.contracts.errors import short_error
from pearnly_cowork.reconciliation.income_vat import GlRow, normalize_doc_no
from pearnly_cowork.reconciliation.recon_contracts import ReconciliationContractError

# Identity of a task admitted from extracted GL content rather than decoded rows.
SOURCE_FORMAT = "decoded_gl_rows_v1"
PARSER_VERSION = "1.0.0"

# Column-name keywords, as mixed as the printed headers are.
DATE_HINTS = frozenset(("วันที่", "date", "วัน", "日期"))
DOC_NO_HINTS = frozenset(("ใบสำคัญ", "เลขที่เอกสาร", "doc no", "voucher", "reference", "เอกสาร",
	"凭证", "单据"))
DESCRIPTION_HINTS = frozenset(("คำอธิบาย", "รายการ", "description", "detail", "รายละเอียด",
	"摘要", "说明"))
DEBIT_HINTS = frozenset(("เดบิต", "debit", "dr", "借方", "借"))
CREDIT_HINTS = frozenset(("เครดิต", "credit", "cr", "贷方", "贷"))
ACCOUNT_HINTS = frozenset(("รหัสบัญชี", "account code", "gl account", "เลขที่บัญชี", "รหัส",
	"账号", "科目"))

# Account-code shape: four to seven digits with an optional two or three digit
# suffix (4110, 4110-01, 41100-001, 411000). A decimal point never separates, so an
# amount such as "419.57" is not read as an account.
ACCOUNT_RE = re.compile(r"(?<![\d.])([4-9]\d{3,6}(?:[\-–]\d{2,3})?)(?![\d.])")

# Summary and carried-forward lines, skipped. The bare "รวม" is too wide to include.
SKIP_ROWS = frozenset((
	"ยอดยกมา", "ยอดยกไป", "ยอดรวมยกมา", "ยอดรวมยกไป",
	"balance forward", "carried forward", "brought forward",
	"subtotal", "opening balance", "closing balance",
	"รวมประจำเดือน", "รวมแต่ละหน้า", "รวมทั้งสิ้น",
))

# Return and credit-note wording: such a line is a debit on the two-number tail.
DEBIT_LINE_KEYWORDS = frozenset(("ลดหนี้", "รับคืน", "คืนสินค้า", "ส่งคืน"))

# Text-line patterns of the printed ledger: a date, a voucher number, and a tail of
# three numbers (debit, credit, balance) or of two (amount, balance; a zero column
# is left out entirely).
DATE_RE = re.compile(r"(\d{1,2}[\-/.]\d{1,2}[\-/.]\d{2,4})")
TAIL_NUMBERS_3_RE = re.compile(
	r"((?:\(\s*[\d,]+\.?\d*\s*\)|-?[\d,]+\.\d{2}|-?[\d,]{4,}))"
	r"\s+"
	r"((?:\(\s*[\d,]+\.?\d*\s*\)|-?[\d,]+\.\d{2}|-?[\d,]{4,}))"
	r"\s+"
	r"((?:\(\s*[\d,]+\.?\d*\s*\)|-?[\d,]+\.\d{2}|-?[\d,]{4,}))"
	r"\s*$"
)
TAIL_NUMBERS_2_RE = re.compile(
	r"((?:\(\s*[\d,]+\.?\d*\s*\)|-?[\d,]+\.\d{2}|-?[\d,]{4,}))"
	r"\s+"
	r"((?:\(\s*[\d,]+\.?\d*\s*\)|-?[\d,]+\.\d{2}|-?[\d,]{4,}))"
	r"\s*$"
)
VOUCHER_RE = re.compile(r"\b[A-Z][A-Z0-9/\-]{2,}\b")

KINDS = {
	"excel": ("sheets",),
	"pdf": ("pages", "lines"),
	"text": ("lines",),
	"pipeline": ("decoded",),
}


def to_float(value) -> float:
	"""Read one printed amount; anything unreadable is the deployed zero."""
	if value is None:
		return 0.0
	text = str(value).strip().replace(",", "").replace(" ", "").replace("\xa0", "")
	if not text or text in {"-", "–"}:
		return 0.0
	negative = False
	if text.startswith("(") and text.endswith(")"):
		negative = True
		text = text[1:-1]
	try:
		number = round(float(text), 2)
		return -number if negative else number
	except Exception:
		return 0.0


def is_revenue_account(code, prefix: str = "4") -> bool:
	"""Whether an account code belongs to the revenue side (four by default)."""
	value = str(code or "").strip()
	return bool(value and value.startswith(prefix))


def header_hit(header, hints) -> bool:
	value = str(header or "").strip().lower()
	return any(hint.lower() in value for hint in hints)


def extract_account_code(text) -> Optional[str]:
	match = ACCOUNT_RE.search(str(text or ""))
	return match.group(1) if match else None


def is_debit_line(text) -> bool:
	"""Whether a printed line carries the return wording that makes it a debit."""
	return any(keyword in text for keyword in DEBIT_LINE_KEYWORDS)


def is_skip_row(cells) -> bool:
	"""Whether a row is a summary or carried-forward line; a coded row never is."""
	if not cells:
		return False
	joined = " ".join(str(cell or "").strip().lower() for cell in cells)
	return any(keyword in joined for keyword in SKIP_ROWS) and not ACCOUNT_RE.search(joined)


def map_gl_columns(header_row) -> Dict[str, int]:
	"""Printed header row to {field name: column index}; the first hit wins."""
	col_map: Dict[str, int] = {}
	for index, cell in enumerate(header_row):
		header = str(cell or "").strip().lower()
		if not header:
			continue
		if "date" not in col_map and header_hit(header, DATE_HINTS):
			col_map["date"] = index
		elif "doc_no" not in col_map and header_hit(header, DOC_NO_HINTS):
			col_map["doc_no"] = index
		elif "description" not in col_map and header_hit(header, DESCRIPTION_HINTS):
			col_map["description"] = index
		elif "debit" not in col_map and header_hit(header, DEBIT_HINTS):
			col_map["debit"] = index
		elif "credit" not in col_map and header_hit(header, CREDIT_HINTS):
			col_map["credit"] = index
		elif "account" not in col_map and header_hit(header, ACCOUNT_HINTS):
			col_map["account"] = index
	return col_map


def row_has_amount(cells, col_map) -> bool:
	"""Whether a row shows a movement in the columns already identified."""
	debit = (to_float(cells[col_map["debit"]])
		if "debit" in col_map and col_map["debit"] < len(cells) else 0.0)
	credit = (to_float(cells[col_map["credit"]])
		if "credit" in col_map and col_map["credit"] < len(cells) else 0.0)
	return debit != 0.0 or credit != 0.0


def _has_header(cells) -> bool:
	"""A row is the printed header when it names both movement columns."""
	return (any(header_hit(str(cell or ""), DEBIT_HINTS) for cell in cells)
		and any(header_hit(str(cell or ""), CREDIT_HINTS) for cell in cells))


def _excel_row_text(cell) -> str:
	"""Read one Excel cell the deployed way: a date cell becomes an ISO string.

	A year from 2500 on is a Buddhist year and is shifted back; a date the calendar
	refuses falls through to the printed value.
	"""
	if cell is None:
		return ""
	if isinstance(cell, (datetime, date)):
		year = cell.year
		if year >= 2500:
			year -= 543
		try:
			return date(year, cell.month, cell.day).isoformat()
		except (ValueError, AttributeError):
			pass
	return str(cell).strip()


def excel_rows(sheets, revenue_prefix: str = "4") -> Dict[str, Any]:
	"""Decode the cell rows of every worksheet into revenue ledger rows.

	``sheets`` is the per-worksheet list of cell rows, as the reader handed them
	over. Two printed shapes are served: an account column carrying the code on
	every row, and the code standing alone on a section-header row.
	"""
	if not isinstance(sheets, (list, tuple)):
		raise ReconciliationContractError("reconciliation.gl_source_invalid")
	rows: List[GlRow] = []
	try:
		for sheet in sheets:
			current_account = ""
			col_map: Dict[str, int] = {}
			has_account_col = False
			for raw_row in sheet:
				cells = list(raw_row)
				if not any(cell is not None and str(cell).strip() for cell in cells):
					continue
				if _has_header(cells):
					col_map = map_gl_columns(cells)
					has_account_col = "account" in col_map
					continue
				row_text = " ".join(str(cell or "") for cell in cells)
				if not has_account_col:
					account = extract_account_code(row_text)
					if account and is_revenue_account(account, revenue_prefix):
						if not row_has_amount(cells, col_map or {}):
							current_account = account
							continue
				if is_skip_row(cells):
					continue
				if not col_map:
					continue

				def _get(field, cells=cells, col_map=col_map):
					index = col_map.get(field)
					if index is None or index >= len(cells):
						return ""
					return _excel_row_text(cells[index])

				if has_account_col:
					raw = _get("account")
					account = extract_account_code(raw) or raw
					if not is_revenue_account(account, revenue_prefix):
						continue
					current_account = account
				elif not current_account:
					continue
				doc_no = _get("doc_no")
				if not doc_no:
					continue
				debit = to_float(_get("debit"))
				credit = to_float(_get("credit"))
				if debit == 0.0 and credit == 0.0:
					continue
				rows.append(GlRow(doc_no=doc_no, norm_doc_no=normalize_doc_no(doc_no),
					date=_get("date"), account_code=current_account,
					description=_get("description"), debit=debit, credit=credit))
	except ReconciliationContractError:
		raise
	except Exception as error:
		return {"ok": False, "rows": [], "row_count": 0, "error": short_error(error),
			"diag": {"method": "excel", "exception": str(error)}}
	return {"ok": True, "rows": rows, "row_count": len(rows), "error": "",
		"diag": {"method": "excel", "rows_kept": len(rows), "sheets_seen": len(sheets)}}


def table_rows(pages, revenue_prefix: str = "4") -> Dict[str, Any]:
	"""Decode the tables a PDF reader extracted, per page, into revenue rows.

	``pages`` is one dictionary per page with the extracted ``tables`` and the page
	``text``: the section heading of a ledger page usually sits outside the table,
	so the first revenue code printed on the page names the section for the rows
	below it.
	"""
	if not isinstance(pages, (list, tuple)):
		raise ReconciliationContractError("reconciliation.gl_source_invalid")
	rows: List[GlRow] = []
	current_account = ""
	col_map: Dict[str, int] = {}
	accounts_seen: List[str] = []
	tables_seen = 0
	header_found = False
	try:
		for page in pages:
			if not isinstance(page, dict):
				raise ReconciliationContractError("reconciliation.gl_source_invalid")
			tables = page.get("tables") or []
			if not isinstance(tables, (list, tuple)):
				raise ReconciliationContractError("reconciliation.gl_source_invalid")
			tables_seen += len(tables)
			for line in str(page.get("text") or "").split("\n"):
				account = extract_account_code(line)
				if account and is_revenue_account(account, revenue_prefix):
					current_account = account
					if account not in accounts_seen:
						accounts_seen.append(account)
					break
			for table in tables:
				if not table:
					continue
				for raw_row in table:
					if not raw_row or not any(cell for cell in raw_row):
						continue
					cells = list(raw_row)
					row_text = " ".join(str(cell or "") for cell in cells)
					account = extract_account_code(row_text)
					if account and is_revenue_account(account, revenue_prefix):
						if not row_has_amount(cells, col_map or {}):
							current_account = account
							if account not in accounts_seen:
								accounts_seen.append(account)
							continue
					if _has_header(cells):
						col_map = map_gl_columns(cells)
						header_found = True
						continue
					if is_skip_row(cells):
						continue
					if not col_map or not current_account:
						continue

					def _get(field, cells=cells, col_map=col_map):
						index = col_map.get(field)
						if index is None or index >= len(cells):
							return ""
						return str(cells[index] or "").strip()

					doc_no = _get("doc_no")
					if not doc_no:
						continue
					debit = to_float(_get("debit"))
					credit = to_float(_get("credit"))
					if debit == 0.0 and credit == 0.0:
						continue
					rows.append(GlRow(doc_no=doc_no, norm_doc_no=normalize_doc_no(doc_no),
						date=_get("date"), account_code=current_account,
						description=_get("description"), debit=debit, credit=credit))
	except ReconciliationContractError:
		raise
	except Exception as error:
		# The deployed reader logged the failure and let the text fallback run.
		rows = []
		return {"ok": True, "rows": [], "row_count": 0, "error": "",
			"diag": {"method": "tables", "tables_seen": tables_seen,
				"header_found": header_found, "accounts_seen": accounts_seen[:10],
				"rows_kept": 0, "exception": str(error)}}
	if rows:
		return {"ok": True, "rows": rows, "row_count": len(rows), "error": "",
			"diag": {"method": "tables", "tables_seen": tables_seen,
				"header_found": header_found, "accounts_seen": accounts_seen[:10],
				"rows_kept": len(rows)}}
	return {"ok": True, "rows": [], "row_count": 0, "error": "",
		"diag": {"method": "tables", "tables_seen": tables_seen, "header_found": header_found,
			"accounts_seen": accounts_seen[:10], "rows_kept": 0}}


def text_rows(lines, revenue_prefix: str = "4") -> Dict[str, Any]:
	"""Decode the printed text lines of a ledger PDF into revenue rows.

	Two printed styles are served: the older one with a three-number tail (debit,
	credit, balance, zeros printed as ``0.00``) and the newer one with a two-number
	tail (amount, balance, an empty column left out) where the direction follows the
	running balance and the return wording of the line.
	"""
	if not isinstance(lines, (list, tuple)):
		raise ReconciliationContractError("reconciliation.gl_source_invalid")
	rows: List[GlRow] = []
	current_account = ""
	accounts_seen: List[str] = []
	data_lines = 0
	last_date = ""
	previous_balance = None
	try:
		for raw_line in lines:
			line = str(raw_line or "").strip()
			if not line:
				continue
			account = extract_account_code(line)
			if account and is_revenue_account(account, revenue_prefix):
				# A section heading names the code but carries no amount tail; its
				# parenthesised opening balance seeds the direction test.
				if not TAIL_NUMBERS_3_RE.search(line) and not TAIL_NUMBERS_2_RE.search(line):
					current_account = account
					last_date = ""
					opening = re.search(r"\(\s*[\d,]+\.\d{2}\s*\)", line)
					previous_balance = to_float(opening.group(0)) if opening else None
					if account not in accounts_seen:
						accounts_seen.append(account)
					continue
			if not current_account:
				continue
			lowered = line.lower()
			if any(keyword in lowered for keyword in SKIP_ROWS):
				continue
			if line.startswith("รวม") and not VOUCHER_RE.search(line):
				continue
			tail_3 = TAIL_NUMBERS_3_RE.search(line)
			tail_2 = None if tail_3 else TAIL_NUMBERS_2_RE.search(line)
			if not (tail_3 or tail_2):
				continue
			if not VOUCHER_RE.search(line):
				continue
			data_lines += 1
			match_date = DATE_RE.search(line)
			if match_date:
				date_text = match_date.group(1)
				last_date = date_text
				after_date = line[match_date.end():].strip()
			else:
				date_text = last_date
				after_date = line
			tokens = after_date.split()
			if len(tokens) < 3:
				continue
			doc_no = ""
			for token in tokens:
				if VOUCHER_RE.fullmatch(token):
					doc_no = token
					break
			if not doc_no:
				continue
			if tail_3:
				debit = to_float(tail_3.group(1))
				credit = to_float(tail_3.group(2))
				previous_balance = to_float(tail_3.group(3))
				tail = tail_3
			else:
				amount = abs(to_float(tail_2.group(1)))
				new_balance = to_float(tail_2.group(2))
				debit, credit = 0.0, 0.0
				# The return wording wins over the balance test, which itself falls
				# back to credit for a revenue ledger.
				if is_debit_line(after_date):
					debit = amount
				elif previous_balance is not None:
					delta = abs(new_balance) - abs(previous_balance)
					if abs(delta - amount) < 0.01:
						credit = amount
					elif abs(delta + amount) < 0.01:
						debit = amount
					else:
						credit = amount
				else:
					credit = amount
				previous_balance = new_balance
				tail = tail_2
			if debit == 0.0 and credit == 0.0:
				continue
			rest = after_date
			doc_index = rest.find(doc_no)
			description_start = doc_index + len(doc_no) if doc_index >= 0 else 0
			amount_text = tail.group(1)
			description_end = rest.rfind(amount_text)
			description = (rest[description_start:description_end].strip()
				if description_end > description_start else "")
			rows.append(GlRow(doc_no=doc_no, norm_doc_no=normalize_doc_no(doc_no),
				date=date_text, account_code=current_account, description=description,
				debit=debit, credit=credit))
	except ReconciliationContractError:
		raise
	except Exception as error:
		return {"ok": False, "rows": [], "row_count": 0, "error": short_error(error),
			"diag": {"method": "text_lines", "exception": str(error)}}
	return {"ok": True, "rows": rows, "row_count": len(rows), "error": "",
		"diag": {"method": "text_lines", "accounts_seen": accounts_seen[:10],
			"candidate_data_lines": data_lines, "rows_kept": len(rows)}}


def pdf_rows(pages, lines, revenue_prefix: str = "4") -> Dict[str, Any]:
	"""Decode a ledger PDF from its tables, falling back to its printed lines.

	The tables are read first because a framed export extracts cleanly; a PDF
	without frames keeps no row there, so the printed text lines are read instead.
	"""
	primary = table_rows(pages, revenue_prefix)
	if primary["rows"]:
		return primary
	fallback = text_rows(lines, revenue_prefix)
	fallback["diag"]["primary_tried"] = "tables"
	fallback["diag"]["primary_tables_seen"] = primary["diag"]["tables_seen"]
	fallback["diag"]["primary_header_found"] = primary["diag"]["header_found"]
	fallback["diag"]["primary_accts"] = primary["diag"]["accounts_seen"]
	return fallback


def pipeline_rows(decoded, revenue_prefix: str = "4") -> List[GlRow]:
	"""Decode the normalized JSON of the OCR pipeline into revenue rows.

	The pipeline path serves spreadsheets, Word, images and plain text, and is what
	a PDF falls back to when the table and text readers cannot serve it. Extraction
	itself stays outside the platform.
	"""
	if decoded in (None, ""):
		decoded = {}
	if not isinstance(decoded, dict):
		raise ReconciliationContractError("reconciliation.gl_source_invalid")
	rows: List[GlRow] = []
	for page in decoded.get("pages") or []:
		document = (page or {}).get("document") or {}
		for entry in document.get("entries") or []:
			account_code = (entry.get("account_code") or "").strip()
			if revenue_prefix and not account_code.startswith(revenue_prefix):
				continue
			try:
				debit = float(entry.get("debit") or 0)
			except (ValueError, TypeError):
				debit = 0.0
			try:
				credit = float(entry.get("credit") or 0)
			except (ValueError, TypeError):
				credit = 0.0
			if debit == 0.0 and credit == 0.0:
				continue
			doc_no = (entry.get("voucher_no") or "").strip()
			try:
				normalized = normalize_doc_no(doc_no) if doc_no else ""
			except Exception:
				normalized = doc_no
			rows.append(GlRow(doc_no=doc_no, norm_doc_no=normalized,
				date=entry.get("transaction_date") or "", account_code=account_code,
				description=entry.get("description") or "", debit=debit, credit=credit))
	return rows


def decode(source, revenue_prefix: str = "4") -> Dict[str, Any]:
	"""Decode one already-extracted GL source into rows, in the deployed shape."""
	if not isinstance(source, dict) or source.get("kind") not in KINDS:
		raise ReconciliationContractError("reconciliation.gl_source_invalid")
	kind = source["kind"]
	missing = [field for field in KINDS[kind] if source.get(field) in (None, "")]
	if missing:
		raise ReconciliationContractError("reconciliation.gl_source_invalid")
	if kind == "excel":
		return excel_rows(source["sheets"], revenue_prefix)
	if kind == "pdf":
		return pdf_rows(source["pages"], source["lines"], revenue_prefix)
	if kind == "text":
		return text_rows(source["lines"], revenue_prefix)
	rows = pipeline_rows(source["decoded"], revenue_prefix)
	return {"ok": True, "rows": rows, "row_count": len(rows), "error": "",
		"diag": {"method": "pipeline", "rows_kept": len(rows)}}


def decoded_rows(rows: Sequence[GlRow]) -> List[Dict[str, Any]]:
	"""Convert decoded ledger rows into the stored row contract."""
	return [{"doc_no": row.doc_no, "norm_doc_no": row.norm_doc_no, "date": row.date,
		"account_code": row.account_code, "description": row.description,
		"debit": row.debit, "credit": row.credit} for row in rows]
