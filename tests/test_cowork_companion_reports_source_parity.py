"""Execute hash-bound legacy heartbeat, report writers and projection ingestion.

Only database/auth/publication/clock boundaries are replaced. Actual legacy
functions perform sanitization, branching, SQL parameter generation and refresh
completion decisions. No legacy application package is imported or executed.
"""
import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace

from pearnly_cowork.connections.reports import ReportError, plan_report


SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "routes/erp_agent.py": "bf253777feb90eeb2c3d9f0bccdc5715a49ac8026a8a9710105b77a8106044bb",
    "services/erp/express_push/agent_account_sets.py": "4a8861d46e748349e0a6b24e1b79b3d6ee404d6426505285476f5d9f2f4bb5a8",
    "services/erp/express_push/agent_reporting.py": "c1ff3a33724ffb302c94e7c4ee44a1ecd7ba7753ca71c35f72a09c3247bf3a65",
    "services/erp/express_target_projection.py": "04cc019472c3bcdf5a776507c24ece1cd29f920f3062f64409a2a9c6008dc478",
    "services/erp/express_account_identity.py": "6271c1277c12bffbad09a02514ca29255bdb68f776106d5232f4f2241b8eaa4b",
}
NOW = datetime(2026, 9, 21, 4, 5, 6, tzinfo=timezone.utc)
BOOK = r"E:\DATA\BOOK"
ROOT = r"D:\APP"


