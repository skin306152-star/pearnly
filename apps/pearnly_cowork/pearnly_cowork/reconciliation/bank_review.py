"""Pure S8 statement review: gate, editable rows and exact balance checks.

The deployed predicate is retained; only PDF is admitted by the native file
flow. No column mapping, file access, model calls or framework dependency here.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from pearnly_cowork.reconciliation import bank_recon_contracts as contracts

EDITABLE_FIELDS = ("date", "description", "withdrawal", "deposit", "balance")
CENT = Decimal("0.01")
TOLERANCE = Decimal("0.02")


def needs_review(result):
	if not result or not result.get("ok") or not result.get("rows"):
		return False
	return (int(result.get("low_conf_count") or 0) > 0
		or int(result.get("amount_fixed_count") or 0) > 0
		or (result.get("completeness") or {}).get("ok") is False)


def serializable(value):
	if isinstance(value, Decimal):
		return str(value)
	if isinstance(value, date):
		return value.isoformat()
	if isinstance(value, dict):
		return {key: serializable(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [serializable(item) for item in value]
	return value


def editable_rows(rows):
	return [{"idx": index, **{key: row.get(key) for key in EDITABLE_FIELDS}}
		for index, row in enumerate(serializable(rows))]


def validate_rows(value, original, gl_rows):
	"""Full replacement; unique original idx or null for a manually added row.

	Omission explicitly removes an original row. Model metadata is server-owned;
	it cannot be overwritten by the caller. The frozen input remains the audit.
	"""
	if (not isinstance(value, list) or not value
			or len(value) + len(gl_rows) > contracts.MAX_ROWS):
		raise contracts.ReconciliationContractError("reconciliation.review_rows_invalid")
	seen, result, canonical = set(), [], []
	for raw in value:
		if not isinstance(raw, dict) or set(raw) != {"idx", *EDITABLE_FIELDS}:
			raise contracts.ReconciliationContractError("reconciliation.review_rows_invalid")
		index = raw["idx"]
		if index is not None:
			if type(index) is not int or not 0 <= index < len(original) or index in seen:
				raise contracts.ReconciliationContractError("reconciliation.review_rows_invalid")
			seen.add(index)
		metadata = ({key: original[index].get(key)
			for key in ("confidence", "autocorrected")} if index is not None else {})
		row = contracts.statement_row({**metadata,
			**{key: raw[key] for key in EDITABLE_FIELDS}})
		if row["balance"] is None or any(
			row[key] != row[key].quantize(CENT)
			for key in ("withdrawal", "deposit", "balance")):
			raise contracts.ReconciliationContractError("reconciliation.review_amount_invalid")
		for key in ("withdrawal", "deposit", "balance"):
			row[key] = row[key].quantize(CENT)
		result.append(row)
		canonical.append({"idx": index, **{key: row[key] for key in EDITABLE_FIELDS}})
	return result, serializable(canonical)


def balance_chain(rows, opening):
	"""Use the actual preceding balance, so one bad row does not poison the rest."""
	checks = []
	with localcontext() as context:
		context.prec = 40
		previous = Decimal(str(opening)).quantize(CENT, rounding=ROUND_HALF_EVEN)
		for index, row in enumerate(rows):
			expected = (previous + row["deposit"] - row["withdrawal"]).quantize(CENT)
			if row["balance"] is None:
				# 余额格空着（PDF 读出的行常没有）：这一行算没对上、请人补，后面的行接着按推算值核；
				# 原来直接对空值取整，保存 / 确认弹系统错误（2026-09-29 排查）。
				checks.append({"idx": index, "expected": str(expected), "actual": "", "diff": "", "ok": False})
				previous = expected
				continue
			actual = row["balance"].quantize(CENT)
			difference = actual - expected
			checks.append({"idx": index, "expected": str(expected), "actual": str(actual),
				"diff": str(difference), "ok": abs(difference) <= TOLERANCE})
			previous = actual
	return checks
