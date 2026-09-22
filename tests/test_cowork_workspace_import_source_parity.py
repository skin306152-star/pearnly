"""Compare deployed header/preamble/summary parsing with the native workspace importer.

Only AST-selected CPU functions are executed, never old app imports or IO.
Resource-limit rejection and binary decode fail-closed are intentional differences.
"""
import argparse
import ast
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pearnly_cowork.masters import workspace_table

HASHES = {
    "services/summary_import/parse.py": "5679261e378fb2ab14358d71c233903a9097c6b512cc269cec9a8c579b8ccbe1",
    "services/workspace/client_import.py": "b2b2da81a93f18a7cecf2b51534f5f1bcbfd1c31112d956699c042cbf97e05dc",
    "services/recon/bank_table_io.py": "19b9161eb5a96f8244efba97b215b50710365ba97f1bd93f8ce04ccd302f190e",
}


def extract(source, names, env):
    nodes = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            nodes.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in names:
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "fixed-workspace-source", "exec"), env)


def run(sources):
    for path, digest in HASHES.items():
        assert hashlib.sha256(sources[path].encode()).hexdigest() == digest, path
    env = {"Any": Any, "Dict": Dict, "List": List, "Optional": Optional, "detect_period": lambda text: None}
    extract(sources["services/recon/bank_table_io.py"], {"_SUMMARY_ROW_KW", "_is_summary_row"}, env)
    extract(sources["services/summary_import/parse.py"], {"_MAX_ROWS", "_HEADER_SCAN", "_TOTAL_CELL", "_looks_summary",
        "_is_num", "_looks_header", "_cell", "_row_is_empty", "_pick_sheet", "_locate_header", "parse_table"}, env)
    extract(sources["services/workspace/client_import.py"], {"MAX_ROWS", "MAX_BYTES", "_HEADER_SYNONYMS",
        "_match_column", "_match_headers", "_cell_at", "parse_client_rows"}, env)
    cases = 0
    headers = [["name"], ["客户名称", "税号"], ["name", "tax id", "phone"],
        ["ชื่อบริษัท", "เลขประจำตัวผู้เสียภาษี", "สาขา", "ที่อยู่"], ["name", "name", "address"], ["wrong", "unknown", "bad"]]
    for header, prefix, size, total in itertools.product(headers, ([], [["Title"]], [["Title"]] * 16),
            (0, 1, 20, 500, 501, 2001), ("Total", "รวม", "合计", "normal")):
        rows = prefix + [header] + [["Company", "0105548103912", "081"] for _ in range(size)] + [[total, "", ""]]
        sheets = [("blank", []), ("companies", rows), ("ignored", [["name", "phone", "tax id"], ["Wrong", "", ""]])]
        env["_sheets"] = lambda *args: sheets
        expected = env["parse_client_rows"](b"", "roster.csv")
        if expected["name_column_found"]:
            actual = workspace_table.from_sheets(sheets)
            assert expected == actual, (header, prefix, size, total)
        else:
            try:
                workspace_table.from_sheets(sheets)
            except ValueError as error:
                assert str(error) == "client_import.header_not_recognized"
            else:
                raise AssertionError("must reject missing header")
        cases += 1
    print(json.dumps({"source_sha": "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188", "cases": cases,
        "differences": 0, "source_hashes": HASHES}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source")
    parser.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    import sys
    sources = json.load(sys.stdin) if args.source_stdin else {p: (Path(args.source) / p).read_text() for p in HASHES}
    run(sources)
