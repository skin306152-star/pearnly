"""Deployed conversion of an output-VAT report into report rows.

Source: ec2e5c957e6b ``services/vat/vat_parser_common.py`` (the leaf helpers, the
column-name keyword tables, the garbage-row filter and ``_build_row``),
``services/vat/vat_parser_excel.py: parse_excel``,
``services/vat/vat_parser_pdf.py`` (``parse_pdf_text`` and
``_parse_vat_pdf_text_lines``), ``services/vat/vat_parser_csv.py``
(``parse_csv_direct`` with its MR.ERP column table) and the conversion half of
``services/vat/vat_report_parser.py: _parse_vat_via_pipeline``.

Opening the workbook, the PDF or the CSV bytes stays with the caller: these
functions start from the cell rows the active sheet produced, the per-page text and
tables a PDF reader extracted, the decoded text of a delimited file, or the entries
the OCR pipeline normalized, and give back the same ``report_*`` rows the deployed
reconciliation consumed, including the deployed rules:

* the header is the first row whose columns name a date plus a document number or
  an amount, scanned over the first thirty rows of a sheet or ten rows of a table;
* a row whose first cell reads as a total fills the meta totals instead of the rows;
* a PDF whose text layer is thin and yields no row is not deterministic (the
  deployed caller falls back to OCR), and a PDF with a text layer but no table row
  is retried through the printed text-line patterns;
* the text-line reader keeps a line only with a date and two two-decimal amounts,
  splits a glued invoice number, refuses a reference number without a digit, and
  strips tax id, branch and stray numbers off the buyer name;
* the CSV reader prefers the MR.ERP export column names over the keyword table,
  parses its amounts through ``Decimal``, fills a missing total from net plus VAT and
  drops a row without a document number (counting it in a warning);
* the pipeline path drops a row without a real invoice number unless the engine is
  an enterprise one.

Pure logic: no Frappe, database, file, clock, HTTP, model or export access.
"""

import csv
from decimal import Decimal, ROUND_HALF_UP
import io
import re
from typing import Any, Dict, List, Optional, Sequence

from pearnly_cowork.reconciliation.normalization import (
	normalize_branch,
	normalize_tax_id,
	parse_date,
)
from pearnly_cowork.reconciliation.recon_contracts import ReconciliationContractError

PARSER_VERSION = "1.1.0"

# A deterministic reader is trusted only from this many rows on; the deployed
# routing (fall back to OCR) and the button that promises "no model" share it.
MIN_DETERMINISTIC_ROWS = 3

# Column-name keywords, in Thai, English, Chinese and Japanese: a template follows
# the language the user reads. The bare word for tax is left out on purpose, so
# tax id, pre-tax and VAT amount never steal each other's column.
DATE_HINTS = frozenset(("วันที่", "date", "วัน/เดือน/ปี", "วันที่ใบกำกับ", "วันที่เอกสาร", "日期",
	"日付"))
NO_HINTS = frozenset((
	"เลขที่", "invoice no", "เลขที่ใบกำกับ", "เลขที่เอกสาร", "no.", "发票号", "发票号码",
	"发票编号", "票号", "請求書番号", "請求書", "インボイス番号", "インボイス",
))
REF_HINTS = frozenset(("อ้างอิง", "reference", "ref no", "เอกสารอ้างอิง", "เลขอ้างอิง", "参考",
	"参照", "参考号"))
NAME_HINTS = frozenset((
	"ชื่อ", "name", "ผู้ซื้อ", "customer", "买方名称", "买方", "购买方", "客户名", "客户名称",
	"名称", "取引先", "顧客", "得意先",
))
TAX_HINTS = frozenset((
	"เลขประจำตัว", "tax id", "เลขภาษี", "tin", "税号", "税务登记号", "纳税人识别号", "买方税号",
	"税务编号", "納税者番号",
))
BRANCH_HINTS = frozenset(("สาขา", "branch", "分支", "分公司", "分店", "支店", "支社"))
NET_HINTS = frozenset((
	"ยอดก่อน vat", "มูลค่า", "net", "ก่อน vat", "ยอดก่อน", "税前", "税前金额", "不含税",
	"不含税金额", "净额", "税抜", "税抜金額",
))
VAT_HINTS = frozenset(("ภาษี", "vat", "7%", "税额", "增值税", "增值税额", "销项税", "销项税额",
	"消费税", "消費税"))
