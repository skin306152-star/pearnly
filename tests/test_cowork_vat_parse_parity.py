"""Differential check of the migrated output-VAT report parsing conversions.

CLI: --source <fixed-source-root>. Only selected pure definitions of the fixed
source are executed. The readers the pinned functions call are replaced by
in-memory doubles, because the migration moves their results, not the reading: a
workbook double yields the sheet rows, a PDF double yields the page text and
tables, and doubles for the OCR pipeline hand over the normalized entries. No
legacy application, database, file, network, OCR or model code runs.
"""
import argparse
import ast
import hashlib
from pathlib import Path
import random
import re
import sys
import types
import unicodedata
from typing import Any, Dict, List, Optional

from pearnly_cowork.reconciliation import vat_parse as parse

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/recon/field_comparator.py":
        "109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037",
    "services/vat/vat_parser_common.py":
        "ae435d57ef32f5c14b2cf43953e77d1a36b5f502cdf2c9539cf940b7aa46e2a1",
    "services/vat/vat_parser_excel.py":
        "3c4733035f994773f4ff2acb27162ef972c731e8c300ad2e951903ec8d7c1aea",
    "services/vat/vat_parser_pdf.py":
        "d3d2c71b1a890b497a0f86dcbc8aa8f3593538c5cc2c5274349c5fd94d19d60b",
    "services/vat/vat_parser_csv.py":
        "183aa63e7a2b4fe545136608f6b2731e6a90755a295efefffee7324655719f5c",
    "services/vat/vat_report_parser.py":
        "8ecf5e796268ce633b99d0a4894068eee126aad237190d7263a4cabf1e2ec296",
}


def extract(root, path, names, namespace, assignments=(), classes=()):
    """Execute only the named top-level definitions of a hash-pinned source file."""
    data = (root / path).read_bytes()
    if hashlib.sha256(data).hexdigest() != HASHES[path]:
        raise AssertionError(f"Source does not match {SOURCE_SHA}: {path}")
    tree = ast.parse(data)
    assignments, classes = set(assignments), set(classes)
    def is_named_assignment(node):
        # Both a plain and an annotated constant are read the same way.
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            return False
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return (len(targets) == 1
            and isinstance(targets[0], ast.Name) and targets[0].id in assignments)

    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or (isinstance(node, ast.ClassDef) and node.name in classes)
        or is_named_assignment(node)]
    if {node.name for node in nodes if isinstance(node, ast.FunctionDef)} != set(names):
        raise AssertionError(f"Missing source definitions: {path}")
    if {node.name for node in nodes if isinstance(node, ast.ClassDef)} != classes:
        raise AssertionError(f"Missing source classes: {path}")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root / path), "exec"), namespace)
    return namespace


def logger_double():
    module = types.ModuleType("legacy_logger")
    module.info = lambda *arguments, **keywords: None
    module.warning = lambda *arguments, **keywords: None
    module.error = lambda *arguments, **keywords: None
    return module


class ReaderDouble:
	"""Stands in for the workbook, PDF and pipeline readers of the pinned code.

	The migration moves what those readers produced, not the reading itself, so the
	differential hands the pinned code the same sheet rows, page text and tables, and
	normalized entries a real file and a real engine would have produced.
	"""

	def __init__(self):
		self.sheets = []
		self.pages = []
		self.decoded = {}
		self.engine = ""

	def install(self):
		reader = self

		class _Worksheet:
			def iter_rows(self, min_row=1, max_row=None, values_only=True):
				rows = reader.sheets
				return iter(rows[min_row - 1:max_row] if max_row else rows[min_row - 1:])

		class _Workbook:
			active = None

			def __init__(self):
				self.active = _Worksheet()

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

		class _PipelinePage:
			validation_warnings = []
			extraction_audit = {"page": 1}

		class _PipelineResult:
			def __init__(self):
				self.engine = reader.engine
				self.pages = [_PipelinePage()]
				self.estimated_cost_thb = 0.0
				self.page_count = len((reader.decoded.get("pages") or [])) or 1

		openpyxl = types.ModuleType("openpyxl")
		openpyxl.load_workbook = lambda stream, data_only=True: _Workbook()
		pdfplumber = types.ModuleType("pdfplumber")
		pdfplumber.open = lambda stream: _Document()
		pipeline = types.ModuleType("services.ocr.pipeline")
		pipeline.IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
		pipeline.TABLE_EXTENSIONS = {".csv", ".txt"}
		for name in ("run_on_image_bytes", "run_on_pdf_bytes", "run_on_table_bytes"):
			setattr(pipeline, name, lambda *arguments, **keywords: _PipelineResult())
		adapter = types.ModuleType("services.ocr.legacy_adapter")
		adapter.pipeline_result_to_legacy_dict = lambda result: reader.decoded
		services_ocr = types.ModuleType("services.ocr")
		services_ocr.pipeline = pipeline
		services_ocr.legacy_adapter = adapter
		sys.modules.update({
			"openpyxl": openpyxl,
			"pdfplumber": pdfplumber,
			"services": types.ModuleType("services"),
			"services.ocr": services_ocr,
			"services.ocr.pipeline": pipeline,
			"services.ocr.legacy_adapter": adapter,
			"services.ocr.error_format": _error_format(),
		})


