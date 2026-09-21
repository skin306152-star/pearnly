"""Explicit fixed-source parity runner; use --source with the verified source export.

This is an offline migration check, not a Site test. It evaluates only selected
pure function definitions from hash-verified source; it imports no legacy app,
database, network client, startup code, or runtime adapter.
"""

import argparse
import ast
import dataclasses
import hashlib
import json
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Dict, List, Optional

from pearnly_cowork.reconciliation import fields
from pearnly_cowork.reconciliation import bank_statement as statement
from pearnly_cowork.reconciliation import bank_matching as matching
from pearnly_cowork.reconciliation import bank_scoring as scoring
from pearnly_cowork.reconciliation.amounts import invoice_total, report_total, within_tolerance
from pearnly_cowork.reconciliation.matching import match_rows, pair_by_invoice_number
from pearnly_cowork.stocktake import contracts

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
	"services/stocktake/excel.py": "c8027111a6f4c77fdb6fa329df6f9cf798c36440211e44bb2d2673939917f13d",
	"services/recon/field_comparator.py": "109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037",
	"services/recon/reconciliation_matcher.py": "bc62255e03f1898a4eb6eb83e088d2af08f0cc93ab1d07d9dde2ee359cdeb4e6",
	"services/recon/bank_stmt_balance.py": "36cb82f137502c65a069dae61cb468e1def6bfeab8169471430fdcdb6ffa7013",
	"services/recon/bank_recon_types.py": "df0508cccb9891a5ea628cbce104ee09001b976ade15afd4d599c5d766de149e",
	"services/recon/bank_table_io.py": "19b9161eb5a96f8244efba97b215b50710365ba97f1bd93f8ce04ccd302f190e",
	"services/recon/bank_recon_utils.py": "35b143f7cfc115fc497397902026c463cdc7a589da70ea690d85f35b4f016745",
	"services/recon/bank_recon_reconcile.py": "8b1da4e4c5879bdcd79ef8225787b3b05bb3ec758535696f1b3c1c1c2349f153",
	"services/recon/bank_recon_scoring.py": "9738f080c874b59c7d3739db4fa9048b8742295a605f9875b38d23a36424133b",
}


class SourceError(Exception):
	def __init__(self, status_code, detail):
		self.status_code = status_code
		self.detail = detail


def definitions(root, path, names, namespace, assignments=(), classes=()):
	assignments = set(assignments)
	classes = set(classes)
	data = (root / path).read_bytes()
	if hashlib.sha256(data).hexdigest() != HASHES[path]:
		raise ValueError(f"Source does not match {SOURCE_SHA}: {path}")
	tree = ast.parse(data)
	nodes = [node for node in tree.body
		if (isinstance(node, ast.FunctionDef) and node.name in names)
		or (isinstance(node, ast.ClassDef) and node.name in classes)
		or (isinstance(node, ast.Assign) and len(node.targets) == 1
			and isinstance(node.targets[0], ast.Name) and node.targets[0].id in assignments)]
	if {node.name for node in nodes if isinstance(node, ast.FunctionDef)} != set(names):
		raise ValueError(f"Missing source definitions: {path}")
	if {node.name for node in nodes if isinstance(node, ast.ClassDef)} != classes:
		raise ValueError(f"Missing source classes: {path}")
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


def describe_rows(rows):
	"""Every field the balance chain can read or write, plus the row fingerprint."""
	return [(item.account_no, item.description, repr(item.withdrawal), repr(item.deposit),
		repr(item.balance), item.balance_ok, item.direction_autocorrected,
		item.amount_autocorrected, item.row_hash) for item in rows]