TOTAL_HINTS = frozenset(("ยอดรวม", "total", "รวม", "合计", "合计金额", "含税", "含税金额",
	"总计", "总额", "合計", "税込", "税込金額"))
SKIP_HINTS = frozenset(("รวม", "total", "ยอดรวม", "grand", "รวมทั้งสิ้น", "รวมแต่ละหน้า", "合计",
	"总计", "小计", "合計"))

# Garbage rows a PDF reader or a model grabs: a page header, a separator, a table
# caption. The document number of a data row is a single token with a real
# alphanumeric pair in it.
VAT_HEADER_KEYWORDS = (
	"ใบกำกับภาษี", "ใบกํากับภาษี", "ลำดับ", "ลําดับ", "ภาษีมูลค่า", "ภาษีมูลคา", "เลขที่ออก",
	"เลขประจำตัว", "เลขประจําตัว", "หมายเหตุ", "ผู้ซื้อ", "ผูซื้อ", "ผู้รับบริการ", "ผูรับบริการ",
	"หรือบริการ", "ชื่อสถาน", "ชื่อผู้", "ชื่อผู",
)
ORNAMENT_RE = re.compile(r"^[\-=<>.\s|*_+]+$")
ALNUM_RE = re.compile(r"[A-Za-z0-9]{2,}")

# Printed text-line patterns of a report without table frames: a date, then a tail
# of two two-decimal amounts.
VAT_DATE_RE = re.compile(r"(\d{1,2}[\-/.]\d{1,2}[\-/.]\d{2,4})")
VAT_TAIL_2_RE = re.compile(
	r"((?:\(\s*[\d,]+\.\d{2}\s*\)|-?[\d,]+\.\d{2}))"
	r"\s+"
	r"((?:\(\s*[\d,]+\.\d{2}\s*\)|-?[\d,]+\.\d{2}))"
	r"\s*$"
)
TAX_ID_RE = re.compile(r"\b(\d{13})\b")
BRANCH_CODE_RE = re.compile(r"\b(0\d{4})\b")
# A reference number starts with a letter and carries a digit, so a bare customer
# name (OHANAHAN, ASAHI) never passes.
REF_TOKEN_RE = re.compile(r"^(?=.*\d)[A-Za-z][A-Za-z0-9\-/.]+$")
# Letter spacing glues a customer name onto the invoice number; the main part is
# the letters and digits, the tail is three or more capitals.
GLUED_TAIL_RE = re.compile(r"^([A-Za-z]+\d+(?:[\-/]\d+)?)([A-Z]{3,}.*)$")

# MR.ERP output-VAT register (acvatsaled) export columns, matched exactly because
# the keyword table cannot read them.
ERP_COLUMN_FIELDS = {
	"ref": "ref_no",
	"txtsino": "invoice_no",
	"txtamt": "amount_pre_vat",
	"txtvatamt": "vat_amount",
	"vat_date": "date",
	"txtsidate": "date",
	"txtvatdate": "date",
}
MAX_HEADER_SCAN_CSV = 20
MAX_HEADER_SCAN_SHEET = 30
MAX_HEADER_SCAN_TABLE = 10
SCANNED_PDF_TEXT_LENGTH = 200

KINDS = {
	"excel": ("cells",),
	"pdf": ("pages", "lines"),
	"text": ("lines",),
	"csv": ("text",),
	"pipeline": ("decoded",),
}


def hit(header, hints) -> bool:
	return any(hint in str(header or "").strip().lower() for hint in hints)


def to_float(value) -> Optional[float]:
	"""Read one printed amount; anything unreadable stays unknown, not zero."""
	try:
		text = str(value or "").strip().replace(",", "").replace(" ", "").replace("\xa0", "")
		if not text or text in {"-", "–"}:
			return None
		negative = False
		if text.startswith("(") and text.endswith(")"):
			negative = True
			text = text[1:-1]
		number = round(float(text), 2)
		return -number if negative else number
	except Exception:
		return None


