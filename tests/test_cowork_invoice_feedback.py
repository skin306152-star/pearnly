"""Pure feedback tests and optional hash-bound comparison with fixed source.

Default unittest discovery needs no legacy tree. Direct execution also accepts
--source <snapshot> or --source-stdin (a JSON source-text mapping). The oracle
executes only hash-verified AST functions/constants with explicit fake inputs;
it imports no legacy app, database, environment or request context.
"""

import argparse
import ast
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import re
import sys
from types import SimpleNamespace
from typing import Optional
import unittest

from pearnly_cowork.ocr.feedback import contracts


SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr/feedback/diff.py": "51c6f00096610e47f3a4564e7c81b0bbc29ab6a9d9cd6033f04162a9406b98ff",
    "services/ocr/feedback/fewshot.py": "32008315e86ad6b14739c22b459501a41dd6c2139ee4fcf37ac302018d03a7d5",
    "services/ocr/feedback/store.py": "9077fede0f1d5f9d15fd280f73309d5bce9648b9bc8e216aeca0a3ccfd6749c5",
}
TAXES = [f"{number:013d}" for number in range(1, 7)]


def page(fields=None, **flags):
    return {"fields": fields or {}, **flags}


def example(field="seller_name", before="old", after="corrected", **extra):
    return {"field_name": field, "ai_value": before, "corrected_value": after, **extra}


