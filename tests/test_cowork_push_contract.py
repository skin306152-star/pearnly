"""Offline push-contract tests; --source runs hash-verified legacy differential checks."""

import argparse
import ast
from copy import deepcopy
import hashlib
import itertools
from pathlib import Path
import random
import re
import sys
from types import SimpleNamespace
from typing import Any, Dict, Optional
import unittest

from pearnly_cowork.contracts import push


class DirectionContract(unittest.TestCase):
	def test_invoice_aliases_do_not_include_bank_direction_or_a_default(self):
		for raw, expected in ((" SALES ", "sales"), ("income", "sales"), ("purchase", "purchase"),
			("Expense", "purchase"), (None, None), ("", None), ("deposit", None), ("withdrawal", None), (1, None)):
			with self.subTest(raw=raw):
				self.assertEqual(push.normalize_direction(raw), expected)

	def test_explicit_precedence_and_invalid_truthy_values_follow_source(self):
		self.assertEqual(push.explicit_direction({"direction": "income", "fields": {"direction": "purchase"}}, {"direction": "expense"}), "sales")
		self.assertEqual(push.explicit_direction({"fields": {"direction": "purchase"}}, {"direction": "income"}), "sales")
		self.assertEqual(push.explicit_direction({"fields": {"direction": "purchase"}}, {}), "purchase")
		self.assertIsNone(push.explicit_direction({"direction": "deposit", "fields": {"direction": "purchase"}}, {"direction": "sales"}))

	def test_tax_anchor_requires_exactly_one_side_and_thirteen_digits(self):
		own = "0107536000188"
		for seller, buyer, expected in ((own, "", "sales"), ("", own, "purchase"),
			(own, own, None), ("other", "", None), ("13", "", None)):
			with self.subTest(seller=seller, buyer=buyer):
				self.assertEqual(push.detect_by_tax({"fields": {"seller_tax": seller, "buyer_tax": buyer}}, own), expected)
		self.assertIsNone(push.detect_by_tax({"fields": {"seller_tax": own}}, "13"))
		self.assertIsNone(push.detect_by_tax({"fields": []}, own))

	def test_tax_aliases_and_length_check_are_not_mod11_validation(self):
		self.assertEqual(push.clean_tax_id("0-1075 3600018-8"), "0107536000188")
		self.assertEqual(push.clean_tax_id("0107536000189 Company"), "0107536000189")
		self.assertEqual(push.detect_by_tax({"fields": {"buyer_tax_id": "0107536000189"}}, "0107536000189"), "purchase")
		self.assertEqual(push.clean_tax_id("13/06/26"), "")

	def test_explicit_direction_wins_and_unknown_declaration_uses_tax_anchor(self):
		flat = {"direction": "purchase", "fields": {"seller_tax": "0107536000188"}}
		self.assertEqual(push.resolve_direction(flat, {}, own_tax_id="0107536000188"), "purchase")
		flat["direction"] = "deposit"
		self.assertEqual(push.resolve_direction(flat, {}, own_tax_id="0107536000188"), "sales")
		self.assertIsNone(push.resolve_direction({}, {}, own_tax_id=""))

	def test_batch_never_overrides_nonempty_row_decisions(self):
		for existing in ("sales", "purchase", "invalid"):
			fields = {"direction": existing}
			self.assertIsNone(push.apply_batch_direction(fields, "purchase"))
			self.assertEqual(fields["direction"], existing)
		fields = {"direction": ""}
		push.apply_batch_direction(fields, "sales")
		self.assertEqual(fields, {"direction": "sales"})
		push.apply_batch_direction(None, "sales")


class PostingKindContract(unittest.TestCase):
	def test_none_and_invalid_values_never_default_to_service(self):
		for value in (None, "", "other", 0, False, [], {}):
			with self.subTest(value=value):
				self.assertIsNone(push.normalize_posting_kind(value))
		self.assertEqual(push.normalize_posting_kind(" STOCK "), "stock")
		self.assertEqual(push.normalize_posting_kind("Service"), "service")

	def test_explicit_valid_then_history_then_none(self):
		self.assertEqual(push.resolve_posting_kind("stock", {"posting_kind": "service"}), "stock")
		self.assertEqual(push.resolve_posting_kind("invalid", {"posting_kind": "service"}), "service")
		self.assertIsNone(push.resolve_posting_kind(None, {"config": {"posting_kind": "service"}}))

	def test_no_line_opt_in_differs_from_partial_or_invalid_declaration(self):
		for rows in (None, {}, [], [None], [{"amount": "10"}]):
			self.assertIsNone(push.item_posting_kinds(rows))
		for rows in ([{"posting_kind": "stock"}, {}], [{"posting_kind": "stock"}, None],
			[{"posting_kind": None}], [{"posting_kind": "invalid"}]):
			self.assertEqual(push.item_posting_kinds(rows), [])
		self.assertEqual(push.item_posting_kinds([{"posting_kind": "stock"}, {"posting_kind": " SERVICE "}]), ["stock", "service"])