def filter_garbage_rows(rows) -> List[Dict[str, Any]]:
	"""Drop the page headers, separators and captions a reader may mistake for rows."""
	kept = []
	for row in rows:
		invoice_no = str(row.get("report_invoice_no") or "").strip()
		ref_no = str(row.get("report_ref_no") or "").strip()
		key = ref_no or invoice_no
		if not key or len(key) < 3 or len(key) > 30:
			continue
		if ORNAMENT_RE.match(key):
			continue
		if not ALNUM_RE.search(key):
			continue
		if any(keyword in key for keyword in VAT_HEADER_KEYWORDS):
			continue
		if key.count(" ") > 1:
			continue
		kept.append(row)
	return kept


def map_columns(headers) -> Dict[str, int]:
	"""Printed header row to {field: column index}; the reference wins over the number."""
	col_map: Dict[str, int] = {}
	for index, header in enumerate(headers):
		header = (str(header) or "").strip()
		if not header:
			continue
		if hit(header, DATE_HINTS) and "date" not in col_map:
			col_map["date"] = index
		elif hit(header, REF_HINTS) and "ref_no" not in col_map:
			col_map["ref_no"] = index
		elif hit(header, NO_HINTS) and "invoice_no" not in col_map:
			col_map["invoice_no"] = index
		elif hit(header, NAME_HINTS) and "buyer_name" not in col_map:
			col_map["buyer_name"] = index
		elif hit(header, TAX_HINTS) and "buyer_tax_id" not in col_map:
			col_map["buyer_tax_id"] = index
		elif hit(header, BRANCH_HINTS) and "buyer_branch" not in col_map:
			col_map["buyer_branch"] = index
		elif hit(header, NET_HINTS) and "amount_pre_vat" not in col_map:
			col_map["amount_pre_vat"] = index
		elif hit(header, VAT_HINTS) and "vat_amount" not in col_map:
			col_map["vat_amount"] = index
		elif hit(header, TOTAL_HINTS) and "total_amount" not in col_map:
			col_map["total_amount"] = index
	return col_map


def build_row(row_no: int, cells: list, col_map: Dict[str, int]) -> Dict[str, Any]:
	"""Turn one printed data row into a report row, with the deployed field names."""
	parsed: Dict[str, Any] = {"row_no": row_no}
	for field, index in col_map.items():
		raw = cells[index] if index < len(cells) else None
		value = str(raw).strip() if raw is not None else ""
		if field == "date":
			parsed_date = parse_date(value)
			parsed["report_date"] = parsed_date.isoformat() if parsed_date else value
		elif field == "invoice_no":
			parsed["report_invoice_no"] = value
		elif field == "ref_no":
			parsed["report_ref_no"] = value
		elif field == "buyer_name":
			parsed["report_buyer_name"] = value
		elif field == "buyer_tax_id":
			parsed["report_buyer_tax_id"] = normalize_tax_id(value)
		elif field == "buyer_branch":
			parsed["report_buyer_branch"] = normalize_branch(value)
		elif field == "amount_pre_vat":
			parsed["report_amount_pre_vat"] = to_float(value)
		elif field == "vat_amount":
			parsed["report_vat_amount"] = to_float(value)
		elif field == "total_amount":
			parsed["report_amount"] = to_float(value)
	parsed["is_individual"] = not bool(parsed.get("report_buyer_tax_id"))
	return parsed


def _is_header_row(cells) -> bool:
	"""A real header names a date column plus a document number or an amount."""
	col_map = map_columns([str(cell or "") for cell in cells])
	return "date" in col_map and bool({"invoice_no", "ref_no", "amount_pre_vat", "vat_amount",
		"total_amount"} & set(col_map))