def _error_format():
    module = types.ModuleType("services.ocr.error_format")
    module.short_error = lambda error: str(error)
    return module


def load_legacy(root):
    """Load the pinned pure definitions of the deployed VAT report path."""
    normalizers = {"re": re, "unicodedata": unicodedata, "Optional": Optional,
                   "SequenceMatcher": __import__("difflib").SequenceMatcher,
                   "datetime": __import__("datetime").datetime,
                   "date": __import__("datetime").date}
    extract(root, "services/recon/field_comparator.py",
            {"normalize_str", "normalize_invoice_no", "normalize_tax_id", "normalize_branch",
             "parse_date", "_thai_to_gregorian"},
            normalizers, assignments={"_HQ_ALIASES"})
    common = {"re": re, "List": List, "Dict": Dict, "Any": Any, "Optional": Optional,
              "normalize_tax_id": normalizers["normalize_tax_id"],
              "normalize_branch": normalizers["normalize_branch"],
              "parse_date": normalizers["parse_date"]}
    common_names = {"_hit", "_to_float", "_filter_garbage_rows", "_map_columns", "_build_row"}
    common_constants = {"PARSER_VERSION", "MIN_DETERMINISTIC_ROWS", "_DATE_H", "_NO_H", "_REF_H",
                        "_NAME_H", "_TAX_H", "_BRANCH_H", "_NET_H", "_VAT_H", "_TOTAL_H", "_SKIP_H",
                        "_VAT_HEAD_KW", "_VAT_ORN_RE", "_VAT_ALNUM_RE"}
    extract(root, "services/vat/vat_parser_common.py", common_names, common,
            assignments=common_constants)
    excel = dict(common, io=__import__("io"), logging=__import__("logging"))
    excel["logger"] = logger_double()
    excel.update({name: common[name] for name in ("_to_float", "_SKIP_H", "_map_columns",
                                                  "_build_row")})
    extract(root, "services/vat/vat_parser_excel.py", {"parse_excel"}, excel)
    pdf = dict(common, io=__import__("io"), logging=__import__("logging"))
    pdf["logger"] = logger_double()
    extract(root, "services/vat/vat_parser_pdf.py", {"parse_pdf_text", "_parse_vat_pdf_text_lines"},
            pdf, assignments={"_VAT_DATE_RE", "_VAT_TAIL_2N", "_TAX_ID_RE", "_BRANCH_RE",
                              "_REF_TOKEN_RE", "_GLUED_TAIL_RE"})
    csv_namespace = dict(common, csv=__import__("csv"), io=__import__("io"))
    csv_namespace.update({"Decimal": __import__("decimal").Decimal,
                          "ROUND_HALF_UP": __import__("decimal").ROUND_HALF_UP})
    csv_namespace["_map_columns"] = common["_map_columns"]
    extract(root, "services/vat/vat_parser_csv.py",
            {"parse_csv_direct", "_decode_bytes", "_to_amount", "_map_erp_columns", "_usable",
             "_build_csv_row"},
            csv_namespace, assignments={"_ERP_COL_FIELDS", "_ENCODINGS", "_MAX_HEADER_SCAN"})
    facade = {"re": re, "List": List, "Dict": Dict, "Any": Any, "Optional": Optional,
              "logger": logger_double(),
              "normalize_tax_id": normalizers["normalize_tax_id"],
              "normalize_branch": normalizers["normalize_branch"],
              "_short_err": _short_error,
              "_to_float": common["_to_float"], "PARSER_VERSION": common["PARSER_VERSION"],
              "MIN_DETERMINISTIC_ROWS": common["MIN_DETERMINISTIC_ROWS"],
              "_filter_garbage_rows": common["_filter_garbage_rows"],
              "_parse_vat_pdf_text_lines": pdf["_parse_vat_pdf_text_lines"]}
    extract(root, "services/vat/vat_report_parser.py", {"_parse_vat_via_pipeline"}, facade)
    return excel, pdf, csv_namespace, facade["_parse_vat_via_pipeline"]


