"""Differential check of the migrated source-column validators.

CLI: --source <fixed-source-root>, or --source-stdin with the fixed source text as
JSON (used to pipe the source into the bench that has pydantic). Only the named
pure definitions of the pinned source are executed, and nothing from the legacy
application, database, file, network, model or export code runs.

Both sides receive the same freshly built rows of the migrated document schema, and
are compared on the returned warnings **and** on the row state the rules may change
(a rejected reference clears the value it carried).
"""
import argparse
import ast
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import re
import sys
from typing import List, Optional, Set

from pearnly_cowork.ocr.schema.documents import (
    BankStatementDocument,
    BankStatementEntry,
    GeneralLedgerDocument,
    GLEntry,
)
from pearnly_cowork.ocr.validation import sources

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr/validators.py":
        "0c2e1d93c23a8a9a4ecfa7597a97b0599deecf655a5bfa5defc58a39ac0ba821",
}

GL_FUNCS = {"_norm_col", "_is_forbidden_amount_source", "_is_allowed_gl_amount_source",
    "_value_appears_in_description", "validate_gl_document", "_clear_amount_field"}
BANK_FUNCS = {"_is_allowed_bank_amount_source", "validate_bank_document",
    "_clear_bank_amount_field"}
CONSTANTS = {"_DESCRIPTION_COLUMN_KEYWORDS", "_VOUCHER_COLUMN_KEYWORDS",
    "_ACCOUNT_CODE_COLUMN_KEYWORDS", "_GL_DEBIT_COLUMNS", "_GL_CREDIT_COLUMNS",
    "_GL_BALANCE_COLUMNS", "_BANK_DEPOSIT_COLUMNS", "_BANK_WITHDRAWAL_COLUMNS",
    "_BANK_BALANCE_COLUMNS", "_AMOUNT_TOKEN_RE"}

GL_FIELDS = ("debit", "credit", "amount", "direction", "balance")
BANK_FIELDS = ("deposit", "withdrawal", "amount", "direction", "balance")


def load_legacy(payload):
    """Execute the pinned pure definitions of the deployed validator module."""
    path = "services/ocr/validators.py"
    data = payload[path].encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == HASHES[path], f"Source hash mismatch: {path}"
    tree = ast.parse(data.decode("utf-8"))

    def constant_name(node):
        """The column tables are annotated assignments in the deployed source."""
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return node.target.id
        return ""

    names = GL_FUNCS | BANK_FUNCS
    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or constant_name(node) in CONSTANTS]
    found = {node.name for node in nodes if isinstance(node, ast.FunctionDef)}
    assert found == names, sorted(names - found)
    namespace = {"re": re, "List": List, "Optional": Optional, "Set": Set,
        "GeneralLedgerDocument": GeneralLedgerDocument, "GLEntry": GLEntry,
        "BankStatementDocument": BankStatementDocument,
        "BankStatementEntry": BankStatementEntry, "Page": object}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace


def gl_state(entries):
    return [tuple(getattr(entry, field) for field in GL_FIELDS)
        + (entry.debit_ref is None, entry.credit_ref is None, entry.balance_ref is None)
        for entry in entries]


def bank_state(entries):
    return [tuple(getattr(entry, field) for field in BANK_FIELDS)
        + (entry.deposit_ref is None, entry.withdrawal_ref is None, entry.balance_ref is None)
        for entry in entries]


GOOD_COLUMNS = ("Debit", "เดบิต", "借方", "Credit", "เครดิต", "贷方", "Balance",
    "ยอดคงเหลือ", "余额", "", "  debit:  ", "Dr")
BAD_COLUMNS = ("Description", "รายการ", "摘要", "备注", "Voucher No.", "เลขที่เอกสาร",
    "凭证号", "Account Code", "รหัสบัญชี", "科目代码", "หมายเหตุ")
MISMATCH_COLUMNS = ("Credit", "Withdrawal", "Deposit", "Deposit ", "  CREDIT  ")
VALUES = ("6091", "6091.00", "1,250.00", "0.00", "", "10.00", "20.00", "n/a", "-",
    "5", "999999.99", "1250")
DESCRIPTIONS = ("ค่าอาหาร 6091 บาท", "โอน 1250 บาท", "ค่าอาหาร", "1,250.00 ค่าเช่า",
    "อ้างอิง 60912", "ค่าอาหาร 6091/2024", "", "no digits", "10.00 กับ 20.00")