def _skip_row_meta(cells, col_map, meta):
	"""A total row fills the meta totals instead of the rows, as deployed."""
	first = str(cells[0] or "").strip().lower()
	if not any(keyword in first for keyword in SKIP_HINTS):
		return False
	if "amount_pre_vat" in col_map and col_map["amount_pre_vat"] < len(cells):
		meta["total_amount_pre_vat"] = to_float(cells[col_map["amount_pre_vat"]])
	if "vat_amount" in col_map and col_map["vat_amount"] < len(cells):
		meta["total_vat"] = to_float(cells[col_map["vat_amount"]])
	return True


def excel_rows(cells) -> Dict[str, Any]:
	"""Decode the active worksheet of a report into rows.

	``cells`` is the list of cell rows of that sheet; the header is looked for over
	the first thirty rows, and the language of the template never matters because the
	column names are read through the keyword table.
	"""
	if not isinstance(cells, (list, tuple)):
		raise ReconciliationContractError("reconciliation.vat_source_invalid")
	rows: List[Dict[str, Any]] = []
	meta: Dict[str, Any] = {}
	header_index = None
	col_map: Dict[str, int] = {}
	for index, raw in enumerate(cells[:MAX_HEADER_SCAN_SHEET], 1):
		candidate = map_columns([str(cell or "") for cell in raw])
		if "date" in candidate and ({"invoice_no", "ref_no", "amount_pre_vat", "vat_amount",
				"total_amount"} & set(candidate)):
			header_index = index
			col_map = candidate
			break
	if header_index is None:
		return {"ok": False, "error": "找不到表头行", "rows": []}
	row_no = 0
	for raw in cells[header_index:]:
		body = list(raw)
		if not any(cell for cell in body if cell not in (None, "")):
			continue
		if _skip_row_meta(body, col_map, meta):
			continue
		row_no += 1
		rows.append(build_row(row_no, body, col_map))
	return {"ok": True, "rows": rows, "meta": meta, "warnings": [],
		"parser_version": PARSER_VERSION, "row_count": len(rows), "method": "excel"}


def text_lines(lines) -> List[Dict[str, Any]]:
	"""Decode the printed text lines of a report into rows.

	This is the channel a report without table frames needs: a line is kept only
	with a date and a tail of two two-decimal amounts.
	"""
	if not isinstance(lines, (list, tuple)):
		raise ReconciliationContractError("reconciliation.vat_source_invalid")
	rows: List[Dict[str, Any]] = []
	row_no = 0
	for raw_line in lines:
		line = str(raw_line or "").strip()
		if not line:
			continue
		tail = VAT_TAIL_2_RE.search(line)
		if not tail:
			continue
		match_date = VAT_DATE_RE.search(line)
		if not match_date:
			continue
		lowered = line.lower()
		if "เลขที่" in line and "ใบกำกับ" in line:
			continue
		if any(keyword in lowered for keyword in ("รวมทั้งสิ้น", "รวมแต่ละหน้า", "grand total")):
			continue
		amount_pre_vat = to_float(tail.group(1))
		vat_amount = to_float(tail.group(2))
		if amount_pre_vat is None and vat_amount is None:
			continue
		middle = line[match_date.end():tail.start()].strip()
		tokens = middle.split()
		invoice_no = ""
		ref_no = ""
		if tokens:
			index = 0
			# A WNF export starts its line with a sequence number.
			if index < len(tokens) and re.fullmatch(r"\d{1,3}", tokens[index]):
				index += 1
			if index < len(tokens):
				invoice_no = tokens[index]
				glued = GLUED_TAIL_RE.match(invoice_no)
				if glued:
					invoice_no = glued.group(1)
				if (index + 1 < len(tokens) and REF_TOKEN_RE.match(tokens[index + 1])
						and not TAX_ID_RE.match(tokens[index + 1])):
					ref_no = tokens[index + 1]
		match_tax = TAX_ID_RE.search(middle)
		tax_id = match_tax.group(1) if match_tax else ""
		match_branch = BRANCH_CODE_RE.search(middle)
		branch = match_branch.group(1) if match_branch else ("00000" if tax_id else "")
		name = middle
		for cut in (tax_id, branch, invoice_no, ref_no):
			if cut and cut in name:
				name = name.replace(cut, " ", 1)
		# Letter spacing leaves branch codes, a spaced-out sequence number and
		# trailing digits inside what should be the buyer name.
		name = re.sub(r"\b0\d{4}\b", " ", name)
		name = re.sub(r"^\s*(?:[A-Za-z]{1,4}\s+\d+\s+){1,3}", " ", name)
		name = re.sub(r"\s+\d{1,5}\s*$", "", name)
		name = re.sub(r"\s+", " ", name).strip()
		parsed_date = parse_date(match_date.group(1))
		row_no += 1
		rows.append({
			"row_no": row_no,
			"report_date": parsed_date.isoformat() if parsed_date else match_date.group(1),
			"report_invoice_no": invoice_no,
			"report_ref_no": ref_no,
			"report_buyer_name": name,
			"report_buyer_tax_id": normalize_tax_id(tax_id),
			"report_buyer_branch": normalize_branch(branch),
			"report_amount_pre_vat": amount_pre_vat,
			"report_vat_amount": vat_amount,
			"report_amount": (amount_pre_vat or 0) + (vat_amount or 0),
			"is_individual": not bool(tax_id),
		})
	return rows


