"""Differential check of the migrated balance-chain repairs.

CLI: --source <fixed-source-root>, or --source-stdin with the fixed source text as
JSON (used to pipe the source into the bench that has pydantic). Only the named
pure definitions of the pinned sources are executed, and the legacy logger is a
no-op stand-in, so no legacy application, database, file, network, model or export
code runs. Both sides receive freshly built rows of the migrated document schema
and are compared on the repair outcome, the returned reasons or warnings, and every
field the repair is allowed to touch.
"""
import argparse
import ast
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any, List, Optional, Tuple

from pearnly_cowork.ocr.schema.documents import (
    BankStatementDocument,
    BankStatementEntry,
    GeneralLedgerDocument,
    GLEntry,
)
from pearnly_cowork.ocr.validation import chains

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr/bank_chain_gate.py":
        "2324467689249c1dcb6562fac3df03b2be2e2d8e03a43af14c0dff16d8e0408e",
    "services/ocr/gl_balance_chain.py":
        "e59f82bd0785114aa52f3c60c5ea5c4c8565f3225737634dddae1a2f523481dd",
}

BANK_FIELDS = ("deposit", "withdrawal", "amount", "direction", "balance",
    "chain_repaired", "chain_amount_imputed", "review_required")
GL_FIELDS = ("debit", "credit", "amount", "direction", "balance")


def extract(payload, path, names, namespace, assignments=()):
    """Execute only the named top-level definitions of a hash-pinned source file."""
    data = payload[path].encode("utf-8")
    if hashlib.sha256(data).hexdigest() != HASHES[path]:
        raise AssertionError(f"Source does not match {SOURCE_SHA}: {path}")
    tree = ast.parse(data)
    names, assignments = set(names), set(assignments)
    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id in assignments)]
    if {node.name for node in nodes if isinstance(node, ast.FunctionDef)} != names:
        raise AssertionError(f"Missing source definitions: {path}")
    if {node.targets[0].id for node in nodes if isinstance(node, ast.Assign)} != assignments:
        raise AssertionError(f"Missing source constants: {path}")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace


def _logger():
    module = __import__("types").ModuleType("legacy_logger")
    module.info = lambda *arguments, **keywords: None
    module.warning = lambda *arguments, **keywords: None
    module.error = lambda *arguments, **keywords: None
    return module


def load_legacy(payload):
    common = {"Decimal": Decimal, "InvalidOperation": InvalidOperation, "Any": Any,
        "List": List, "Optional": Optional, "Tuple": Tuple, "logger": _logger()}
    bank = dict(common)
    extract(payload, "services/ocr/bank_chain_gate.py",
        {"_dec", "_side_amount", "_flip_entry", "_impute_chain_amount",
         "repair_bank_chain", "_audit_warnings"}, bank,
        assignments={"_TOL", "_IMPUTE_BAND"})
    gl = dict(common)
    extract(payload, "services/ocr/gl_balance_chain.py",
        {"_dec", "_fmt", "_set_side", "repair_gl_document", "_reconcile_printed_totals",
         "_derive_opening"}, gl, assignments={"_TOL"})
    return bank, gl


def bank_state(entries):
    return [tuple(getattr(entry, field) for field in BANK_FIELDS) for entry in entries]


def gl_state(doc):
    return ([tuple(getattr(entry, field) for field in GL_FIELDS)
        + (tuple(sorted((entry.raw_row_data or {}).items())),) for entry in doc.entries],
        doc.opening_balance, doc.closing_balance)


def amounts(value):
    return f"{value:.2f}"


