"""Differential check of the migrated income (GL) parsing conversions.

CLI: --source <fixed-source-root>. Only selected pure definitions of the fixed
source are executed. The two external readers the pinned functions call are
replaced by in-memory doubles, because the migration moves their results, not the
reading: a workbook double yields the cell rows, and a PDF double yields the page
text and tables. No legacy application, database, file, network, OCR or model code
runs.
"""
import argparse
import ast
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from pathlib import Path
import random
import re
import sys
import types
from typing import Any, Dict, List, Optional

from pearnly_cowork.contracts.errors import short_error
from pearnly_cowork.reconciliation import income_parse as parse

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/recon/field_comparator.py":
        "109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037",
    "services/recon/gl_vat_parse_common.py":
        "ca59a5d1b313806e4358ad5a29b8abfa8b906b1dcc1d2f26128aee932eac5403",
    "services/recon/gl_vat_parse_excel.py":
        "1b81310ee241186e6288db01376ea90217d2e1f6771167c7aa9d457baa65b115",
    "services/recon/gl_vat_parse_pdf.py":
        "a97a188b1d0bedd3ab0862091baa863c4a49d15ae5a30f72eb0bbde41f0a049e",
    "services/recon/gl_vat_types.py":
        "ee24b7dd0a7a24e67ac338f7c7deae73c71c36aca5b731ddc18b6e6af27a300d",
}


def extract(root, path, names, namespace, assignments=(), classes=()):
    """Execute only the named top-level definitions of a hash-pinned source file."""
    data = (root / path).read_bytes()
    if hashlib.sha256(data).hexdigest() != HASHES[path]:
        raise AssertionError(f"Source does not match {SOURCE_SHA}: {path}")
    tree = ast.parse(data)
    assignments, classes = set(assignments), set(classes)
    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or (isinstance(node, ast.ClassDef) and node.name in classes)
        or (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id in assignments)]
    if {node.name for node in nodes if isinstance(node, ast.FunctionDef)} != set(names):
        raise AssertionError(f"Missing source definitions: {path}")
    if {node.name for node in nodes if isinstance(node, ast.ClassDef)} != classes:
        raise AssertionError(f"Missing source classes: {path}")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root / path), "exec"), namespace)
    return namespace


class ReaderDouble:
	"""Stands in for the workbook and PDF readers the pinned functions call.

	The migration moves what those readers produced, not the reading itself, so the
	differential hands the pinned code the same cell rows, page text and tables it
	would have received from a real file.
	"""

	def __init__(self):
		self.sheets = []
		self.pages = []

	def install(self):
		reader = self

		class _Worksheet:
			def __init__(self, rows):
				self._rows = rows

			def iter_rows(self, values_only=True):
				return iter(self._rows)

		class _Workbook:
			def __init__(self):
				self.worksheets = [_Worksheet(rows) for rows in reader.sheets]

		class _Page:
			def __init__(self, spec):
				self._spec = spec

			def extract_text(self):
				return self._spec["text"]

			def extract_tables(self):
				return self._spec["tables"]

		class _Document:
			def __init__(self):
				self.pages = [_Page(spec) for spec in reader.pages]

			def __enter__(self):
				return self

			def __exit__(self, *arguments):
				return False

		openpyxl = types.ModuleType("openpyxl")
		openpyxl.load_workbook = lambda stream, data_only=True: _Workbook()
		pdfplumber = types.ModuleType("pdfplumber")
		pdfplumber.open = lambda stream: _Document()
		sys.modules["openpyxl"] = openpyxl
		sys.modules["pdfplumber"] = pdfplumber