def _table_header(table):
	"""The header row of one extracted table, if it names a document number."""
	for index, row in enumerate(table[:MAX_HEADER_SCAN_TABLE]):
		joined = " ".join(str(cell or "").lower() for cell in row)
		if "เลขที่" in joined or "invoice" in joined or "วันที่" in joined:
			return index, row
	return None, None


def pdf_rows(pages, lines) -> Optional[Dict[str, Any]]:
	"""Decode an electronic report PDF; ``None`` means its text needs OCR.

	The tables are read first. A report whose text layer is thin and yields no row
	is a scan, which the deployed caller sends to the model; a report that has text
	but no table frames is retried through the printed text-line patterns.
	"""
	if not isinstance(pages, (list, tuple)):
		raise ReconciliationContractError("reconciliation.vat_source_invalid")
	rows: List[Dict[str, Any]] = []
	meta: Dict[str, Any] = {}
	text_length = 0
	row_no = 0
	try:
		for page in pages:
			if not isinstance(page, dict):
				raise ReconciliationContractError("reconciliation.vat_source_invalid")
			tables = page.get("tables") or []
			if not isinstance(tables, (list, tuple)):
				raise ReconciliationContractError("reconciliation.vat_source_invalid")
			for table in tables:
				if not table or len(table) < 2:
					continue
				header_index, header = _table_header(table)
				if header is None:
					continue
				col_map = map_columns(header)
				for body in table[header_index + 1:]:
					if not body or not any(cell for cell in body if cell):
						continue
					if _skip_row_meta(body, col_map, meta):
						continue
					row_no += 1
					rows.append(build_row(row_no, body, col_map))
			text_length += len(str(page.get("text") or ""))
	except ValueError:
		raise
	except Exception:
		return None
	if text_length < SCANNED_PDF_TEXT_LENGTH and not rows:
		return None
	if not rows:
		regex_rows = text_lines(lines)
		if not regex_rows:
			return None
		return {"ok": True, "rows": regex_rows, "meta": {}, "warnings": [],
			"parser_version": PARSER_VERSION, "row_count": len(regex_rows),
			"method": "pdf_text_regex"}
	return {"ok": True, "rows": rows, "meta": meta, "warnings": [],
		"parser_version": PARSER_VERSION, "row_count": len(rows), "method": "pdf_text"}


def to_amount(value) -> Optional[float]:
	"""Read one delimited-file amount through ``Decimal``, as the CSV reader does."""
	text = str(value or "").strip().replace(",", "").replace(" ", "").replace("\xa0", "")
	if not text or text in {"-", "–"}:
		return None
	negative = False
	if text.startswith("(") and text.endswith(")"):
		negative = True
		text = text[1:-1]
	try:
		number = Decimal(text)
	except Exception:
		return None
	quantized = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	return float(-quantized if negative else quantized)


def erp_columns(headers) -> Dict[str, int]:
	"""MR.ERP export columns to fields, matched exactly; first hit wins."""
	col_map: Dict[str, int] = {}
	for index, header in enumerate(headers):
		field = ERP_COLUMN_FIELDS.get((header or "").strip().lower())
		if field is None or field in col_map:
			continue
		col_map[field] = index
	return col_map


