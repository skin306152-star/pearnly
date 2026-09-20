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

from pearnly_cowork.reconciliation.matching import pair_by_invoice_number
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


def definitions(root, path, names, namespace):
	data = (root / path).read_bytes()
	if hashlib.sha256(data).hexdigest() != HASHES[path]:
		raise ValueError(f"Source does not match {SOURCE_SHA}: {path}")
	tree = ast.parse(data)
	nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
	if {node.name for node in nodes} != names:
		raise ValueError(f"Missing source definitions: {path}")
	exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root / path), "exec"), namespace)


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
		"normalize_str", "normalize_invoice_no", "normalize_tax_id", "_thai_to_gregorian", "parse_date",
	}, namespace)
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
	# Investigation only: no Decimal amount matcher is installed in product code.
	amount_cases = [
		("2.675", "2.68", False, True), ("-2.675", "-2.68", False, True),
		("2.675", "2.69", False, True), ("1.00", "1.01", False, True),
		("1000.00", "1000.01", True, True), ("-1.00", "-1.01", False, True),
		("0.00", "0.01", True, True), ("1.005", "1.015", False, False),
	]
	for left, right, old_expected, decimal_expected in amount_cases:
		old_left = namespace["_get_total"]({"amount_pre_vat": left})
		old_right = namespace["_get_total"]({"report_amount": right}, True)
		old_match = abs(old_left - old_right) <= 0.01
		decimal_left = Decimal(left).quantize(Decimal(".01"), rounding=ROUND_HALF_EVEN)
		decimal_right = Decimal(right).quantize(Decimal(".01"), rounding=ROUND_HALF_EVEN)
		decimal_match = abs(decimal_left - decimal_right) <= Decimal(".01")
		if (old_match, decimal_match) != (old_expected, decimal_expected):
			raise AssertionError(("amount investigation drift", left, right, old_match, decimal_match))
		print(json.dumps({"input": [left, right], "source_match": old_match, "decimal_match": decimal_match}))
	print("PASS: 4020 source quantity cases; 1000 source first-pass batches; 8 amount investigations")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--source", type=Path, required=True)
	verify(parser.parse_args().source)