def verify_balance_chain(root, namespace):
	"""Differential check of the migrated statement balance chain against fixed source."""
	namespace.setdefault("dataclass", dataclass)
	namespace.setdefault("hashlib", hashlib)
	definitions(root, "services/recon/bank_recon_types.py", set(), namespace, classes={"StatementRow"})
	definitions(root, "services/recon/bank_table_io.py", {"_is_summary_row"}, namespace,
		assignments={"_SUMMARY_ROW_KW"})
	tolerance_namespace = {}
	definitions(root, "services/recon/bank_recon_utils.py", set(), tolerance_namespace,
		assignments={"AMOUNT_TOL"})
	namespace["AMOUNT_TOL"] = tolerance_namespace["AMOUNT_TOL"]
	if namespace["AMOUNT_TOL"] != statement.AMOUNT_TOL:
		raise AssertionError(("tolerance constant drifted", namespace["AMOUNT_TOL"]))
	definitions(root, "services/recon/bank_stmt_balance.py", {
		"_stmt_bad_ratio", "_correct_direction_from_balance", "_verify_row_balances",
		"_repair_amount_from_balance", "finalize_rows", "_audit_completeness",
	}, namespace, assignments={"_REPAIR_RATIO"})
	if namespace["_REPAIR_RATIO"] != statement.REPAIR_RATIO:
		raise AssertionError(("repair ratio drifted", namespace["_REPAIR_RATIO"]))
	if tuple(namespace["_SUMMARY_ROW_KW"]) != statement.SUMMARY_ROW_KEYWORDS:
		raise AssertionError(("summary keyword list drifted", namespace["_SUMMARY_ROW_KW"]))

	descriptions = ["SWD 65,573.75", "TRF to KKP", "ค่าสินค้า", "Total Deposit", "ยอดยกมา",
		"Total Credit", "Opening Balance", "ค่าบริการ", "CHEQUE", "รวมรายการ", "", "  ", "合计",
		"Grand Total", "B/F"]
	for description in descriptions:
		if namespace["_is_summary_row"](description) != statement.is_summary_row(description):
			raise AssertionError(("summary row decision drifted", description))
	# The deployed dataclass formats the balance inside its fingerprint, so an
	# unread balance column must be assigned after construction in both classes.
	for cls in (namespace["StatementRow"], statement.StatementRow):
		with self_expect(TypeError):
			cls(date(2026, 6, 1), "tx", 0.0, 0.0, None)

	def materialize(cls, specs):
		rows = []
		for spec in specs:
			item = cls(date(2026, 6, 1), spec["description"], spec["withdrawal"], spec["deposit"],
				spec["balance"])
			item.account_no = spec["account_no"]
			if spec["unknown_balance"]:
				item.balance = None
			rows.append(item)
		return rows

	amounts = [0.5, 100.0, 250.5, 1000.0, 15000.0]
	spec_rng = random.Random(20260924)
	counts = {"balance_batches": 0, "summary_rows_filtered": 0, "direction_corrections": 0,
		"amount_repairs": 0, "audit_issues": 0}
	for _ in range(1500):
		opening = spec_rng.choice([0.0, 1000.0, 100000.0])
		running = opening if opening else 1000.0
		specs = []
		for index in range(spec_rng.randint(0, 12)):
			kind = spec_rng.choice(["none", "deposit", "deposit", "withdrawal", "withdrawal"])
			amount = round(spec_rng.choice(amounts) * spec_rng.choice([1, 1, 2]), 2)
			deposit = amount if kind == "deposit" else 0.0
			withdrawal = amount if kind == "withdrawal" else 0.0
			running = round(running + deposit - withdrawal, 2)
			balance = running
			style = spec_rng.random()
			if style < 0.08:
				balance = round(balance + spec_rng.choice([0.01, 0.05, 0.5, 1.5, 25.0]), 2)
			elif style > 0.92:
				balance = 0.0
			if spec_rng.random() < 0.10:
				deposit, withdrawal = withdrawal, deposit
			if spec_rng.random() < 0.10:
				deposit = round(deposit * 1.1, 2) if deposit else deposit
				withdrawal = round(withdrawal * 0.9, 2) if withdrawal else withdrawal
			specs.append({"description": spec_rng.choice(descriptions), "deposit": deposit,
				"withdrawal": withdrawal, "balance": balance,
				"unknown_balance": spec_rng.random() < 0.07, "account_no": f"a{index}"})
		old_ratio = namespace["_stmt_bad_ratio"](materialize(namespace["StatementRow"], specs), opening)
		new_ratio = statement.statement_row_bad_ratio(materialize(statement.StatementRow, specs), opening)
		if repr(old_ratio) != repr(new_ratio):
			raise AssertionError(("bad ratio drifted", batch, old_ratio, new_ratio))
		for source_name, new_function in (
				("_correct_direction_from_balance", statement.correct_direction_from_balance),
				("_verify_row_balances", statement.verify_row_balances),
				("_repair_amount_from_balance", statement.repair_amount_from_balance)):
			old_rows = materialize(namespace["StatementRow"], specs)
			new_rows = materialize(statement.StatementRow, specs)
			namespace[source_name](old_rows, opening)
			new_function(new_rows, opening)
			if describe_rows(old_rows) != describe_rows(new_rows):
				raise AssertionError((source_name, specs))
		old_rows = materialize(namespace["StatementRow"], specs)
		new_rows = materialize(statement.StatementRow, specs)
		old_kept = namespace["finalize_rows"](old_rows, opening)
		new_kept = statement.finalize_rows(new_rows, opening)
		if describe_rows(old_kept) != describe_rows(new_kept):
			raise AssertionError(("finalize_rows drifted", specs))
		counts["summary_rows_filtered"] += len(specs) - len(new_kept)
		counts["direction_corrections"] += sum(1 for item in new_kept if item.direction_autocorrected)
		counts["amount_repairs"] += sum(1 for item in new_kept if item.amount_autocorrected)
		closing = (round(opening + round(sum(item["deposit"] for item in specs), 2)
			- round(sum(item["withdrawal"] for item in specs), 2), 2)
			if spec_rng.random() < 0.5 else round(running + spec_rng.choice([0.0, 1.5, 250.0]), 2))
		printed = None
		if spec_rng.random() < 0.7:
			printed = {"total_credit": round(sum(item["deposit"] for item in specs), 2),
				"total_debit": round(sum(item["withdrawal"] for item in specs), 2),
				"credit_count": sum(1 for item in specs if item["deposit"] > 0),
				"debit_count": sum(1 for item in specs if item["withdrawal"] > 0)}
			if spec_rng.random() < 0.15:  # a balance mis-filed into both totals
				printed["total_credit"] = printed["total_debit"] = closing
			if spec_rng.random() < 0.15:  # a missed or misread row
				printed["credit_count"] += spec_rng.choice([-1, 1])
			if spec_rng.random() < 0.15:
				printed["total_credit"] = round(printed["total_credit"] + spec_rng.choice([1.0, 500.0]), 2)
		old_audit = namespace["_audit_completeness"](materialize(namespace["StatementRow"], specs),
			opening, closing, printed)
		new_audit = statement.audit_completeness(materialize(statement.StatementRow, specs),
			opening, closing, printed)
		if old_audit != new_audit:
			raise AssertionError(("audit_completeness drifted", specs, closing, printed))
		counts["audit_issues"] += len(new_audit["issues"])
		counts["balance_batches"] += 1
	for name, minimum, message in (("summary_rows_filtered", 100, "footer filter"),
			("direction_corrections", 40, "direction correction"),
			("amount_repairs", 10, "amount repair"), ("audit_issues", 200, "audit issue")):
		if counts[name] < minimum:
			raise AssertionError((f"{message} barely exercised", counts[name]))
	return counts


