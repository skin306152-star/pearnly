"""Exact count totals and report projections; no stock ledger or external writes.

Source: legacy ec2e5c957e6b services/stocktake/entries.py:_total and
services/stocktake/reports.py:workbook. Inputs must be from one authorized,
consistent snapshot. Presentation translates the returned comparison codes.
"""

from collections.abc import Iterable, Mapping
from decimal import Decimal

from pearnly_cowork.stocktake.contracts import quantity


def active_quantity(entries: Iterable[Mapping]) -> Decimal | None:
	"""Mirror SQL SUM: no non-voided entries means None; a zero count means 0."""
	values = [quantity(entry["quantity"], nonnegative=True) for entry in entries if not entry["voided"]]
	if not values:
		return None
	return quantity(sum(values, Decimal("0")), nonnegative=True)


def compare_location(book: str, actual_values: Iterable[str]) -> str:
	"""Retain source comparison order: uncounted, unspecified, equal, different."""
	values = tuple(actual_values)
	if not values:
		return "not_counted"
	if not book:
		return "not_specified"
	return "matched" if all(value == book for value in values) else "mismatched"


def summarize_item(item: Mapping, entries: Iterable[Mapping]) -> dict:
	"""Project one item using its persisted quantity and its active locations.

	Do not silently recalculate actual_qty: it is read with the entries from a
	locked snapshot, as in the deployed report. active_quantity is used by the
	transactional writer when updating that value. Voided entries remain audit
	data but cannot alter active locations or totals.
	"""
	active = [entry for entry in entries if entry["item_id"] == item["id"] and not entry["voided"]]
	warehouses = sorted({entry["warehouse"] for entry in active})
	locations = sorted({entry["location"] for entry in active})
	book = quantity(item["book_qty"])
	actual = None if item["actual_qty"] is None else quantity(item["actual_qty"], nonnegative=True)
	difference = None if actual is None else actual - book
	result = (
		"not_counted" if actual is None
		else "matched" if difference == 0
		else "over" if difference > 0
		else "short"
	)
	return {
		"item_id": item["id"],
		"book_qty": book,
		"actual_qty": actual,
		"difference": difference,
		"count_result": result,
		"warehouses": warehouses,
		"locations": locations,
		"warehouse_comparison": compare_location(item["warehouse"], warehouses),
		"location_comparison": compare_location(item["location"], locations),
	}
