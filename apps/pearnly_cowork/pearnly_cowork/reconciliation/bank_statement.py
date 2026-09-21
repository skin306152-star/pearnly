"""Deployed bank-statement row contract and balance-chain decisions.

Source: ec2e5c957e6b ``services/recon/bank_stmt_balance.py`` (verbatim split of
``bank_recon_v2.py``), plus the statement-row dataclass from
``services/recon/bank_recon_types.py``, ``AMOUNT_TOL`` from
``services/recon/bank_recon_utils.py`` and the summary-row keywords from
``services/recon/bank_table_io.py``.

Migrated here: the summary-row filter, the parse-quality ratio, direction
auto-correction from the running balance, per-row arithmetic verification,
balance-driven amount repair, the ordered ``finalize_rows`` pipeline and the
printed-footer completeness audit. Not migrated: the statement/GL file parsers
(xlsx, PDF, text, model extraction), their stores, the matching/scoring stage,
``GlRow``/``BankReconRow``/``BankReconSummary``, exports and the work-order
step that calls them.

``bank_recon_utils`` itself also carries in-memory and on-disk model-result
caches, so only its tolerance constant is reproduced here; the module is not
copied. All arithmetic keeps the deployed binary-float decisions, including the
per-row tolerance ``min(max(AMOUNT_TOL, amount * 0.005), 1.0)``.

The functions mutate the passed rows in place, exactly like the deployed ones,
because the parsers hand their own row objects to this pipeline. Nothing here
reads files, calls a model, writes ERP or touches the database.
"""

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Optional

AMOUNT_TOL = 0.02

# Deployed v118.35.0.60 list: footer totals such as "Total Credit" or
# "จำนวนเงินฝาก" were once parsed as transactions and broke the balance chain.
SUMMARY_ROW_KEYWORDS = (
    "total credit",
    "total debit",
    "total deposit",
    "total withdrawal",
    "total transaction",
    "grand total",
    "subtotal",
    "sub total",
    "รวมรายการ",
    "ยอดรวม",
    "รวมยอด",
    "รวมเงิน",
    "总计",
    "合计",
    "小计",
    "本期合计",
    "累计",
)

# Deployed v118.33.13.1 list: rows that carry a balance but no movement.
OPENING_BALANCE_KEYWORDS = (
    "ยอดยกมา",
    "ยอดคงเหลือยกมา",
    "brought forward",
    "opening balance",
    "balance b/f",
    "期初余额",
    "上期结转",
)

# Deployed v118.35.0.62 cap: a larger gap looks like a missing row, not a
# misread digit, so the amount is left for the accountant.
REPAIR_RATIO = 0.30


@dataclass
class StatementRow:
	"""One parsed statement row; ``balance`` may be unknown for a statement page."""

	date: Optional[date]
	description: str
	withdrawal: float
	deposit: float
	balance: float
	source_file: str = ""
	account_no: str = ""
	row_hash: str = ""
	confidence: str = "high"
	balance_ok: Optional[bool] = None
	direction_autocorrected: bool = False
	amount_autocorrected: bool = False

	def __post_init__(self):
		if not self.row_hash:
			# The deployed fingerprint includes the balance so two legitimate
			# same-day, same-amount transactions stay distinct.
			key = (
				f"{self.account_no}|{self.date}|{self.withdrawal:.2f}|{self.deposit:.2f}"
				f"|{self.balance:.2f}|{self.description[:40]}"
			)
			# Deduplication fingerprint, never a security primitive.
			self.row_hash = hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:12]


def is_summary_row(description: str) -> bool:
	"""Deployed footer-total detection; an empty description is not a summary row."""
	value = (description or "").strip().lower()
	if not value:
		return False
	return any(keyword in value for keyword in SUMMARY_ROW_KEYWORDS)


def statement_row_bad_ratio(rows: list[StatementRow], opening: float) -> float:
	"""Deployed parse-quality gate: share of movement rows whose balance disagrees.

	Only the magnitude is compared, so a statement whose debit/credit columns are
	swapped while the balance is right keeps a low ratio and is not re-read by a
	model. Fewer than three movement rows never counts as bad.
	"""
	movement = [row for row in rows if row.balance is not None and (row.withdrawal or row.deposit)]
	if len(movement) < 3:
		return 0.0
	opening_reliable = bool(opening)
	previous = opening
	first_seen = False
	bad = 0
	total = 0
	for row in rows:
		if row.balance is None:
			continue
		amount = max(row.withdrawal or 0.0, row.deposit or 0.0)
		if amount == 0.0:
			previous = row.balance
			continue
		if row.balance == 0.0:
			bad += 1
			total += 1
			previous = row.balance
			continue
		if not first_seen and not opening_reliable:
			first_seen = True
			previous = row.balance
			continue
		first_seen = True
		delta = round(row.balance - previous, 2)
		if abs(abs(delta) - amount) > 1.0:
			bad += 1
		total += 1
		previous = row.balance
	return (bad / total) if total else 0.0


