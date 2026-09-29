"""Deployed income reconciliation core: general ledger against the output-VAT report.

Source: ec2e5c957e6b ``services/recon/gl_vat_types.py`` (the three dataclasses) and
``services/recon/gl_vat_reconcile.py`` (``_vat_doc_no`` and ``reconcile_gl_vat``),
with ``normalize_doc_no`` from ``services/recon/gl_vat_parse_common.py``, which
delegates to the already-migrated invoice-number normalizer.

The deployed rule set, kept as it runs: the VAT reference number pairs with the GL
voucher number after visual normalization; a difference inside the tolerance counts
as matched and is stored as zero; the summary also carries the itemized GL-only and
VAT-only rows the accountant expands in the report.

Intentional changes (2026-09-29, found reproducing wrong results offline):

* the VAT amount is compared with the voucher's net credit (credit − debit). The
  deployed rule took only the credits for a positive VAT amount (only the negated
  debits for a negative one), so a discount debited on the same voucher showed as a
  difference and the reconciliation ladder could not add up (VAT 950 against GL
  1,000 credit and 50 debit read as −50). For a voucher with one side only both
  rules give the same number;
* a document number printed on several VAT lines is compared once: every line but
  the last takes its own VAT amount from the voucher, the last takes the rest. The
  deployed rule compared every line with the whole voucher, so lines 600 + 400
  against a 1,000 voucher showed −400 and −600;
* a VAT line without a document number (the daily retail summary an Express report
  prints as «บันทึกขายวันที่ 1/4/2569») pairs with the one ledger voucher of the same
  date and the same net amount that no numbered VAT line claims, and then counts as
  that voucher. The deployed rule could never pair such a line, so a month of daily
  summaries showed every day twice, once as VAT-only and once as GL-only (2026-09-29,
  I03). A line with several candidate vouchers stays unpaired.

Pure logic: no Frappe, database, file, clock, HTTP, model or export access. Reading
the GL and VAT files, persisting tasks and building the workbook stay in the legacy
project and are the following slices of this family.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pearnly_cowork.reconciliation.normalization import normalize_invoice_no, parse_date

AMOUNT_TOLERANCE = 0.01  # deployed default: differences within ฿0.01 count as matched


@dataclass
class GlRow:
	doc_no: str  # printed voucher number (ใบสำคัญ)
	norm_doc_no: str  # after visual normalization
	date: str
	account_code: str  # e.g. 4110-01
	description: str
	debit: float
	credit: float


@dataclass
class ReconRow:
	doc_no: str  # the reference number read from the VAT report
	date: str
	customer_name: str
	vat_amount: float  # pre-VAT amount from the VAT report
	gl_amount: Optional[float]
	diff: Optional[float]
	account_codes: str  # de-duplicated, comma joined


@dataclass
class GlVatSummary:
	gl_total: float
	gl_only_credit: float  # GL credits missing from VAT (positive)
	gl_only_debit: float  # GL debits missing from VAT (positive)
	vat_only_positive: float  # VAT positives missing from GL
	vat_only_negative: float  # VAT negatives missing from GL (already negative)
	vat_total: float
	# The deployed summary shows the adjustment lines, not only the totals.
	gl_only_credit_items: List[Dict[str, Any]] = None  # [{doc_no, date, name, amount}]
	gl_only_debit_items: List[Dict[str, Any]] = None
	vat_only_positive_items: List[Dict[str, Any]] = None  # [{doc_no, date, name, amount}]
	vat_only_negative_items: List[Dict[str, Any]] = None

	def __post_init__(self):
		if self.gl_only_credit_items is None:
			self.gl_only_credit_items = []
		if self.gl_only_debit_items is None:
			self.gl_only_debit_items = []
		if self.vat_only_positive_items is None:
			self.vat_only_positive_items = []
		if self.vat_only_negative_items is None:
			self.vat_only_negative_items = []


def normalize_doc_no(value: str) -> str:
	"""Deployed document-number normalization (the shared invoice-number reader)."""
	return normalize_invoice_no(value)


def vat_doc_no(vat_row: Dict[str, Any]) -> str:
	"""Reference number of one VAT row: reference first, then the invoice number."""
	raw = (
		vat_row.get("report_ref_no")
		or vat_row.get("report_reference_no")
		or vat_row.get("report_invoice_no")
		or ""
	)
	return str(raw).strip()


def pair_unnumbered_lines(gl_rows: List[GlRow], vat_rows: List[Dict[str, Any]],
		amount_tolerance: float = AMOUNT_TOLERANCE) -> List[Dict[str, Any]]:
	"""VAT lines without a document number, paired by date and net amount with a free voucher.

	A voucher is free when no numbered VAT line names it. A paired line is returned as a
	copy carrying the voucher number as its reference; every other line is returned as is.
	"""
	def number(value):
		try:
			return float(value or 0.0)
		except (TypeError, ValueError):
			return None

	def day_of(value):
		try:
			return parse_date(value)
		except Exception:
			return None

	unnumbered = [index for index, vat in enumerate(vat_rows) if not normalize_doc_no(vat_doc_no(vat))]
	if not unnumbered:
		return vat_rows
	numbered = {normalize_doc_no(vat_doc_no(vat)) for vat in vat_rows} - {""}
	vouchers: Dict[str, List[GlRow]] = defaultdict(list)
	for row in gl_rows:
		if row.norm_doc_no and row.norm_doc_no not in numbered:
			vouchers[row.norm_doc_no].append(row)
	candidates: Dict[Any, List[Tuple[str, float]]] = defaultdict(list)
	for norm, rows in vouchers.items():
		day = day_of(rows[0].date)
		amounts = [(number(row.credit), number(row.debit)) for row in rows]
		if day is not None and all(None not in pair for pair in amounts):
			candidates[day].append((norm, sum(credit - debit for credit, debit in amounts)))
	if not candidates:
		return vat_rows
	used = set()
	paired = list(vat_rows)
	for index in unnumbered:
		vat = vat_rows[index]
		day, amount = day_of(vat.get("report_date")), number(vat.get("report_amount_pre_vat"))
		if day is None or not amount:
			continue
		hits = [norm for norm, net in candidates.get(day, []) if norm not in used
			and abs(net - amount) <= amount_tolerance]
		if len(hits) == 1:
			used.add(hits[0])
			paired[index] = dict(vat, report_ref_no=vouchers[hits[0]][0].doc_no)
	return paired


def reconcile_gl_vat(
	gl_rows: List[GlRow],
	vat_rows: List[Dict[str, Any]],
	amount_tolerance: float = AMOUNT_TOLERANCE,
) -> Tuple[List[ReconRow], GlVatSummary]:
	"""Pair the VAT report with the ledger and summarize what each side misses.

	The VAT side is the driver of the detail list; a difference within
	``amount_tolerance`` is reported as a zero difference.
	"""
	vat_rows = pair_unnumbered_lines(gl_rows, vat_rows, amount_tolerance)
	gl_idx: Dict[str, List[GlRow]] = defaultdict(list)
	for row in gl_rows:
		if row.norm_doc_no:
			gl_idx[row.norm_doc_no].append(row)

	vat_norm_set = set()
	for vat in vat_rows:
		normalized = normalize_doc_no(vat_doc_no(vat))
		if normalized:
			vat_norm_set.add(normalized)

	def amount_of(vat):
		try:
			return float(vat.get("report_amount_pre_vat") or 0.0)
		except Exception:
			return 0.0

	# 同一单号在税表里有几行：前几行各认自己的税表金额，最后一行认余下的（整张凭证只比一次）。
	lines_of: Dict[str, List[int]] = defaultdict(list)
	for index, vat in enumerate(vat_rows):
		lines_of[normalize_doc_no(vat_doc_no(vat))].append(index)

	detail: List[ReconRow] = []
	for index, vat in enumerate(vat_rows):
		raw_no = vat_doc_no(vat)
		norm_no = normalize_doc_no(raw_no)
		vat_amt = amount_of(vat)

		gl_matches = gl_idx.get(norm_no, [])
		if not gl_matches:
			detail.append(
				ReconRow(
					doc_no=raw_no,
					date=str(vat.get("report_date") or ""),
					customer_name=str(vat.get("report_buyer_name") or ""),
					vat_amount=round(vat_amt, 2),
					gl_amount=None,
					diff=None,
					account_codes="",
				)
			)
		else:
			gl_amt = round(sum(row.credit for row in gl_matches) - sum(row.debit for row in gl_matches), 2)
			siblings = lines_of[norm_no]
			if len(siblings) > 1:
				if index != siblings[-1]:
					gl_amt = round(vat_amt, 2)
				else:
					gl_amt = round(gl_amt - sum(amount_of(vat_rows[other]) for other in siblings[:-1]), 2)
			raw_diff = round(vat_amt - gl_amt, 2)
			# A difference inside the tolerance is reported as an exact match.
			effective_diff = 0.0 if abs(raw_diff) <= amount_tolerance else raw_diff
			accounts = ",".join(sorted({row.account_code for row in gl_matches if row.account_code}))
			detail.append(
				ReconRow(
					doc_no=raw_no,
					date=str(vat.get("report_date") or ""),
					customer_name=str(vat.get("report_buyer_name") or ""),
					vat_amount=round(vat_amt, 2),
					gl_amount=gl_amt,
					diff=effective_diff,
					account_codes=accounts,
				)
			)

	detail.sort(key=lambda row: (row.date or "", row.doc_no or ""))

	gl_only_credit = 0.0
	gl_only_debit = 0.0
	gl_only_credit_items: List[Dict[str, Any]] = []
	gl_only_debit_items: List[Dict[str, Any]] = []
	for row in gl_rows:
		if row.norm_doc_no in vat_norm_set:
			continue
		if row.credit > 0:
			gl_only_credit += row.credit
			gl_only_credit_items.append(
				{
					"doc_no": row.doc_no,
					"date": row.date,
					"name": row.description,
					"amount": round(row.credit, 2),
				}
			)
		if row.debit > 0:
			gl_only_debit += row.debit
			gl_only_debit_items.append(
				{
					"doc_no": row.doc_no,
					"date": row.date,
					"name": row.description,
					"amount": round(row.debit, 2),
				}
			)

	vat_only_pos = 0.0
	vat_only_neg = 0.0
	vat_only_positive_items: List[Dict[str, Any]] = []
	vat_only_negative_items: List[Dict[str, Any]] = []
	for vat in vat_rows:
		normalized = normalize_doc_no(vat_doc_no(vat))
		if normalized in gl_idx:
			continue
		amt = vat.get("report_amount_pre_vat") or 0.0
		try:
			amt = float(amt)
		except Exception:
			amt = 0.0
		if amt > 0:
			vat_only_pos += amt
			vat_only_positive_items.append(
				{
					"doc_no": vat_doc_no(vat),
					"date": str(vat.get("report_date") or ""),
					"name": str(vat.get("report_buyer_name") or ""),
					"amount": round(amt, 2),
				}
			)
		elif amt < 0:
			vat_only_neg += amt
			vat_only_negative_items.append(
				{
					"doc_no": vat_doc_no(vat),
					"date": str(vat.get("report_date") or ""),
					"name": str(vat.get("report_buyer_name") or ""),
					"amount": round(amt, 2),
				}
			)

	gl_total = sum(row.credit for row in gl_rows) - sum(row.debit for row in gl_rows)
	vat_total = sum(float(vat.get("report_amount_pre_vat") or 0) for vat in vat_rows)

	summary = GlVatSummary(
		gl_total=round(gl_total, 2),
		gl_only_credit=round(gl_only_credit, 2),
		gl_only_debit=round(gl_only_debit, 2),
		vat_only_positive=round(vat_only_pos, 2),
		vat_only_negative=round(vat_only_neg, 2),
		vat_total=round(vat_total, 2),
		gl_only_credit_items=gl_only_credit_items,
		gl_only_debit_items=gl_only_debit_items,
		vat_only_positive_items=vat_only_positive_items,
		vat_only_negative_items=vat_only_negative_items,
	)
	return detail, summary