def bank_specs(rng):
    """Random statements built from a consistent truth, then perturbed."""
    movements = [Decimal("1200.00"), Decimal("300.00"), Decimal("44.67"), Decimal("1780.00"),
        Decimal("0.01"), Decimal("99999.99"), Decimal("12.34")]
    for _ in range(260):
        opening = rng.choice([Decimal("0.00"), Decimal("1000.00"), Decimal("25000.50")])
        balance = opening
        rows = []
        for _ in range(rng.randrange(1, 7)):
            side = rng.choice(["deposit", "withdrawal", "carried", "both", "empty"])
            amount = rng.choice(movements)
            if side == "deposit":
                balance += amount
            elif side == "withdrawal":
                balance -= amount
            read = {"deposit": "", "withdrawal": "", "balance": amounts(balance),
                "direction": side if side in ("deposit", "withdrawal") else ""}
            if side == "deposit":
                read["deposit"] = amounts(amount)
            elif side == "withdrawal":
                read["withdrawal"] = amounts(amount)
            roll = rng.random()
            if roll < 0.10:  # the columns are swapped
                read["deposit"], read["withdrawal"] = read["withdrawal"], read["deposit"]
                read["direction"] = {
                    "deposit": "withdrawal", "withdrawal": "deposit"}.get(
                    read["direction"], read["direction"])
            elif roll < 0.20:  # a single side is read short, inside the band
                for field in ("deposit", "withdrawal"):
                    if read[field]:
                        read[field] = amounts(Decimal(read[field]) * Decimal("0.9"))
            elif roll < 0.26:  # a single side is read very wrong
                for field in ("deposit", "withdrawal"):
                    if read[field]:
                        read[field] = amounts(Decimal(read[field]) * Decimal("7"))
            elif roll < 0.30:  # a value that cannot be parsed
                for field in ("deposit", "withdrawal"):
                    if read[field]:
                        read[field] = "n/a"
            elif roll < 0.34:  # the balance is missing
                read["balance"] = ""
            elif roll < 0.37:  # a digit is added to the balance
                read["balance"] = amounts(balance + Decimal("10000.00"))
            rows.append(read)
        opening_text = amounts(opening) if rng.random() >= 0.08 else ""
        yield {"rows": rows, "opening": opening_text}
    # Fixed shapes: a swapped pair, a proven one-sided swap, an inferred amount on
    # each side, an out-of-band misread, two filled sides, a jump and equal sides.
    fixed = (
        ("1000.00", [{"deposit": "300.00", "withdrawal": "1200.00", "balance": "1900.00"}]),
        ("1000.00", [{"deposit": "", "withdrawal": "1200.00", "balance": "2200.00"}]),
        ("1000.00", [{"deposit": "1100.00", "withdrawal": "", "balance": "2200.00"}]),
        ("1000.00", [{"deposit": "", "withdrawal": "90.00", "balance": "900.00"}]),
        ("1000.00", [{"deposit": "90.00", "withdrawal": "", "balance": "900.00"}]),
        ("1000.00", [{"deposit": "3.00", "withdrawal": "", "balance": "1600.00"}]),
        ("1000.00", [{"deposit": "500.00", "withdrawal": "400.00", "balance": "2000.00"}]),
        ("1000.00", [{"deposit": "300.00", "withdrawal": "300.00", "balance": "1500.00"}]),
        ("1000.00", [{"deposit": "", "withdrawal": "", "balance": "1500.00"}]),
        ("1000.00", [{"deposit": "", "withdrawal": "", "balance": "1000.00"},
            {"deposit": "50.00", "withdrawal": "", "balance": "1050.00"}]),
        ("", [{"deposit": "50.00", "withdrawal": "", "balance": "1050.00"}]),
        ("1000.00", []),
    )
    for opening, rows in fixed:
        yield {"rows": rows, "opening": opening}


def gl_specs(rng):
    """Random ledgers built from a consistent truth, then perturbed."""
    movements = [Decimal("1000.00"), Decimal("1070.00"), Decimal("30.00"), Decimal("0.05"),
        Decimal("5000.00")]
    for _ in range(260):
        opening = rng.choice([Decimal("0.00"), Decimal("1000.00"), Decimal("99999.99")])
        balance = opening
        rows, total_debit, total_credit = [], Decimal(0), Decimal(0)
        for _ in range(rng.randrange(1, 6)):
            amount = rng.choice(movements)
            is_debit = rng.random() < 0.5
            balance = balance + amount if is_debit else balance - amount
            total_debit += amount if is_debit else Decimal(0)
            total_credit += Decimal(0) if is_debit else amount
            read = {"debit": amounts(amount) if is_debit else "",
                "credit": "" if is_debit else amounts(amount),
                "balance": amounts(balance),
                "direction": "deposit" if is_debit else "withdrawal"}
            roll = rng.random()
            if roll < 0.15:  # the columns are swapped
                read["debit"], read["credit"] = read["credit"], read["debit"]
                read["direction"] = "withdrawal" if is_debit else "deposit"
            elif roll < 0.22:  # the amount is misread
                if read["debit"]:
                    read["debit"] = amounts(amount * Decimal("2"))
                else:
                    read["credit"] = amounts(amount * Decimal("2"))
            elif roll < 0.26:  # the balance is missing on this row
                read["balance"] = ""
            elif roll < 0.30:  # a digit is added to the balance
                read["balance"] = amounts(balance + Decimal("100.00"))
            rows.append(read)
        if rng.random() < 0.35:  # a printed footer, sometimes off by a little
            drift = rng.choice([Decimal(0), Decimal("0.02"), Decimal("50.00")])
            printed = {"total_debit": amounts(total_debit + drift),
                "total_credit": amounts(total_credit)}
        else:
            printed = {"total_debit": "", "total_credit": ""}
        # A page that does not print its opening balance is common (the stacked
        # layouts this chain exists for), and it is the only way the derivation
        # runs, so it is generated often.
        opening_text = amounts(opening) if rng.random() >= 0.35 else ""
        yield {"rows": rows, "opening": opening_text, "backfill": rng.random() < 0.5,
            "printed": printed}
    fixed = (
        ([{"debit": "", "credit": "1000.00", "balance": "2000.00"}], "1000.00", {}),
        ([{"debit": "", "credit": "1000.00", "balance": "2000.00"}], "",
            {"total_debit": "1000.00", "total_credit": "0.00"}),
        ([{"debit": "", "credit": "1000.00", "balance": "2000.00"},
            {"debit": "", "credit": "50.00", "balance": "2050.00"}], "",
            {"total_debit": "1.00", "total_credit": "2.00"}),
        ([{"debit": "1000.00", "credit": "", "balance": "2000.00"}], "", {}),
        ([{"debit": "", "credit": "1000.00", "balance": ""},
            {"debit": "300.00", "credit": "", "balance": "2300.00"}], "1000.00", {}),
        ([], "1000.00", {}),
    )
    for rows, opening, printed in fixed:
        yield {"rows": rows, "opening": opening, "backfill": False, "printed": printed}


