"""Deployed statement-versus-GL reconciliation core and its row types.

Source: ec2e5c957e6b ``services/recon/bank_recon_reconcile.py`` (documented there
as a pure function), with the GL/result/summary dataclasses from
``services/recon/bank_recon_types.py`` and the ``_day_diff``/``_amount_matches``
helpers plus ``DATE_TOL_DAYS`` from ``services/recon/bank_recon_utils.py``.

Three layers consume each statement row at most once and each GL row at most
once: L1 needs the same date, L2 accepts a gap up to ``DATE_TOL_DAYS`` and keeps
the nearest unused GL row, L3 matches on amount alone and is flagged as such.
Remaining rows become one-sided findings, and the summary carries the counts,
the unmatched amounts and the reconciliation formula.

The deployed docstrings say "±3-day" while the shipped tolerance constant is 7;
the constant is reproduced as it runs. Not migrated: candidate retrieval and
result persistence (``bank_recon_match`` is a SQL DAL), the parsers, the
session orchestrator, exports and the work-order step.

Pure contract: no Frappe, database, file, clock, HTTP or model access.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from pearnly_cowork.reconciliation.bank_statement import AMOUNT_TOL, StatementRow

DATE_TOL_DAYS = 7


def amount_matches(left: float, right: float) -> bool:
	"""Deployed amount comparison for statement/GL pairing."""
	return abs(left - right) <= AMOUNT_TOL


def day_diff(first: Optional[date], second: Optional[date]) -> Optional[int]:
	"""Absolute day distance; an unknown date is never a match candidate."""
	if first is None or second is None:
		return None
	return abs((first - second).days)


@dataclass
class GlRow:
	"""One parsed general-ledger row; ``balance`` is display-only."""

	date: Optional[date]
	doc_no: str
	account_code: str
	description: str
	debit: float
	credit: float
	source_file: str = ""
	balance: float = 0.0
	row_hash: str = ""


@dataclass
class BankReconRow:
	"""One reconciliation outcome, holding whichever side supplied the row."""

	match_status: str
	match_layer: Optional[int]
	stmt_date: Optional[date] = None
	stmt_desc: str = ""
	stmt_withdrawal: float = 0.0
	stmt_deposit: float = 0.0
	stmt_balance: float = 0.0
	gl_date: Optional[date] = None
	gl_doc_no: str = ""
	gl_account_code: str = ""
	gl_desc: str = ""
	gl_debit: float = 0.0
	gl_credit: float = 0.0
	gl_balance: float = 0.0
	date_diff_days: Optional[int] = None
	source_stmt_file: str = ""
	source_gl_file: str = ""
	stmt_confidence: str = "high"
	stmt_balance_ok: Optional[bool] = None
	stmt_autocorrected: bool = False


@dataclass
class BankReconSummary:
	"""Totals, counts and the reconciliation formula for one statement/GL pair."""

	bank_code: str = ""
	gl_account_code: str = ""
	stmt_opening: float = 0.0
	stmt_closing: float = 0.0
	gl_opening: float = 0.0
	gl_closing: float = 0.0
	stmt_total_deposit: float = 0.0
	stmt_total_withdrawal: float = 0.0
	gl_total_credit: float = 0.0
	gl_total_debit: float = 0.0
	matched_count: int = 0
	gl_debit_only_count: int = 0
	gl_credit_only_count: int = 0
	stmt_withdrawal_only_count: int = 0
	stmt_deposit_only_count: int = 0
	gl_debit_only_amount: float = 0.0
	gl_credit_only_amount: float = 0.0
	stmt_withdrawal_only_amount: float = 0.0
	stmt_deposit_only_amount: float = 0.0
	opening_diff: float = 0.0
	formula_stmt_closing: float = 0.0
	formula_diff: float = 0.0


def _matched_row(stmt_row: StatementRow, gl_row: GlRow, layer: int, date_diff: Optional[int]) -> BankReconRow:
	"""The deployed matched-row projection, shared by the three layers."""
	return BankReconRow(
		match_status="matched",
		match_layer=layer,
		stmt_date=stmt_row.date,
		stmt_desc=stmt_row.description,
		stmt_withdrawal=stmt_row.withdrawal,
		stmt_deposit=stmt_row.deposit,
		stmt_balance=stmt_row.balance,
		stmt_confidence=stmt_row.confidence,
		stmt_balance_ok=stmt_row.balance_ok,
		stmt_autocorrected=(stmt_row.amount_autocorrected or stmt_row.direction_autocorrected),
		gl_date=gl_row.date,
		gl_doc_no=gl_row.doc_no,
		gl_account_code=gl_row.account_code,
		gl_desc=gl_row.description,
		gl_debit=gl_row.debit,
		gl_credit=gl_row.credit,
		gl_balance=gl_row.balance,
		date_diff_days=date_diff,
		source_stmt_file=stmt_row.source_file,
		source_gl_file=gl_row.source_file,
	)


def reconcile(stmt_rows: list[StatementRow], gl_rows: list[GlRow], stmt_opening: float = 0.0,
		gl_opening: float = 0.0, stmt_closing: float = 0.0, gl_closing: float = 0.0,
		bank_code: str = "", gl_account_code: str = "") -> tuple[list[BankReconRow], BankReconSummary]:
	"""Score-free, index-based three-layer pairing of statement rows against GL rows.

	Money leaving the bank is compared with the GL credit column and money
	arriving with the debit column; a GL row is consumed once across all layers.
	"""
	recon_rows: list[BankReconRow] = []
	gl_used = [False] * len(gl_rows)
	stmt_used = [False] * len(stmt_rows)

	def try_match_gl(stmt_row: StatementRow, layer: int) -> Optional[int]:
		target_amount = stmt_row.withdrawal if stmt_row.withdrawal > 0 else stmt_row.deposit
		direction = "C" if stmt_row.withdrawal > 0 else "D"
		best_index = None
		best_day_diff = 999
		for gl_index, gl_row in enumerate(gl_rows):
			if gl_used[gl_index]:
				continue
			gl_amount = gl_row.debit if direction == "D" else gl_row.credit
			if not amount_matches(target_amount, gl_amount):
				continue
			if gl_amount == 0:
				continue
			difference = day_diff(stmt_row.date, gl_row.date)
			if layer == 1:
				if difference is None or difference > 0:
					continue
				best_index = gl_index
				break
			elif layer == 2:
				if difference is None or difference > DATE_TOL_DAYS:
					continue
				if difference < best_day_diff:
					best_day_diff = difference
					best_index = gl_index
			elif layer == 3:
				if best_index is None:
					best_index = gl_index
		return best_index

	for stmt_index, stmt_row in enumerate(stmt_rows):
		gl_index = try_match_gl(stmt_row, layer=1)
		if gl_index is not None:
			stmt_used[stmt_index] = True
			gl_used[gl_index] = True
			recon_rows.append(_matched_row(stmt_row, gl_rows[gl_index], 1, 0))

	for stmt_index, stmt_row in enumerate(stmt_rows):
		if stmt_used[stmt_index]:
			continue
		gl_index = try_match_gl(stmt_row, layer=2)
		if gl_index is not None:
			stmt_used[stmt_index] = True
			gl_used[gl_index] = True
			difference = day_diff(stmt_row.date, gl_rows[gl_index].date) or 0
			recon_rows.append(_matched_row(stmt_row, gl_rows[gl_index], 2, difference))

	for stmt_index, stmt_row in enumerate(stmt_rows):
		if stmt_used[stmt_index]:
			continue
		gl_index = try_match_gl(stmt_row, layer=3)
		if gl_index is not None:
			stmt_used[stmt_index] = True
			gl_used[gl_index] = True
			difference = day_diff(stmt_row.date, gl_rows[gl_index].date)
			recon_rows.append(_matched_row(stmt_row, gl_rows[gl_index], 3, difference))

	for stmt_index, stmt_row in enumerate(stmt_rows):
		if stmt_used[stmt_index]:
			continue
		recon_rows.append(BankReconRow(
			match_status="stmt_withdrawal_only" if stmt_row.withdrawal > 0 else "stmt_deposit_only",
			match_layer=None,
			stmt_date=stmt_row.date,
			stmt_desc=stmt_row.description,
			stmt_withdrawal=stmt_row.withdrawal,
			stmt_deposit=stmt_row.deposit,
			stmt_balance=stmt_row.balance,
			stmt_confidence=stmt_row.confidence,
			stmt_balance_ok=stmt_row.balance_ok,
			stmt_autocorrected=(stmt_row.amount_autocorrected or stmt_row.direction_autocorrected),
			source_stmt_file=stmt_row.source_file,
		))

	for gl_index, gl_row in enumerate(gl_rows):
		if gl_used[gl_index]:
			continue
		recon_rows.append(BankReconRow(
			match_status="gl_debit_only" if gl_row.debit > 0 else "gl_credit_only",
			match_layer=None,
			gl_date=gl_row.date,
			gl_doc_no=gl_row.doc_no,
			gl_account_code=gl_row.account_code,
			gl_desc=gl_row.description,
			gl_debit=gl_row.debit,
			gl_credit=gl_row.credit,
			gl_balance=gl_row.balance,
			source_gl_file=gl_row.source_file,
		))

	def _sort_key(row: BankReconRow):
		order = 0 if row.match_status == "matched" else 1
		when = row.stmt_date or row.gl_date or date.min
		return (order, when)

	# Stable sort keeps the append order (matched, statement-only, GL-only) for
	# rows sharing a date, exactly like the deployed sort.
	recon_rows.sort(key=_sort_key)

	matched_rows = [row for row in recon_rows if row.match_status == "matched"]
	gl_debit_only = [row for row in recon_rows if row.match_status == "gl_debit_only"]
	gl_credit_only = [row for row in recon_rows if row.match_status == "gl_credit_only"]
	stmt_withdrawal_only = [row for row in recon_rows if row.match_status == "stmt_withdrawal_only"]
	stmt_deposit_only = [row for row in recon_rows if row.match_status == "stmt_deposit_only"]

	gl_debit_only_amount = round(sum(row.gl_debit for row in gl_debit_only), 2)
	gl_credit_only_amount = round(sum(row.gl_credit for row in gl_credit_only), 2)
	stmt_withdrawal_only_amount = round(sum(row.stmt_withdrawal for row in stmt_withdrawal_only), 2)
	stmt_deposit_only_amount = round(sum(row.stmt_deposit for row in stmt_deposit_only), 2)

	opening_diff = round(stmt_opening - gl_opening, 2)
	formula_closing = round(gl_closing + opening_diff - gl_debit_only_amount + gl_credit_only_amount
		- stmt_withdrawal_only_amount + stmt_deposit_only_amount, 2)
	formula_diff = round(stmt_closing - formula_closing, 2)

	summary = BankReconSummary(
		bank_code=bank_code,
		gl_account_code=gl_account_code,
		stmt_opening=stmt_opening,
		stmt_closing=stmt_closing,
		gl_opening=gl_opening,
		gl_closing=gl_closing,
		stmt_total_deposit=round(sum(row.deposit for row in stmt_rows), 2),
		stmt_total_withdrawal=round(sum(row.withdrawal for row in stmt_rows), 2),
		gl_total_credit=round(sum(row.credit for row in gl_rows), 2),
		gl_total_debit=round(sum(row.debit for row in gl_rows), 2),
		matched_count=len(matched_rows),
		gl_debit_only_count=len(gl_debit_only),
		gl_credit_only_count=len(gl_credit_only),
		stmt_withdrawal_only_count=len(stmt_withdrawal_only),
		stmt_deposit_only_count=len(stmt_deposit_only),
		gl_debit_only_amount=gl_debit_only_amount,
		gl_credit_only_amount=gl_credit_only_amount,
		stmt_withdrawal_only_amount=stmt_withdrawal_only_amount,
		stmt_deposit_only_amount=stmt_deposit_only_amount,
		opening_diff=opening_diff,
		formula_stmt_closing=formula_closing,
		formula_diff=formula_diff,
	)
	return recon_rows, summary
