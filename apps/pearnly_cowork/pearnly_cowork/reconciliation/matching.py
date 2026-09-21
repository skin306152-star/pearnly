"""Both deployed strong-key passes of the VAT matcher, plus their composition.

Source: ec2e5c957e6b services/recon/reconciliation_matcher.py:run_matching.
Pass one pairs exact normalized invoice numbers; pass two pairs
date + buyer tax ID + amount with a 0.95 confidence. The deployed third pass was
permanently removed in the source, so remaining rows stay orphans for the
accountant instead of being matched fuzzily.

Amount decisions delegate to reconciliation.amounts, the registered binary-float
contract, so imported historical rows pair exactly as they did in production.
Pairing is a strong-key assertion, never a fuzzy judgement, and never an ERP write.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pearnly_cowork.reconciliation.amounts import invoice_total, report_total, within_tolerance
from pearnly_cowork.reconciliation.normalization import (
	normalize_invoice_no,
	normalize_tax_id,
	parse_date,
)


@dataclass(frozen=True)
class InvoiceNumberPass:
	pairs: list[dict]
	unmatched_invoice_indices: list[int]
	unmatched_report_indices: list[int]


def pair_by_invoice_number(
	invoice_rows: Sequence[Mapping], report_rows: Sequence[Mapping],
) -> InvoiceNumberPass:
	"""Consume each report row at most once, preserving duplicate input order.

	Only visual separators, width and case are normalized. INV/IV prefixes,
	leading zeroes and distinct document numbers remain different. A strong
	key pairs rows for subsequent field comparison; it does not assert that
	their dates, tax IDs or amounts agree.
	"""
	pairs = []
	unmatched_invoices = []
	unmatched_reports = set(range(len(report_rows)))
	report_index: dict[str, list[int]] = {}
	for index in unmatched_reports:
		key = normalize_invoice_no(
			report_rows[index].get("report_invoice_no") or report_rows[index].get("invoice_no") or ""
		)
		if key:
			report_index.setdefault(key, []).append(index)
	for index, invoice in enumerate(invoice_rows):
		key = normalize_invoice_no(invoice.get("invoice_no") or "")
		if key and report_index.get(key):
			report = report_index[key].pop(0)
			pairs.append({
				"invoice_idx": index,
				"report_idx": report,
				"invoice_id": invoice.get("id"),
				"report_row_no": report_rows[report].get("row_no"),
				"pair_confidence": 1.0,
				"pass": 1,
			})
			unmatched_reports.discard(report)
		else:
			unmatched_invoices.append(index)
	return InvoiceNumberPass(pairs, unmatched_invoices, list(unmatched_reports))


@dataclass(frozen=True)
class DateTaxAmountPass:
	pairs: list[dict]
	unmatched_invoice_indices: list[int]
	unmatched_report_indices: list[int]


def _pair(invoice_index: int, report_index: int, invoice_rows: Sequence[Mapping],
		report_rows: Sequence[Mapping], confidence: float, pass_number: int) -> dict:
	return {
		"invoice_idx": invoice_index,
		"report_idx": report_index,
		"invoice_id": invoice_rows[invoice_index].get("id"),
		"report_row_no": report_rows[report_index].get("row_no"),
		"pair_confidence": confidence,
		"pass": pass_number,
	}


def pair_by_date_tax_amount(
	invoice_rows: Sequence[Mapping],
	report_rows: Sequence[Mapping],
	unmatched_invoice_indices: Sequence[int],
	unmatched_report_indices: Sequence[int],
) -> DateTaxAmountPass:
	"""Second deployed pass: same date, same buyer tax ID, amount within 0.01.

	All three keys are required: an unparsable date or an empty tax ID never
	pairs. Each report row is consumed at most once, and a row already paired by
	the first pass must not be offered again.

	Candidates are visited in ascending row order. The deployed loop iterated a
	CPython set of small integer indices built from ``range(n)`` and then removed
	the first-pass rows; that iteration is ascending as well, so when duplicate
	report rows share one key the same sibling is chosen (regression-tested).
	"""
	pairs = []
	remaining_reports = list(unmatched_report_indices)
	remaining_invoices = []
	for index in unmatched_invoice_indices:
		invoice = invoice_rows[index]
		date = parse_date(invoice.get("invoice_date"))
		tax_id = normalize_tax_id(str(invoice.get("buyer_tax_id") or ""))
		amount = invoice_total(invoice)
		found = None
		for report_index in remaining_reports:
			report = report_rows[report_index]
			report_date = parse_date(report.get("report_date"))
			report_tax_id = normalize_tax_id(str(report.get("report_buyer_tax_id") or ""))
			report_amount = report_total(report)
			if (date and report_date and date == report_date and tax_id and tax_id == report_tax_id
					and within_tolerance(amount, report_amount)):
				found = report_index
				break
		if found is None:
			remaining_invoices.append(index)
		else:
			remaining_reports.remove(found)
			pairs.append(_pair(index, found, invoice_rows, report_rows, 0.95, 2))
	return DateTaxAmountPass(pairs, remaining_invoices, remaining_reports)


@dataclass(frozen=True)
class MatchingResult:
	pairs: list[dict]
	invoice_orphans: list[Any]
	report_orphans: list[Any]
	stats: dict


def match_rows(invoice_rows: Sequence[Mapping], report_rows: Sequence[Mapping]) -> MatchingResult:
	"""Compose both deployed passes into the source ``run_matching`` response.

	Orphans are reported by each row's own identity field (invoice ``id``, report
	``row_no``) for the accountant to review; they are never auto-resolved and the
	third fuzzy pass stays removed.
	"""
	first = pair_by_invoice_number(invoice_rows, report_rows)
	second = pair_by_date_tax_amount(invoice_rows, report_rows,
		first.unmatched_invoice_indices, first.unmatched_report_indices)
	pairs = first.pairs + second.pairs
	invoice_orphans = [invoice_rows[index].get("id") for index in second.unmatched_invoice_indices]
	report_orphans = [report_rows[index].get("row_no") for index in second.unmatched_report_indices]
	stats = {
		"total_invoices": len(invoice_rows),
		"total_report_rows": len(report_rows),
		"matched": len(pairs),
		"invoice_orphan_count": len(invoice_orphans),
		"report_orphan_count": len(report_orphans),
		"pass1_count": sum(1 for pair in pairs if pair["pass"] == 1),
		"pass2_count": sum(1 for pair in pairs if pair["pass"] == 2),
		"pass3_count": 0,
	}
	return MatchingResult(pairs, invoice_orphans, report_orphans, stats)
