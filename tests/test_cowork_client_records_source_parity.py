"""Differential check of the migrated client-record field rules (batch 44).

CLI: --source <fixed-source-root>, or --source-stdin with the fixed source as JSON.
The pinned create/update functions write through a cursor, so the cursor is the
device replaced here: it records the statement and its parameters, and the test
compares the columns the pinned code would have written with the values this side
cleans. Both sides see the same inputs.

Non-string text values are deliberately outside the comparison: the pinned code
calls .strip() on them and swallows the resulting error (no client is created),
while this side stringifies. That difference is recorded in the batch evidence
rather than hidden by the fixture.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional

from pearnly_cowork.masters import client_records

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
PATH = "services/clients/store.py"
SHA256 = "24407c4e501bbc950c4e9489e02f583682d6e1efbf91ac05cf33834951f646e7"
FUNCS = {"create_client", "update_client"}

# The column order both deployed INSERT statements use.
CREATE_COLUMNS = ("name", "short_name", "tax_id", "address", "contact_person",
    "contact_phone", "contact_email", "notes", "color", "party_type", "branch",
    "promptpay_id")


class RecordingCursor:
    def __init__(self):
        self.sql = ""
        self.params = ()
        self.calls: List[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params
        self.calls.append((sql.strip().split()[0].upper(), tuple(params)))
        return self

    def fetchone(self):
        return {"id": 7}

    def fetchall(self):
        return []

    def rowcount(self):
        return 1


class RecordingDb:
    def __init__(self):
        self.cursor = RecordingCursor()

    def get_cursor_rls(self, tenant_id=None, user_id=None, commit=False):
        return self.cursor


class QuietLogger:
    def error(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


def load_legacy(source_text):
    data = source_text.encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == SHA256, "Source hash mismatch"
    tree = ast.parse(source_text)
    nodes = [node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in FUNCS]
    assert {node.name for node in nodes} == FUNCS
    namespace = {"__name__": "legacy", "logger": QuietLogger(), "Optional": Optional,
        "Dict": Dict, "Any": Any, "List": List}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), PATH, "exec"), namespace)
    return namespace


def pinned_create(db, name, values):
    """The columns the pinned INSERT would write, in the deployed column order."""
    db["db"] = RecordingDb()
    cursor_db = db["db"]
    db["create_client"]("user-1", None, name, **values)
    if not cursor_db.cursor.calls:
        return None  # the deployed guard refuses a blank name before any statement
    _verb, params = cursor_db.cursor.calls[-1]
    # params: user_id, tenant_id, then the twelve columns in order
    return dict(zip(CREATE_COLUMNS, params[2:]))


def pinned_update(db, values):
    db["db"] = RecordingDb()
    cursor_db = db["db"]
    wrote = db["update_client"]("user-1", 5, None, **values)
    if not cursor_db.cursor.calls:
        return None if not wrote else "no statement"
    sql, params = cursor_db.cursor.calls[-1]
    assert sql == "UPDATE"
    # Rebuild {column: value} from "col = %s, ..." in order (updated_at is appended
    # without a parameter).
    assignments = db["db"].cursor.sql.split(" SET ", 1)[1].split(" WHERE ")[0]
    columns = [part.split("=")[0].strip() for part in assignments.split(",")
               if part.split("=")[0].strip() != "updated_at"]
    return dict(zip(columns, params[:len(columns)]))


TEXTS = ["", "   ", "ACME Co., Ltd.", "  padded  ", "x" * 30, "x" * 300, "0-1055-48103-91-2",
    None, "N" * 1200]


def scenarios(rng):
    for index in range(200):
        values = {}
        if index % 8 == 0:
            # A call that carries nothing writable (only the scope, as a caller that
            # rebuilt the payload might) must write nothing at all.
            values["workspace"] = "WS-1"
            yield {"name": rng.choice(["ACME", "  "]), "values": values}
            continue
        for field in CREATE_COLUMNS:
            if field == "name":
                continue
            if rng.random() < 0.7:
                values[field] = rng.choice(TEXTS)
        if rng.random() < 0.3:
            values["is_active"] = rng.choice([True, False, 0, 1])
        if rng.random() < 0.2:
            values["workspace"] = "WS-1"
        yield {"name": rng.choice(["ACME Co., Ltd.", "  ACME  ", "x" * 300, ""]), "values": values}


def run(root=None, source_text=None):
    legacy = load_legacy(source_text if source_text is not None
        else (Path(root) / PATH).read_text())
    rng = random.Random(20260922)
    counts = {"creates": 0, "created": 0, "refused_create": 0, "updates": 0, "wrote": 0,
        "nothing_to_write": 0, "defaulted_colour": 0, "truncated": 0, "unset": 0,
        "active_written": 0}
    for scenario in scenarios(rng):
        values = {key: value for key, value in scenario["values"].items()
                  if key not in ("is_active", "workspace")}
        # --- create ---
        expected = pinned_create(legacy, scenario["name"], values)
        produced = client_records.create_values({"name": scenario["name"], **values})
        counts["creates"] += 1
        if produced is None:
            counts["refused_create"] += 1
            # The pinned code returns None for a blank name as well.
            assert not (scenario["name"] or "").strip(), scenario
            continue
        counts["created"] += 1
        assert expected == produced, (scenario, expected, produced)
        counts["defaulted_colour"] += 1 if produced["color"] == client_records.DEFAULT_COLOR else 0
        counts["truncated"] += 1 if any(value and len(str(value)) == 300
            or (value and len(str(value)) > 200) for value in values.values()) else 0
        counts["unset"] += sum(1 for key, value in produced.items()
            if value is None and key != "color")

        # --- update ---
        update_values = {key: value for key, value in scenario["values"].items()
                         if key != "workspace"}
        expected_update = pinned_update(legacy, update_values)
        produced_update = client_records.update_values(update_values)
        counts["updates"] += 1
        if expected_update is None:
            counts["nothing_to_write"] += 1
            assert produced_update is None, (update_values, produced_update)
            continue
        counts["wrote"] += 1
        counts["active_written"] += 1 if "is_active" in produced_update else 0
        assert expected_update == produced_update, (scenario, expected_update, produced_update)

    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    # Floors sit just under the measured coverage so a fixture that stops reaching a
    # path is caught without pretending the counts are targets.
    floors = {"creates": 195, "created": 130, "refused_create": 40, "updates": 140, "wrote": 120,
        "nothing_to_write": 10, "defaulted_colour": 60, "truncated": 110, "unset": 800,
        "active_written": 25}
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
        run(None, json.load(sys.stdin)[PATH])
    else:
        run(arguments.source)
