"""Differential check of the migrated income-reconciliation workbook.

CLI: --source <fixed-source-root>. The pinned writer is executed from its fixed
source text with only its sibling-module import removed (the two result types are
injected from the pinned types module), so no legacy application, database, file,
network or model code runs. Both writers then render the same rows and summary and
the two workbooks are compared cell by cell, style included.
"""
import argparse
import ast
from copy import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import random
import sys

import openpyxl

from pearnly_cowork.reconciliation.export import income_workbook
from pearnly_cowork.reconciliation.income_vat import GlVatSummary, ReconRow

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/recon/gl_vat_types.py":
        "ee24b7dd0a7a24e67ac338f7c7deae73c71c36aca5b731ddc18b6e6af27a300d",
    "services/recon/gl_vat_excel.py":
        "8527af0e5ec1cb7115a1cc8af284903f8ebb156bdf72bfc6700f577ef799fd20",
}
ALLOWED_IMPORTS = {"io", "typing", "openpyxl", "dataclasses"}


class RemoveSiblingImports(ast.NodeTransformer):
	"""Drop the legacy sibling imports; every other import must be stdlib or openpyxl."""

	def visit_ImportFrom(self, node):
		if (node.module or "").startswith("services."):
			return None
		assert (node.module or "").split(".")[0] in ALLOWED_IMPORTS, node.module
		return node

	def visit_Import(self, node):
		for alias in node.names:
			assert alias.name.split(".")[0] in ALLOWED_IMPORTS, alias.name
		return node


def load_legacy(root=None, source_text=None):
	trees = {}
	for path, digest in HASHES.items():
		data = (source_text[path].encode("utf-8") if source_text is not None
			else (Path(root) / path).read_bytes())
		assert hashlib.sha256(data).hexdigest() == digest, f"Source hash mismatch: {path}"
		trees[path] = RemoveSiblingImports().visit(ast.parse(data.decode("utf-8")))

	def execute(path, namespace):
		exec(compile(trees[path], path, "exec"), namespace)
		return namespace

	types = execute("services/recon/gl_vat_types.py", {"__name__": "fixed_gl_vat_types"})
	excel = execute("services/recon/gl_vat_excel.py", {"__name__": "fixed_gl_vat_excel",
		"ReconRow": types["ReconRow"], "GlVatSummary": types["GlVatSummary"]})
	return excel["export_gl_vat_excel"], excel["_I18N"], excel["_t"]


def compare(old_bytes, new_bytes):
	old = openpyxl.load_workbook(BytesIO(old_bytes), data_only=False)
	new = openpyxl.load_workbook(BytesIO(new_bytes), data_only=False)
	cells = 0
	try:
		assert old.sheetnames == new.sheetnames, (old.sheetnames, new.sheetnames)
		for left, right in zip(old, new, strict=True):
			for attr in ("max_row", "max_column", "freeze_panes", "sheet_state"):
				assert getattr(left, attr) == getattr(right, attr), (left.title, attr)
			assert str(left.merged_cells) == str(right.merged_cells)
			assert {k: (v.width, v.hidden) for k, v in left.column_dimensions.items()} == {
				k: (v.width, v.hidden) for k, v in right.column_dimensions.items()}, left.title
			assert {k: (v.height, v.hidden) for k, v in left.row_dimensions.items()} == {
				k: (v.height, v.hidden) for k, v in right.row_dimensions.items()}, left.title
			for arow, brow in zip(left, right, strict=True):
				for a, b in zip(arow, brow, strict=True):
					assert (a.value, a.data_type) == (b.value, b.data_type), (
						left.title, a.coordinate, a.value, b.value)
					for attr in ("font", "fill", "border", "alignment", "number_format",
							"protection"):
						assert copy(getattr(a, attr)) == copy(getattr(b, attr)), (
							left.title, a.coordinate, attr)
					cells += 1
	finally:
		old.close()
		new.close()
	return cells


def verify_label_table(old_labels, old_t):
	"""Every label and helper answer must match the deployed table."""
	assert old_labels == income_workbook._I18N
	cases = 0
	keys = list(income_workbook._I18N["th"]) + ["missing_key", ""]
	for key in keys:
		for lang in ("th", "en", "zh", "ja", "bogus", ""):
			assert old_t(lang, key) == income_workbook._t(lang, key), (key, lang)
			cases += 1
	return cases


def row(spec):
	return ReconRow(doc_no=spec["doc_no"], date=spec["date"], customer_name=spec["customer_name"],
		vat_amount=spec["vat_amount"], gl_amount=spec["gl_amount"], diff=spec["diff"],
		account_codes=spec["account_codes"])


def summary(spec):
	return GlVatSummary(gl_total=spec["gl_total"], gl_only_credit=spec["gl_only_credit"],
		gl_only_debit=spec["gl_only_debit"], vat_only_positive=spec["vat_only_positive"],
		vat_only_negative=spec["vat_only_negative"], vat_total=spec["vat_total"],
		gl_only_credit_items=spec["gl_only_credit_items"],
		gl_only_debit_items=spec["gl_only_debit_items"],
		vat_only_positive_items=spec["vat_only_positive_items"],
		vat_only_negative_items=spec["vat_only_negative_items"])


