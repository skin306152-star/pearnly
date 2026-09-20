"""Hash-bound offline comparison against the deployed pure grouping functions.

Use --source for the fixed source directory, or --source-stdin with a JSON map
of the three source paths to source texts. Only verified pure function/constant
AST nodes run; no legacy application, provider, framework or IO module imports.
"""
import argparse
import ast
from copy import deepcopy
from decimal import Decimal
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import random
import re
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from pearnly_cowork.ocr import grouping, money

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr/invoice_grouper.py": "5905242c620c13306d16aa2e49cc47e763b83aadf7408a23800e4d0f60835b6a",
    "services/ocr/money.py": "48aacdd033dbf9bc5d77f8bcc64835c96394ba4114e3f9684ea9d615249d105c",
    "services/ocr/schemas_invoice.py": "c3c9f848cb587c725b519f7af7defdfc7fab2599b8f9beced482d04531f8c793",
}


def source_namespace(contents):
    trees = {}
    for path, expected in HASHES.items():
        content = contents[path]
        if hashlib.sha256(content.encode()).hexdigest() != expected:
            raise ValueError(f"Source does not match {SOURCE_SHA}: {path}")
        trees[path] = ast.parse(content)
    invoice = next(node for node in trees["services/ocr/schemas_invoice.py"].body
                   if isinstance(node, ast.ClassDef) and node.name == "ThaiInvoice")
    # Read field names from the independently hash-verified source model, rather
    # than feeding the new model's field list back to the legacy oracle.
    fields = [node.target.id for node in invoice.body if isinstance(node, ast.AnnAssign)
              and isinstance(node.target, ast.Name)]
    namespace = {"re": re, "SequenceMatcher": SequenceMatcher, "Any": Any, "Dict": Dict,
                 "List": List, "Optional": Optional, "ThaiInvoice": SimpleNamespace(model_fields=dict.fromkeys(fields))}
    for path in ("services/ocr/money.py", "services/ocr/invoice_grouper.py"):
        nodes = [node for node in trees[path].body if isinstance(node, (ast.FunctionDef, ast.Assign))]
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        exec(compile(module, f"fixed-source:{path}", "exec"), namespace)
    assert tuple(namespace["_SCALAR_FIELDS"]) == grouping._SCALAR_FIELDS, "Invoice scalar fields drifted"
    return namespace


def page(index, **fields):
    return {"page_index": index, "fields": fields}


def item(name="Rice", qty="1", price="10"):
    return {"name": name, "qty": qty, "price": price, "subtotal": "10", "source_note": "retain"}


def compare_groups(namespace, value):
    old = namespace["group_pages_to_invoices"](deepcopy(value))
    original = deepcopy(value)
    new = grouping.group_invoices(value)
    assert value == original, "New grouping changed caller evidence"
    assert old == new, (value, old, new)