def load_legacy(root):
	"""Load the pinned pure definitions of the deployed GL parsing path."""
	common = {"re": re, "List": List, "Dict": Dict, "Optional": Optional}
	names = {"_to_float", "_is_revenue_acct", "_hit", "_extract_account_code", "_is_debit_line",
		"_is_skip_row", "normalize_doc_no", "_map_gl_columns", "_row_has_amount"}
	constants = {"_GL_DATE_H", "_GL_DOC_H", "_GL_DESC_H", "_GL_DEBIT_H", "_GL_CRED_H",
		"_GL_ACCT_H", "_ACCT_RE", "_SKIP_ROWS", "_DEBIT_LINE_KW"}
	normalizers = {"re": re, "unicodedata": __import__("unicodedata"), "Optional": Optional}
	extract(root, "services/recon/field_comparator.py",
		{"normalize_str", "normalize_invoice_no"}, normalizers)
	common["normalize_invoice_no"] = normalizers["normalize_invoice_no"]
	extract(root, "services/recon/gl_vat_parse_common.py", names, common, assignments=constants)
	types_namespace = {"dataclass": dataclass, "List": List, "Dict": Dict, "Any": Any,
		"Optional": Optional}
	extract(root, "services/recon/gl_vat_types.py", set(), types_namespace,
		classes={"GlRow", "ReconRow", "GlVatSummary"})
	shared = {**common, "GlRow": types_namespace["GlRow"], "logger": _logger(), "io": __import__("io"),
		"date": __import__("datetime").date, "datetime": __import__("datetime").datetime,
		"Any": Any}
	excel = dict(shared)
	# The deployed ``_short_err`` is migrated now, so the pinned source calls the
	# same helper the native parser does; the two are compared verbatim against
	# the fixed source in tests/test_cowork_ocr_error_format_source_parity.py.
	excel.update({"List": List, "Dict": Dict, "Optional": Optional, "_short_err": short_error})
	extract(root, "services/recon/gl_vat_parse_excel.py",
		{"parse_gl_excel", "_gl_rows_from_pipeline_legacy"}, excel)
	pdf = dict(shared)
	pdf.update({"List": List, "Dict": Dict, "Optional": Optional})
	extract(root, "services/recon/gl_vat_parse_pdf.py", {"_parse_gl_text_lines", "parse_gl_pdf"},
		pdf, assignments={"_DATE_RE", "_TAIL_NUMS", "_TAIL_NUMS_2", "_DOCNO_RE"})
	return excel, pdf


def _logger():
	module = types.ModuleType("legacy_logger")
	module.info = lambda *arguments, **keywords: None
	module.warning = lambda *arguments, **keywords: None
	module.error = lambda *arguments, **keywords: None
	return module


def describe(row):
	return (row.doc_no, row.norm_doc_no, row.date, row.account_code, row.description, row.debit,
		row.credit)


def outcome(function, *arguments):
	"""Compare success payloads and exception identity, never a partial outcome."""
	try:
		return "ok", function(*arguments)
	except Exception as error:
		return "error", type(error).__name__, str(error)


def rows_of(value):
	return [describe(row) for row in value["rows"]]


def page_lines(pages):
	"""The lines a caller reads off the pages, in the order the reader prints them."""
	return [line for page in pages for line in str(page.get("text") or "").split("\n")]