class FeedbackContractsTests(unittest.TestCase):
    def test_missing_and_unchanged_fields_do_not_create_examples(self):
        self.assertEqual(contracts.compute_field_corrections(None, []), [])
        self.assertEqual(contracts.primary_seller([]), (None, None))
        self.assertEqual(contracts.compute_field_corrections([page({"vat": " 7 "})], [page({"vat": "7"})]), [])

    def test_clearing_is_ignored_and_real_zero_is_learned(self):
        result = contracts.compute_field_corrections([page({"vat": "7", "subtotal": "100"})],
                                                     [page({"vat": " ", "subtotal": 0})])
        self.assertEqual(result, [{"field_name": "subtotal", "ai_value": "100", "corrected_value": "0"}])

    def test_tracked_fields_order_and_no_money_reinterpretation(self):
        fields = {field: "new" for field in reversed(contracts.TRACKED_FIELDS)}
        fields.update({"discount": "10", "wht_amount": "3", "_internal": "private", "items": [{"name": "x"}]})
        result = contracts.compute_field_corrections([], [page(fields)])
        self.assertEqual([row["field_name"] for row in result], list(contracts.TRACKED_FIELDS))
        self.assertTrue(all(row["ai_value"] is None for row in result))
        result = contracts.compute_field_corrections([page({"total_amount": "1,000.00"})],
                                                     [page({"total_amount": Decimal("1000.00")})])
        self.assertEqual(result[0]["corrected_value"], "1000.00")
        self.assertEqual(result[0]["ai_value"], "1,000.00")

    def test_primary_page_skips_copies_and_duplicates(self):
        pages = [page({"seller_tax": "copy"}, is_copy=True),
                 page({"seller_tax": "duplicate"}, is_duplicate=True),
                 page({"seller_tax": "  tax  ", "seller_name": " ชื่อ "})]
        self.assertEqual(contracts.primary_seller(pages), ("tax", "ชื่อ"))
        self.assertEqual(contracts.primary_seller([page({"seller_tax": "first"}, is_copy=True),
                                                  page({"seller_tax": "second"}, is_duplicate=True)]), ("first", None))

    def test_empty_primary_does_not_fall_through_to_later_seller(self):
        self.assertEqual(contracts.primary_seller([page(), page({"seller_tax": "later"})]), (None, None))
        self.assertEqual(contracts.compute_field_corrections([page({"vat": "7"})],
                                                             [page(), page({"vat": "8"})]), [])

    def test_corrections_do_not_mutate_original_or_edited_evidence(self):
        first, edited = [page({"vat": "7"})], [page({"vat": "8"})]
        before = deepcopy((first, edited))
        contracts.compute_field_corrections(first, edited)[0]["corrected_value"] = "9"
        self.assertEqual((first, edited), before)

    def test_tax_selection_preserves_first_seen_order_and_four_limit(self):
        text = " ".join([TAXES[2], TAXES[0], TAXES[2], TAXES[1], TAXES[3], TAXES[4]])
        self.assertEqual(contracts.tax_ids_for_text(text), [TAXES[2], TAXES[0], TAXES[1], TAXES[3]])

    def test_tax_regex_keeps_source_unicode_and_long_digit_behavior(self):
        thai = "๑๒๓๔๕๖๗๘๙๐๑๒๓"
        self.assertEqual(contracts.tax_ids_for_text(TAXES[0] + TAXES[1] + " " + thai), TAXES[:2] + [thai])
        self.assertEqual(contracts.tax_ids_for_text("0-1234-56789-01-2"), [])

    def test_selection_uses_only_matching_authorized_groups_and_preserves_order(self):
        examples = {TAXES[0]: [example(before="same", after="first", use_count=1), example(before="x")],
                    TAXES[1]: [example(before="same", after="later", use_count=999), example(before="y")],
                    TAXES[5]: [example(before="never-match")]}
        picked = contracts.select_authorized_examples(" ".join(TAXES[:2]), examples)
        self.assertEqual([row["ai_value"] for row in picked], ["same", "x", "y"])
        self.assertEqual(picked[0]["corrected_value"], "first")
        picked[0]["corrected_value"] = "mutation"
        self.assertEqual(examples[TAXES[0]][0]["corrected_value"], "first")

    def test_raw_dedup_key_keeps_none_empty_and_whitespace_distinct(self):
        rows = [example(before=value) for value in (None, "", " old ", "old", "old")]
        picked = contracts.select_authorized_examples(TAXES[0], {TAXES[0]: rows})
        self.assertEqual([row["ai_value"] for row in picked], [None, "", " old ", "old"])

    def test_each_tax_has_twenty_row_limit_before_dedup(self):
        rows = [example(before="same") for _ in range(20)] + [example(before="row21")]
        picked = contracts.select_authorized_examples(" ".join(TAXES[:2]),
                                                       {TAXES[0]: rows, TAXES[1]: [example(before="second")]})
        self.assertEqual([row["ai_value"] for row in picked], ["same", "second"])

    def test_prompt_bytes_and_cap_are_unchanged(self):
        self.assertEqual(contracts.build_prompt_block([]), "")
        self.assertEqual(contracts.build_prompt_block([example(before=None, after="ชื่อใหม่")]),
                         "已知该商户历史人工修正(同字段优先按修正后口径输出,除非本张确实不同):\n"
                         "- seller_name: AI 曾输出「(空)」→ 正确「ชื่อใหม่」\n\n")
        block = contracts.build_prompt_block([example(before=str(index)) for index in range(13)])
        self.assertEqual(block.count("AI 曾输出"), 12)
        self.assertNotIn("「12」", block)

    def test_disabled_absent_and_malformed_examples_return_no_hint(self):
        self.assertEqual(contracts.prompt_for_text(TAXES[0], None, enabled=False), "")
        self.assertEqual(contracts.prompt_for_text("", None, enabled=True), "")
        for data in ({}, None, {TAXES[0]: [None]}, {TAXES[0]: [example(before=[])]}):
            self.assertEqual(contracts.prompt_for_text(TAXES[0], data, enabled=True), "")
        for enabled in ("false", 0, 1, None):
            with self.assertRaises(ValueError):
                contracts.prompt_for_text(TAXES[0], {}, enabled=enabled)

    def test_source_hash_mismatch_is_rejected_before_execution(self):
        with self.assertRaisesRegex(ValueError, "Source does not match"):
            source_namespace({path: "raise AssertionError('must not run')" for path in HASHES})


