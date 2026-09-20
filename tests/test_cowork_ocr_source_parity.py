"""Explicit offline parity: --source must point to the verified deployed snapshot.

Only hash-verified pure definitions/statement blocks are evaluated. This script
does not import the legacy application, PDF renderer, database or any provider.
"""

import argparse
import ast
from copy import deepcopy
import hashlib
from itertools import product
from pathlib import Path
import random
from types import SimpleNamespace
from typing import Any, Dict, List

from pearnly_cowork.ocr import inputs, pages
from test_cowork_ocr_contracts import Invoice, pipeline_page

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
	"services/ocr/legacy_adapter.py": "e1a1b38a25a7d799dd2d7b79eb86a30f150f9ac41fc8b0cfe7c1e3a0f58a809c",
	"services/ocr/recognize/core.py": "ac6df612ba37223e8e84dc382c7ee98fee1a270f0a9fa5651801cf03085c0f98",
	"services/ocr/entrypoints.py": "3676ef159ef98cc7a2f60bfb8d737fb56e2829d2f9ce273f21e10c59f959f993",
	"services/ocr/pipeline.py": "9e8fa8fb566a8b457c0ae6f64f6961069c757994269c9b65f6284a14a7843e52",
	"services/ocr/table_path.py": "4c12cc5acea0e5633261ca866ba45bfd74c02bde0fcb5e2e2b617f21e62a671d",
	"core/route_helpers.py": "ac1ad0d381685cbe8a28e36b176ce966b3c1ddee0f2f87fc7d0ea2a21a815edf",
}


class SourceError(Exception):
	def __init__(self, status_code, detail):
		self.detail = detail


def tree(root, name):
	content = (root / name).read_bytes()
	if hashlib.sha256(content).hexdigest() != HASHES[name]:
		raise ValueError(f"Source does not match {SOURCE_SHA}: {name}")
	return ast.parse(content)


def execute(nodes, namespace):
	module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
	exec(compile(module, "fixed-source-ocr", "exec"), namespace)


def functions(parsed, names):
	return [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name in names]


def assignment(node, name):
	return isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)


def wrapper(signature, body, return_expression):
	function = ast.parse(signature + "\n pass").body[0]
	function.body = deepcopy(body) + [ast.parse("return " + return_expression).body[0]]
	return function


