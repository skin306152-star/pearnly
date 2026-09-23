"""Execute the fixed legacy duplicate SQL in SQLite and compare native predicates.

Offline only. --source-stdin accepts the fixed queries.py file as JSON; no source
imports, database credentials, or legacy service initialization are executed.
Scope/permissions, persistence and native MariaDB queries are tested in the lab.
"""
import argparse
import ast
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import itertools
import json
import logging
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any, Dict, Optional

from pearnly_cowork.contracts import duplicates as rules

PATH = "services/ocr_history/queries.py"
HASH = "8dc590392f5b2898136a226ac48f3e86bc10e14eaa2d34ec4f110ddd32ebc085"


def run(source):
    assert hashlib.sha256(source.encode()).hexdigest() == HASH
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE ocr_history (id TEXT, user_id TEXT, workspace_client_id INTEGER, "
        "filename TEXT, invoice_no TEXT, invoice_date TEXT, seller_name TEXT, total_amount NUMERIC, created_at TEXT)")

    class Cursor:
        def execute(self, sql, params):
            self.rows = connection.execute(sql.replace("%s", "?"), params)

        def fetchone(self):
            row = self.rows.fetchone()
            if not row:
                return None
            row = dict(row)
            row["invoice_date"] = date.fromisoformat(row["invoice_date"]) if row["invoice_date"] else None
            row["created_at"] = datetime.fromisoformat(row["created_at"])
            return row

    @contextmanager
    def cursor(**kwargs):
        yield Cursor()

    env = {"Optional": Optional, "Dict": Dict, "Any": Any,
           "db": SimpleNamespace(get_cursor_rls=cursor), "logger": logging.getLogger(__name__)}
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
             and n.name in {"_workspace_clause", "check_duplicate_invoice"}]
    assert len(nodes) == 2
    exec(compile(ast.Module(body=nodes, type_ignores=[]), PATH, "exec"), env)
    baseline = {"invoice_no": "INV-01", "invoice_date": "2026-09-22", "seller_name": "Acme", "total_amount": "107"}
    candidates = [baseline, dict(baseline, invoice_no="OTHER"), dict(baseline, invoice_no=None),
                  dict(baseline, invoice_date=None), dict(baseline, total_amount="0"),
                  dict(baseline, seller_name="ACME"), dict(baseline, seller_name="Acme ")]
    checked = 0
    for previous in candidates:
        connection.execute("DELETE FROM ocr_history")
        connection.execute("INSERT INTO ocr_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("old", "actor", 7, "synthetic.pdf", previous["invoice_no"], previous["invoice_date"],
             previous["seller_name"], previous["total_amount"], "2026-09-22T00:00:00"))
        for inv, seller, day, money in itertools.product(
                ("INV-01", " inv-01 ", "OTHER", "", None), ("Acme", " acME ", "Other", None),
                ("2026-09-22", None), ("107.00", "0", "107.01", None)):
            current = {"invoice_no": inv, "seller_name": seller, "invoice_date": day, "total_amount": money}
            legacy = env["check_duplicate_invoice"]("actor", inv, day, seller,
                float(money) if money is not None else None, workspace_client_id=7)
            prepared = rules.terms(current)
            native = next((level for level in ("exact", "likely") if rules.matches(prepared, previous, level)), None)
            assert native == (legacy or {}).get("level"), (current, previous, legacy, native)
            checked += 1
    connection.close()
    return {"source_sha": "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188",
            "source_sha256": HASH, "cases": checked, "differences": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source")
    parser.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    source = json.load(sys.stdin)[PATH] if args.source_stdin else (Path(args.source) / PATH).read_text()
    print(json.dumps(run(source), sort_keys=True))