def usable_columns(col_map) -> bool:
	"""A mapping is usable with a document number and at least one amount column."""
	has_document = "invoice_no" in col_map or "ref_no" in col_map
	has_amount = bool({"amount_pre_vat", "vat_amount", "total_amount"} & set(col_map))
	return has_document and has_amount


def _csv_row(row_no: int, cells, col_map) -> Dict[str, Any]:
	"""Build one delimited-file row: same shape as ``build_row``, Decimal amounts."""
	parsed: Dict[str, Any] = {"row_no": row_no}
	for field, index in col_map.items():
		raw = cells[index] if index < len(cells) else None
		value = str(raw).strip() if raw is not None else ""
		if field == "date":
			parsed_date = parse_date(value)
			parsed["report_date"] = parsed_date.isoformat() if parsed_date else value
		elif field == "invoice_no":
			parsed["report_invoice_no"] = value
		elif field == "ref_no":
			parsed["report_ref_no"] = value
		elif field == "buyer_name":
			parsed["report_buyer_name"] = value
		elif field == "buyer_tax_id":
			parsed["report_buyer_tax_id"] = normalize_tax_id(value)
		elif field == "buyer_branch":
			parsed["report_buyer_branch"] = normalize_branch(value)
		elif field == "amount_pre_vat":
			parsed["report_amount_pre_vat"] = to_amount(value)
		elif field == "vat_amount":
			parsed["report_vat_amount"] = to_amount(value)
		elif field == "total_amount":
			parsed["report_amount"] = to_amount(value)
	if "report_amount" not in parsed:
		pre_vat = parsed.get("report_amount_pre_vat")
		vat = parsed.get("report_vat_amount")
		if pre_vat is not None and vat is not None:
			parsed["report_amount"] = round(pre_vat + vat, 2)
	# An ERP export has no buyer columns; the keys stay present so the stored shape
	# does not drift.
	parsed.setdefault("report_buyer_name", "")
	parsed.setdefault("report_buyer_tax_id", "")
	parsed.setdefault("report_buyer_branch", normalize_branch(""))
	parsed["is_individual"] = not bool(parsed.get("report_buyer_tax_id"))
	return parsed


def csv_rows(text, delimiter: str = ",") -> Optional[Dict[str, Any]]:
	"""Decode a delimited report from its text; ``None`` means the columns are unknown.

	Decoding the bytes into text (the encoding sniff) stays with the caller. A file
	whose columns are recognised is read in full, because reading a table is the
	work of code; anything else is left to the caller's own fallback.
	"""
	if not isinstance(text, str):
		raise ReconciliationContractError("reconciliation.vat_source_invalid")
	reader = csv.reader(io.StringIO(text), delimiter=delimiter)
	try:
		rows = [row for row in reader]
	except Exception:
		return None
	if not rows:
		return None
	header_index = None
	col_map: Dict[str, int] = {}
	for index, row in enumerate(rows[:MAX_HEADER_SCAN_CSV]):
		headers = [(cell or "").strip() for cell in row]
		erp = erp_columns(headers)
		if usable_columns(erp):
			header_index, col_map = index, erp
			break
		generic = map_columns(headers)
		if usable_columns(generic):
			header_index, col_map = index, generic
			break
	if header_index is None:
		return None
	parsed_rows: List[Dict[str, Any]] = []
	skipped = 0
	for row in rows[header_index + 1:]:
		cells = [(cell or "").strip() for cell in row]
		if not any(cells):
			continue
		if any(keyword in cells[0].lower() for keyword in SKIP_HINTS):
			continue
		parsed = _csv_row(len(parsed_rows) + 1, cells, col_map)
		document_no = parsed.get("report_invoice_no") or parsed.get("report_ref_no") or ""
		if not document_no:
			skipped += 1
			continue
		parsed_rows.append(parsed)
	warnings = [f"跳过 {skipped} 行(无单据号)"] if skipped else []
	return {"ok": True, "rows": parsed_rows, "row_count": len(parsed_rows), "meta": {},
		"warnings": warnings, "parser_version": PARSER_VERSION, "method": "csv_direct_v1",
		"needs_review": False}


