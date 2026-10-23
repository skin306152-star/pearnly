"""Hash-bound queue wire differential against complete deployed source functions.

Run with PYTHONPATH=apps/pearnly_cowork and --source SNAPSHOT, or feed a JSON
relative-path -> source-text mapping with --source-stdin. Source authorization,
DB rows and refresh leases are fixtures; no old application import or IO occurs.
Pydantic's actual runtime version is reported, not called the old runtime.
"""
import argparse
import ast
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from uuid import UUID

import pydantic
from pydantic import BaseModel, Field

from pearnly_core.cc_desktop.wire.queue_contracts import (
    QueueContractError, ack_request, ack_response, job, lease_request, lease_response,
)

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "routes/erp_agent.py": "bf253777feb90eeb2c3d9f0bccdc5715a49ac8026a8a9710105b77a8106044bb",
    "services/erp/express_push/agent_store.py": "03d86de7de82761f33f386913d3d32a93874953a237a8151e02c4e5e3000c9bb",
    "services/erp/express_push/common.py": "e00c6410c711208cf2bcbf20579d9d67e365b2957aa9d90a3cd9f68e4fe09f58",
}
ID = "972778cf-0b5a-4815-a687-bec2d3738db5"
REFRESH_ID = "18a84c4b-023d-49c6-bcf3-c277472c836a"


class SourceHTTPError(ValueError):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


class SourceImports(ast.NodeTransformer):
    """Only replace application imports; do not rewrite any business branch."""
    def visit_ImportFrom(self, node):
        if (node.module or "").split(".")[0] in {"services", "core"}:
            return ast.copy_location(ast.Pass(), node)
        return node


class Cursor:
    def __init__(self, fixture): self.fixture = fixture
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def execute(self, sql, parameters): self.fixture.calls.append((sql, parameters))
    def fetchone(self): return deepcopy(self.fixture.row)


async def inline_thread(function, *args):
    return function(*args)


def load_sources(root=None, source_texts=None):
    trees = {}
    for relative, expected in HASHES.items():
        data = ((Path(root) / relative).read_bytes() if source_texts is None
                else source_texts[relative].encode("utf-8"))
        assert hashlib.sha256(data).hexdigest() == expected, relative
        trees[relative] = ast.parse(data.decode("utf-8"))

    def extract(relative, functions=(), constants=(), **injected):
        selected = []
        for node in deepcopy(trees[relative]).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in functions:
                node.decorator_list = []
                selected.append(node)
            elif isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in constants for target in node.targets):
                selected.append(node)
        found = {node.name for node in selected if hasattr(node, "name")}
        assert found == set(functions), (relative, found, functions)
        tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                                *selected], type_ignores=[])
        tree = ast.fix_missing_locations(SourceImports().visit(tree))
        namespace = {"__name__": __name__, "Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
                     "json": json, "BaseModel": BaseModel, "Field": Field, **injected}
        exec(compile(tree, relative, "exec"), namespace)
        return SimpleNamespace(**namespace)

    common_tree = trees["services/erp/express_push/common.py"]
    constants = {target.id for node in common_tree.body if isinstance(node, ast.Assign)
                 for target in node.targets if isinstance(target, ast.Name)
                 and (target.id.startswith(("STAGE_", "_META_", "ITEM_MODE_")) or target.id == "ACK_OUTCOMES")}
    common = extract("services/erp/express_push/common.py", ("sanitize_push_meta",), constants)
    fixture = SimpleNamespace(row=None, calls=[], rows=[], refresh=None, errors=[])
    logger = SimpleNamespace(error=lambda *args: fixture.errors.append(args),
                             exception=lambda *args: fixture.errors.append(args), warning=lambda *args: None)
    store = extract("services/erp/express_push/agent_store.py", ("_load_owned_log", "_build_response_body", "ack"),
                    ("_MAX_ATTEMPTS",), C=common, logger=logger,
                    db=SimpleNamespace(get_cursor=lambda **kw: Cursor(fixture)),
                    lock_endpoint_binding=lambda *args: None, lock_legacy_endpoint=lambda *args: True,
                    _mirror_history_status=lambda *args: None, _bump_created_stock_masters=lambda *args: None)
    endpoint = {"id": "fixture-endpoint", "enabled": True, "config": {"account_set": "C:/ERP/BOOK"}}
    store.authenticate = lambda token: deepcopy(endpoint)
    store.lease_pending = lambda *args: deepcopy(fixture.rows)
    route = extract("routes/erp_agent.py", ("LeaseRequest", "AckRequest", "_legacy_agent_lease",
                    "_run_agent_ack", "erp_agent_ack", "_offer_master_refresh"), agent_store=store,
                    HTTPException=SourceHTTPError, logger=logger,
                    _require_enabled=lambda: None, _bearer_token=lambda request: "fixture-token",
                    asyncio=SimpleNamespace(to_thread=inline_thread), authorized_account_sets=lambda ep: [],
                    account_set_allowed=lambda *args: True,
                    lease_express_refresh=lambda *args: deepcopy(fixture.refresh))
    return SimpleNamespace(route=route, common=common, fixture=fixture, endpoint=endpoint)