def source_namespace(contents):
    trees = {}
    for path, expected in HASHES.items():
        content = contents[path]
        if hashlib.sha256(content.encode()).hexdigest() != expected:
            raise ValueError(f"Source does not match {SOURCE_SHA}: {path}")
        trees[path] = ast.parse(content)
    diff_path, fewshot_path, store_path = HASHES
    fetch = next(node for node in trees[store_path].body
                 if isinstance(node, ast.FunctionDef) and node.name == "fetch_examples")
    source_limit = ast.literal_eval(fetch.args.defaults[-1])
    namespace = {"Optional": Optional, "re": re}
    nodes = [node for node in trees[diff_path].body if isinstance(node, (ast.FunctionDef, ast.Assign))]
    for node in trees[fewshot_path].body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"_MAX_EXAMPLES", "_TAX_ID"}
                                                for t in node.targets):
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {"maybe_block_for_text", "build_prompt_block"}:
            # Keep the original algorithm; replace only its local service import
            # with explicit fake context/store below. No sys.modules/import hooks.
            for body in ast.walk(node):
                if isinstance(body, ast.Try):
                    body.body = [statement for statement in body.body if not isinstance(statement, ast.ImportFrom)]
            nodes.append(node)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 "fixed-source:feedback-contracts", "exec"), namespace)
    assert namespace["TRACKED_FIELDS"] == contracts.TRACKED_FIELDS
    assert source_limit == contracts.MAX_EXAMPLES_PER_TAX
    original_builder = namespace["build_prompt_block"]
    state = {}

    def capture(examples):
        state["picked"] = deepcopy(examples)
        return original_builder(examples)

    def fetch_examples(user, tenant, tax):
        state["taxes"].append(tax)
        return state["data"].get(tax, ())[:source_limit]

    namespace.update({"build_prompt_block": capture, "is_enabled": lambda: state["enabled"],
                      "context": SimpleNamespace(current=lambda: {"user_id": "fixture", "tenant_id": "fixture"}),
                      "store": SimpleNamespace(fetch_examples=fetch_examples),
                      "logger": SimpleNamespace(warning=lambda *args: None)})
    return namespace, state


def verify_source_parity(contents):
    old, state = source_namespace(contents)
    rng = random.Random(20260921)
    values = [None, "", " ", "a", " a ", "ชื่อ", "1,000.00", "1000.00", "0", 0, False, 7]
    for _ in range(3000):
        pairs = []
        for _side in range(2):
            pages = [page({field: rng.choice(values) for field in contracts.TRACKED_FIELDS},
                          is_copy=rng.choice([False, True]), is_duplicate=rng.choice([False, True]))
                     for _page in range(rng.randrange(6))]
            pairs.append(pages)
            assert old["primary_seller"](pages) == contracts.primary_seller(pages)
        saved = deepcopy(pairs)
        assert old["compute_field_corrections"](*pairs) == contracts.compute_field_corrections(*pairs)
        assert pairs == saved

    for _ in range(3000):
        text = " ".join(rng.choice(TAXES + ["none", "๑๒๓๔๕๖๗๘๙๐๑๒๓"]) for _tax in range(rng.randrange(9)))
        data = {tax: [example(field=rng.choice(contracts.TRACKED_FIELDS), before=rng.choice(values),
                             after=rng.choice(values), use_count=rng.randrange(9))
                      for _example in range(rng.randrange(25))] for tax in TAXES}
        state.update(data=data, enabled=True, taxes=[], picked=[])
        expected = old["maybe_block_for_text"](text)
        saved = deepcopy(data)
        assert expected == contracts.prompt_for_text(text, data, enabled=True)
        assert state["taxes"] == contracts.tax_ids_for_text(text)
        assert state["picked"] == contracts.select_authorized_examples(text, data)
        assert data == saved
        state.update(enabled=False, taxes=[], picked=[])
        assert old["maybe_block_for_text"](text) == contracts.prompt_for_text(text, data, enabled=False) == ""
        assert state["taxes"] == []
    print(json.dumps({"status": "PASS", "source": SOURCE_SHA, "correction_batches": 3000,
                      "seller_selections": 6000, "prompt_batches": 3000, "disabled_prompt_batches": 3000}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source", type=Path)
    source.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FeedbackContractsTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
    if args.source or args.source_stdin:
        contents = json.load(sys.stdin) if args.source_stdin else {
            path: (args.source / path).read_text() for path in HASHES}
        verify_source_parity(contents)