def correct_direction_from_balance(rows: list[StatementRow], opening: float) -> None:
	"""Deployed v118.35.0.50 rule: swap a column pair that the balance contradicts.

	Only when the previous balance is known, the recorded direction contradicts
	the balance movement and the recorded amount matches that movement within
	``AMOUNT_TOL``; a missing or misread row is left for the accountant to check.
	"""
	if not rows:
		return
	previous = opening
	opening_reliable = opening != 0.0
	first_movement_seen = False
	for row in rows:
		if row.balance is None:
			continue
		deposit = row.deposit or 0
		withdrawal = row.withdrawal or 0
		if deposit == 0 and withdrawal == 0:
			previous = row.balance
			continue
		if not first_movement_seen and not opening_reliable:
			first_movement_seen = True
			previous = row.balance
			continue
		first_movement_seen = True
		delta = round(row.balance - previous, 2)
		amount = max(deposit, withdrawal)
		if amount > 0 and abs(abs(delta) - amount) <= AMOUNT_TOL:
			if delta > 0 and withdrawal > 0:
				row.deposit, row.withdrawal = amount, 0.0
				row.direction_autocorrected = True
			elif delta < 0 and deposit > 0:
				row.withdrawal, row.deposit = amount, 0.0
				row.direction_autocorrected = True
		previous = row.balance


def verify_row_balances(rows: list[StatementRow], opening: float) -> None:
	"""Deployed per-row check: does ``previous + deposit - withdrawal`` equal the balance?

	``balance_ok`` stays ``None`` where the row cannot be checked: no balance, no
	movement, an explicit opening/closing label, or the first movement after an
	unreliable opening (a continuation page has nothing to compare against).
	Rows without a movement still seed the running balance.
	"""
	if not rows:
		return
	previous = opening
	opening_reliable = opening != 0.0
	first_movement_seen = False
	for row in rows:
		if row.balance is None:
			row.balance_ok = None
			continue
		deposit = row.deposit or 0
		withdrawal = row.withdrawal or 0
		description_lower = (row.description or "").lower()
		is_opening_row = any(keyword in row.description for keyword in OPENING_BALANCE_KEYWORDS) or any(
			keyword in description_lower for keyword in (item.lower() for item in OPENING_BALANCE_KEYWORDS)
		)
		if (deposit == 0 and withdrawal == 0) or is_opening_row:
			row.balance_ok = None
			previous = row.balance
			continue
		if not first_movement_seen and not opening_reliable:
			row.balance_ok = None
			previous = row.balance
			first_movement_seen = True
			continue
		first_movement_seen = True
		expected = round(previous + deposit - withdrawal, 2)
		difference = abs(expected - row.balance)
		amount = max(abs(deposit), abs(withdrawal))
		tolerance = min(max(AMOUNT_TOL, amount * 0.005), 1.0)
		row.balance_ok = difference <= tolerance
		previous = row.balance


def repair_amount_from_balance(rows: list[StatementRow], opening: float) -> None:
	"""Deployed v118.35.0.62 repair for a misread digit, never for a missing row.

	Requires a reliable previous balance, a next row that corroborates this row's
	balance, and a gap under ``REPAIR_RATIO``; the corrected amount follows the
	balance movement and the row is marked. Anything larger stays flagged for the
	accountant. Must run after ``verify_row_balances``, whose flags it reads.
	"""
	if not rows or len(rows) < 2:
		return

	def movement(row):
		return (row.deposit or 0) - (row.withdrawal or 0)

	for index in range(1, len(rows) - 1):
		row = rows[index]
		if row.balance_ok is not False or row.balance is None:
			continue
		previous_row = rows[index - 1]
		if previous_row.balance is None or previous_row.balance_ok is False:
			continue
		next_row = rows[index + 1]
		if next_row.balance is None:
			continue
		next_expected = round(row.balance + movement(next_row), 2)
		next_amount = max(abs(next_row.deposit or 0), abs(next_row.withdrawal or 0))
		next_tolerance = min(max(AMOUNT_TOL, next_amount * 0.005), 1.0)
		if abs(next_expected - next_row.balance) > next_tolerance:
			continue
		corrected = round(row.balance - previous_row.balance, 2)
		if abs(corrected) < AMOUNT_TOL:
			continue
		printed = movement(row)
		if abs(corrected - printed) <= max(AMOUNT_TOL, abs(corrected) * REPAIR_RATIO):
			if corrected > 0:
				row.deposit, row.withdrawal = abs(corrected), 0.0
			else:
				row.withdrawal, row.deposit = abs(corrected), 0.0
			row.amount_autocorrected = True
			row.balance_ok = True