def describe_dataclass(value):
	"""Every declared field in declaration order, so a new column cannot hide."""
	return tuple((field.name, getattr(value, field.name)) for field in dataclasses.fields(value))


def verify_bank_matching(root, namespace):
	"""Differential check of the migrated matching core and candidate scoring."""
	definitions(root, "services/recon/bank_recon_types.py", set(), namespace,
		classes={"StatementRow", "GlRow", "BankReconRow", "BankReconSummary"})
	definitions(root, "services/recon/bank_recon_utils.py", {"_day_diff", "_amount_matches"},
		namespace, assignments={"AMOUNT_TOL", "DATE_TOL_DAYS"})
	definitions(root, "services/recon/bank_recon_reconcile.py", {"reconcile"}, namespace)
	definitions(root, "services/recon/bank_recon_scoring.py", {
		"score_amount", "score_date", "score_direction", "score_keyword", "match_one_tx",
	}, namespace, assignments={"_W_AMOUNT", "_W_DATE", "_W_DIRECTION", "_W_KEYWORD",
		"THRESH_AUTO", "THRESH_SUGGEST", "AMOUNT_TOL_EQUAL", "AMOUNT_TOL_SMALL",
		"AMOUNT_TOL_MEDIUM"})
	constants = {
		"DATE_TOL_DAYS": (namespace["DATE_TOL_DAYS"], matching.DATE_TOL_DAYS),
		"AMOUNT_TOL": (namespace["AMOUNT_TOL"], statement.AMOUNT_TOL),
		"_W_AMOUNT": (namespace["_W_AMOUNT"], scoring.WEIGHT_AMOUNT),
		"_W_DATE": (namespace["_W_DATE"], scoring.WEIGHT_DATE),
		"_W_DIRECTION": (namespace["_W_DIRECTION"], scoring.WEIGHT_DIRECTION),
		"_W_KEYWORD": (namespace["_W_KEYWORD"], scoring.WEIGHT_KEYWORD),
		"THRESH_AUTO": (namespace["THRESH_AUTO"], scoring.THRESHOLD_AUTO),
		"THRESH_SUGGEST": (namespace["THRESH_SUGGEST"], scoring.THRESHOLD_SUGGEST),
		"AMOUNT_TOL_EQUAL": (namespace["AMOUNT_TOL_EQUAL"], scoring.AMOUNT_TOL_EQUAL),
		"AMOUNT_TOL_SMALL": (namespace["AMOUNT_TOL_SMALL"], scoring.AMOUNT_TOL_SMALL),
		"AMOUNT_TOL_MEDIUM": (namespace["AMOUNT_TOL_MEDIUM"], scoring.AMOUNT_TOL_MEDIUM),
	}
	for name, (source_value, new_value) in constants.items():
		if source_value != new_value:
			raise AssertionError((f"{name} drifted", source_value, new_value))

	dates = [None, date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 5), date(2026, 6, 9),
		date(2026, 7, 1)]
	amounts = [0.0, 50.0, 100.0, 100.01, 250.5, 1000.0]
	local_rng = random.Random(20260925)
	counts = {"matching_batches": 0, "layer1": 0, "layer2": 0, "layer3": 0,
		"one_sided": 0, "scoring_batches": 0, "scored_candidates": 0}
	for _ in range(1200):
		stmt_specs = []
		for index in range(local_rng.randint(0, 6)):
			amount = local_rng.choice(amounts)
			withdrawal = amount if local_rng.random() < 0.5 else 0.0
			deposit = 0.0 if withdrawal else amount
			stmt_specs.append({"withdrawal": withdrawal, "deposit": deposit,
				"when": local_rng.choice(dates), "description": f"s{index}",
				"confidence": local_rng.choice(["high", "medium", "low"]),
				"balance_ok": local_rng.choice([True, False, None]),
				"amount_autocorrected": local_rng.random() < 0.2,
				"direction_autocorrected": local_rng.random() < 0.2,
				"source_file": f"stmt{index}.pdf"})
		gl_specs = []
		for index in range(local_rng.randint(0, 6)):
			amount = local_rng.choice(amounts)
			debit = amount if local_rng.random() < 0.5 else 0.0
			credit = 0.0 if debit else amount
			gl_specs.append({"debit": debit, "credit": credit, "when": local_rng.choice(dates),
				"doc_no": f"GL{index}", "description": f"g{index}", "source_file": f"gl{index}.xlsx"})
		openings = {}
		for name in ("stmt_opening", "gl_opening", "stmt_closing", "gl_closing"):
			openings[name] = local_rng.choice([0.0, 1000.0, 50000.5])

		def build_statement_rows(cls):
			rows = []
			for index, spec in enumerate(stmt_specs):
				item = cls(spec["when"], spec["description"], spec["withdrawal"], spec["deposit"],
					round(spec["deposit"] - spec["withdrawal"], 2))
				item.confidence = spec["confidence"]
				item.balance_ok = spec["balance_ok"]
				item.amount_autocorrected = spec["amount_autocorrected"]
				item.direction_autocorrected = spec["direction_autocorrected"]
				item.source_file = spec["source_file"]
				rows.append(item)
			return rows

		def build_gl_rows(cls):
			return [cls(spec["when"], spec["doc_no"], "1000", spec["description"], spec["debit"],
				spec["credit"], spec["source_file"], round(spec["debit"] - spec["credit"], 2))
				for spec in gl_specs]

		old_rows, old_summary = namespace["reconcile"](build_statement_rows(namespace["StatementRow"]),
			build_gl_rows(namespace["GlRow"]), **openings, bank_code="BBL", gl_account_code="1000")
		new_rows, new_summary = matching.reconcile(build_statement_rows(statement.StatementRow),
			build_gl_rows(matching.GlRow), **openings, bank_code="BBL", gl_account_code="1000")
		if [describe_dataclass(item) for item in old_rows] != [describe_dataclass(item)
				for item in new_rows]:
			raise AssertionError(("reconcile rows drifted", stmt_specs, gl_specs))
		if describe_dataclass(old_summary) != describe_dataclass(new_summary):
			raise AssertionError(("reconcile summary drifted", stmt_specs, gl_specs, openings))
		counts["matching_batches"] += 1
		for item in new_rows:
			if item.match_layer == 1:
				counts["layer1"] += 1
			elif item.match_layer == 2:
				counts["layer2"] += 1
			elif item.match_layer == 3:
				counts["layer3"] += 1
			else:
				counts["one_sided"] += 1

		bank_tx = {"amount": local_rng.choice(amounts), "tx_date": local_rng.choice(["2026-06-01", "bad", None]),
			"direction": local_rng.choice(["IN", "OUT", "", None]),
			"description": local_rng.choice(["payment ACME INV-77", "", "โอนให้ บริษัท ไทย"])}
		candidates = [{"id": f"c{index}", "amount_total": local_rng.choice(amounts),
			"total": local_rng.choice(amounts),
			"invoice_date": local_rng.choice(["2026-06-01", "2026-06-02", "2026-06-09", None, "bad"]),
			"category_tag": local_rng.choice(["sale", "purchase", "ค่าใช้จ่าย", None, "费用"]),
			"vendor": local_rng.choice(["ACME Trading", "AC", "บริษัท ไทย", None]),
			"invoice_no": local_rng.choice(["INV-77", "inv-1", None])}
			for index in range(local_rng.randint(0, 8))]
		old_ranked = namespace["match_one_tx"](bank_tx, candidates)
		new_ranked = scoring.match_one_tx(bank_tx, candidates)
		if json.dumps(old_ranked, sort_keys=True) != json.dumps(new_ranked, sort_keys=True):
			raise AssertionError(("match_one_tx drifted", bank_tx, candidates, old_ranked, new_ranked))
		counts["scoring_batches"] += 1
		counts["scored_candidates"] += len(new_ranked)
		for name, arguments in (
				("score_amount", (bank_tx["amount"], candidates[0]["amount_total"] if candidates else 0)),
				("score_date", (bank_tx["tx_date"], candidates[0]["invoice_date"]) if candidates else (None, None)),
				("score_direction", (bank_tx["direction"] or "", candidates[0] if candidates else {})),
				("score_keyword", (bank_tx["description"] or "", candidates[0] if candidates else {}))):
			old_value = namespace[name](*arguments)
			new_value = getattr(scoring, name)(*arguments)
			if repr(old_value) != repr(new_value):
				raise AssertionError((f"{name} drifted", arguments, old_value, new_value))
	for name, minimum, message in (("layer1", 100, "layer-one match"), ("layer2", 40, "layer-two match"),
			("layer3", 10, "layer-three match"), ("one_sided", 300, "one-sided finding"),
			("scored_candidates", 300, "scored candidate")):
		if counts[name] < minimum:
			raise AssertionError((f"{message} barely exercised", counts[name]))
	return counts