DIRECTIONS = ("", "deposit", "withdrawal")


def gl_refs(rng):
    """A reference block whose column may be right, wrong or forbidden."""
    roll = rng.random()
    if roll < 0.25:
        column = rng.choice(GOOD_COLUMNS)
    elif roll < 0.45:
        column = rng.choice(BAD_COLUMNS)
    elif roll < 0.6:
        column = rng.choice(MISMATCH_COLUMNS)
    elif roll < 0.7:
        column = None
    else:
        column = rng.choice(GOOD_COLUMNS + BAD_COLUMNS + MISMATCH_COLUMNS)
    return {"value": rng.choice(VALUES) or None, "source_column": column} if column is not None \
        else None


def gl_specs(rng):
    for _ in range(400):
        row = {"voucher_no": rng.choice(("JV1", "6091", "", "IV-2")),
            "description": rng.choice(DESCRIPTIONS),
            "account_code": rng.choice(("4110-01", "6091", "")),
            "debit": rng.choice(VALUES), "credit": rng.choice(VALUES),
            "amount": rng.choice(VALUES), "balance": rng.choice(VALUES),
            "direction": rng.choice(DIRECTIONS)}
        for field in ("debit_ref", "credit_ref", "balance_ref"):
            ref = gl_refs(rng)
            if ref is not None:
                row[field] = ref
        yield row
    # Fixed shapes: the deployed 6091 case, every rejection kind, both sides filled,
    # a non-numeric side, direction mismatches and a clean row.
    yield {"description": "ค่าอาหาร 6091", "debit": "6091.00"}
    yield {"description": "ค่าอาหาร 6091", "voucher_no": "6091", "debit": "6091.00"}
    yield {"description": "ค่าอาหาร", "credit": "250.00", "amount": "250.00",
        "direction": "withdrawal",
        "credit_ref": {"value": 250, "source_column": "Description"}}
    yield {"balance": "100.00", "balance_ref": {"value": 100, "source_column": "备注"}}
    yield {"credit": "250.00", "credit_ref": {"value": 250, "source_column": "Debit"}}
    yield {"debit": "10.00", "credit": "20.00"}
    yield {"debit": "n/a"}
    yield {"direction": "deposit"}
    yield {"direction": "withdrawal", "debit": "5.00"}
    yield {"debit": "10.00", "amount": "99.00"}
    yield {"debit": "10.00", "amount": "ten"}
    yield {"debit": "10.00", "direction": "deposit", "amount": "10.00"}
    yield {"voucher_no": "", "debit": "10.00", "credit": "20.00"}


def bank_specs(rng):
    for _ in range(400):
        row = {"reference": rng.choice(("R1", "1250", "", "REF-9")),
            "description": rng.choice(DESCRIPTIONS),
            "deposit": rng.choice(VALUES), "withdrawal": rng.choice(VALUES),
            "amount": rng.choice(VALUES), "balance": rng.choice(VALUES),
            "direction": rng.choice(DIRECTIONS)}
        for field in ("deposit_ref", "withdrawal_ref", "balance_ref"):
            ref = gl_refs(rng)
            if ref is not None:
                row[field] = ref
        yield row
    yield {"description": "โอน 1250 บาท", "deposit": "1250.00"}
    yield {"reference": "1250", "description": "โอน 1250 บาท", "deposit": "1250.00"}
    yield {"description": "ถอนเงิน", "withdrawal": "500.00", "amount": "500.00",
        "direction": "withdrawal",
        "withdrawal_ref": {"value": 500, "source_column": "备注"}}
    yield {"deposit": "10.00", "deposit_ref": {"value": 10, "source_column": "Withdrawal"}}
    yield {"deposit": "10.00", "withdrawal": "20.00"}
    yield {"deposit": "n/a"}
    yield {"reference": "", "deposit": "10.00", "withdrawal": "20.00"}