def pipeline_rows(decoded, engine: str = "") -> List[Dict[str, Any]]:
	"""Decode the normalized JSON of the OCR pipeline into report rows.

	The pipeline is what the deployed dispatch uses for images, Word, plain text and
	any other format, and what it falls back to when a PDF needs the model. A row
	without a real invoice number is dropped unless the engine is an enterprise one.
	"""
	if decoded in (None, ""):
		decoded = {}
	if not isinstance(decoded, dict):
		raise ReconciliationContractError("reconciliation.vat_source_invalid")
	rows: List[Dict[str, Any]] = []
	row_no = 0
	for page in decoded.get("pages") or []:
		document = (page or {}).get("document") or {}
		for entry in document.get("entries") or []:
			row_no += 1
			invoice_no = str(entry.get("invoice_no") or "").strip()
			if not str(engine).startswith("enterprise-") and not ALNUM_RE.search(invoice_no):
				continue
			seq_no = entry.get("seq_no")
			parsed = {
				"row_no": (int(seq_no) if str(seq_no or "").isdigit() else row_no),
				"report_date": entry.get("transaction_date") or "",
				"report_invoice_no": invoice_no,
				"report_ref_no": invoice_no,
				"report_buyer_name": str(entry.get("customer_name") or "").strip(),
				"report_buyer_tax_id": normalize_tax_id(entry.get("customer_tax") or ""),
				"report_buyer_branch": normalize_branch(entry.get("customer_branch") or ""),
				"report_amount_pre_vat": to_float(entry.get("subtotal")),
				"report_vat_amount": to_float(entry.get("vat")),
				"report_amount": to_float(entry.get("total")),
			}
			parsed["is_individual"] = not bool(parsed["report_buyer_tax_id"])
			rows.append(parsed)
	return rows


def decode(source) -> Optional[Dict[str, Any]]:
	"""Decode one already-extracted report source, in the deployed result shape.

	``None`` means the content is not deterministic and the caller is the one that
	may send it to a model, exactly as the deployed facade signalled it.
	"""
	if not isinstance(source, dict) or source.get("kind") not in KINDS:
		raise ReconciliationContractError("reconciliation.vat_source_invalid")
	kind = source["kind"]
	if any(source.get(field) in (None, "") for field in KINDS[kind]):
		raise ReconciliationContractError("reconciliation.vat_source_invalid")
	if kind == "excel":
		result = excel_rows(source["cells"])
	elif kind == "pdf":
		result = pdf_rows(source["pages"], source["lines"])
	elif kind == "text":
		rows = text_lines(source["lines"])
		result = {"ok": True, "rows": rows, "meta": {}, "warnings": [],
			"parser_version": PARSER_VERSION, "row_count": len(rows), "method": "pdf_text_regex"}
	elif kind == "csv":
		result = csv_rows(source["text"], source.get("delimiter") or ",")
	else:
		rows = pipeline_rows(source["decoded"], source.get("engine") or "")
		result = {"ok": True, "rows": rows, "meta": {}, "warnings": [],
			"parser_version": PARSER_VERSION, "row_count": len(rows),
			"method": source.get("engine") or "pipeline"}
	if result is None:
		return None
	# Every deployed path filters its rows once before reconciliation reads them.
	if result.get("ok") and result.get("rows"):
		result["rows"] = filter_garbage_rows(result["rows"])
		result["row_count"] = len(result["rows"])
	return result


def decoded_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
	"""Convert report rows into the stored row contract of the income store."""
	return [{"report_ref_no": row.get("report_ref_no") or "",
		"report_reference_no": row.get("report_reference_no") or "",
		"report_invoice_no": row.get("report_invoice_no") or "",
		"report_date": row.get("report_date") or "",
		"report_buyer_name": row.get("report_buyer_name") or "",
		"report_amount_pre_vat": row.get("report_amount_pre_vat")} for row in rows]