def compare(expected, actual, case):
    assert expected == actual, (case, expected, actual)


def rejected(call):
    try:
        call()
    except (QueueContractError, pydantic.ValidationError, SourceHTTPError):
        return True
    return False


def run(root=None, source_texts=None):
    old = load_sources(root, source_texts)
    rng = random.Random(20260921)
    counts = {key: 0 for key in ("lease_request", "ack_request", "lease_response", "ack_response", "both_reject")}
    differences = {key: 0 for key in ("strict_integer", "uuid_mapping", "line_mode_objects", "finite_json")}
    for maximum in range(1, 51):
        for owner in (None, "", " A ", "ผู้ใช้"):
            raw = {"max": maximum, "agent_id": owner, "future_field": {"ignored": True}}
            compare(old.route.LeaseRequest(**raw).model_dump(), lease_request(raw), raw)
            counts["lease_request"] += 1
    compare(old.route.LeaseRequest().model_dump(), lease_request({}), "lease defaults")
    counts["lease_request"] += 1

    outcomes = (None, "", *old.common.ACK_OUTCOMES, "unknown_future_outcome")
    for index in range(500):
        raw = {"log_id": ID, "result": rng.choice(("success", "failed")),
               "express_docnum": rng.choice((None, "", "  RR-๑  ", "x" * 300)),
               "error": rng.choice((None, "", "failure" * 200)), "agent_id": rng.choice((None, "", " A ")),
               "line_modes": rng.choice((None, [], [{"mode": "direct_account", "reason": "unmapped", "other": [1]}])),
               "outcome": rng.choice(outcomes), "meta": rng.choice((None, {},
                   {"stage": "old-client-stage", "written": False, "unknown": {"nested": [1, None, True]}})),
               "extra_client_field": "ignored"}
        compare(old.route.AckRequest(**raw).model_dump(), ack_request(raw), index)
        counts["ack_request"] += 1

    for size in (0, 1, 10, 50):
        for refresh in (None, {}, {"request_id": REFRESH_ID, "scope_kind": "endpoint", "account_set_key": "@endpoint"},
                        {"request_id": REFRESH_ID, "scope_kind": "account_set", "account_set_key": "c:/erp/book"}):
            old.fixture.rows = [{"id": str(UUID(int=index + 1)), "history_id": "history-" + str(index),
                "invoice_no": None if index % 2 else "INV-๑", "request_body": {"account_set": "C:/ERP/BOOK",
                "amount": "123.45", "items": [{"amount": "123.45"}], "meta": {"nested": [True, None]}}}
                for index in range(size)]
            old.fixture.refresh = refresh
            expected = old.route._legacy_agent_lease(old.endpoint, "default", 50)
            actual = lease_response([job(row["id"], {"history": row["history_id"], "invoice_no": row["invoice_no"],
                "frozen_payload": row["request_body"], "attempt_token": "must-not-leak"}) for row in old.fixture.rows], refresh)
            compare(expected, actual, ("lease response", size, refresh))
            counts["lease_response"] += 1

    for result in ("success", "failed"):
        for outcome in outcomes:
            for attempt in (0, 1, 2, 3, 6):
                for already_success in (False, True):
                    old.fixture.calls, old.fixture.errors = [], []
                    old.fixture.row = {"id": ID, "status": "success" if already_success else "pending",
                                       "attempt": attempt, "lease_owner": "A", "history_id": "history-1"}
                    raw = {"log_id": ID, "result": result, "outcome": outcome, "agent_id": " A ",
                           "express_docnum": "RR-๑", "error": "failure" * 200,
                           "line_modes": [{"mode": "direct_account", "reason": "unmapped", "stkcod": "P1"}],
                           "meta": {"written": True, "tables_written": ["APTRN"], "extra": "not persisted"}}
                    expected = asyncio.run(old.route.erp_agent_ack(old.route.AckRequest(**raw), None))
                    assert not old.fixture.errors, old.fixture.errors
                    assert expected["ok"] is True, expected
                    bodies = [json.loads(value) for _sql, parameters in old.fixture.calls for value in parameters
                              if isinstance(value, str) and value.startswith('{"ok":')]
                    body = {"meta": {"stage": "success"}} if already_success else bodies[-1]
                    projection = {"response_body": body, "status": expected["status"], "wire_attempt": expected.get("attempt", attempt),
                                  "external_docnum": "RR-๑", "replayed": already_success, "result_unknown": False,
                                  "attempt_token": "must-not-leak", "fence": 100}
                    compare(expected, ack_response(projection), (result, outcome, attempt, already_success))
                    counts["ack_response"] += 1

    for raw in ({"max": 0}, {"max": 51}, {"max": None}, {"agent_id": 1}):
        assert rejected(lambda: old.route.LeaseRequest(**raw)) and rejected(lambda: lease_request(raw)), raw
        counts["both_reject"] += 1
    for extra in ({"meta": []}, {"result": None}, {"express_docnum": 1}, {"outcome": []}, {"line_modes": {}}):
        raw = {"log_id": ID, "result": "success", **extra}
        assert rejected(lambda: old.route.AckRequest(**raw)) and rejected(lambda: ack_request(raw)), raw
        counts["both_reject"] += 1
    # The source request model accepts these result strings, but its real route
    # rejects them before agent_store.ack is called.
    for result in ("SUCCESS", " success ", "unknown", ""):
        raw = {"log_id": ID, "result": result}
        assert rejected(lambda: asyncio.run(old.route.erp_agent_ack(old.route.AckRequest(**raw), None)))
        assert rejected(lambda: ack_request(raw)), raw
        counts["both_reject"] += 1

    for value in (True, "1", " 2 ", 2.0):
        old.route.LeaseRequest(max=value)
        assert rejected(lambda: lease_request({"max": value}))
        differences["strict_integer"] += 1
    for value in ("native-record", "m1_secret", ID.upper(), ID.replace("-", ""), " " + ID, ""):
        raw = {"log_id": value, "result": "success"}
        old.route.AckRequest(**raw)
        assert rejected(lambda: ack_request(raw))
        differences["uuid_mapping"] += 1
    for value in ([None], ["row"], [["row"]]):
        raw = {"log_id": ID, "result": "success", "line_modes": value}
        old.route.AckRequest(**raw)  # Accepted shape, not necessarily a successful source DB mutation.
        assert rejected(lambda: ack_request(raw))
        differences["line_mode_objects"] += 1
    for value in (float("nan"), float("inf"), {1: "non-string key"}, (1, 2)):
        raw = {"log_id": ID, "result": "success", "meta": {"custom": value}}
        old.route.AckRequest(**raw)
        assert rejected(lambda: ack_request(raw))
        differences["finite_json"] += 1

    return {"source_sha": SOURCE_SHA, "source_hashes": HASHES, "pydantic_runtime": pydantic.__version__,
            "parity": counts, "parity_total": sum(counts.values()), "intentional_differences": differences,
            "difference_total": sum(differences.values()),
            "boundary": "Source models/functions on candidate runtime; synthetic authorized rows, no DB/network or old-client acceptance."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source")
    source.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.source, json.load(sys.stdin) if args.source_stdin else None), ensure_ascii=False, indent=2))