def compare_gl(legacy, spec, counts):
    expected_entry = GLEntry(**spec)
    produced_entry = GLEntry(**spec)
    expected = legacy["validate_gl_document"](GeneralLedgerDocument(entries=[expected_entry]),
        None)
    produced = sources.validate_gl_document(GeneralLedgerDocument(entries=[produced_entry]), None)
    assert expected == produced, (spec, expected, produced)
    assert gl_state([expected_entry]) == gl_state([produced_entry]), (spec, gl_state([expected_entry]),
        gl_state([produced_entry]))
    counts["gl_batches"] += 1
    for warning in produced:
        counts["gl_warnings"] += 1
        counts["gl_rejected"] += 1 if "— rejected" in warning else 0
        counts["gl_column"] += 1 if "is not a" in warning else 0
        counts["gl_leak"] += 1 if "possibly mis-sourced" in warning else 0
        counts["gl_exclusive"] += 1 if "must have exactly one" in warning else 0
        counts["gl_direction"] += 1 if "direction=" in warning else 0
        counts["gl_amount"] += 1 if "doesn't match" in warning else 0
        counts["gl_nonnumeric"] += 1 if "not numeric" in warning else 0
    counts["gl_cleared"] += 1 if gl_state([produced_entry]) != gl_state([GLEntry(**spec)]) else 0


def compare_bank(legacy, spec, counts):
    expected_entry = BankStatementEntry(**spec)
    produced_entry = BankStatementEntry(**spec)
    expected = legacy["validate_bank_document"](
        BankStatementDocument(entries=[expected_entry]), None)
    produced = sources.validate_bank_document(
        BankStatementDocument(entries=[produced_entry]), None)
    assert expected == produced, (spec, expected, produced)
    assert bank_state([expected_entry]) == bank_state([produced_entry]), (
        spec, bank_state([expected_entry]), bank_state([produced_entry]))
    counts["bank_batches"] += 1
    for warning in produced:
        counts["bank_warnings"] += 1
        counts["bank_rejected"] += 1 if "— rejected" in warning else 0
        counts["bank_column"] += 1 if "is not a" in warning else 0
        counts["bank_leak"] += 1 if "possibly mis-sourced" in warning else 0
        counts["bank_exclusive"] += 1 if "non-zero" in warning else 0
    counts["bank_cleared"] += 1 if bank_state([produced_entry]) != bank_state(
        [BankStatementEntry(**spec)]) else 0


def run(root=None, source_text=None):
    payload = (source_text if source_text is not None
        else {path: (Path(root) / path).read_text() for path in HASHES})
    legacy = load_legacy(payload)
    # The column tables and helpers must be the deployed ones, not a re-typed list.
    for name in sorted(CONSTANTS - {"_AMOUNT_TOKEN_RE"}):
        assert legacy[name] == getattr(sources, name), name
    assert legacy["_norm_col"]("  DESCRIPTION:  ") == sources._norm_col("  DESCRIPTION:  ")
    for column in GOOD_COLUMNS + BAD_COLUMNS + MISMATCH_COLUMNS:
        assert (legacy["_is_forbidden_amount_source"](column)
            == sources._is_forbidden_amount_source(column)), column
        for kind in ("debit", "credit", "balance"):
            assert (legacy["_is_allowed_gl_amount_source"](column, kind)
                == sources._is_allowed_gl_amount_source(column, kind)), (column, kind)
            assert (legacy["_is_allowed_bank_amount_source"](column, kind)
                == sources._is_allowed_bank_amount_source(column, kind)), (column, kind)
    for value in VALUES:
        for description in DESCRIPTIONS:
            assert (legacy["_value_appears_in_description"](value, description)
                == sources._value_appears_in_description(value, description)), (value, description)

    rng = random.Random(20260922)
    counts = {key: 0 for key in ("gl_batches", "gl_warnings", "gl_rejected", "gl_column",
        "gl_leak", "gl_exclusive", "gl_direction", "gl_amount", "gl_nonnumeric", "gl_cleared",
        "bank_batches", "bank_warnings", "bank_rejected", "bank_column", "bank_leak",
        "bank_exclusive", "bank_cleared")}
    for spec in gl_specs(rng):
        compare_gl(legacy, spec, counts)
    for spec in bank_specs(rng):
        compare_bank(legacy, spec, counts)
    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    if (counts["gl_batches"] < 400 or counts["bank_batches"] < 400
            or counts["gl_rejected"] < 20 or counts["gl_column"] < 20
            or counts["gl_leak"] < 20 or counts["gl_exclusive"] < 5
            or counts["gl_direction"] < 5 or counts["gl_amount"] < 5
            or counts["gl_nonnumeric"] < 1 or counts["gl_cleared"] < 20
            or counts["bank_rejected"] < 20 or counts["bank_column"] < 20
            or counts["bank_leak"] < 20 or counts["bank_exclusive"] < 5
            or counts["bank_cleared"] < 20):
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