class RemoveApplicationImports(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        if (node.module or "").startswith(("services", "core")):
            return None
        assert (node.module or "").split(".")[0] in {"__future__", "typing", "datetime"}, node.module
        return node

    def visit_Import(self, node):
        assert all(name.name in {"json", "logging", "ntpath"} for name in node.names), ast.dump(node)
        return node


class SourceBoundaries:
    """Known SQL output adapter, not an implementation of heartbeat decisions."""
    rowcount = 1

    def reset(self, previous):
        self.config = deepcopy(previous)
        self.previous = deepcopy(previous)
        self.observations = []
        self.completions = []
        self.projection_errors = []
        self.lease_selections = []
        self.errors = []

    def get_cursor(self, **kwargs):
        assert kwargs == {"commit": True}
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def error(self, *args):
        self.errors.append(args)

    exception = error

    def execute(self, sql, parameters):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT ep.id, ep.tenant_id"):
            assert parameters == ("E",)
            return
        assert normalized.startswith("UPDATE erp_endpoints SET config ="), normalized
        assert "adapter = 'express' AND binding_generation = 0" in normalized
        assert parameters[-1] == "E"
        stamp = NOW.isoformat()
        for key, seen_key in (("reported_account_sets", "account_sets_seen_at"),
                              ("reported_accounts", "accounts_seen_at"),
                              ("reported_stock_acc_groups", "stock_acc_groups_seen_at")):
            if f"'{key}'" in normalized:
                assert len(parameters) == 2
                self.config.update({key: json.loads(parameters[0]), seen_key: stamp})
                return
        if "'reported_products'" in normalized:
            assert len(parameters) == 5
            self.config.update(reported_products=json.loads(parameters[0]),
                               reported_customers=json.loads(parameters[1]), catalog_seen_at=stamp)
            fingerprint = json.loads(parameters[2])
            assert fingerprint == json.loads(parameters[3])
            if fingerprint:
                self.config["catalog_fingerprint"] = fingerprint
        elif "1970-01-01 00:00:00+00" in normalized:
            assert len(parameters) == 1
            self.config["agent_last_seen_at"] = "1970-01-01 00:00:00+00"
        elif "'{agent_last_seen_at}'" in normalized:
            self.config["agent_last_seen_at"] = stamp
            if "'{agent_device_name}'" in normalized:
                assert len(parameters) == 2
                self.config["agent_device_name"] = parameters[0]
            else:
                assert len(parameters) == 1
        elif "|| %s::jsonb" in normalized:
            assert len(parameters) == 2
            self.config.update(json.loads(parameters[0]))
        else:
            raise AssertionError(normalized)

    def fetchone(self):
        return {"id": "E", "tenant_id": "NEW_SITE_SYNTHETIC", "binding_generation": 0}

    def authenticate(self, token):
        assert token == "synthetic"
        return {"id": "E", "enabled": True, "config": deepcopy(self.previous)}

    def publish(self, _cursor, **kwargs):
        assert kwargs["endpoint_id"] == "E"
        self.observations.append(deepcopy(kwargs["projection"]))
        return {"revision": len(self.observations)}

    def record_error(self, _cursor, **kwargs):
        assert kwargs["status"] == "error"
        account = kwargs["account_set_key"]
        self.projection_errors.append({"scope": "account_set" if account is not None else "endpoint",
                                       "account_set": account, "error_code": kwargs["error_code"]})

    def complete(self, _cursor, **kwargs):
        self.completions.append({"request_id": kwargs["request_id"], "scope": kwargs["scope_kind"],
                                 "account_set": kwargs["account_set_key"],
                                 "error_code": kwargs.get("error_code")})

    def lease(self, endpoint_id, account_set):
        assert endpoint_id == "E"
        self.lease_selections.append(account_set)
        return None


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        assert tz == timezone.utc
        return NOW


def load_legacy(root=None, source_text=None):
    trees = {}
    for path, digest in HASHES.items():
        data = source_text[path].encode("utf-8") if source_text is not None else (Path(root) / path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, f"Source hash mismatch: {path}"
        trees[path] = ast.parse(data.decode("utf-8"))
    state = SourceBoundaries()

    def execute(path, injected=None, names=None):
        tree = trees[path]
        if names is not None:
            tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
            assert {n.name for n in tree.body} == set(names)
        tree = RemoveApplicationImports().visit(tree)
        namespace = {"__name__": "fixed_companion_source", "db": state, **(injected or {})}
        exec(compile(ast.fix_missing_locations(tree), path, "exec"), namespace)
        namespace["logger"] = state
        return namespace

    identity = execute("services/erp/express_account_identity.py")
    state.normalize_account = identity["normalize_express_account_key"]
    account_sets = execute("services/erp/express_push/agent_account_sets.py")
    reporting = execute("services/erp/express_push/agent_reporting.py", {
        "store_account_sets": account_sets["store_account_sets"],
        "_sanitize_account_sets": account_sets["_sanitize_account_sets"],
    })
    projection = execute("services/erp/express_target_projection.py", {
        "normalize_express_account_key": identity["normalize_express_account_key"],
        "reported_account_set_roots": identity["reported_account_set_roots"],
        # Observe before the already-migrated standard projection normalizer.
        "normalize_projection": deepcopy,
        "publish_with_cursor": state.publish,
        "record_refresh_state_with_cursor": state.record_error,
        "complete_express_refresh_with_cursor": state.complete,
    })
    projection["datetime"] = FixedDatetime
    route = execute("routes/erp_agent.py", {
        "Dict": dict, "Any": object,
        "agent_store": SimpleNamespace(authenticate=state.authenticate, **{
            name: value for name, value in reporting.items() if callable(value) and not name.startswith("_")
        }),
        "ingest_express_heartbeat": projection["ingest_express_heartbeat"],
        "lease_express_refresh": state.lease,
        "_connection_identity": lambda _ep: {},
    }, names={"_run_agent_heartbeat", "_ingest_target_projection", "_offer_master_refresh"})
    return state, route["_run_agent_heartbeat"]


def fixtures():
    base = {"account_set": BOOK, "account_dir": BOOK, "express_root": ROOT,
            "account_sets": [{"code": "BOOK", "path": BOOK, "root": ROOT, "writable": True}]}
    previous = {"account_set": BOOK, "express_root": ROOT, "method": "direct", "companion_version": "old",
                "reported_accounts": [{"code": "OLD"}], "reported_products": [{"code": "OLD"}],
                "reported_stock_acc_groups": [{"acccod": "OLD"}], "catalog_fingerprint": {"stcrd_lines": 2}}
    yield {}, previous
    yield {"account_dir": r" E:\DATA\OTHER "}, previous
    yield {"account_set": " NEW ", "account_dir": BOOK}, previous
    for key, values in {
        "account_sets": [None, [], {}, "bad", [{"name": "only"}], [{"path": BOOK}],
                         [{"code": " B ", "path": BOOK, "writable": "false", "row": "bad", "mapping": {"ar_acc": "13"}}]],
        "accounts": [None, [], {}, "bad", [{"name": "only"}], [{"code": " 001 ", "name": " n "}]],
        "mapping": [None, {}, [], "", {"revenue_acc": " 41 ", "ar_acc": ""}],
        "catalog": [None, [], "bad", {}, {"products": [{"name": "only"}]},
                    {"fingerprint": {"stock_master_count": -1}}, {"fingerprint": {"stcrd_lines": "bad"}},
                    {"isacc_candidates": []}, {"isacc_candidates": [{"CODE": "01", "fit": "false", "used_by": -2}]}],
        "max_payload_version": [None, "", "bad", True, False, -1, "3", 2.9],
        "companion_version": [None, "", " v1 ", "v" * 21, "v" * 201],
        "account_set": [None, "", " A ", " 0 "], "account_set_row": [None, "", "bad", "0", -2],
        "device": [None, "", "d" * 61], "offline": [None, False, True, "false"],
    }.items():
        for value in values:
            yield {**deepcopy(base), key: value}, previous
    # Boundaries must exercise source truncation independently from projection.
    for key, count in (("account_sets", 51), ("accounts", 3001)):
        yield {**base, key: [{"code": str(i), "name": "n" * 201} for i in range(count)]}, previous
    yield {**base, "catalog": {"products": [{"code": str(i)} for i in range(20001)],
                                "isacc_candidates": [{"code": str(i)} for i in range(101)]}}, previous
    rng = random.Random(20260921)
    options = [None, [], "bad", {}, [{"code": "BOOK", "path": BOOK, "root": ROOT}],
               [{"code": "OTHER", "path": r"E:\DATA\OTHER"}], [{"name": "only"}],
               [{"path": BOOK}, {"path": BOOK.lower()}]]
    missing = object()
    for _index in range(1200):
        value = {}
        candidates = {
            "account_set": [missing, None, "", BOOK, r"E:/DATA/OTHER/"],
            "account_dir": [missing, BOOK], "account_sets": [missing, *options],
            "accounts": [missing, *options], "catalog": [missing, None, "bad", {},
                {"products": [{"code": "P", "kind": "stock"}], "customers": [{"name": "name-only"}]}],
            "master_refresh_scope": [missing, "endpoint", "account_set", "bad", " account_set "],
            "master_refresh_request_id": [missing, "R", ""],
            "master_refresh_account_set": [missing, BOOK, r"E:\DATA\OTHER"],
            "master_refresh_error": [missing, "ERR_SCAN", ""],
            "mapping": [missing, None, {}, {"revenue_acc": " 41 ", "ar_acc": ""}],
            "max_payload_version": [missing, True, "3", "bad"],
            "companion_version": [missing, None, "new", "v" * 21],
            "offline": [missing, False, False, True],
        }
        for key, values in candidates.items():
            selected = rng.choice(values)
            if selected is not missing:
                value[key] = deepcopy(selected)
        yield value, previous


def run(root=None, source_text=None):
    state, old = load_legacy(root, source_text)
    count = 0
    for body, previous in fixtures():
        state.reset(previous)
        result = old("synthetic", deepcopy(body))
        plan = plan_report(body, previous, NOW)
        assert not state.errors, (count, state.errors)
        assert state.config == {**previous, **plan.patch}, (count, body, state.config, plan.patch)
        assert state.observations == plan.observations, (count, body, state.observations, plan.observations)
        assert state.completions == ([] if plan.completion is None else [plan.completion]), (count, body, state.completions, plan.completion)
        assert state.projection_errors == ([] if plan.projection_error is None else [plan.projection_error]), (count, body, state.projection_errors, plan.projection_error)
        assert {k: v for k, v in result.items() if k not in {"ok", "endpoint_id", "connection"}} == plan.response
        expected_lease = [] if plan.offline else [plan.lease_selection]
        # The legacy route sends the raw string; its lease immediately applies
        # account normalization. Native trimming has the same effective key.
        assert [value.strip() for value in state.lease_selections] == expected_lease, (count, body)
        assert [state.normalize_account(value) for value in state.lease_selections] == [
            state.normalize_account(value) for value in expected_lease], (count, body)
        count += 1
    adaptations = 0
    for invalid in (["bad"], "bad", 7, True):
        state.reset({})
        try:
            old("synthetic", {"mapping": invalid})
        except AttributeError:
            pass
        else:
            raise AssertionError("Fixed source no longer rejects non-object mapping")
        try:
            plan_report({"mapping": invalid}, {}, NOW)
        except ReportError as exc:
            assert exc.code == "companion.report_invalid"
            adaptations += 1
        else:
            raise AssertionError("Native plan accepted malformed mapping")
    print(f"PASS source={SOURCE_SHA} reports={count} value_differences=0 malformed_mapping_adaptations={adaptations} "
          "lease_selection_differences=0 auth_db_clock_publish=controlled_boundaries no_external_io=true")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source")
    inputs.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    run(args.source, json.load(sys.stdin) if args.source_stdin else None)
