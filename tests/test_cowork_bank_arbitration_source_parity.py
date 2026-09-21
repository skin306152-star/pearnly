"""Differential check of the migrated two-method divergence guard.

CLI: --source <fixed-source-root>, or --source-stdin with the fixed source as JSON.
The pinned module is pure (no IO, no model), so its functions are executed as they
are and compared with the ported ones over a broad grid plus randomised inputs.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import random
import sys

from pearnly_cowork.reconciliation import bank_arbitration

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
PATH = "services/recon/bank_arbitration_guard.py"
SHA256 = "5bb3eb437203b9229c57986c8ce34c342ea907535305fcb2bf3139150098eb64"
FUNCS = {"_diverges", "divergence_reasons"}
CONSTS = {"_REL_THRESHOLD", "_ABS_FLOOR"}


def load_legacy(source_text):
    data = source_text.encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == SHA256, "Source hash mismatch"
    tree = ast.parse(source_text)

    def assigned(node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
        return ""

    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in FUNCS)
        or assigned(node) in CONSTS]
    found = {node.name for node in nodes if isinstance(node, ast.FunctionDef)}
    assert found == FUNCS, sorted(FUNCS - found)
    namespace = {"__name__": "legacy", "Optional": __import__("typing").Optional,
        "List": __import__("typing").List}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), PATH, "exec"), namespace)
    return namespace


def run(root=None, source_text=None):
    legacy = load_legacy(source_text if source_text is not None
        else (Path(root) / PATH).read_text())
    assert legacy["_REL_THRESHOLD"] == bank_arbitration.REL_THRESHOLD
    assert legacy["_ABS_FLOOR"] == bank_arbitration.ABS_FLOOR

    counts = {"pairs": 0, "diverging_pairs": 0, "batches": 0, "reasons": 0,
        "both_labels": 0, "no_reason": 0, "one_side_empty": 0, "threshold_override": 0}
    grid = [0.0, 0.5, 1.0, 1.5, -1.5, 100.0, -100.0, 1000.0, 39749.85, -7409714.58,
        1e9, -1e9, 0.01, 5.0, 5.2, 5.4]
    for first in grid:
        for second in grid:
            expected = legacy["_diverges"](first, second, legacy["_REL_THRESHOLD"],
                legacy["_ABS_FLOOR"])
            produced = bank_arbitration.diverges(first, second)
            assert expected == produced, (first, second, expected, produced)
            counts["pairs"] += 1
            counts["diverging_pairs"] += 1 if produced else 0

    rng = random.Random(20260922)
    amounts = (None, 0.0, 0.0, 1.0, 0.9, 100.0, 3460000.0, -1140000.0, 39749.85,
        -39.15, 8_000_000.0, -0.5)
    for _ in range(240):
        arguments = (
            rng.choice(amounts), rng.choice(amounts), rng.choice((0, 1, 3, 12, 274)),
            rng.choice(amounts), rng.choice(amounts), rng.choice((0, 1, 4, 212)),
        )
        for overrides in ({}, {"rel": 0.5}, {"abs_floor": 1e5}):
            expected = legacy["divergence_reasons"](*arguments, **overrides)
            produced = bank_arbitration.divergence_reasons(*arguments, **overrides)
            assert expected == produced, (arguments, overrides, expected, produced)
            counts["batches"] += 1
            if overrides:
                counts["threshold_override"] += 1
        expected = legacy["divergence_reasons"](*arguments)
        counts["reasons"] += len(expected)
        counts["both_labels"] += 1 if len(expected) == 2 else 0
        counts["no_reason"] += 1 if not expected else 0
        counts["one_side_empty"] += 1 if arguments[2] <= 0 or arguments[5] <= 0 else 0

    # The deployed case: a statement whose two methods agree on the opening but
    # disagree in sign on the closing.
    assert bank_arbitration.divergence_reasons(0.0, 3460000.0, 274, 0.0, -1140000.0, 212) == [
        "期末两法分歧:免费解析 3460000.0 vs Gemini -1140000.0 — 需人工核票面"]
    assert bank_arbitration.divergence_reasons(None, None, 3, 1.0, 2.0, 3) == []
    assert bank_arbitration.divergence_reasons(0.0, 0.0, 0, 0.0, 0.0, 0) == []

    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    floors = {"pairs": 250, "diverging_pairs": 200, "batches": 700, "reasons": 200,
        "both_labels": 60, "no_reason": 80, "one_side_empty": 80, "threshold_override": 480}
    for name, floor in floors.items():
        if counts[name] < floor:
            raise SystemExit(f"fixture coverage below the floor: {name}={counts[name]} < {floor}")
    raise SystemExit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path)
    group.add_argument("--source-stdin", action="store_true")
    arguments = parser.parse_args()
    if arguments.source_stdin:
        run(None, json.load(sys.stdin)["services/recon/bank_arbitration_guard.py"])
    else:
        run(arguments.source)