class InternalBoundaryContract(unittest.TestCase):
	def test_internal_history_is_rejected_independently_of_user_portal(self):
		for source in ("erp_web", "line_erp"):
			with self.subTest(source=source), self.assertRaisesRegex(push.PushContractError, "^erp.internal_only$"):
				push.require_external_history({"source": source})
		for source in ("manual", "cowork_line", None):
			self.assertIsNone(push.require_external_history({"source": source}))

	def test_internal_user_cannot_trigger_external_push_with_external_history(self):
		with self.assertRaisesRegex(push.PushContractError, "^erp.internal_only$"):
			push.require_external_user({"entry": "erp"})
		self.assertIsNone(push.require_external_user({"entry": "cowork"}))


class DocumentPrecheckContract(unittest.TestCase):
	def test_line_precheck_requires_explicit_direction_without_alias_inference(self):
		for value in (None, "", "income", "expense", "deposit", "withdrawal"):
			with self.subTest(value=value), self.assertRaisesRegex(push.PushContractError, "^direction_required$"):
				push.require_document_choices(value)
		self.assertEqual(push.require_document_choices(" SALES ", " CREDIT "), ("sales", "credit"))
		self.assertEqual(push.require_document_choices("purchase"), ("purchase", None))

	def test_payment_invalid_is_distinct_from_not_declared(self):
		for value in ("", " ", "bank", "paid", False, 0):
			with self.subTest(value=value), self.assertRaisesRegex(push.PushContractError, "^payment_invalid$"):
				push.require_document_choices("sales", value)

	def test_workspace_is_document_company_and_preserves_type_sensitive_source_check(self):
		self.assertEqual(push.require_history_workspace({"workspace_client_id": 8}, "8"), 8)
		for history, workspace, code in (({}, None, "workspace_required"), ({}, 8, "history_not_found"),
			({"workspace_client_id": 7}, 8, "history_workspace_mismatch"),
			({"workspace_client_id": "8"}, 8, "history_workspace_mismatch")):
			with self.subTest(code=code), self.assertRaisesRegex(push.PushContractError, "^" + code + "$"):
				push.require_history_workspace(history, workspace)

	def test_express_requires_explicit_stock_or_service_without_inference(self):
		for value, code in ((None, "posting_kind_required"), ("", "posting_kind_required"),
			(" ", "posting_kind_invalid"), ("other", "posting_kind_invalid"), (False, "posting_kind_invalid")):
			with self.subTest(value=value), self.assertRaisesRegex(push.PushContractError, "^" + code + "$"):
				push.require_adapter_choices("express", "sales", posting_kind=value)
		self.assertEqual(push.require_adapter_choices("express", "purchase", posting_kind=" STOCK "), "stock")
		self.assertEqual(push.require_adapter_choices("express", "sales", posting_kind="service", payment="cash"), "service")

	def test_mrerp_requires_payment_and_preserves_unverified_cash_purchase_block(self):
		with self.assertRaisesRegex(push.PushContractError, "^payment_required$"):
			push.require_adapter_choices("mrerp", "sales")
		with self.assertRaisesRegex(push.PushContractError, "^mrerp_purchase_cash_unverified$"):
			push.require_adapter_choices("mrerp", "purchase", payment="cash")
		for direction, payment in (("sales", "cash"), ("sales", "credit"), ("purchase", "credit")):
			self.assertIsNone(push.require_adapter_choices("mrerp", direction, payment=payment))

	def test_unknown_adapter_rejects_and_normal_return_has_no_success_payload(self):
		with self.assertRaisesRegex(push.PushContractError, "^adapter_not_supported$"):
			push.require_adapter_choices("other", "sales", payment="cash")
		self.assertIsNone(push.require_adapter_choices("mrerp", "sales", payment="cash"))
		self.assertEqual(push.require_adapter_choices("express", "sales", posting_kind="service"), "service")

	def test_manual_choices_edit_first_nonduplicate_page_copy(self):
		history = {"source": "cowork_line", "pages": [
			{"is_duplicate": True, "fields": {"direction": "purchase"}},
			{"is_copy": True, "fields": {"direction": "purchase"}},
			{"fields": {"raw": "evidence", "posting_payment_manual": "credit"}},
		]}
		original = deepcopy(history)
		result = push.with_manual_fields(history, "sales", "cash")
		self.assertEqual(result["pages"][2]["fields"], {"raw": "evidence", "posting_payment_manual": "cash", "direction": "sales"})
		self.assertEqual(result["pages"][:2], history["pages"][:2])
		self.assertEqual(history, original)

	def test_manual_fields_empty_pages_fallback_and_undeclared_payment_preserved(self):
		for history in ({}, {"pages": []}, {"pages": "invalid"}, {"pages": [None]}):
			self.assertEqual(push.with_manual_fields(history, "purchase", None)["pages"], [{"fields": {"direction": "purchase"}}])
		history = {"pages": [{"is_copy": True, "fields": {"posting_payment_manual": "credit"}}]}
		result = push.with_manual_fields(history, "sales", None)
		self.assertEqual(result["pages"][0]["fields"], {"direction": "sales", "posting_payment_manual": "credit"})


SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
SOURCE_HASHES = {
	"services/erp/express_push/direction.py": "808be2cb59be1f5270ae3e0d5b8aa16915b7603c2c8448a7df339dd80630ec8a",
	"services/erp/express_push/posting_kind.py": "4742aec35acfdcc04741300d57eb1fce0c7e9184f09d81767bc3df21e8cc0efa",
	"services/erp/internal_push_guard.py": "b64ba33ae6a1dd8fda9319aaf485b0280125c6aff2b17b42c058f963a356f14c",
	"services/cowork_line/document_preflight.py": "e5e4d38db837059c913263c0fa2afe2d0fc5f9ab9efd8d754f675e6b43777f6f",
	"services/purchase/field_clean.py": "43912e65fcce3afa53fa4f7cdfd2d57afc02ff5556c5e17a6d76c2d030bdbed8",
}


def source_definitions(root, path, namespace, names, constants=(), *, remove_runtime_imports=False):
	data = (root / path).read_bytes()
	if hashlib.sha256(data).hexdigest() != SOURCE_HASHES[path]:
		raise ValueError(f"Source does not match {SOURCE_SHA}: {path}")
	nodes = []
	for node in ast.parse(data).body:
		if isinstance(node, ast.FunctionDef) and node.name in names:
			if remove_runtime_imports:
				# Only these function-local dependency imports are replaced by
				# explicit test doubles. No sys.modules/import-path mutation.
				node.body = [part for part in node.body if not isinstance(part, ast.ImportFrom)]
			nodes.append(node)
		elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets):
			nodes.append(node)
	if {node.name for node in nodes if isinstance(node, ast.FunctionDef)} != set(names):
		raise ValueError(f"Missing source definitions: {path}")
	exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root / path), "exec"), namespace)


