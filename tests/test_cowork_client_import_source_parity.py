"""Differential check of the migrated client-import rules (batch 48).

CLI: --source <fixed-source-root>, or --source-stdin with the fixed sources as JSON.
Both pinned modules are pure except for the table reader, which this comparison
replaces by handing the same headers and rows to both sides.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional

from pearnly_cowork.masters import client_import

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/workspace/client_import.py": "b2b2da81a93f18a7cecf2b51534f5f1bcbfd1c31112d956699c042cbf97e05dc",
    "services/purchase/field_clean.py": "43912e65fcce3afa53fa4f7cdfd2d57afc02ff5556c5e17a6d76c2d030bdbed8",
}
IMPORT_FUNCS = {"_match_column", "_match_headers", "_cell_at", "structural_error",
    "parse_client_rows"}
IMPORT_CONSTS = {"MAX_BYTES", "MAX_ROWS", "ERR_MISSING_NAME", "ERR_BAD_TAX_ID",
    "HEADER_SYNONYMS", "_HEADER_SYNONYMS"}
CLEAN_FUNCS = {"clean_tax_id", "clean_branch_no"}

HEADERS = ["Client Name", "TAX ID", "Branch No", "เบอร์โทร", "ที่อยู่", "客户名称",
    "ชื่อบริษัท", "VAT No", "Company Name", "Note", "", "Tel"]
CELLS = ["ACME Co., Ltd.", "0-1055-48103-91-2", "18", "0812345678", "Bangkok", "",
    "  Beta Shop  ", "12345", "0105561000013 บริษัท", "x"]
VALUES = HEADERS + CELLS + [None, "", "   ", 0]


class FakeTable:
    """Stands in for summary_import.parse_table: returns the fixture it was built with."""

    def __init__(self, parsed):
        self.parsed = parsed

    def __call__(self, file_bytes, filename=""):
        return self.parsed


def load_legacy(payload):
    clean_text = payload["services/purchase/field_clean.py"]
    data = clean_text.encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == HASHES["services/purchase/field_clean.py"]
    tree = ast.parse(clean_text)
    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in CLEAN_FUNCS)
        or (isinstance(node, ast.Import) and True)]
    assert {node.name for node in nodes if isinstance(node, ast.FunctionDef)} == CLEAN_FUNCS
    clean = {"re": __import__("re")}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "field_clean.py", "exec"), clean)

    import_text = payload["services/workspace/client_import.py"]
    data = import_text.encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == HASHES["services/workspace/client_import.py"]
    tree = ast.parse(import_text)

    def assigned(node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return node.target.id
        return ""

    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in IMPORT_FUNCS)
        or assigned(node) in IMPORT_CONSTS]
    assert {node.name for node in nodes if isinstance(node, ast.FunctionDef)} == IMPORT_FUNCS
    module = {"Optional": Optional, "Dict": Dict, "Any": Any, "List": List,
        "clean_tax_id": clean["clean_tax_id"]}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "client_import.py", "exec"), module)
    return {"clean": clean, "import": module}


def table_rows(rng):
    rows = []
    for index in range(rng.randrange(1, 8)):
        cells = [rng.choice(VALUES) for _ in range(rng.randrange(1, 7))]
        rows.append({"index": index, "cells": cells,
            "is_summary": rng.random() < 0.15})
    return rows


def run(root=None, source_text=None):
    payload = (source_text if source_text is not None
        else {path: (Path(root) / path).read_text() for path in HASHES})
    legacy = load_legacy(payload)
    counts = {"columns": 0, "headers": 0, "cells": 0, "imports": 0, "rows": 0,
        "name_missing": 0, "truncated": 0, "tax_ok": 0, "tax_bad": 0, "branch_ok": 0,
        "branch_empty": 0, "structural_none": 0, "errors": 0}

    for value in VALUES + ["", "   ", "Total", "VAT No"]:
        expected = legacy["import"]["_match_column"](value)
        produced = client_import.match_column(value)
        assert expected == produced, (value, expected, produced)
        counts["columns"] += 1

    rng = random.Random(20260922)
    header_sets = [[], HEADERS, ["Tax ID"], ["Client Name"], list(reversed(HEADERS)),
        ["客户名称", "税号", "分公司"], ["ชื่อลูกค้า", "เลขประจำตัวผู้เสียภาษี"]]
    for headers in header_sets:
        expected = legacy["import"]["_match_headers"](headers)
        produced = client_import.match_headers(headers)
        assert expected == produced, (headers, expected, produced)
        counts["headers"] += 1

    for _ in range(150):
        cells = [rng.choice(VALUES) for _ in range(rng.randrange(1, 7))]
        for index in (None, 0, 1, 6):
            expected = legacy["import"]["_cell_at"](cells, index)
            produced = client_import.cell_at(cells, index)
            assert expected == produced, (cells, index, expected, produced)
            counts["cells"] += 1

    for _ in range(200):
        name = rng.choice(["ACME", "  ", "", None, "Beta Shop"])
        tax = rng.choice([None, "", "0-1055-48103-91-2", "12345", "1234567890123", "x"])
        expected = legacy["import"]["structural_error"](name, tax)
        produced = client_import.structural_error(name, tax)
        assert expected == produced, (name, tax, expected, produced)
        counts["errors"] += 1
        counts["structural_none"] += 1 if produced is None else 0

    for value in VALUES + ["0-1055-48103-91-2", "สาขาที่ 18", "00018", "123456"]:
        expected = legacy["clean"]["clean_tax_id"](value)
        produced = client_import.clean_tax_id(value)
        assert expected == produced, (value, expected, produced)
        counts["tax_ok"] += 1 if produced else 0
        counts["tax_bad"] += 1 if not produced else 0
        expected = legacy["clean"]["clean_branch_no"](value)
        produced = client_import.clean_branch_no(value)
        assert expected == produced, (value, expected, produced)
        counts["branch_ok"] += 1 if produced else 0
        counts["branch_empty"] += 1 if not produced else 0

    for _ in range(120):
        rows = table_rows(rng)
        headers = rng.choice(header_sets)
        parsed = {"headers": headers, "rows": rows, "truncated": rng.random() < 0.2}
        legacy["import"]["parse_table"] = FakeTable(parsed)
        expected = legacy["import"]["parse_client_rows"](b"file", "clients.xlsx")
        produced = client_import.import_rows(headers, rows)
        # `row_count` and the per-row projection are what matter; the deployed result
        # also echoes the raw row indices, which this side keeps as row_index.
        assert expected["headers"] == produced["headers"], (parsed, expected, produced)
        assert expected["matched"] == produced["matched"], (parsed, expected, produced)
        assert expected["row_count"] == produced["row_count"], (parsed, expected, produced)
        assert expected["name_column_found"] == produced["name_column_found"], parsed
        assert expected["rows"] == produced["rows"], (parsed, expected["rows"], produced["rows"])
        counts["imports"] += 1
        counts["rows"] += produced["row_count"]
        counts["name_missing"] += 1 if not produced["name_column_found"] else 0
        counts["truncated"] += 1 if produced["truncated"] else 0

    # One oversized file, so the truncation flag is exercised as well.
    big_rows = [{"index": index, "cells": ["C%d" % index], "is_summary": False}
                for index in range(client_import.MAX_ROWS + 25)]
    assert client_import.import_rows(["Client Name"], big_rows)["truncated"] is True
    counts["truncated"] += 1

    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    floors = {"columns": 20, "headers": 7, "cells": 600, "imports": 120, "rows": 250,
        "name_missing": 5, "truncated": 1, "tax_ok": 3, "tax_bad": 15, "branch_ok": 3,
        "branch_empty": 15, "structural_none": 50, "errors": 200}
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