def _short_error(error):
    return str(error)


def describe(row):
	return tuple(sorted((key, value) for key, value in row.items()))


def outcome(function, *arguments):
	"""Compare success payloads and exception identity, never a partial outcome."""
	try:
		return "ok", function(*arguments)
	except Exception as error:
		return "error", type(error).__name__, str(error)


def rows_of(result):
	return None if result is None else [describe(row) for row in result["rows"]]


def page_lines(pages):
	"""The lines a caller reads off the pages, in the order the reader prints them."""
	return [line for page in pages for line in str(page.get("text") or "").split("\n")]


def fixtures(rng):
	dates = ["01/06/2569", "2026-06-01", "", "31/12/2569", None, "15/06/69"]
	invoice_nos = ["IV001", "OHC06/2025", "DXOHC25-006OHANAHAN", "", "1", "SI000001", "INV-1"]
	names = ["OHANAHAN", "ASAHI", "บริษัท ทดสอบ จำกัด", "", "SI 1 ACME"]
	amounts = ["1,070.00", "0.00", "500", "(250.00)", "", "abc", "-", "1,234.56"]
	tax_ids = ["1234567890123", "", "0-1234-56789-01-2"]
	branches = ["00000", "00001", "", "สำนักงานใหญ่"]
	headers = [
		["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "สาขา", "มูลค่า", "ภาษี",
			"ยอดรวม"],
		["Date", "Invoice No", "Customer", "Tax ID", "Branch", "Net", "VAT", "Total"],
		["日期", "发票号", "买方名称", "税号", "分支", "税前金额", "税额", "含税金额"],
		["ref", "txtsino", "txtamt", "txtvatamt", "vat_date"],
		["วันที่", "เลขที่เอกสาร", "อ้างอิง", "มูลค่า", "ภาษี"],
	]

	def data_row():
		return [rng.choice(dates), rng.choice(invoice_nos), rng.choice(names), rng.choice(tax_ids),
			rng.choice(branches), rng.choice(amounts), rng.choice(amounts), rng.choice(amounts)]

	for _ in range(250):
		cells = []
		if rng.random() < 0.85:
			cells.append(list(rng.choice(headers)))
		for _ in range(rng.randint(0, 6)):
			roll = rng.random()
			if roll < 0.12:
				cells.append(["รวมทั้งสิ้น", "", "", "", "", rng.choice(amounts),
					rng.choice(amounts), rng.choice(amounts)])
			elif roll < 0.2:
				cells.append(["", "", "", "", "", "", "", ""])
			else:
				cells.append(data_row())
		pages = []
		for _ in range(rng.randint(0, 2)):
			tables = []
			if rng.random() < 0.8:
				tables.append([list(rng.choice(headers))]
					+ [data_row() for _ in range(rng.randint(0, 4))])
			pages.append({"text": rng.choice(["", "x" * 250, "รายงานภาษีขาย"]), "tables": tables})
		lines = []
		for _ in range(rng.randint(0, 6)):
			roll = rng.random()
			invoice = rng.choice(invoice_nos)
			if roll < 0.5:
				lines.append(" ".join([rng.choice(dates) or "", inventory_no(rng), invoice,
					rng.choice(names), rng.choice(tax_ids), rng.choice(branches),
					amounts_two(rng), amounts_two(rng)]))
			elif roll < 0.7:
				lines.append(" ".join(["1", rng.choice(dates) or "", invoice, rng.choice(names),
					amounts_two(rng), amounts_two(rng)]))
			elif roll < 0.85:
				lines.append(" ".join([rng.choice(dates) or "", invoice, rng.choice(names),
					rng.choice(amounts), rng.choice(amounts)]))
			else:
				lines.append(rng.choice(["รวมทั้งสิ้น 10,000.00 700.00", "เลขที่ใบกำกับภาษี", "",
					"grand total 100.00 7.00", "1 2 3"]))
		csv_lines = [",".join(str(cell) for cell in rng.choice(headers))]
		csv_lines += [",".join(str(cell) for cell in data_row()) for _ in range(rng.randint(0, 4))]
		if rng.random() < 0.3:
			csv_lines.append(",".join(["รวมทั้งสิ้น", "", "", "", "", "100.00", "7.00", "107.00"]))
		decoded = {"pages": [{"document": {"entries": [
			{"seq_no": str(rng.randint(1, 9)), "invoice_no": inventory_no(rng),
				"transaction_date": rng.choice(dates), "customer_name": rng.choice(names),
				"customer_tax": rng.choice(tax_ids), "customer_branch": rng.choice(branches),
				"subtotal": rng.choice(amounts), "vat": rng.choice(amounts),
				"total": rng.choice(amounts)}
			for _ in range(rng.randint(0, 4))]}}], "_needs_review": rng.random() < 0.5}
		yield {"cells": cells, "pages": pages, "lines": lines, "csv": csv_lines,
			"decoded": decoded, "engine": rng.choice(["", "enterprise-2025-01", "qwen3-32b"])}

	# Fixed shapes: a keyword header, the MR.ERP export columns, a framed table, the
	# printed text lines, and the pipeline entries.
	yield {"cells": [
		["รายงานภาษีขาย"],
		["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "สาขา", "มูลค่า", "ภาษี",
			"ยอดรวม"],
		["01/06/2569", "IV001", "บริษัท ทดสอบ จำกัด", "1234567890123", "00000", "1,000.00",
			"70.00", "1,070.00"],
		["02/06/2569", "IV002", "OHANAHAN", "0-1234-56789-01-2", "00001", "2,000.00", "140.00",
			"2,140.00"],
		["รวมทั้งสิ้น", "", "", "", "", "3,000.00", "210.00", "3,210.00"],
	], "pages": [], "lines": [], "csv": [], "decoded": {}, "engine": ""}
	yield {"cells": [
		["ref", "txtsino", "txtamt", "txtvatamt", "vat_date"],
		["REF-1", "IV001", "1,000.00", "70.00", "01/06/2569"],
		["", "", "", "", ""],
	], "pages": [], "lines": [], "csv": [], "decoded": {}, "engine": ""}
	yield {"cells": [], "pages": [{"text": "รายงานภาษีขาย " + "x" * 240, "tables": [[
		["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "มูลค่า", "ภาษี"],
		["01/06/2569", "IV001", "ACME", "1,000.00", "70.00"],
		["รวมทั้งสิ้น", "", "", "1,000.00", "70.00"],
		["02/06/2569", "IV002", "ACME", "2,000.00", "140.00"],
	]]}], "lines": [], "csv": [], "decoded": {}, "engine": ""}
	yield {"cells": [], "pages": [],
		"lines": [
			"1 01/06/2569 DXOHC25-006OHANAHAN REF-1 บริษัท ทดสอบ จำกัด 1234567890123 00000 "
			"1,000.00 70.00",
			"02/06/2569 IV002 OHANAHAN 2,000.00 140.00",
			"รวมทั้งสิ้น 3,000.00 210.00",
			"SI 1 ACME 3 100.00 7.00",
		], "csv": [], "decoded": {}, "engine": ""}
	# A page with a real text layer and no table frames: the pinned reader falls back
	# to the printed text lines, which is the branch the floor below insists on.
	yield {"cells": [], "pages": [{"text": "\n".join([
		"รายงานภาษีขาย",
		"1 01/06/2569 DXOHC25-006OHANAHAN REF-1 บริษัท ทดสอบ จำกัด 1234567890123 00000 "
		"1,000.00 70.00",
		"02/06/2569 IV002 OHANAHAN 2,000.00 140.00",
		"รวมทั้งสิ้น 3,000.00 210.00",
		"x" * 220,
	]), "tables": []}], "lines": [], "csv": [], "decoded": {}, "engine": ""}
	yield {"cells": [], "pages": [], "lines": [], "csv": [
		"วันที่,เลขที่ใบกำกับ,ชื่อผู้ซื้อ,มูลค่า,ภาษี",
		"01/06/2569,IV001,ACME,1,000.00,70.00",
		",,,,",
		",,no document,,,,",
	], "decoded": {}, "engine": ""}
	yield {"cells": [], "pages": [], "lines": [], "csv": [],
		"decoded": {"pages": [{"document": {"entries": [
			{"seq_no": "1", "invoice_no": "IV001", "transaction_date": "2026-06-01",
				"customer_name": "ACME", "customer_tax": "1234567890123",
				"customer_branch": "00000", "subtotal": "1,000.00", "vat": "70.00",
				"total": "1,070.00"},
			{"seq_no": "", "invoice_no": "1", "transaction_date": "", "customer_name": "",
				"customer_tax": "", "customer_branch": "", "subtotal": "0", "vat": "0",
				"total": "0"},
		]}}]}, "engine": "qwen3-32b"}


def inventory_no(rng):
	return rng.choice(["IV001", "OHC06/2025", "DXOHC25-006", "INV-1", "1", "SI000001"])


def amounts_two(rng):
	return rng.choice(["1,000.00", "70.00", "0.00", "(250.00)"])


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--source", required=True)
	parser.add_argument("--rounds", type=int, default=250)
	arguments = parser.parse_args()
	root = Path(arguments.source)
	readers = ReaderDouble()
	readers.install()
	excel, pdf, csv_namespace, via_pipeline = load_legacy(root)
	rng = random.Random(20260921)
	differences = []
	counts = {"batches": 0, "excel_rows": 0, "table_rows": 0, "text_rows": 0, "csv_rows": 0,
		"pipeline_rows": 0}
	branches = {"regex_fallback": 0, "scanned_pdf": 0, "erp_columns": 0, "skipped_rows": 0}
	helper_cases = {"to_float": 0, "map_columns": 0, "filter_garbage_rows": 0, "build_row": 0}
	for value in [None, "", "-", "–", "0.00", "1,070.00", "(250.00)", " 12 ", "abc", "1e5",
			"1,234,567.89", "\xa0", 0, 12.345, True]:
		helper_cases["to_float"] += 1
		if outcome(excel["_to_float"], value) != outcome(parse.to_float, value):
			differences.append(("to_float", value))
	headers = [["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "สาขา", "มูลค่า",
		"ภาษี", "ยอดรวม"],
		["Date", "Invoice No", "Customer", "Tax ID", "Branch", "Net", "VAT", "Total"],
		["日期", "发票号", "买方名称", "税号", "分支", "税前金额", "税额", "含税金额"],
		["ref", "txtsino", "txtamt", "txtvatamt", "vat_date"],
		["วันที่", "อ้างอิง", "เลขที่เอกสาร"],
		["", "", ""]]
	for header in headers:
		helper_cases["map_columns"] += 1
		if outcome(excel["_map_columns"], header) != outcome(parse.map_columns, header):
			differences.append(("map_columns", header))
	garbage_pool = ["", "1", "AB", "-----", "1234567890123456789012345678901",
		"ใบกำกับภาษี เลขที่", "IV001", "IV 001 extra", "IV001OHANAHAN"]
	for key in garbage_pool:
		rows = [{"report_ref_no": key, "report_invoice_no": key}]
		helper_cases["filter_garbage_rows"] += 1
		if (outcome(excel["_filter_garbage_rows"], rows)
				!= outcome(parse.filter_garbage_rows, rows)):
			differences.append(("filter_garbage_rows", key))
	cells_pool = [["01/06/2569", "IV001", "ACME", "1234567890123", "00000", "1,000.00", "70.00",
		"1,070.00"], ["", "", "", "", "", "", "", ""], ["x", None, "1,000.00"]]
	col_map = excel["_map_columns"](headers[0])
	for cells in cells_pool:
		helper_cases["build_row"] += 1
		if (outcome(excel["_build_row"], 1, cells, col_map)
				!= outcome(parse.build_row, 1, cells, col_map)):
			differences.append(("build_row", cells))

	for batch in fixtures(rng):
		counts["batches"] += 1
		readers.sheets = batch["cells"]
		readers.pages = batch["pages"]
		readers.decoded = batch["decoded"]
		readers.engine = batch["engine"]
		_deployed = outcome(excel["parse_excel"], b"")
		_produced = outcome(parse.excel_rows, batch["cells"])

		deployed = excel["parse_excel"](b"")
		produced = parse.excel_rows(batch["cells"])
		counts["excel_rows"] += len(produced.get("rows") or [])
		if (deployed["ok"], deployed.get("row_count"), deployed.get("meta"), deployed.get("error"),
				rows_of(deployed)) != (produced["ok"], produced.get("row_count"),
				produced.get("meta"), produced.get("error"), rows_of(produced)):
			differences.append(("excel", batch, deployed, produced))

		deployed = pdf["parse_pdf_text"](b"")
		# The regex fallback inside the pinned reader re-reads the same pages, so
		# the migrated call gets the lines those pages print, not the separate pool.
		produced = parse.pdf_rows(batch["pages"], page_lines(batch["pages"]))
		counts["table_rows"] += len(produced["rows"]) if produced else 0
		if deployed is None and produced is None:
			branches["scanned_pdf"] += 1
		elif (deployed is None) != (produced is None):
			differences.append(("pdf_none", batch, deployed, produced))
		elif (deployed["ok"], deployed.get("row_count"), deployed.get("meta"),
				deployed.get("method"), rows_of(deployed)) != (produced["ok"],
				produced.get("row_count"), produced.get("meta"), produced.get("method"),
				rows_of(produced)):
			differences.append(("pdf", batch, deployed, produced))
		if produced and produced.get("method") == "pdf_text_regex":
			branches["regex_fallback"] += 1

		readers.pages = [{"text": "\n".join(batch["lines"]), "tables": []}]
		deployed = pdf["_parse_vat_pdf_text_lines"](b"")
		produced = parse.text_lines(batch["lines"])
		counts["text_rows"] += len(produced)
		if [describe(row) for row in deployed] != [describe(row) for row in produced]:
			differences.append(("text", batch, deployed, produced))

		csv_text = "\n".join(batch["csv"])
		deployed = csv_namespace["parse_csv_direct"](csv_text.encode("utf-8"), "report.csv")
		produced = parse.csv_rows(csv_text, ",")
		counts["csv_rows"] += len(produced["rows"]) if produced else 0
		if deployed is None and produced is None:
			pass
		elif (deployed is None) != (produced is None):
			differences.append(("csv_none", batch, deployed, produced))
		elif (deployed["ok"], deployed.get("row_count"), deployed.get("warnings"),
				deployed.get("method"), rows_of(deployed)) != (produced["ok"],
				produced.get("row_count"), produced.get("warnings"), produced.get("method"),
				rows_of(produced)):
			differences.append(("csv", batch, deployed, produced))
		if produced and produced.get("warnings"):
			branches["skipped_rows"] += 1
		if batch["csv"] and batch["csv"][0].lower().startswith("ref,txtsino,txtamt"):
			branches["erp_columns"] += 1

		deployed = via_pipeline(b"", "report.png", api_key=None)
		produced = parse.pipeline_rows(batch["decoded"], batch["engine"])
		# The deployed facade filters every path on its way out, so the comparison
		# applies the same filter to the pinned result.
		deployed_rows = excel["_filter_garbage_rows"](deployed["rows"])
		produced_rows = parse.filter_garbage_rows(produced)
		counts["pipeline_rows"] += len(produced_rows)
		if ([describe(row) for row in deployed_rows]
				!= [describe(row) for row in produced_rows]):
			differences.append(("pipeline", batch, deployed_rows, produced_rows))

	print({"source": SOURCE_SHA, "counts": counts, "branches": branches,
		"helper_cases": helper_cases, "differences": len(differences)})
	if differences:
		for item in differences[:3]:
			print("DIFF", str(item)[:600])
		raise SystemExit(1)
	if (counts["batches"] < arguments.rounds or counts["excel_rows"] < 50
			or counts["text_rows"] < 50 or counts["csv_rows"] < 30
			or counts["pipeline_rows"] < 30 or branches["regex_fallback"] < 1
			or branches["scanned_pdf"] < 1 or branches["erp_columns"] < 1
			or branches["skipped_rows"] < 1):
		raise SystemExit("fixture coverage below the floor")
	raise SystemExit(0)


if __name__ == "__main__":
	main()
