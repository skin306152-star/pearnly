"""Cowork history contract, traced to deployed legacy ec2e5c957e6b.

Sources: services/ocr_history/{mutations,list_status}.py, core/thai_date.py,
routes/history_routes.py, services/erp/express_push/posting_kind.py and
services/summary_import/commit.py. No storage, permission or external actions.

Summary money intentionally uses a finite Decimal string instead of the old
float. Complete page data stays separate from this lossy list projection.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import math
import re

from pearnly_cowork.contracts.push import normalize_posting_kind


SOURCES = frozenset({"manual", "cowork_line", "summary_table_batch"})
_INTERNAL_ERP_SOURCES = frozenset({"erp_web", "line_erp"})
_ISO_DATE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")
_PRINTED_DATE = re.compile(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\s*$")
_NAMED_DATE = re.compile(r"^\s*(\d{1,2})\s+([^\d\s,]+)\s*,?\s*(\d{2,4})\s*$", re.I)
_TH_MONTHS = (
	"มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
	"กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
)
_TH_ABBR = ("ม.ค", "ก.พ", "มี.ค", "เม.ย", "พ.ค", "มิ.ย", "ก.ค", "ส.ค", "ก.ย", "ต.ค", "พ.ย", "ธ.ค")
_EN_MONTHS = (
	"january", "february", "march", "april", "may", "june",
	"july", "august", "september", "october", "november", "december",
)
_MONTHS = {name: index for index, name in enumerate(_TH_MONTHS, 1)}
for _index, _name in enumerate(_TH_ABBR, 1):
	_MONTHS.update({_name: _index, _name + ".": _index, _name.replace(".", ""): _index})
for _index, _name in enumerate(_EN_MONTHS, 1):
	_MONTHS.update({_name: _index, _name[:3]: _index})


class HistoryContractError(ValueError):
	def __init__(self, code: str):
		self.code = code
		super().__init__(code)


def _json_copy(value):
	"""Preserve JSON value types without silently stringifying raw evidence."""
	if value is None or isinstance(value, (str, bool, int)):
		return value
	if isinstance(value, float) and math.isfinite(value):
		return value
	if isinstance(value, list):
		return [_json_copy(item) for item in value]
	if isinstance(value, dict) and all(isinstance(key, str) for key in value):
		return {key: _json_copy(item) for key, item in value.items()}
	raise HistoryContractError("history.json_invalid")


def normalize_source(value, *, public_create: bool = False) -> str:
	"""Sources are routing identities, never caller-selected ERP aliases."""
	if not isinstance(value, str) or value not in SOURCES:
		code = "history.erp_source_forbidden" if isinstance(value, str) and value in _INTERNAL_ERP_SOURCES else "history.source_invalid"
		raise HistoryContractError(code)
	if public_create and value != "manual":
		raise HistoryContractError("history.source_not_public")
	return value


def _printed_date(value, *, anchor_year: int | None) -> str | None:
	printed = str(value or "").strip()
	iso = _ISO_DATE.match(printed)
	if iso:
		year, month, day = map(int, iso.groups())
	else:
		numeric = _PRINTED_DATE.match(printed)
		named = _NAMED_DATE.match(printed) if not numeric else None
		if numeric:
			day, month, raw_year = int(numeric[1]), int(numeric[2]), numeric[3]
		elif named:
			day, token, raw_year = int(named[1]), named[2].casefold(), named[3]
			month = _MONTHS.get(token) or _MONTHS.get(token.rstrip("."))
			if month is None:
				return None
		else:
			return None
		if len(raw_year) == 2:
			# The runtime injects the Bangkok calendar year; this contract has no clock.
			if anchor_year is None:
				raise HistoryContractError("history.date_anchor_required")
			if isinstance(anchor_year, bool) or not isinstance(anchor_year, int) or not 1 <= anchor_year <= 9999:
				raise HistoryContractError("history.date_anchor_invalid")
			yy = int(raw_year)
			year = min((2000 + yy, 1957 + yy), key=lambda candidate: abs(anchor_year - candidate))
		else:
			year = int(raw_year)
	if year >= 2400:
		year -= 543
	try:
		return date(year, month, day).isoformat()
	except ValueError:
		return None


def prepare_pages(pages, *, anchor_year: int | None = None) -> list[dict]:
	"""Copy complete pages and derive AD dates; incomplete invoices remain valid.

	Empty pages can represent failed recognition, as in legacy insert. An update
	service must separately enforce the old PUT route's nonempty-pages rule.
	"""
	if not isinstance(pages, list):
		raise HistoryContractError("history.pages_invalid")
	prepared = _json_copy(pages)
	for page in prepared:
		if not isinstance(page, dict):
			raise HistoryContractError("history.pages_invalid")
		fields = page.get("fields")
		if fields is None:
			continue
		if not isinstance(fields, dict):
			raise HistoryContractError("history.fields_invalid")
		printed = fields.get("date_raw")
		if not str(printed or "").strip():
			iso = _ISO_DATE.match(str(fields.get("date") or ""))
			if iso and int(iso[1]) >= 2400:
				raise HistoryContractError("history.date_unreadable")
			continue
		derived = _printed_date(printed, anchor_year=anchor_year)
		if derived is None:
			raise HistoryContractError("history.date_unreadable")
		fields["date"] = derived
	return prepared


def _summary_text(value) -> str | None:
	if not value:
		return None
	if not isinstance(value, str):
		raise HistoryContractError("history.fields_invalid")
	return value[:200] or None


def _summary_amount(value) -> str | None:
	if value is None:
		return None
	try:
		amount = Decimal(str(value).replace(",", ""))
	except InvalidOperation:
		return None
	return str(amount) if amount.is_finite() else None


PARTY_FIELDS = ("seller_tax", "buyer_name", "buyer_tax")


def primary_page_fields(pages: list[dict]) -> dict:
	"""The page the summary, push and learning read: legacy first original page, then best-filled page."""
	def score(page):
		fields = page.get("fields") or {}
		return sum(bool(fields.get(key)) for key in ("invoice_number", "total_amount", "seller_name", "date"))

	selected = None
	for page in pages:
		fields = page.get("fields") or {}
		if not page.get("is_copy") and not page.get("is_duplicate"):
			if fields.get("invoice_number") or fields.get("total_amount") or fields.get("seller_name"):
				selected = page
				break
	if selected is None and pages:
		best = max(pages, key=score)
		if score(best) > 0:
			selected = best
	return (selected or {}).get("fields") or {}


def extract_summary_fields(pages: list[dict]) -> dict:
	"""Keep legacy first-original-page selection, then highest-field-count fallback."""
	fields = primary_page_fields(pages)
	invoice_date = None
	if fields.get("date"):
		try:
			candidate = str(fields["date"]).replace("/", "-")[:10]
			datetime.strptime(candidate, "%Y-%m-%d")
			invoice_date = candidate
		except ValueError:
			pass
	return {
		"invoice_no": _summary_text(fields.get("invoice_number")),
		"invoice_date": invoice_date,
		"seller_name": _summary_text(fields.get("seller_name")),
		"total_amount": _summary_amount(fields.get("total_amount")),
	}


def extract_party_fields(pages: list[dict]) -> dict:
	"""List columns for both parties, read from the same primary page as the summary."""
	fields = primary_page_fields(pages)
	return {key: _summary_text(fields.get(key)) for key in PARTY_FIELDS}


def list_status(confidence, summary: dict) -> str:
	"""Recognition quality only; does not attest review, posting or push success."""
	if all(summary.get(key) is None for key in ("total_amount", "invoice_no", "seller_name")):
		return "failed"
	if confidence == "high" and summary.get("total_amount") is not None and summary.get("invoice_no") is not None:
		return "confirmed"
	return "pending"


def source_page_indices(pages: list[dict]) -> list[int]:
	"""Original PDF's 1-based page_index; keep duplicate indices as legacy did."""
	indices = []
	for index, page in enumerate(pages, 1):
		value = page.get("page_index", index)
		if isinstance(value, bool) or not isinstance(value, int) or value < 1:
			raise HistoryContractError("history.page_index_invalid")
		indices.append(value)
	return sorted(indices)


