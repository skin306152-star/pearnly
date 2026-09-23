"""Differential check of the migrated buyer-to-client decision core (batch 43).

CLI: --source <fixed-source-root>, or --source-stdin with the fixed source as JSON.
The pinned module talks to a database, so the database is the device replaced here:
a fake `db` answers exactly the three queries the pinned code issues (the learned
memory row, the tax-id row, the active-client list), and the two write paths
(learning a mapping, finding or creating a client by tax id) are recording stubs.
Both sides therefore run on the same fixture and are compared on the resolved
decision, on what they learned, and on what they tried to create.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional

from pearnly_cowork.masters import buyer_resolve

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
PATH = "services/clients/buyer_resolve.py"
SHA256 = "eff506ba79ac781eb0412c58d8f3d06448dc29491e725d49ae2cdf40c461fa94"
FUNCS = {"_buyer_candidates_conflict", "try_resolve_buyer_to_client",
    "resolve_or_create_buyer_client"}


class FakeCursor:
    """Answers the pinned queries from the fixture the scenario supplies."""

    def __init__(self, fixture):
        self.fixture = fixture
        self.sql = ""
        self.params = ()
        self.rows: List[Dict[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params
        if "buyer_to_client_memory" in sql:
            self.rows = [self.fixture["memory"]] if self.fixture["memory"] else []
        elif "REPLACE(REPLACE" in sql:
            tax = self.params[-1]
            self.rows = [{"id": client["client_id"], "name": client["name"]}
                         for client in self.fixture["clients"]
                         if _client_tax(client) == tax][:1]
        else:
            self.rows = [{"id": client["client_id"], "name": client["name"],
                          "short_name": client.get("short_name") or ""}
                         for client in self.fixture["clients"]]
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


def _client_tax(client):
    return (client.get("tax_id") or "").replace("-", "").replace(" ", "")


class FakeDb:
    def __init__(self, fixture):
        self.fixture = fixture
        self.created: List[tuple] = []

    def get_cursor_rls(self, tenant_id=None, user_id=None, commit=False):
        return FakeCursor(self.fixture)

    def find_or_create_client_by_tax_id(self, user_id, tenant_id, tax_clean, buyer_name):
        self.created.append((tax_clean, buyer_name))
        return self.fixture["create_returns"]


def load_legacy(source_text):
    data = source_text.encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == SHA256, "Source hash mismatch"
    tree = ast.parse(source_text)
    nodes = [node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in FUNCS]
    found = {node.name for node in nodes}
    assert found == FUNCS, sorted(FUNCS - found)
    namespace = {"__name__": "legacy", "logger": None, "Optional": Optional, "Dict": Dict,
        "Any": Any, "List": List}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), PATH, "exec"), namespace)
    return namespace


def normalize_decision(decision):
    """The comparable projection: the six decision fields plus the learn flag."""
    return {key: decision.get(key) for key in ("action", "client_id", "client_name",
        "confidence", "match_source", "reason")}


def normalize_match(match):
    if match is None:
        return None
    return {key: match.get(key) for key in ("client_id", "client_name", "confidence",
        "match_source")}


CLIENTS = [
    {"client_id": 1, "name": "ACME Co., Ltd.", "short_name": "ACME",
     "tax_id": "0-1055-48103-91-2"},
    {"client_id": 2, "name": "Bangkok Logistics Co., Ltd.", "short_name": "BKK Log",
     "tax_id": "0105559999999"},
    {"client_id": 3, "name": "Siam Foods", "short_name": "", "tax_id": None},
    {"client_id": 4, "name": "ACME Trading", "short_name": "ACME-T", "tax_id": None},
]
BUYERS = ["ACME Co., Ltd.", "acme co., ltd.", "ACME", "ACME Co., Ltd. Bangkok Branch",
    "ACME Trading", "ACME Trading Branch Office", "A.C.M.E.", "ACME-T",
    "Bangkok Logistics Co., Ltd.", "BKK Log", "BKK Logistics", "Bangkok Logistics (TH)",
    "Siam Foods", "Siam Food", "Siam Foods Co., Ltd.", "Completely Different", "",
    "   ", "Somsak Trading"]
# The last two are valid thirteen-digit numbers no fixture client carries, which is
# what reaches the "create by tax id" branch of the ladder.
TAXES = [None, "", "0-1055-48103-91-2", "0105548103912", "010554810391", "0105548",
    "0105559999999", "  0105559999999  ", "abc", "01055481039123",
    "0999999999999", "0-9999-99999-99-9"]


def scenarios(rng):
    for _ in range(260):
        memory = rng.choice([None, None, None, {"client_id": 2, "client_name":
            "Bangkok Logistics Co., Ltd."}])
        yield {"buyer_name": rng.choice(BUYERS), "buyer_tax": rng.choice(TAXES),
            "memory": memory,
            "candidates": rng.choice([None, [], ["ACME Co., Ltd."],
                ["ACME Co., Ltd.", "BKK Log"], ["ACME Co., Ltd.", "Siam Foods"],
                ["acme", "ACME Co., Ltd."]]),
            "create_returns": rng.choice([None, 42])}


def run(root=None, source_text=None):
    legacy = load_legacy(source_text if source_text is not None
        else (Path(root) / PATH).read_text())
    rng = random.Random(20260922)
    counts = {"batches": 0, "matches": 0, "memory": 0, "tax": 0, "name_exact": 0,
        "short_exact": 0, "substring": 0, "no_match": 0, "assigned": 0, "created": 0,
        "suggest": 0, "review": 0, "none": 0, "learned": 0, "conflict": 0}

    # The pure conflict rule first, over a wider grid.
    for first in ("", "ACME", "acme co., ltd.", "Siam Foods", "Siam Food", "ACME Trading"):
        for second in ("", "ACME", "BKK Log", "Siam Foods"):
            expected = legacy["_buyer_candidates_conflict"]([first, second])
            produced = buyer_resolve.buyer_candidates_conflict([first, second])
            assert expected == produced, (first, second, expected, produced)
            counts["conflict"] += 1

    for scenario in scenarios(rng):
        fixture = {"memory": scenario["memory"], "clients": CLIENTS,
            "create_returns": scenario["create_returns"]}
        # 1. the matching core (the extracted functions keep this namespace as their
        # globals, so dependencies are injected by assigning into it)
        legacy["db"] = FakeDb(fixture)
        expected_match = normalize_match(legacy["try_resolve_buyer_to_client"](
            scenario["buyer_name"], scenario["buyer_tax"], "user-1", None))
        produced_match = normalize_match(buyer_resolve.match_client(scenario["buyer_name"],
            scenario["buyer_tax"], memory=scenario["memory"], clients=CLIENTS))
        assert expected_match == produced_match, (scenario, expected_match, produced_match)
        counts["matches"] += 1
        if expected_match:
            source = expected_match["match_source"]
            counts["memory"] += 1 if source == "memory" else 0
            counts["tax"] += 1 if source == "tax_id_exact" else 0
            counts["name_exact"] += 1 if source == "name_exact" else 0
            counts["short_exact"] += 1 if source == "short_name_exact" else 0
            counts["substring"] += 1 if source.endswith("substring") else 0
        else:
            counts["no_match"] += 1

        # 2. the decision ladder, including what each side learned and created
        learned_legacy: List[int] = []
        learned_native: List[int] = []
        legacy_db = FakeDb(fixture)
        legacy["db"] = legacy_db
        legacy["learn_buyer_to_client"] = (
            lambda name, tax, client_id, user_id, tenant_id=None:
                bool(learned_legacy.append(int(client_id))) or True)
        expected_decision = normalize_decision(legacy["resolve_or_create_buyer_client"](
            scenario["buyer_name"], scenario["buyer_tax"], "user-1", None,
            buyer_candidates=scenario["candidates"]))
        native_db = FakeDb(fixture)
        produced_decision = normalize_decision(buyer_resolve.resolve_buyer_client(
            scenario["buyer_name"], scenario["buyer_tax"], memory=scenario["memory"],
            clients=CLIENTS, buyer_candidates=scenario["candidates"],
            create_by_tax_id=lambda tax, name, db=native_db: db.find_or_create_client_by_tax_id(
                "user-1", None, tax, name),
            learn=learned_native.append))
        assert expected_decision == produced_decision, (scenario, expected_decision,
            produced_decision)
        assert learned_legacy == learned_native, (scenario, learned_legacy, learned_native)
        assert legacy_db.created == native_db.created, (scenario, legacy_db.created,
            native_db.created)
        counts["batches"] += 1
        counts["learned"] += len(learned_native)
        action = expected_decision["action"]
        counts[action] += 1

    print({"source": SOURCE_SHA, "counts": counts, "differences": 0})
    # Floors are set just under the measured coverage so a fixture that stops
    # reaching a branch is caught, without pretending the counts are targets.
    floors = {"batches": 250, "matches": 250, "conflict": 24, "memory": 50, "tax": 40,
        "name_exact": 30, "short_exact": 15, "substring": 25, "no_match": 40, "assigned": 90,
        "created": 4, "suggest": 20, "review": 80, "none": 20, "learned": 90}
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