def verify_source(root):
	"""Compare pure gates; injected target/subject readiness is not an auth test."""
	common = {"Any": Any, "Dict": Dict, "Optional": Optional, "re": re, "deepcopy": deepcopy}
	direction = dict(common)
	source_definitions(root, "services/purchase/field_clean.py", direction, {"clean_tax_id"})
	source_definitions(root, "services/erp/express_push/direction.py", direction,
		{"_fields", "normalize", "explicit_direction", "apply_batch_direction", "detect_by_tax", "resolve_direction"},
		{"_SALES_TOKENS", "_PURCHASE_TOKENS"})
	posting = dict(common)
	source_definitions(root, "services/erp/express_push/posting_kind.py", posting,
		{"normalize", "item_posting_kinds", "resolve_posting_kind"},
		{"POSTING_KIND_SERVICE", "POSTING_KIND_STOCK", "VALID_POSTING_KINDS"})
	values = [None, "", " ", "sales", " SALES ", "purchase", "income", "expense", "deposit", "withdrawal", 0, False, [], {}, "stock", "service", "invalid"]
	for value in values:
		assert direction["normalize"](value) == push.normalize_direction(value), value
		assert posting["normalize"](value) == push.normalize_posting_kind(value), value
	for explicit, saved in itertools.product(values, repeat=2):
		assert posting["resolve_posting_kind"](explicit, {"posting_kind": saved}) == push.resolve_posting_kind(explicit, {"posting_kind": saved})
	rng = random.Random(20260920)
	tax_ids = ["", None, "13", "0107536000188", "0-1075 3600018-8", "0107536000189"]
	for _ in range(1500):
		flat = {"direction": rng.choice(values), "fields": {"direction": rng.choice(values), "seller_tax": rng.choice(tax_ids), "buyer_tax_id": rng.choice(tax_ids)}}
		history = {"direction": rng.choice(values)}
		own = rng.choice(tax_ids)
		assert direction["resolve_direction"](flat, history, own_tax_id=own) == push.resolve_direction(flat, history, own_tax_id=own)
		rows = [rng.choice([None, {}, {"posting_kind": rng.choice(values)}]) for _ in range(rng.randrange(6))]
		assert posting["item_posting_kinds"](rows) == push.item_posting_kinds(rows)
	class SourceHttpError(Exception):
		def __init__(self, status_code, detail):
			self.status_code, self.detail = status_code, detail
	guard = {"HTTPException": SourceHttpError}
	source_definitions(root, "services/erp/internal_push_guard.py", guard,
		{"require_external_history", "require_external_user"}, {"INTERNAL_SOURCES"})
	for source in ["erp_web", "line_erp", "manual", "cowork_line", None, "ERP_WEB", "erp_web "]:
		try:
			guard["require_external_history"]({"source": source})
			old = None
		except SourceHttpError as error:
			old = error.detail
		try:
			push.require_external_history({"source": source})
			new = None
		except push.PushContractError as error:
			new = error.code
		assert old == new
	document = {**common, "normalize_posting_kind": posting["normalize"]}
	source_definitions(root, "services/cowork_line/document_preflight.py", document,
		{"_result", "_with_manual_fields", "_express_projection", "preflight_document"},
		{"_DIRECTIONS", "_PAYMENTS"}, remove_runtime_imports=True)
	document.update({
		"CoworkLineErpTargetError": SourceHttpError,
		"subject_matches": lambda *args: (True, None),
		"_express_endpoint": lambda *args: {"config": {}},
		"endpoint_with_account_choice": lambda endpoint, config: endpoint,
		"preflight_express": lambda *args, **kwargs: SimpleNamespace(disabled=False, reason=None, checks_json=lambda: []),
	})
	identity = {"user_id": "test-user", "tenant_id": "test-tenant"}
	for _ in range(3000):
		selected_direction, payment, kind = rng.choice(values), rng.choice(values), rng.choice(values)
		workspace = rng.choice([None, 7, 8, "8"])
		history = rng.choice([None, {}, {"workspace_client_id": 7}, {"workspace_client_id": 8}, {"workspace_client_id": "8"}])
		target = {"endpoint_id": "test-endpoint", "workspace_client_id": workspace, "adapter": rng.choice(["express", "mrerp", "unknown"])}
		document["require_target"] = lambda *args: target
		document["db"] = SimpleNamespace(get_ocr_history_detail=lambda *args, **kwargs: history)
		old = document["preflight_document"](identity, target, "history", selected_direction, posting_kind=kind, payment=payment)["block_reason"]
		try:
			normal_direction, normal_payment = push.require_document_choices(selected_direction, payment)
			push.require_history_workspace(history, workspace)
			push.require_adapter_choices(target["adapter"], normal_direction, posting_kind=kind, payment=normal_payment)
			new = None
		except push.PushContractError as error:
			new = error.code
		assert old == new, (target, history, selected_direction, payment, kind, old, new)
	for history in [{}, {"pages": []}, {"pages": [None]}, {"pages": [{"is_copy": True, "fields": {}}]},
		{"pages": [{"is_duplicate": True}, {"fields": {"amount": "1.00"}}]}]:
		for direction_value, payment in itertools.product(["purchase", "sales"], [None, "cash", "credit"]):
			assert document["_with_manual_fields"](history, direction_value, payment) == push.with_manual_fields(history, direction_value, payment)
	print("PASS: 34 normalization, 289 posting precedence, 1500 direction + 1500 line-kind, 8 source guards, 3000 document prechecks, 30 evidence-copy differential cases")


if __name__ == "__main__":
	if "--source" in sys.argv:
		parser = argparse.ArgumentParser(description=__doc__)
		parser.add_argument("--source", type=Path, required=True)
		verify_source(parser.parse_args().source)
	else:
		unittest.main()