def finalize_rows(rows: list[StatementRow], opening: float) -> list[StatementRow]:
	"""Deployed single source of truth for the balance-chain sequence.

	The order is a correctness invariant and must not be rearranged: filter
	footer totals, then auto-correct direction, then verify, then repair. Rows are
	modified in place and the filtered list is returned (it can be shorter).
	``opening`` is resolved by each caller under its own rule; this function does
	not guess one.
	"""
	kept = [row for row in rows if not is_summary_row(row.description)]
	correct_direction_from_balance(kept, opening)
	verify_row_balances(kept, opening)
	repair_amount_from_balance(kept, opening)
	return kept


def audit_completeness(rows: list[StatementRow], opening: float, closing: float,
		printed: Optional[dict] = None) -> dict:
	"""Deployed v118.35.0.63 cross-check against the statement's printed footer.

	Three independent gates: the closing balance, the printed deposit/withdrawal
	totals, and the printed line counts. A count mismatch is the strongest
	evidence of a missed row, so counts are always checked; a total that merely
	repeats a balance is treated as a mis-filed field and skipped.
	"""
	sum_deposit = round(sum(row.deposit or 0 for row in rows), 2)
	sum_withdrawal = round(sum(row.withdrawal or 0 for row in rows), 2)
	count_deposit = sum(1 for row in rows if (row.deposit or 0) > 0)
	count_withdrawal = sum(1 for row in rows if (row.withdrawal or 0) > 0)
	issues: list[dict] = []

	def tolerance(value):
		return max(1.0, abs(value) * 0.001)

	if opening and closing:
		calculated = round(opening + sum_deposit - sum_withdrawal, 2)
		if abs(calculated - closing) > tolerance(closing):
			issues.append({
				"type": "closing_mismatch",
				"calc": calculated,
				"printed": closing,
				"diff": round(calculated - closing, 2),
			})
	source = printed or {}
	printed_credit, printed_debit = source.get("total_credit"), source.get("total_debit")
	printed_credit_count = source.get("credit_count")
	printed_debit_count = source.get("debit_count")
	# A total copied from a balance is not evidence of a misread amount.
	balance_misfile_epsilon = 0.5

	def sum_reliable(value, other):
		if value is None or value <= 0:
			return False
		if other is not None and abs(value - other) < 0.01:
			return False
		if closing and abs(value - closing) < balance_misfile_epsilon:
			return False
		if opening and abs(value - opening) < balance_misfile_epsilon:
			return False
		return True

	if sum_reliable(printed_credit, printed_debit) and abs(sum_deposit - printed_credit) > tolerance(
			printed_credit):
		issues.append({
			"type": "credit_sum_mismatch",
			"sum": sum_deposit,
			"printed": printed_credit,
			"diff": round(sum_deposit - printed_credit, 2),
		})
	if sum_reliable(printed_debit, printed_credit) and abs(sum_withdrawal - printed_debit) > tolerance(
			printed_debit):
		issues.append({
			"type": "debit_sum_mismatch",
			"sum": sum_withdrawal,
			"printed": printed_debit,
			"diff": round(sum_withdrawal - printed_debit, 2),
		})
	if printed_credit_count is not None and 0 <= printed_credit_count < 100000 and int(
			printed_credit_count) != count_deposit:
		issues.append({"type": "credit_count_mismatch", "count": count_deposit,
			"printed": int(printed_credit_count)})
	if printed_debit_count is not None and 0 <= printed_debit_count < 100000 and int(
			printed_debit_count) != count_withdrawal:
		issues.append({"type": "debit_count_mismatch", "count": count_withdrawal,
			"printed": int(printed_debit_count)})
	return {
		"ok": len(issues) == 0,
		"issues": issues,
		"sum_deposit": sum_deposit,
		"sum_withdrawal": sum_withdrawal,
		"count_deposit": count_deposit,
		"count_withdrawal": count_withdrawal,
	}