def verify(root):
	namespace = {"Dict": Dict, "List": List, "Any": Any, "ThaiInvoice": object,
		"PipelinePageResult": object, "PipelineResult": object, "HTTPException": SourceError,
		"logger": SimpleNamespace(info=lambda *args, **kwargs: None)}
	adapter = tree(root, "services/ocr/legacy_adapter.py")
	execute(functions(adapter, {"_invoice_to_legacy_fields", "_page_to_legacy", "pipeline_result_to_legacy_dict"}), namespace)
	core = tree(root, "services/ocr/recognize/core.py")
	execute(functions(core, {"_page_confidence"}), namespace)
	workflow = functions(core, {"run_recognition_core"})[0]
	start = next(i for i, node in enumerate(workflow.body) if assignment(node, "seen_invoice_numbers"))
	end = next(i for i, node in enumerate(workflow.body) if assignment(node, "_persist"))
	execute([wrapper("def quality(result):", workflow.body[start:end], "{'pages': result['pages'], 'confidence': confidence}")], namespace)
	execute(functions(tree(root, "services/ocr/entrypoints.py"), {"all_pages_not_invoice"}), namespace)
	for source, names in (("services/ocr/pipeline.py", {"PDF_EXTENSIONS", "IMAGE_EXTENSIONS"}),
						 ("services/ocr/table_path.py", {"SUPPORTED_TABLE_EXTENSIONS"})):
		parsed = tree(root, source)
		for node in parsed.body:
			for name in names:
				if assignment(node, name): namespace[name] = ast.literal_eval(node.value)
	namespace["TABLE_EXTENSIONS"] = namespace["SUPPORTED_TABLE_EXTENSIONS"]
	assert inputs.SUPPORTED_EXTENSIONS == namespace["PDF_EXTENSIONS"] | namespace["IMAGE_EXTENSIONS"] | namespace["TABLE_EXTENSIONS"]
	execute(functions(tree(root, "core/route_helpers.py"), {"_plan_permissions"}), namespace)
	start = next(i for i, node in enumerate(workflow.body) if assignment(node, "_all_exts"))
	end = next(i for i, node in enumerate(workflow.body) if assignment(node, "quota_info"))
	gate = wrapper("def input_gate(content, file, plan=None):", workflow.body[start:end], "page_count")
	class RemoveImport(ast.NodeTransformer):
		def visit_ImportFrom(self, node):
			return None
	gate = RemoveImport().visit(gate)
	execute([gate], namespace)

	input_cases = 0
	for filename, size, count in product(
		["x" + ext.upper() for ext in sorted(inputs.SUPPORTED_EXTENSIONS)] + ["", "x", "x.pdf ", "x.exe"],
		(0, 1, inputs.MAX_FILE_BYTES, inputs.MAX_FILE_BYTES + 1), (0, 1, 50, 51),
	):
		namespace["count_pdf_pages"] = lambda content: count
		content = type("SizedInput", (), {"__len__": lambda self: size})()
		try:
			old = ("ok", namespace["input_gate"](content, SimpleNamespace(filename=filename)))
		except SourceError as error:
			old = ("error", error.detail)
		try:
			new = ("ok", inputs.validate_input_metadata(filename, size, pdf_page_count=count)["page_count"])
		except inputs.OcrInputError as error:
			new = ("error", error.details)
		assert old == new, (filename, size, count, old, new)
		input_cases += 1

	rng = random.Random(20260921)
	for _ in range(2000):
		batch = []
		for index in range(rng.randint(0, 12)):
			fields = {"invoice_number": rng.choice([None, "A", "B", "A ", "a"])}
			for key in ("date", "seller_tax", "seller_name", "buyer_tax", "buyer_name", "total_amount", "subtotal", "vat"):
				fields[key] = rng.choice([None, "", 0, "0", "value"])
			fields["items"] = rng.choice([None, [], [{"name": "item"}]])
			batch.append({"fields": fields, "page_index": index + 1, "is_copy": rng.choice([False, True])})
		assert namespace["quality"]({"pages": deepcopy(batch)}) == pages.assess_confidence(batch)
		for page in batch: assert namespace["_page_confidence"](page) == pages.page_confidence(page)

	for _ in range(200):
		batch = []
		for index in range(rng.randint(0, 7)):
			invoice = Invoice(invoice_number=str(index), seller_tax="001", buyer_tax=rng.choice(["", "001", "002"]),
				additional_invoices=[Invoice(invoice_number=f"{index}/{n}") for n in range(rng.randint(0, 3))])
			batch.append(pipeline_page(index + 1, invoice, confidence_band=rng.choice(["auto", "yellow_confirm", "needs_review"])))
		old = namespace["pipeline_result_to_legacy_dict"](SimpleNamespace(pages=batch, page_count=len(batch), elapsed_ms=12, engine="test", estimated_cost_thb=0))
		assert old["pages"] == pages.flatten_pipeline_pages(batch)
		assert old["_confidence_band"] == pages.aggregate_confidence_band(batch)
	for value in (True, False, None, "true", " TRUE ", "false", "yes", 0, 1):
		batch = [{"fields": {"is_not_invoice": value}}]
		assert namespace["all_pages_not_invoice"](batch) == pages.all_pages_not_invoice(batch)
	print(f"PASS {SOURCE_SHA}: {input_cases} input gates, 2000 quality batches, 200 multi-invoice batches, 9 noninvoice cases")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--source", required=True, type=Path)
	verify(parser.parse_args().source)
