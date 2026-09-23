"""Execute AST-selected fixed legacy CPU rules; never import the old application."""
import argparse
import ast
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from pearnly_cowork.contracts import classification as native

HASHES = {
    "services/purchase/categories.py": "32240121d7c253d9af45a8b6e038ba4d2d9c302384da981656ea66ebbf04c372",
    "services/expense/category_ai.py": "da413578fb3610cc88127138087ecf8a6cc63d9a1f5de1bad2735c7f325630b9",
    "services/purchase/image_category.py": "96503acacd68842342118fda1bfe936dd6791cc1c661634f2a7561e732ba2d77",
    "services/ocr/recognize/category_tag.py": "4b6a17f7cbdd5adada1ff2c2de0f1e30aaad4599ca37ae6e2bfab4638f70246a",
}


def execute(source, functions, values=(), env=None):
    env = env if env is not None else {"re": re, "Any": Any, "Dict": Dict, "List": List, "Optional": Optional}
    nodes = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            nodes.append(node)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in values for t in node.targets):
            nodes.append(node)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in values:
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "fixed-legacy-categories", "exec"), env)
    return env


def run(sources):
    for path, checksum in HASHES.items():
        assert hashlib.sha256(sources[path].encode()).hexdigest() == checksum, path
    preset = execute(sources["services/purchase/categories.py"], [], ["_PRESET"])["_PRESET"]
    assert native._PRESET == preset
    tree = [{"id": f"p{i}", "name": label, "children": [
        {"id": f"p{i}c{j}", "name": child} for j, child in enumerate(children)]}
        for i, (label, children) in enumerate(preset)]
    rules = execute(sources["services/expense/category_ai.py"], ["_norm_name", "_alias_match",
        "_resolve_target", "_first_rule", "classify_rules", "match_user_category"],
        ["_TARGETS", "_ITEM_RULES", "_VENDOR_RULES"])
    tags = execute(sources["services/purchase/image_category.py"], ["_category_names"])
    tags["category_ai"] = SimpleNamespace(**rules)
    tags = execute(sources["services/ocr/recognize/category_tag.py"],
        ["item_descriptions", "_name", "resolve_tag", "sanitize_learned"], env=tags)
    trees = [tree, tree[:2], [], [{"id": "custom", "name": "Other", "children": [
        {"id": "coffee", "name": "Coffee"}, {"id": "personal", "name": "Custom"}]}]]
    vendors = ["", "PTT", "Cafe Amazon", "Makro", "7-Eleven", "AIS", "Bank", "ร้านอาหาร", "unmatched"]
    descriptions = ["", "coffee", "diesel", "stationery", "ค่าไฟฟ้า", "ยา", "rent", "cloud", "unmatched"]
    words = ["", "fuel", "油费", "food", "银行手续费", "Custom", "x", "no match"]
    cases = 0
    for cats, vendor, desc, word in itertools.product(trees, vendors, descriptions, words):
        fields = {"seller_name": vendor, "items": [{"name": desc}], "category": word}
        assert native.resolve_tag(fields, cats) == tags["resolve_tag"](fields, cats), fields
        assert native.sanitize_learned(word, cats) == tags["sanitize_learned"](word, cats), word
        cases += 1
    # Exercise every alias and every preset/custom name as learned input.
    aliases = [word for pair in rules["_TARGETS"].values() for group in pair for word in group]
    aliases += [p["name"] for p in tree] + [c["name"] for p in tree for c in p["children"]]
    for cats, word in itertools.product(trees, aliases):
        assert native.sanitize_learned(word, cats) == tags["sanitize_learned"](word, cats)
        cases += 1
    return {"source_sha": "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188", "cases": cases,
        "differences": 0, "presets": len(preset), "source_hashes": HASHES}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source")
    parser.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    sources = json.load(sys.stdin) if args.source_stdin else {p: (Path(args.source) / p).read_text() for p in HASHES}
    print(json.dumps(run(sources), sort_keys=True))