def verify(contents):
    old = source_namespace(contents)
    fixtures = [[],
        [{"fields": {"notes": "lead"}}, {"fields": {"notes": "tail"}}],
        [page(1, invoice_number=" A "), page(2, invoice_number="B"), page(3, invoice_number="A")],
        [page(1, invoice_number="A", total_amount="107"), page(2, total_amount="214", subtotal="200")],
        [page(1, invoice_number="A", total_amount="107"), page(2, total_amount="฿107.00", vat="7")],
        [page(1, notes="cover"), page(2, invoice_number="A"), page(3, invoice_number="B")],
        [page(1, items=[item("Prefix")]), page(2, total_amount="107"), page(3, items=[item("Tail")])],
        [page(4, invoice_number="A", total_amount="107"), page(4, invoice_number="B", total_amount="214")],
        [page(1, invoice_number="A", items=[item("ราคา ส่ง")]),
         page(2, invoice_number="A", items=[item("ราคาส่ง", "1 ก้อน", "฿10.00")])],
        [page(1, invoice_number="A", items=[item("Chocolate cookie"), item("Pearnly rice 1kg")]),
         page(2, invoice_number="A", items=[item("Chocolate cookie"), item("Pearnly rice 1k9")])],
    ]
    for fixture in fixtures:
        compare_groups(old, fixture)
    rng = random.Random(20260921)
    amounts = [None, "", "0", "-1.25", "100", "107.00", "฿ 1,070.00", "214", "1.2.3", "unknown"]
    prices = [None, "", "0", "10", "10.00", "฿10.00", "10.01", "-1.25", "unknown"]
    for _ in range(3000):
        pages = []
        for index in range(rng.randint(0, 12)):
            fields = {"invoice_number": rng.choice([None, "", "A", "B", " A ", "a"]),
                      "total_amount": rng.choice(amounts), "subtotal": rng.choice(amounts),
                      "vat": rng.choice(amounts), "seller_name": rng.choice([None, "", "Seller A", "Seller B"]),
                      "currency": rng.choice([None, "", "THB", "USD"]), "discount": rng.choice(amounts),
                      "notes": rng.choice([None, "", "note A", "note B"]),
                      "direction": rng.choice([None, "", "purchase", "sales"]),
                      "history_id": rng.choice([None, "", "history-1", "history-2"]),
                      "source_refs": {"total_amount": {"physical_page": index + 1}},
                      "items": [item(rng.choice(["Rice", "Rice A", "Rice B", "ราคา ส่ง", "ราคาส่ง", "Tea"]),
                                     rng.choice(["1", "1 ก้อน", "1.0", "2", "1,000"]), rng.choice(prices))
                                for _ in range(rng.randint(0, 5))]}
            entry = {"fields": fields, "is_copy": rng.choice([False, True]), "_multi_invoice_sibling": False}
            if rng.choice([True, False]):
                entry["page_index"] = index // 2 + 1  # Same physical page can hold two invoices.
            pages.append(entry)
        compare_groups(old, pages)

    money_cases = 0
    for cents in range(-20000, 20001, 7):
        amount = Decimal(cents) / 100
        for value in (str(amount), f"฿{amount:,.2f}"):
            assert Decimal(str(old["normalize_money"](value))) == money.normalize_money(value), value
            assert old["_norm_price"](value) == grouping._norm_price(value), value
            money_cases += 1
    for value in (None, "", "-", ".", "-.", "unreadable", "1.2.3", "--2", True, "NaN", "Infinity"):
        assert old["normalize_money"](value) is money.normalize_money(value) is None, value
        money_cases += 1
    for _ in range(3000):
        value = "-".join(str(rng.randint(0, 9)) for _ in range(rng.randint(0, 16)))
        assert old["normalize_id"](value) == money.normalize_id(value)
        assert old["valid_thai_tax_id"](value) == money.valid_thai_tax_id(value)

    # Explicit semantic differences, never tolerated by the normal parity loop.
    rounding = [("2.675", "2.67", "2.68"), ("2.665", "2.67", "2.66"),
                ("1.015", "1.01", "1.02"), ("-2.675", "-2.67", "-2.68"),
                ("-0.005", "-0.01", "-0.00")]
    for value, previous, exact in rounding:
        assert old["_norm_price"](value) == previous, (value, "source boundary changed")
        assert grouping._norm_price(value) == exact, (value, "new boundary changed")
    for second, previous_count, exact_count in [("2.67", 1, 2), ("2.68", 2, 1)]:
        source = [page(1, invoice_number="A", items=[item(price="2.675")]),
                  page(2, invoice_number="A", items=[item(price=second)])]
        assert len(old["group_pages_to_invoices"](deepcopy(source))[0]["invoice_fields"]["items"]) == previous_count
        assert len(grouping.group_invoices(source)[0]["invoice_fields"]["items"]) == exact_count
    boundaries = [
        ("binary_integer_precision", "9007199254740992", "9007199254740993"),
        ("float_overflow", "9" * 400, "8" * 400),
        ("float_underflow", "0", "0." + "0" * 399 + "1"),
    ]
    for label, first, second in boundaries:
        source = [page(1, invoice_number="A", total_amount=first),
                  page(2, total_amount=second, subtotal="1")]
        assert len(old["group_pages_to_invoices"](deepcopy(source))) == 1, label
        assert len(grouping.group_invoices(source)) == 2, label
        assert money.normalize_money(first) != money.normalize_money(second), label
    assert old["_norm_price"]("9" * 400) == "inf"
    assert grouping._norm_price("9" * 400) == "9" * 400 + ".00"
    print(json.dumps({"status": "PASS", "source": SOURCE_SHA, "fixed_group_cases": len(fixtures),
        "random_group_batches": 3000, "ordinary_money_cases": money_cases, "tax_cases": 3000,
        "explicit_differences": {"half_cent_price_keys": rounding, "item_count_cases": 2,
                                 "invoice_count_cases": [entry[0] for entry in boundaries], "overflow_price_key": 1}},
        ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    contents = json.load(sys.stdin) if args.source_stdin else {path: (args.source / path).read_text() for path in HASHES}
    verify(contents)