def item(rng, names):
	return {"doc_no": rng.choice(["GL-1", "INV-9", "", "x" * 30]),
		"date": rng.choice(["01/06/2569", "2026-06-01", ""]),
		"name": rng.choice(names), "amount": round(rng.choice([0.0, 12.5, -250.0, 12345.67]), 2)}


def fixtures():
	rng = random.Random(20261001)
	names = ["ACME จำกัด", "中文客户名称", "=SUM(A1)", "", "x" * 80, "BETA"]
	doc_nos = ["GL-1", "GL/1", "", "INV-9", "x" * 40, "4110-01"]
	for _ in range(30):
		rows = []
		for _ in range(rng.randrange(0, 9)):
			matched = rng.random() < 0.7
			rows.append({
				"doc_no": rng.choice(doc_nos),
				"date": rng.choice(["01/06/2569", "", "2026-06-01"]),
				"customer_name": rng.choice(names),
				"vat_amount": round(rng.choice([0.0, 1070.0, -107.0, 12345.67]), 2),
				"gl_amount": round(rng.choice([0.0, 1070.0, -107.0]), 2) if matched else None,
				"diff": round(rng.choice([0.0, 30.0, -0.01]), 2) if matched else None,
				"account_codes": rng.choice(["4110-01", "4110-01,4110-02", ""]),
			})
		yield {"rows": rows, "summary": {
			"gl_total": round(rng.choice([0.0, 1493.0, -500.25]), 2),
			"gl_only_credit": round(rng.choice([0.0, 500.0]), 2),
			"gl_only_debit": round(rng.choice([0.0, 75.0]), 2),
			"vat_only_positive": round(rng.choice([0.0, 250.0]), 2),
			"vat_only_negative": round(rng.choice([0.0, -107.0]), 2),
			"vat_total": round(rng.choice([0.0, 1243.0]), 2),
			"gl_only_credit_items": [item(rng, names) for _ in range(rng.randrange(0, 3))],
			"gl_only_debit_items": [item(rng, names) for _ in range(rng.randrange(0, 2))],
			"vat_only_positive_items": [item(rng, names) for _ in range(rng.randrange(0, 3))],
			"vat_only_negative_items": [item(rng, names) for _ in range(rng.randrange(0, 2))],
		}}
	# Fixed shapes: a full ladder with one item per section, an empty task, every row
	# state and a very long name.
	yield {"rows": [
		{"doc_no": "GL-1", "date": "01/06/2569", "customer_name": "ACME", "vat_amount": 1100.0,
			"gl_amount": 1100.0, "diff": 0.0, "account_codes": "4110-01"},
		{"doc_no": "GL-2", "date": "02/06/2569", "customer_name": "ACME", "vat_amount": -107.0,
			"gl_amount": -107.0, "diff": 0.0, "account_codes": "4110-01"},
		{"doc_no": "INV-9", "date": "", "customer_name": "", "vat_amount": 250.0,
			"gl_amount": None, "diff": None, "account_codes": ""},
		{"doc_no": "GL-3", "date": "03/06/2569", "customer_name": "x" * 80, "vat_amount": 500.0,
			"gl_amount": 470.0, "diff": 30.0, "account_codes": "4110-01,4110-02"},
	], "summary": {
		"gl_total": 1493.0, "gl_only_credit": 500.0, "gl_only_debit": 0.0,
		"vat_only_positive": 250.0, "vat_only_negative": 0.0, "vat_total": 1243.0,
		"gl_only_credit_items": [{"doc_no": "GL-3", "date": "03/06/2569", "name": "no vat row",
			"amount": 500.0}],
		"gl_only_debit_items": [{"doc_no": "GL-4", "date": "", "name": "", "amount": -75.0}],
		"vat_only_positive_items": [{"doc_no": "INV-9", "date": "05/06/2569", "name": "BETA",
			"amount": 250.0}],
		"vat_only_negative_items": [{"doc_no": "CN-1", "date": "06/06/2569", "name": "BETA",
			"amount": -107.0}],
	}}
	yield {"rows": [], "summary": {"gl_total": 0.0, "gl_only_credit": 0.0, "gl_only_debit": 0.0,
		"vat_only_positive": 0.0, "vat_only_negative": 0.0, "vat_total": 0.0,
		"gl_only_credit_items": [], "gl_only_debit_items": [], "vat_only_positive_items": [],
		"vat_only_negative_items": []}}


def run(root=None, source_text=None, rounds=30):
	legacy_export, legacy_labels, legacy_t = load_legacy(root, source_text)
	cells = 0
	batch_count = 0
	label_cases = verify_label_table(legacy_labels, legacy_t)
	for batch in fixtures():
		rows = [row(spec) for spec in batch["rows"]]
		value = summary(batch["summary"])
		for lang in ("th", "en", "zh", "ja", "bogus", ""):
			old = legacy_export(rows, value, lang=lang)
			new = income_workbook.export_gl_vat_excel(rows, value, lang=lang)
			cells += compare(old, new)
			batch_count += 1
	print({"source": SOURCE_SHA, "workbooks": batch_count, "cells": cells,
		"label_cases": label_cases, "differences": 0})
	if batch_count < rounds * 6 or cells < 10000:
		raise SystemExit("fixture coverage below the floor")
	raise SystemExit(0)


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument("--source", type=Path)
	group.add_argument("--source-stdin", action="store_true")
	arguments = parser.parse_args()
	if arguments.source_stdin:
		run(None, json.load(sys.stdin))
	else:
		run(arguments.source)
