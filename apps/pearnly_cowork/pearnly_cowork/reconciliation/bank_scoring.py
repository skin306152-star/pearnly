"""Deployed bank-transaction to invoice-candidate scoring.

Source: ec2e5c957e6b ``services/recon/bank_recon_scoring.py`` per-field scorers
and ``match_one_tx``. Weights total 100 (amount 50, date 30, direction 15,
keyword 5); ``THRESH_AUTO``/``THRESH_SUGGEST`` are the selection bands the
session layer applies to these scores.

Scoring is evidence for the accountant: candidates above the automatic band are
offered with their reason and per-field breakdown, never booked automatically.
Zeros keep their deployed meaning — a missing side scores zero, and an amount
that scores zero drops the candidate entirely.

Not migrated: ``run_matching_for_session`` (it reads and writes the legacy
database), candidate retrieval and result persistence (``bank_recon_match`` is a
SQL DAL), the statement/GL parsers and the review API.

Pure contract: no Frappe, database, file, clock, HTTP or model access.
"""

import re
from datetime import date
from typing import Any, Optional

from pearnly_cowork.reconciliation.bank_matching import DATE_TOL_DAYS

WEIGHT_AMOUNT = 50
WEIGHT_DATE = 30
WEIGHT_DIRECTION = 15
WEIGHT_KEYWORD = 5

THRESHOLD_AUTO = 85
THRESHOLD_SUGGEST = 60

AMOUNT_TOL_EQUAL = 0.01
AMOUNT_TOL_SMALL = 1.00
AMOUNT_TOL_MEDIUM = 10.00

INCOME_WORDS = ("sale", "sales", "revenue", "income", "销售", "收入")
EXPENSE_WORDS = ("purchase", "expense", "cost", "fee", "采购", "费用", "开支")

# Deployed keyword scan: runs of at least three Latin, Thai or ideographic letters.
KEYWORD_PATTERN = re.compile(r"[A-Za-z\u0E00-\u0E7F\u4e00-\u9fff]{3,}")

MAX_CANDIDATES = 5


def score_amount(bank_amount: float, invoice_amount: float) -> float:
	"""Amount proximity, 0..50; a missing or grossly different amount scores zero."""
	if not bank_amount or not invoice_amount:
		return 0.0
	difference = abs(float(bank_amount) - float(invoice_amount))
	if difference <= AMOUNT_TOL_EQUAL:
		return float(WEIGHT_AMOUNT)
	if difference <= AMOUNT_TOL_SMALL:
		return float(WEIGHT_AMOUNT) - 5
	if difference <= AMOUNT_TOL_MEDIUM:
		return float(WEIGHT_AMOUNT) - 15
	percentage = difference / max(float(invoice_amount), 0.01)
	if percentage <= 0.01:
		return 20.0
	if percentage <= 0.05:
		return 10.0
	return 0.0


def score_date(bank_date: Optional[str], invoice_date: Optional[str]) -> float:
	"""Date proximity, 0..30; unparsable or missing dates score zero."""
	if not bank_date or not invoice_date:
		return 0.0
	try:
		first = date.fromisoformat(bank_date)
		second = date.fromisoformat(invoice_date)
	except (ValueError, TypeError):
		return 0.0
	days = abs((first - second).days)
	if days == 0:
		return float(WEIGHT_DATE)
	if days <= 1:
		return 25.0
	if days <= 3:
		return 20.0
	if days <= DATE_TOL_DAYS:
		return 10.0
	return 0.0


def score_direction(bank_direction: str, invoice_meta: dict[str, Any]) -> float:
	"""Direction agreement, 0..15: money out pairs with purchases, money in with sales.

	The deployed default assumes most recognised histories are purchase invoices,
	so an unclassified outgoing payment keeps partial credit.
	"""
	if not bank_direction:
		return 0.0
	category = (invoice_meta.get("category_tag") or "").lower()
	is_income = any(word in category for word in INCOME_WORDS)
	is_expense = any(word in category for word in EXPENSE_WORDS)
	if bank_direction == "IN" and is_income:
		return float(WEIGHT_DIRECTION)
	if bank_direction == "OUT" and is_expense:
		return float(WEIGHT_DIRECTION)
	if bank_direction == "OUT" and not is_income:
		return float(WEIGHT_DIRECTION) * 0.7
	return float(WEIGHT_DIRECTION) * 0.3


def score_keyword(bank_desc: str, invoice_meta: dict[str, Any]) -> float:
	"""Description overlap, 0..5: vendor word match plus an invoice-number match."""
	if not bank_desc:
		return 0.0
	description_lower = bank_desc.lower()
	vendor = (invoice_meta.get("vendor") or "").lower()
	reference = (invoice_meta.get("invoice_no") or "").lower()
	score = 0.0
	if vendor and len(vendor) >= 3:
		for word in KEYWORD_PATTERN.findall(vendor):
			if word in description_lower:
				score += 3.0
				break
	if reference and reference in description_lower:
		score += 2.0
	return min(score, float(WEIGHT_KEYWORD))


def match_one_tx(bank_tx: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Rank invoice candidates for one bank transaction, strongest first.

	A candidate whose amount scores zero is dropped before the other fields are
	considered. Each result carries the total, a human-readable reason and the
	per-field breakdown; at most five candidates are returned.
	"""
	scored: list[dict[str, Any]] = []
	for invoice in candidates:
		amount = score_amount(bank_tx.get("amount") or 0,
			invoice.get("amount_total") or invoice.get("total") or 0)
		if amount <= 0:
			continue
		when = score_date(bank_tx.get("tx_date"), invoice.get("invoice_date"))
		direction = score_direction(bank_tx.get("direction") or "", invoice)
		keyword = score_keyword(bank_tx.get("description") or "", invoice)
		total = round(amount + when + direction + keyword, 2)
		reasons = []
		if amount >= WEIGHT_AMOUNT - 0.5:
			reasons.append("金额精确")
		elif amount >= WEIGHT_AMOUNT - 5.5:
			reasons.append("金额接近")
		if when >= WEIGHT_DATE - 0.5:
			reasons.append("同日")
		elif when >= 25:
			reasons.append("日期差 1 天")
		elif when >= 20:
			reasons.append("日期差 3 天内")
		elif when >= 10:
			reasons.append("日期差 7 天内")
		if keyword > 0:
			reasons.append("描述匹配")
		scored.append({
			"history_id": invoice["id"],
			"score": total,
			"reason": " + ".join(reasons) if reasons else "低置信",
			"breakdown": {"amount": amount, "date": when, "direction": direction, "keyword": keyword},
		})
	scored.sort(key=lambda item: item["score"], reverse=True)
	return scored[:MAX_CANDIDATES]
