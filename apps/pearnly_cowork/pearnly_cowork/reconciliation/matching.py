"""Exact invoice-number pairing, the first pass of the deployed VAT matcher.

Source: ec2e5c957e6b services/recon/reconciliation_matcher.py:run_matching.
This returns unpaired indices for a later matching stage; it does not label
them final orphans or claim the full two-pass reconciliation has completed.
Date/tax/amount pairing is deliberately not exposed while its legacy float
rounding contract is being reconciled with the platform's Decimal policy.
"""

from dataclasses import dataclass
from typing import Mapping, Sequence

from pearnly_cowork.reconciliation.normalization import normalize_invoice_no


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
