"""Differential check of the migrated product name and unit rules (batch 46).

CLI: --source <fixed-source-root>, or --source-stdin with the fixed sources as JSON.
`names.py` is pure, so it runs exactly as pinned. `units.py` writes through a
cursor, so the cursor is the device replaced: it records the columns each pinned
statement would touch, and the test compares that with the values this side
decides to write (including whether the "clear the other defaults" statement is
issued first).
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import random
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pearnly_cowork.masters import product_names, product_units

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/products/names.py": "c690b97f9c3760f7bb522a5de6d0c6b01220d151ad967ed64a17327e8c12c3ba",
    "services/products/units.py": "b686a65bba251cd7be14cee8438ddbb03c37c3b6f0c7598bcceebd9e66f42bdc",
}
NAME_FUNCS = {"product_names", "display_product_name", "product_name_object"}
UNIT_FUNCS = {"_num", "create_unit", "update_unit", "_clear_default",
    "_sync_product_active", "get_unit"}
UNIT_CONSTS = {"_UWRITABLE", "NULLABLE_UNIT_FIELDS", "_NUMERIC", "_UCOLS"}


class RecordingCursor:
    def __init__(self):
        self.statements: List[tuple] = []

    def execute(self, sql, params=()):
        head = sql.strip().split()[0].upper()
        self.statements.append((head, " ".join(sql.split()), tuple(params)))
        return self

    def fetchone(self):
        return {"id": "unit-1"}

    def fetchall(self):
        return []


def load_legacy(payload):
    names = {}
    data = payload["services/products/names.py"].encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == HASHES["services/products/names.py"]
    tree = ast.parse(data.decode())
    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in NAME_FUNCS)
        or (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_FIELDS")]
    assert {node.name for node in nodes if isinstance(node, ast.FunctionDef)} == NAME_FUNCS
    assert names is not None
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "names.py", "exec"), names)

    units_text = payload["services/products/units.py"]
    data = units_text.encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == HASHES["services/products/units.py"]
    tree = ast.parse(units_text)

    def assigned(node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
        return ""

    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in UNIT_FUNCS)
        or assigned(node) in UNIT_CONSTS]
    assert {node.name for node in nodes if isinstance(node, ast.FunctionDef)} == UNIT_FUNCS
    units = {"__name__": "legacy", "logger": None, "Any": Any, "Optional": Optional,
        "Decimal": Decimal}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "units.py", "exec"), units)
    return {"names": names, "units": units}


NAME_ROWS = [
    {"name_th": "น้ำ", "name_en": "Water", "name_zh": "水"},
    {"name_th": "  น้ำ  ", "name_en": "water", "name_zh": "WATER"},
    {"name_en": "Water", "name_zh": ""},
    {"name_th": "", "name_en": None, "name_zh": "   "},
    {},
    {"name_th": "คะแฟ", "name_en": "Coffee", "name_zh": "咖啡"},
    {"name_en": 42, "name_zh": True},
]
UNIT_TEXTS = ["12", "0.5", "1", 0, "0.00", "-3", "12.50", None, "  7  ", 3.5, "1e3"]
UNIT_NAMES = ["กล่อง", "แผง", "โหล", "", "  ", "粒", None]


def scenarios(rng):
    for _ in range(240):
        fields = {}
        for field in product_units.UNIT_WRITABLE:
            if rng.random() < 0.6:
                if field in product_units.NUMERIC_UNIT_FIELDS:
                    fields[field] = rng.choice(UNIT_TEXTS)
                elif field == "unit_name":
                    fields[field] = rng.choice(UNIT_NAMES)
                else:
                    fields[field] = rng.choice([None, "x", 1, 0, "8850000000001"])
        if rng.random() < 0.2:
            fields["surprise"] = "dropped"
        yield {"fields": fields, "create": rng.random() < 0.5}


def run(root=None, source_text=None):
    payload = (source_text if source_text is not None
        else {path: (Path(root) / path).read_text() for path in HASHES})
    legacy = load_legacy(payload)

    counts = {"rows": 0, "names": 0, "duplicates": 0, "empty": 0, "inserts": 0,
        "updates": 0, "nothing_to_write": 0, "cleared_default": 0, "decimal_values": 0,
        "null_clears": 0, "surprise": 0}
    for row in NAME_ROWS:
        expected = (legacy["names"]["product_names"](row),
            legacy["names"]["display_product_name"](row),
            legacy["names"]["product_name_object"](row))
        produced = (product_names.product_names(row),
            product_names.display_product_name(row), product_names.product_name_object(row))
        assert expected == produced, (row, expected, produced)
        counts["rows"] += 1
        counts["names"] += len(produced[0])
        counts["empty"] += 1 if not produced[0] else 0
    assert set(product_names.FIELDS) == {"name_th", "name_en", "name_zh"}

    rng = random.Random(20260922)
    for scenario in scenarios(rng):
        fields = scenario["fields"]
        cursor = RecordingCursor()
        if scenario["create"]:
            legacy["units"]["create_unit"](cursor, tenant_id="t", workspace_client_id=1,
                product_id="p", fields=fields)
            # The INSERT is the last statement; a clear-default UPDATE precedes it when the
            # incoming flag is truthy.
            insert = [entry for entry in cursor.statements if entry[0] == "INSERT"][-1]
            columns = [part.strip() for part in
                insert[1].split("(", 1)[1].split(")", 1)[0].split(",")]
            # The first three columns are the scope (tenant, workspace, product).
            written = dict(zip(columns[3:], insert[2][3:]))
            produced = product_units.unit_insert_values(fields)
            # The deployed create also issues an UPDATE afterwards to sync the
            # product-active flag, so the clear is identified by its own SQL.
            expected_clear = any("SET is_default_sell = FALSE" in entry[1]
                for entry in cursor.statements)
            produced_clear = product_units.clears_other_defaults(fields)
            assert produced_clear == expected_clear, (fields, expected_clear, produced_clear)
            counts["inserts"] += 1
            counts["cleared_default"] += 1 if produced_clear else 0
        else:
            legacy["units"]["update_unit"](cursor, tenant_id="t", workspace_client_id=1,
                product_id="p", unit_id="u", fields=fields)
            statements = [entry for entry in cursor.statements
                if entry[0] == "UPDATE" and "SET is_default_sell = FALSE" not in entry[1]]
            if not statements:
                produced = product_units.unit_update_values(fields)
                assert produced is None, (fields, produced)
                counts["nothing_to_write"] += 1
                continue
            update = statements[-1]
            assignments = update[1].split(" SET ", 1)[1].split(" WHERE ")[0]
            columns = [part.split("=")[0].strip() for part in assignments.split(",")
                       if part.split("=")[0].strip() != "updated_at"]
            written = dict(zip(columns, update[2][:len(columns)]))
            produced = product_units.unit_update_values(fields)
            counts["updates"] += 1
            expected_clear = any("SET is_default_sell = FALSE" in entry[1]
                for entry in cursor.statements)
            assert product_units.clears_other_defaults(fields) == expected_clear, fields
        assert set(written) == set(produced), (fields, written, produced)
        for key, value in written.items():
            if key in product_units.NUMERIC_UNIT_FIELDS or value is None:
                expected_value = legacy["units"]["_num"](value) if key in \
                    product_units.NUMERIC_UNIT_FIELDS else value
                assert str(produced[key]) == str(expected_value), (key, value, produced[key])
                counts["decimal_values"] += 1 if key in product_units.NUMERIC_UNIT_FIELDS else 0
            else:
                assert produced[key] == value, (key, value, produced[key])
            counts["null_clears"] += 1 if value is None else 0
        counts["surprise"] += 1 if "surprise" in fields else 0

    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    floors = {"rows": 7, "names": 10, "empty": 2, "inserts": 100, "updates": 80,
        "nothing_to_write": 1, "cleared_default": 30, "decimal_values": 200,
        "null_clears": 15, "surprise": 40}
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
        run(None, json.load(sys.stdin))
    else:
        run(arguments.source)
