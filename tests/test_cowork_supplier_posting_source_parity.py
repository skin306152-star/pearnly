"""Differential check of the migrated supplier posting rules (batch 49).

CLI: --source <fixed-source-root>, or --source-stdin with the fixed sources as JSON.

Three pinned modules are compared against ``masters/supplier_posting.py``:

* ``services/ocr_history/posting_manual.py`` — the two value domains;
* ``services/purchase/field_clean.py`` — ``clean_tax_id``, the profile anchor;
* ``services/purchase/supplier_posting.py`` — ``upsert_profile``'s statement is captured from a
  stub cursor, translated (``%s``→``?``, ``now()``→``CURRENT_TIMESTAMP``) and executed in SQLite,
  so both sides really write a row and the final (payment, item type, source) is compared.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List, Optional

from pearnly_cowork.masters import supplier_posting

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr_history/posting_manual.py": "35883a492ca1f9dd14d69a47f5456ae5ea8e8f2bbf2ac4d346a86b8e392e27c5",
    "services/purchase/field_clean.py": "43912e65fcce3afa53fa4f7cdfd2d57afc02ff5556c5e17a6d76c2d030bdbed8",
    "services/purchase/supplier_posting.py": "a121f3cacbacaaf1c043ff7b8bd464ec6f0e1e4e7bf333d916b7d3c066bd4b58",
}
DOMAIN_CONSTS = {"_PAYMENT_VALUES", "_ITEM_TYPE_VALUES"}
TABLE = "supplier_posting_profiles"
DDL = """CREATE TABLE supplier_posting_profiles (
    tenant_id TEXT, workspace_client_id INTEGER, seller_tax_id TEXT,
    default_payment TEXT, default_item_type TEXT, source TEXT, updated_at TEXT)"""

TAX_VALUES = ["0-1055-48103-91-2", "0105561000013", "0105561000013 บริษัท", "13", "13/06/26",
    "", None, "  010556100001.3  ", 1234567890123, "01055610000134"]
INCOMINGS = [None, {}, {"default_payment": "credit"}, {"default_item_type": "expense"},
    {"default_payment": "cash", "default_item_type": "goods"},
    {"default_payment": "", "default_item_type": ""},
    {"default_payment": None, "default_item_type": "expense"},
    {"default_payment": "food"}]
EXISTINGS = [None,
    {"source": "correction", "default_payment": "cash", "default_item_type": "goods"},
    {"source": "user_rule", "default_payment": "credit", "default_item_type": "expense"},
    {"source": "user_rule", "default_payment": "", "default_item_type": ""},
    {"source": "correction", "default_payment": "", "default_item_type": None}]
SOURCES = ["user_rule", "correction"]
TENANT, WORKSPACE, TAX = "tenant-parity", 7, "0105548103912"


class Capture:
    """Stands in for the injected cursor: remembers the statement the DAL builds."""

    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    @property
    def rowcount(self):
        return 0


def load_legacy(payload):
    manual_text = payload["services/ocr_history/posting_manual.py"]
    assert hashlib.sha256(manual_text.encode("utf-8")).hexdigest() == \
        HASHES["services/ocr_history/posting_manual.py"]
    tree = ast.parse(manual_text)

    def assigned(node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return node.target.id
        return ""

    nodes = [node for node in tree.body if assigned(node) in DOMAIN_CONSTS]
    assert {assigned(node) for node in nodes} == DOMAIN_CONSTS
    domains = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "posting_manual.py", "exec"), domains)

    clean_text = payload["services/purchase/field_clean.py"]
    assert hashlib.sha256(clean_text.encode("utf-8")).hexdigest() == \
        HASHES["services/purchase/field_clean.py"]
    tree = ast.parse(clean_text)
    nodes = [node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "clean_tax_id"]
    assert len(nodes) == 1
    clean = {"re": __import__("re")}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "field_clean.py", "exec"), clean)

    store_text = payload["services/purchase/supplier_posting.py"]
    assert hashlib.sha256(store_text.encode("utf-8")).hexdigest() == \
        HASHES["services/purchase/supplier_posting.py"]
    tree = ast.parse(store_text)
    nodes = [node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {"upsert_profile", "delete_profile"}]
    assert {node.name for node in nodes} == {"upsert_profile", "delete_profile"}
    store = {"Optional": Optional, "Dict": Dict, "List": List}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "supplier_posting.py", "exec"), store)
    return {"domains": domains, "clean": clean, "store": store}


def legacy_row(existing, incoming, source):
    """Run the captured deployed statement in SQLite and read the row back."""
    connection = sqlite3.connect(":memory:")
    connection.execute(DDL)
    connection.execute("CREATE UNIQUE INDEX profiles_unique ON supplier_posting_profiles"
        " (tenant_id, workspace_client_id, seller_tax_id)")
    if existing is not None:
        connection.execute(f"INSERT INTO {TABLE} VALUES (?, ?, ?, ?, ?, ?, ?)",
            (TENANT, WORKSPACE, TAX, existing["default_payment"], existing["default_item_type"],
                existing["source"], "seed"))
    statement, params = STATEMENT
    connection.execute(statement.replace("%s", "?").replace("now()", "CURRENT_TIMESTAMP"), params)
    row = connection.execute(f"SELECT default_payment, default_item_type, source FROM {TABLE}"
        " WHERE tenant_id = ? AND workspace_client_id = ? AND seller_tax_id = ?",
        (TENANT, WORKSPACE, TAX)).fetchone()
    connection.close()
    return row


def expected_row(existing, incoming, source):
    decision = supplier_posting.merge(existing, incoming, source)
    if existing is None:
        return (decision["fields"]["default_payment"], decision["fields"]["default_item_type"],
            decision["source"])
    row = dict(existing)
    row.update(decision["fields"])
    row["source"] = decision["source"]
    return (row.get("default_payment"), row.get("default_item_type"), row["source"])


def run(root=None, source_text=None):
    payload = source_text
    if payload is None:
        payload = {name: (root / name).read_text() for name in HASHES}
    legacy = load_legacy(payload)
    counts = {"domains": 0, "tax": 0, "cases": 0, "preserved": 0, "created": 0, "empty_tax": 0}

    # 1) The two value domains, fed one value at a time.
    ours = {"_PAYMENT_VALUES": supplier_posting.PAYMENT_VALUES,
        "_ITEM_TYPE_VALUES": supplier_posting.ITEM_TYPE_VALUES}
    candidates = ["cash", "credit", "goods", "expense", "food", "CASH", "", " ", None, "cheque"]
    for name, domain in ours.items():
        theirs = legacy["domains"][name]
        for value in candidates:
            assert (value in theirs) == (value in domain), (name, value)
            counts["domains"] += 1
        assert set(theirs) == set(domain), name
        assert len(theirs) == len(set(theirs))

    # 2) The profile anchor: clean_tax_id as the deployed route cleaned it.
    for value in TAX_VALUES:
        assert legacy["clean"]["clean_tax_id"](value) == \
            supplier_posting.clean_seller_tax_id(value), value
        counts["tax"] += 1

    # 3) The statement the deployed DAL builds for one (existing, incoming, source) case.
    global STATEMENT
    for existing in EXISTINGS:
        for incoming in INCOMINGS:
            for source in SOURCES:
                cursor = Capture()
                legacy["store"]["upsert_profile"](cursor, tenant_id=TENANT,
                    workspace_client_id=WORKSPACE, seller_tax_id=TAX,
                    default_payment=(incoming or {}).get("default_payment"),
                    default_item_type=(incoming or {}).get("default_item_type"), source=source)
                assert len(cursor.statements) == 1, (existing, incoming, source)
                STATEMENT = cursor.statements[0]
                theirs = legacy_row(existing, incoming, source)
                assert theirs == expected_row(existing, incoming, source), \
                    (existing, incoming, source, theirs, expected_row(existing, incoming, source))
                counts["cases"] += 1
                counts["created"] += 1 if existing is None else 0
                if existing is not None and existing["source"] == "user_rule" \
                        and source != "user_rule":
                    counts["preserved"] += 1

    # 4) An empty anchor: the deployed DAL issues no statement at all.
    for blank in ("", None, "   "):
        cursor = Capture()
        legacy["store"]["upsert_profile"](cursor, tenant_id=TENANT, workspace_client_id=WORKSPACE,
            seller_tax_id=blank, default_payment="cash", source="user_rule")
        assert cursor.statements == [], blank
        assert supplier_posting.clean_seller_tax_id(blank) == ""
        counts["empty_tax"] += 1

    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    floors = {"domains": 20, "tax": 10, "cases": 80, "preserved": 16, "created": 16, "empty_tax": 3}
    for name, floor in floors.items():
        if counts[name] < floor:
            raise SystemExit(f"fixture coverage below the floor: {name}={counts[name]} < {floor}")
    raise SystemExit(0)


STATEMENT: Any = None


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