def clean_summary_fields(fields: dict) -> dict:
	"""Only summary-import rows drop internal root keys; ordinary pages never do."""
	if not isinstance(fields, dict):
		raise HistoryContractError("history.fields_invalid")
	fields = _json_copy(fields)
	declared = str(fields.get("_direction") or "").strip()
	cleaned = {key: value for key, value in fields.items() if not key.startswith("_")}
	if declared and not cleaned.get("direction"):
		cleaned["direction"] = declared
	return cleaned


def initial_history(
	pages, *, source: str = "manual", confidence: str = "", posting_kind=None,
	ai_raw=None, staged: bool = True, public_create: bool = False,
	anchor_year: int | None = None,
) -> dict:
	"""Prepare first persistence; ai_raw is independent and is never an update field."""
	source = normalize_source(source, public_create=public_create)
	if not isinstance(confidence, str) or not isinstance(staged, bool):
		raise HistoryContractError("history.metadata_invalid")
	prepared = prepare_pages(pages, anchor_year=anchor_year)
	summary = extract_summary_fields(prepared)
	return {
		"pages": prepared,
		"ai_raw": _json_copy(pages if ai_raw is None else ai_raw),
		"source": source,
		"confidence": confidence,
		"posting_kind": normalize_posting_kind(posting_kind),
		"staged": staged,
		"source_page_indices": source_page_indices(prepared),
		**summary,
		"list_status": list_status(confidence, summary),
	}


def commit_history(history: dict) -> dict:
	"""Idempotent draft completion; all review data and posting state stay intact."""
	committed = deepcopy(history)
	committed["staged"] = False
	return committed