class self_expect:
	"""Minimal context manager mirroring assertRaises for this script."""

	def __init__(self, error):
		self.error = error

	def __enter__(self):
		return self

	def __exit__(self, kind, value, traceback):
		if kind is None:
			raise AssertionError(f"{self.error.__name__} was not raised")
		if not issubclass(kind, self.error):
			return False
		self.value = value
		return True


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
	balance = verify_balance_chain(root, namespace)
	bank = verify_bank_matching(root, namespace)
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
		"source_pass2_pairs": pass_two_cases, **balance, **bank}))
	print("PASS: 4020 source quantity cases; 1000 source first-pass batches; "
		f"{amount_cases} amount contract cases; {field_cases} compare_field cases; "
		f"{all_field_cases} compare_all_fields cases; {match_cases} full matcher batches "
		f"({pass_two_cases} second-pass pairs); {balance['balance_batches']} balance-chain batches "
		f"({balance['summary_rows_filtered']} footer rows, {balance['direction_corrections']} direction "
		f"corrections, {balance['amount_repairs']} amount repairs, {balance['audit_issues']} audit "
		f"issues); {bank['matching_batches']} matching batches "
		f"({bank['layer1']} L1, {bank['layer2']} L2, {bank['layer3']} L3, {bank['one_sided']} one-sided) "
		f"and {bank['scoring_batches']} scoring batches ({bank['scored_candidates']} candidates); "
		f"8 amount investigations")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--source", type=Path, required=True)
	verify(parser.parse_args().source)
