"""Explicit fixed-source parity runner; use --source with the verified source export.

This is an offline migration check, not a Site test. It evaluates only selected
pure function definitions from hash-verified source; it imports no legacy app,
database, network client, startup code, or runtime adapter.
"""

import argparse
import ast
import hashlib
import json
import random
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Dict, List, Optional

from pearnly_cowork.reconciliation import fields
from pearnly_cowork.reconciliation.amounts import invoice_total, report_total, within_tolerance
from pearnly_cowork.reconciliation.matching import match_rows, pair_by_invoice_number
from pearnly_cowork.stocktake import contracts

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
	"services/stocktake/excel.py": "c8027111a6f4c77fdb6fa329df6f9cf798c36440211e44bb2d2673939917f13d",
	"services/recon/field_comparator.py": "109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037",
	"services/recon/reconciliation_matcher.py": "bc62255e03f1898a4eb6eb83e088d2af08f0cc93ab1d07d9dde2ee359cdeb4e6",
}


class SourceError(Exception):
	def __init__(self, status_code, detail):
		self.status_code = status_code
		self.detail = detail


def definitions(root, path, names, namespace, assignments=()):
	assignments = set(assignments)
	data = (root / path).read_bytes()
	if hashlib.sha256(data).hexdigest() != HASHES[path]:
		raise ValueError(f"Source does not match {SOURCE_SHA}: {path}")
	tree = ast.parse(data)
	nodes = [node for node in tree.body
		if (isinstance(node, ast.FunctionDef) and node.name in names)
		or (isinstance(node, ast.Assign) and len(node.targets) == 1
			and isinstance(node.targets[0], ast.Name) and node.targets[0].id in assignments)]
	if {node.name for node in nodes if isinstance(node, ast.FunctionDef)} != set(names):
		raise ValueError(f"Missing source definitions: {path}")
	if not assignments <= {node.targets[0].id for node in nodes if isinstance(node, ast.Assign)}:
		raise ValueError(f"Missing source assignments: {path}")
	exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root / path), "exec"), namespace)


def result(function, *args):
	"""Compare success payloads and exception identity, never a partial outcome."""
	try:
		return "ok", function(*args)
	except Exception as error:
		return "error", type(error).__name__, str(error)


def outcome(function, value, nonnegative):
	try:
		return "ok", function(value, nonnegative=nonnegative)
	except SourceError as error:
		return "error", error.detail
	except contracts.StocktakeValidationError as error:
		return "error", str(error)