def fixtures(rng):
	# A date cell can be printed text or a real calendar cell, which the Excel path
	# rewrites into an ISO string and shifts from a Buddhist year.
	date_cells = ["01/06/2569", "2026-06-01", "", "31/12/2569", None,
		datetime(2569, 6, 1), datetime(2025, 6, 1, 13, 30), date(2568, 12, 31),
		date(2024, 2, 29), datetime(2569, 12, 31)]
	doc_nos = ["IV001", "IV-002", "OHC06/2025", "SBT10/2025", "", "GL/1", "IV003"]
	texts = ["ขายสินค้า", "sale", "ค่าบริการ", "", "ลดหนี้"]
	amounts = ["1,070.00", "0.00", "500", "(250.00)", "1,500.00", "", "abc", "-"]
	numeric_amounts = ["1,070.00", "0.00", "500", "(250.00)", "1,500.00", "107.00", "2,070.00"]
	accounts = ["4110-01", "4110", "411000", "5010-01", "", "41100-001"]
	prefixes = ["4", "41", "9"]

	def numeric():
		return rng.choice(numeric_amounts) if rng.random() < 0.85 else rng.choice(amounts)
	header_cells = [
		["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
		["Date", "Voucher", "Description", "Debit", "Credit", "Account Code"],
		["วันที่", "เลขที่เอกสาร", "รายละเอียด", "เดบิต", "เครดิต"],
		["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัส"],
		["วันที่", "ใบสำคัญ", "คำอธิบาย", "debit only"],
	]
	skip_cells = [["ยอดยกมา", "", "", "", "", ""], ["", "subtotal", "", "", "", ""],
		["รวมทั้งสิ้น", "", "", "", "", ""], ["", "", "This row carries an account 4110-01", "", "", ""]]

	def data_row():
		return [rng.choice(date_cells), rng.choice(doc_nos), rng.choice(texts),
			rng.choice(amounts), rng.choice(amounts), rng.choice(accounts)]

	for _ in range(300):
		sheet_count = rng.randint(1, 2)
		sheets = []
		for _ in range(sheet_count):
			rows = []
			if rng.random() < 0.85:
				rows.append(list(rng.choice(header_cells)))
			for _ in range(rng.randint(0, 6)):
				roll = rng.random()
				if roll < 0.2:
					rows.append([" ".join([rng.choice(accounts), rng.choice(texts)]), "", "", "", "", ""])
				elif roll < 0.3:
					rows.append(list(rng.choice(skip_cells)))
				elif roll < 0.35:
					rows.append(["", "", "", "", "", ""])
				else:
					rows.append(data_row())
			sheets.append(rows)
		pages = []
		for _ in range(rng.randint(0, 2)):
			tables = []
			if rng.random() < 0.8:
				tables.append([list(rng.choice(header_cells))]
					+ [data_row() for _ in range(rng.randint(0, 4))])
			pages.append({"text": "\n".join([" ".join([rng.choice(accounts), rng.choice(texts)])]
				+ [rng.choice(texts)]), "tables": tables})
		lines = []
		# A printed ledger page opens with its section heading: without one every
		# later line is skipped, which is the deployed rule this path must keep.
		if rng.random() < 0.8:
			opening = "(%s)" % rng.choice(numeric_amounts) if rng.random() < 0.5 else ""
			lines.append(" ".join([rng.choice(accounts), rng.choice(texts), opening]).strip())
		for _ in range(rng.randint(0, 8)):
			roll = rng.random()
			date_text = str(rng.choice(date_cells) or "")
			if roll < 0.3:
				lines.append(" ".join([date_text, rng.choice(doc_nos), rng.choice(texts), numeric(),
					numeric(), numeric()]))
			elif roll < 0.6:
				lines.append(" ".join([date_text, rng.choice(doc_nos), rng.choice(texts), numeric(),
					numeric()]))
			elif roll < 0.7:
				lines.append(" ".join([date_text, rng.choice(doc_nos),
					rng.choice(["ลดหนี้", "รับคืน", "คืนสินค้า", "ส่งคืน", "ขายสินค้า"]), numeric(),
					numeric()]))
			elif roll < 0.8:
				lines.append(" ".join([date_text, rng.choice(doc_nos), rng.choice(texts)]))
			elif roll < 0.9:
				lines.append(" ".join([rng.choice(accounts), rng.choice(texts), numeric()]))
			else:
				lines.append(rng.choice(["รวมประจำเดือน", "ยอดยกมา", "", "รวมทั้งสิ้น", "subtotal",
					"ขายสินค้า " + numeric()]))
		decoded = {"pages": [{"document": {"entries": [
			{"voucher_no": rng.choice(doc_nos), "account_code": rng.choice(accounts),
				"description": rng.choice(texts), "debit": rng.choice(amounts),
				"credit": rng.choice(amounts), "transaction_date": rng.choice(date_cells)}
			for _ in range(rng.randint(0, 4))]}}], "_needs_review": rng.random() < 0.5}
		yield {"sheets": sheets, "pages": pages, "lines": lines, "decoded": decoded,
			"revenue_prefix": rng.choice(prefixes)}

	# Fixed shapes: an account column, a section heading, a framed table, the two
	# printed tails with a credit note, and the pipeline entries.
	yield {"sheets": [[
		["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
		["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00", "4110-01"],
		["02/06/2569", "IV002", "ต้นทุน", "500.00", "0.00", "5010-01"],
		["03/06/2569", "", "no voucher", "0.00", "500.00", "4110-01"],
		["04/06/2569", "IV004", "no movement", "0.00", "0.00", "4110-01"],
		["", "", "", "", "", ""],
	]], "pages": [], "lines": [], "decoded": {}, "revenue_prefix": "4"}
	yield {"sheets": [[
		["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต"],
		["4110-01", "", "", "", ""],
		["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00"],
		["4110-02", "5,000.00", "", "", ""],
		["02/06/2569", "IV002", "ขายสินค้า 2", "", "250.00"],
	]], "pages": [], "lines": [], "decoded": {}, "revenue_prefix": "4"}
	yield {"sheets": [], "pages": [{"text": "4110-01 รายได้จากการขาย", "tables": [
		["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต"],
		["01/06/2569", "IV001", "ขายสินค้า", "0.00", "1,070.00"],
		["ยอดยกมา", "", "", "", ""],
		["02/06/2569", "IV002", "ขายสินค้า 2", "0.00", "500.00"],
		["4110-02 รายได้อื่น", "", "", "", ""],
		["03/06/2569", "IV003", "ขายสินค้า 3", "0.00", "100.00"],
	]}], "lines": [], "decoded": {}, "revenue_prefix": "4"}
	yield {"sheets": [], "pages": [],
		"lines": [
			"4110-01 รายได้จากการขาย (1,000.00)",
			"01/06/2569 IV001 ขายสินค้า 0.00 1,070.00 2,070.00",
			"02/06/2569 IV002 ขายสินค้า 500.00 2,570.00",
			"IV003 ขายสินค้า 500.00 2,070.00",
			"03/06/2569 CN001 ลดหนี้ 107.00 1,963.00",
			"รวมประจำเดือน 0.00 5,000.00",
			"04/06/2569 IV004 ขายสินค้า 200.00 2,163.00",
		], "decoded": {}, "revenue_prefix": "4"}
	yield {"sheets": [], "pages": [], "lines": [],
		"decoded": {"pages": [{"document": {"entries": [
			{"voucher_no": "IV001", "account_code": "4110-01", "description": "ขายสินค้า",
				"debit": 0, "credit": 1070.0, "transaction_date": "2026-06-01"},
			{"voucher_no": "GL-2", "account_code": "5010-01", "description": "cost",
				"debit": 500.0, "credit": 0, "transaction_date": "2026-06-02"},
			{"voucher_no": "", "account_code": "4110-01", "description": "no voucher",
				"debit": 0, "credit": 250.0, "transaction_date": ""},
			{"voucher_no": "IV004", "account_code": "4110-01", "description": "no movement",
				"debit": 0, "credit": 0, "transaction_date": "2026-06-04"},
		]}}], "_needs_review": True}, "revenue_prefix": "4"}


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--source", required=True)
	parser.add_argument("--rounds", type=int, default=300)
	arguments = parser.parse_args()
	root = Path(arguments.source)
	readers = ReaderDouble()
	readers.install()
	excel, pdf = load_legacy(root)
	rng = random.Random(20260921)
	differences = []
	counts = {"batches": 0, "excel_rows": 0, "table_rows": 0, "text_rows": 0, "pipeline_rows": 0}
	branches = {"excel_date_cells": 0, "text_credit": 0, "text_debit": 0, "text_tail_three": 0}
	helper_cases = {"to_float": 0, "extract_account_code": 0, "is_skip_row": 0,
		"map_gl_columns": 0, "row_has_amount": 0, "is_debit_line": 0}
	helper_values = [None, "", "-", "–", "0.00", "1,070.00", "(250.00)", " 12 ", "1 234.50",
		"abc", "1e5", "1.005", "1,234,567.89", "\xa0", True, 0, 12.345, -7]
	for value in helper_values:
		helper_cases["to_float"] += 1
		if outcome(excel["_to_float"], value) != outcome(parse.to_float, value):
			differences.append(("to_float", value))
	helper_texts = ["", "4110", "4110-01", "41100-001", "411000", "419.57", "5010-01", "411",
		"12345678", "ยอดยกมา", "IV001", "รวมทั้งสิ้น", "ลดหนี้", "4110-01 รายได้"]
	helper_headers = [["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
		["Date", "Voucher", "Description", "Debit", "Credit", "Account Code"],
		["วันที่", "เลขที่เอกสาร", "รายละเอียด", "เดบิต", "เครดิต"], ["เดบิต", "เครดิต"],
		["", "", ""], ["description", "detail", "account code"]]
	for text in helper_texts:
		helper_cases["extract_account_code"] += 1
		if outcome(excel["_extract_account_code"], text) != outcome(parse.extract_account_code, text):
			differences.append(("extract_account_code", text))
		helper_cases["is_debit_line"] += 1
		if outcome(excel["_is_debit_line"], text) != outcome(parse.is_debit_line, text):
			differences.append(("is_debit_line", text))
	for cells in ([text] for text in helper_texts):
		helper_cases["is_skip_row"] += 1
		if outcome(excel["_is_skip_row"], cells) != outcome(parse.is_skip_row, cells):
			differences.append(("is_skip_row", cells))
	for header in helper_headers:
		helper_cases["map_gl_columns"] += 1
		if outcome(excel["_map_gl_columns"], header) != outcome(parse.map_gl_columns, header):
			differences.append(("map_gl_columns", header))
		for col_map in ({}, excel["_map_gl_columns"](header), {"debit": 3, "credit": 4},
				{"debit": 9}, {"credit": 0}):
			helper_cases["row_has_amount"] += 1
			resolved = {key: value for key, value in col_map.items() if isinstance(value, int)}
			if (outcome(excel["_row_has_amount"], header, resolved)
					!= outcome(parse.row_has_amount, header, resolved)):
				differences.append(("row_has_amount", header, resolved))

	for batch in fixtures(rng):
		counts["batches"] += 1
		prefix = batch["revenue_prefix"]
		readers.sheets = batch["sheets"]
		readers.pages = batch["pages"]
		deployed = excel["parse_gl_excel"](b"", prefix)
		produced = parse.excel_rows(batch["sheets"], prefix)
		counts["excel_rows"] += len(produced["rows"])
		branches["excel_date_cells"] += sum(1 for row in produced["rows"]
			if row.date and row.date[4:5] == "-" and row.date.count("-") == 2)
		if (deployed["ok"], deployed["row_count"], deployed["error"], rows_of(deployed)) != (
				produced["ok"], produced["row_count"], produced["error"], rows_of(produced)):
			differences.append(("excel", batch, deployed, produced))

		deployed = pdf["parse_gl_pdf"](b"", prefix)
		produced = parse.pdf_rows(batch["pages"], page_lines(batch["pages"]), prefix)
		counts["table_rows"] += len(produced["rows"])
		if (deployed["ok"], deployed["row_count"], deployed["error"], rows_of(deployed)) != (
				produced["ok"], produced["row_count"], produced["error"], rows_of(produced)):
			differences.append(("pdf", batch, deployed, produced))
		for key, value in deployed["diag"].items():
			if produced["diag"].get(key) != value:
				differences.append(("pdf_diag", key, value, produced["diag"].get(key)))

		readers.pages = [{"text": "\n".join(batch["lines"]), "tables": []}]
		deployed = pdf["_parse_gl_text_lines"](b"", prefix)
		produced = parse.text_rows(batch["lines"], prefix)
		counts["text_rows"] += len(produced["rows"])
		branches["text_credit"] += sum(1 for row in produced["rows"] if row.credit)
		branches["text_debit"] += sum(1 for row in produced["rows"] if row.debit)
		branches["text_tail_three"] += sum(1 for row in produced["rows"]
			if row.debit and row.credit)
		if (deployed["ok"], deployed["row_count"], deployed["error"], rows_of(deployed)) != (
				produced["ok"], produced["row_count"], produced["error"], rows_of(produced)):
			differences.append(("text", batch, deployed, produced))
		if deployed["diag"] != produced["diag"]:
			differences.append(("text_diag", deployed["diag"], produced["diag"]))

		deployed_rows = excel["_gl_rows_from_pipeline_legacy"](batch["decoded"], prefix)
		produced_rows = parse.pipeline_rows(batch["decoded"], prefix)
		counts["pipeline_rows"] += len(produced_rows)
		if [describe(row) for row in deployed_rows] != [describe(row) for row in produced_rows]:
			differences.append(("pipeline", batch, deployed_rows, produced_rows))

	print({"source": SOURCE_SHA, "counts": counts, "branches": branches,
		"helper_cases": helper_cases, "differences": len(differences)})
	if differences:
		for item in differences[:5]:
			print("DIFF", item)
		raise SystemExit(1)
	if (counts["batches"] < arguments.rounds or counts["excel_rows"] < 100
			or counts["table_rows"] < 100 or counts["text_rows"] < 100
			or counts["pipeline_rows"] < 40 or branches["excel_date_cells"] < 20
			or branches["text_credit"] < 30 or branches["text_debit"] < 10):
		raise SystemExit("fixture coverage below the floor")
	raise SystemExit(0)


if __name__ == "__main__":
	main()