def compare_bank(legacy, spec, counts):
    def build():
        return BankStatementDocument(opening_balance=spec["opening"], closing_balance="",
            entries=[BankStatementEntry(**row) for row in spec["rows"]])

    expected_document = build()
    produced_document = deepcopy(expected_document)
    expected_out, expected_reasons = legacy["repair_bank_chain"](
        expected_document.entries, expected_document.opening_balance)
    produced_out, produced_reasons = chains.repair_bank_chain(
        produced_document.entries, produced_document.opening_balance)
    assert bank_state(expected_out) == bank_state(produced_out), (
        spec, bank_state(expected_out), bank_state(produced_out))
    assert expected_reasons == produced_reasons, (spec, expected_reasons, produced_reasons)
    expected_warnings = legacy["_audit_warnings"](expected_out, expected_reasons)
    produced_warnings = chains.bank_chain_warnings(produced_out, produced_reasons)
    assert expected_warnings == produced_warnings, (spec, expected_warnings, produced_warnings)
    counts["bank_batches"] += 1
    counts["bank_breaks"] += 1 if expected_reasons else 0
    counts["bank_flips"] += sum(1 for entry in produced_out if entry.chain_repaired)
    counts["bank_imputed"] += sum(1 for entry in produced_out if entry.chain_amount_imputed)
    counts["bank_reviewed"] += sum(1 for entry in produced_out if entry.review_required)
    counts["bank_warnings"] += len(produced_warnings)


def compare_gl(legacy, spec, counts):
    def build():
        return GeneralLedgerDocument(opening_balance=spec["opening"],
            closing_balance="" if spec["backfill"] else "0.00",
            entries=[GLEntry(**row) for row in spec["rows"]], **spec["printed"])

    expected_document = build()
    produced_document = deepcopy(expected_document)
    expected_warnings = legacy["repair_gl_document"](expected_document)
    produced_warnings = chains.repair_gl_document(produced_document)
    assert gl_state(expected_document) == gl_state(produced_document), (
        spec, gl_state(expected_document), gl_state(produced_document))
    assert expected_warnings == produced_warnings, (spec, expected_warnings, produced_warnings)
    counts["gl_batches"] += 1
    counts["gl_warnings"] += len(produced_warnings)
    counts["gl_autocorrected"] += sum(1 for entry in produced_document.entries
        if (entry.raw_row_data or {}).get("direction_autocorrected"))
    counts["gl_opening_derived"] += 1 if not spec["opening"] and (
        produced_document.opening_balance or "").strip() else 0
    counts["gl_closing_backfilled"] += 1 if spec["backfill"] and (
        produced_document.closing_balance or "").strip() else 0


def run(root=None, source_text=None):
    payload = (source_text if source_text is not None
        else {path: (Path(root) / path).read_text() for path in HASHES})
    bank_legacy, gl_legacy = load_legacy(payload)
    assert bank_legacy["_TOL"] == chains.BANK_TOL
    assert bank_legacy["_IMPUTE_BAND"] == chains.IMPUTE_BAND
    assert gl_legacy["_TOL"] == chains.GL_TOL
    rng = random.Random(20260921)
    counts = {key: 0 for key in ("bank_batches", "bank_breaks", "bank_flips", "bank_imputed",
        "bank_reviewed", "bank_warnings", "gl_batches", "gl_warnings", "gl_autocorrected",
        "gl_opening_derived", "gl_closing_backfilled")}
    for spec in bank_specs(rng):
        compare_bank(bank_legacy, spec, counts)
    for spec in gl_specs(rng):
        compare_gl(gl_legacy, spec, counts)
    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    if (counts["bank_batches"] < 250 or counts["gl_batches"] < 250
            or counts["bank_flips"] < 20 or counts["bank_imputed"] < 20
            or counts["bank_reviewed"] < 20 or counts["bank_breaks"] < 20
            or counts["gl_autocorrected"] < 20 or counts["gl_warnings"] < 20
            or counts["gl_opening_derived"] < 20):
        raise SystemExit(f"fixture coverage below the floor: {counts}")
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