def verify(root):
	quantity_ns = {"Decimal": Decimal, "InvalidOperation": InvalidOperation, "HTTPException": SourceError}
	definitions(root, "services/stocktake/excel.py", {"quantity"}, quantity_ns)
	rng = random.Random(20260920)
	values = [None, True, False, "", "NaN", "sNaN", "Infinity", "-Infinity", "1e99", "1,000"]
	values += [format(Decimal(rng.randint(-10**20, 10**20)).scaleb(-rng.randint(0, 9)), "f") for _ in range(2000)]
	for value in values:
		for nonnegative in (False, True):
			old = outcome(quantity_ns["quantity"], value, nonnegative)
			new = outcome(contracts.quantity, value, nonnegative)
			if old != new:
				raise AssertionError((value, nonnegative, old, new))
	namespace = {
		"re": re, "unicodedata": unicodedata, "date": date, "datetime": datetime,
		"Optional": Optional, "Dict": Dict, "List": List, "Any": Any,
	}
	definitions(root, "services/recon/field_comparator.py", {
		"normalize_str", "normalize_name", "normalize_invoice_no", "normalize_tax_id",
		"normalize_branch", "_thai_to_gregorian", "parse_date", "_r", "compare_field",
		"compare_all_fields",
	}, namespace, assignments={"_HQ_ALIASES", "_FIELD_MAP"})
	definitions(root, "services/recon/reconciliation_matcher.py", {"_get_total", "run_matching", "_make_pair"}, namespace)
	keys = ["A", "a", "B-1", "b/1", "INV001", "IV001", "INV1", "", None, "１２３"]
	for batch in range(1000):
		invoices = [{"id": index, "invoice_no": rng.choice(keys)} for index in range(rng.randint(0, 30))]
		reports = [{"row_no": index, "report_invoice_no": rng.choice(keys)} for index in range(rng.randint(0, 30))]
		old = namespace["run_matching"](invoices, reports)
		new = pair_by_invoice_number(invoices, reports)
		if (old["pairs"], old["invoice_orphans"], old["report_orphans"]) != (
			new.pairs, new.unmatched_invoice_indices, new.unmatched_report_indices,
		):
			raise AssertionError(("first-pass pairing mismatch", batch))
	# Registered amount contract: totals, fallbacks and the binary-float band.
	amount_values = [
		None, "", " ", 0, 1, "0", "0.00", "0.01", "1.00", "1.01", "1.005", "1.015", "2.675", "2.68",
		"2.685", "2.69", "-2.675", "-2.68", "13.00", "13.01", "100.00", "100.004", "100.005",
		"100.01", "100.02", "107.00", "107.01", "999.99", "1000.00", "1000.01", "1,000.00",
		"1,234.50", "abc", "NaN", "inf", "-inf", "1e5", "1E-3", 100.0, 2.675, True, False, [1],
		{"a": 1}, "\t5\t",
	]
	amount_rng = random.Random(20260921)
	amount_values += [format(Decimal(amount_rng.randint(-10**9, 10**9)).scaleb(-2), "f")
		for _ in range(300)]
	amount_cases = 0
	for left in amount_values:
		for right in amount_values:
			old_left = namespace["_get_total"]({"amount_pre_vat": left})
			old_right = namespace["_get_total"]({"report_amount": right}, True)
			new_left = invoice_total({"amount_pre_vat": left})
			new_right = report_total({"report_amount": right})
			# repr keeps NaN-comparing-NaN equal without hiding a real difference.
			if (repr(old_left), repr(old_right), abs(old_left - old_right) <= 0.01) != (
					repr(new_left), repr(new_right), within_tolerance(new_left, new_right)):
				raise AssertionError(("amount contract mismatch", left, right))
			amount_cases += 1
	for row in ({"amount_pre_vat": "100", "total_amount": "107"}, {"total_amount": "107"}, {},
			{"amount_pre_vat": ""}, {"amount_pre_vat": None, "total_amount": "0.00"},
			{"amount_pre_vat": "0", "total_amount": "107"}):
		for is_report in (False, True):
			old = namespace["_get_total"](row, is_report)
			new = report_total(row) if is_report else invoice_total(row)
			if repr(old) != repr(new):
				raise AssertionError(("amount fallback mismatch", row, is_report, old, new))
			amount_cases += 1
	# Per-field comparison, including both amount messages and the date bands.
	field_values = amount_values + [
		"0107536000188", "0-1075 3600018-8", "0107536000189", "สำนักงานใหญ่", "สนญ", "00000", "12",
		"Head Office", "HQ", " ACME ", "acme co", "ACME\nCO", "15/03/2026", "15/03/69", "2026-03-15",
		"15-03-2026", "15/03/2569", "2026/03/15", "15 Mar 2026", "15 March 2026", "31/02/2026",
		"INV-001", "inv001", "IV001", "ＡＢＣ", "叻",
	]
	field_names = ["date", "document_date", "invoice_date", "invoice_no", "document_no", "doc_no",
		"buyer_name", "customer_name", "buyer_tax_id", "customer_tax_id", "buyer_branch",
		"customer_branch", "amount_pre_vat", "net_amount", "vat_amount", "tax_amount", "output_vat",
		"VAT_Amount", "Net_Amount", "note", "unknown"]
	field_rng = random.Random(20260922)
	field_cases = 0
	for _ in range(6000):
		name = field_rng.choice(field_names)
		left = field_rng.choice(field_values)
		right = field_rng.choice(field_values)
		if result(namespace["compare_field"], name, left, right) != result(
				fields.compare_field, name, left, right):
			raise AssertionError(("compare_field mismatch", name, left, right))
		field_cases += 1
	invoice_keys = [key for _, key, _ in namespace["_FIELD_MAP"]]
	report_keys = [key for _, _, key in namespace["_FIELD_MAP"]]
	all_field_cases = 0
	for _ in range(1500):
		invoice_row = {key: field_rng.choice(field_values) for key in invoice_keys}
		report_row = {key: field_rng.choice(field_values) for key in report_keys}
		for skip_buyer in (False, True):
			if namespace["compare_all_fields"](invoice_row, report_row, skip_buyer) != fields.compare_all_fields(
					invoice_row, report_row, skip_buyer):
				raise AssertionError(("compare_all_fields mismatch", invoice_row, report_row, skip_buyer))
			all_field_cases += 1
	# Whole deployed matcher: strong-key pairing, orphans and stats.
	number_pool = ["INV-001", "inv001", "IV-002", "IV002", "INV-003", "OTHER", "", None]
	date_pool = ["15/03/2026", "15/03/2569", "16/03/2026", "2026-03-15", "17/03/2026", "bad"]
	tax_pool = ["0107536000188", "0-1075 3600018-8", "0107536000189", "", None]
	amount_pool = ["100.00", "100.004", "100.01", "250.00", "2.675", "2.67", "500.00", "abc", ""]
	match_rng = random.Random(20260923)
	match_cases = 0
	pass_two_cases = 0
	for batch in range(2000):
		invoices = [{"id": f"i{index}", "invoice_no": match_rng.choice(number_pool),
			"invoice_date": match_rng.choice(date_pool), "buyer_tax_id": match_rng.choice(tax_pool),
			"amount_pre_vat": match_rng.choice(amount_pool), "total_amount": match_rng.choice(amount_pool)}
			for index in range(match_rng.randint(0, 12))]
		reports = [{"row_no": 100 + index, "report_invoice_no": match_rng.choice(number_pool),
			"report_date": match_rng.choice(date_pool), "report_buyer_tax_id": match_rng.choice(tax_pool),
			"report_amount": match_rng.choice(amount_pool),
			"report_amount_pre_vat": match_rng.choice(amount_pool)}
			for index in range(match_rng.randint(0, 12))]
		old = namespace["run_matching"](invoices, reports)
		new = match_rows(invoices, reports)
		if (old["pairs"], old["invoice_orphans"], old["report_orphans"], old["stats"]) != (
				new.pairs, new.invoice_orphans, new.report_orphans, new.stats):
			raise AssertionError(("matching mismatch", batch, old["pairs"], new.pairs))
		match_cases += 1
		pass_two_cases += old["stats"]["pass2_count"]
	if pass_two_cases < 100:
		raise AssertionError(("second pass barely exercised", pass_two_cases))
	# Investigation only: no Decimal amount matcher is installed in product code.
	decimal_investigations = [
		("2.675", "2.68", False, True), ("-2.675", "-2.68", False, True),
		("2.675", "2.69", False, True), ("1.00", "1.01", False, True),
		("1000.00", "1000.01", True, True), ("-1.00", "-1.01", False, True),
		("0.00", "0.01", True, True), ("1.005", "1.015", False, False),
	]
	for left, right, old_expected, decimal_expected in decimal_investigations:
		old_left = namespace["_get_total"]({"amount_pre_vat": left})
		old_right = namespace["_get_total"]({"report_amount": right}, True)
		old_match = abs(old_left - old_right) <= 0.01
		decimal_left = Decimal(left).quantize(Decimal(".01"), rounding=ROUND_HALF_EVEN)
		decimal_right = Decimal(right).quantize(Decimal(".01"), rounding=ROUND_HALF_EVEN)
		decimal_match = abs(decimal_left - decimal_right) <= Decimal(".01")
		if (old_match, decimal_match) != (old_expected, decimal_expected):
			raise AssertionError(("amount investigation drift", left, right, old_match, decimal_match))
		if within_tolerance(invoice_total({"amount_pre_vat": left}),
				report_total({"report_amount": right})) != old_match:
			raise AssertionError(("installed rule diverges from source", left, right))
		print(json.dumps({"input": [left, right], "source_match": old_match, "decimal_match": decimal_match}))
	print(json.dumps({"amount_cases": amount_cases, "field_cases": field_cases,
		"all_field_cases": all_field_cases, "match_cases": match_cases,
		"source_pass2_pairs": pass_two_cases}))
	print("PASS: 4020 source quantity cases; 1000 source first-pass batches; "
		f"{amount_cases} amount contract cases; {field_cases} compare_field cases; "
		f"{all_field_cases} compare_all_fields cases; {match_cases} full matcher batches "
		f"({pass_two_cases} second-pass pairs); 8 amount investigations")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--source", type=Path, required=True)
	verify(parser.parse_args().source)
