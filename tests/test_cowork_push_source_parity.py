"""Run fixed source definitions with a recording DB sink, never the legacy app.

Only valid-owned ACK transition effects are compared against the old SQL params.
The sink does not prove DB locks, authentication, SQL execution or real ERP ACKs.
"""
import argparse
import ast
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from pearnly_cowork.push import lifecycle, retry
from pearnly_core.cc_desktop.wire import ack_rules
from pearnly_core.express_rules import receipts

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/erp/push_retry.py": "0452eb43208fe14291c673d3ff3f4c24b0270f8869d52359711f2a727b81e79f",
    "services/erp/express_push/common.py": "e00c6410c711208cf2bcbf20579d9d67e365b2957aa9d90a3cd9f68e4fe09f58",
    "services/erp/express_push/agent_store.py": "03d86de7de82761f33f386913d3d32a93874953a237a8151e02c4e5e3000c9bb",
    "services/erp/confirmed_push_reservation.py": "0ce2b8ad49862e9b6ac26a8b4e5af89f1b7bf41fd674af01d5233bf1acb49e2d",
    "services/erp/confirmed_push.py": "550cef504360acb444c9a8304d22139de462030d22cc0cf0c7b330eeaaa6aac8",
    "services/erp/external_ref.py": "456836bd76a07773e58c64f35721d3be6923d3df89524e9c8b5ab664281ed1cc",
}


class NoImports(ast.NodeTransformer):
    def visit_Import(self, node):
        return None

    def visit_ImportFrom(self, node):
        return None


def definitions(content, names, extra=None):
    nodes = []
    for node in ast.parse(content).body:
        name = (node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else
                node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else
                node.target.id if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) else "")
        if name in names:
            nodes.append(NoImports().visit(node))
    namespace = {"__name__": "fixed_push_source", "json": json, "copy": SimpleNamespace(deepcopy=deepcopy),
                 "logger": SimpleNamespace(error=lambda *a: None, exception=lambda *a: None), **(extra or {})}
    # Annotations are evaluated lazily on Python 3.14; never use application
    # import hooks, sys.modules aliases or production startup to satisfy them.
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "fixed_push_source", "exec"), namespace)
    return namespace


class Sink:
    def __init__(self, row):
        self.row = row
        self.patch, self.rowcount = None, 1

    def execute(self, sql, params=()):
        if "UPDATE erp_push_logs" not in sql:
            return
        if "status = 'success'" in sql:
            self.patch = {"status": "success", "attempt": self.row["attempt"], "body": params[0], "error": None}
        elif "status = 'pending'" in sql:
            self.patch = {"status": "pending", "attempt": self.row["attempt"], "body": params[0], "error": None}
        elif "status = 'manual'" in sql:
            self.patch = {"status": "manual", "attempt": self.row["attempt"], "body": params[0], "error": params[1]}
        else:
            self.patch = {"status": params[0], "attempt": params[1], "body": params[3], "error": params[2]}

    def fetchone(self):
        return deepcopy(self.row)

    @contextmanager
    def get_cursor(self, **kwargs):
        yield self


def verify(source):
    contents = {}
    for path, expected in HASHES.items():
        raw = source[path].encode() if isinstance(source, dict) else (Path(source) / path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected, "Source mismatch: " + path
        contents[path] = raw.decode()
    counters = {"classifications": 0, "ack_transitions": 0, "external_references": 0, "frozen_requests": 0, "dispatch_results": 0}
    constant_names = {"ACK_OUTCOMES", "STAGE_SUCCESS", "STAGE_FAILED", "STAGE_WAITING_LOCK",
                      "STAGE_NEEDS_MAPPING", "STAGE_NEEDS_REVIEW", "STAGE_ROLLED_BACK"}
    constants = definitions(contents["services/erp/express_push/common.py"], constant_names)
    common = SimpleNamespace(**{k: constants[k] for k in constant_names}, sanitize_push_meta=lambda meta: {})
    retry_names = {"_ERP_RETRY_DELAYS_SEC", "ERP_MAX_RETRIES", "USER_DATA_ERROR_CODES", "USER_DATA_ERROR_THAI_PATTERNS",
                   "_ALREADY_PUSHED_THAI_PATTERNS", "is_already_pushed_error", "classify_push_status",
                   "counts_as_endpoint_success", "is_user_data_error", "get_erp_retry_delay_sec"}
    old_retry = definitions(contents["services/erp/push_retry.py"], retry_names)
    errors = [None, "", "timeout", "EXPRESS_QUEUED", "EXPRESS_MANUAL: mapping", "EXPRESS_MANUAL EXPRESS_QUEUED"]
    errors += list(old_retry["USER_DATA_ERROR_CODES"]) + list(old_retry["USER_DATA_ERROR_THAI_PATTERNS"])
    errors += [f"raw prefix {error} suffix" for error in errors if error]
    for error in errors:
        for name in ("is_already_pushed_error", "is_user_data_error"):
            assert old_retry[name](error) == getattr(retry, name)(error)
            counters["classifications"] += 1
        for success in (False, True):
            assert old_retry["classify_push_status"](success, error) == retry.classify_push_status(success, error)
            counters["classifications"] += 1
    for count in range(-3, 10):
        assert old_retry["get_erp_retry_delay_sec"](count) == retry.get_erp_retry_delay_sec(count)
        counters["classifications"] += 1
    for status in (None, "pending", "success", "manual", "failed", "skipped_dup", "retrying", ""):
        assert old_retry["counts_as_endpoint_success"](status) == retry.counts_as_endpoint_success(status)
        counters["classifications"] += 1

    # Only the legacy agent_store.ack is compared: the managed shared-agent
    # branch (shared_express_agent_queue.ack_managed) was never enabled and is
    # retired (D9), so the native lifecycle rejects it outright.
    try:
        ack_rules.acknowledgement(branch="managed", attempt=1, success=True)
    except lifecycle.LifecycleError:
        pass
    else:
        raise AssertionError("retired managed branch was accepted")
    for attempt in (0, 1, 2, 3, 4):
        for outcome in (None, "bad", *common.ACK_OUTCOMES):
            for success in (False, True):
                for error in (None, "timeout", "x" * 600):
                    row = {"id": "L1", "status": "pending", "history_id": "H1", "attempt": attempt,
                           "lease_owner": "W", "request_body": {}}
                    sink = Sink(row)
                    extra = {"C": common, "common": common, "db": sink, "_MAX_ATTEMPTS": 3,
                        "_load_owned_log": lambda *a: deepcopy(row), "_build_response_body": lambda **kw: kw,
                        "_mirror_history_status": lambda *a: None, "_bump_created_stock_masters": lambda *a: None,
                        "_agent_id": lambda value: value}
                    ack = definitions(contents["services/erp/express_push/agent_store.py"], {"ack"}, extra)["ack"]
                    args = dict(success=success, error=error, outcome=outcome, express_docnum="IV69001")
                    result = ack("E1", "L1", "W", **args)
                    assert result.get("ok") and sink.patch, result
                    expected = sink.patch
                    new = ack_rules.acknowledgement(branch="legacy", attempt=attempt, **{k: args[k] for k in ("success", "error", "outcome")})
                    assert (expected["status"], expected["attempt"], expected["body"]["stage"], expected["error"] or "") == (
                        new.status, new.attempt, new.stage, new.reason), (attempt, args, expected, new)
                    counters["ack_transitions"] += 1

    external_names = {"_EMPTY", "_result", "request_payload", "_coerce_body", "_derive_mrerp", "_derive_express",
                      "_derive_default", "derive_external_ref"}
    old = definitions(contents["services/erp/external_ref.py"], external_names)
    old["_DERIVERS"] = {"express": old["_derive_express"], "mrerp": old["_derive_mrerp"]}
    bodies = [None, "", "bad", "1", b"{}", [], {}, {"express_docnum": " IV001 "}, {"mrerp_bill_no": " B001 "},
              {"doc_no": 12, "id": 17, "url": "https://erp.example.invalid/doc/17"}, {"meta": {"docnum": "R1"}}]
    bodies += [json.dumps(body) for body in bodies if isinstance(body, (dict, list))]
    for body in bodies:
        assert old["request_payload"](body) == receipts.request_payload(body)
        for adapter in (None, "express", "EXPRESS", "mrerp", "webhook"):
            for status in (None, "success", "failed", "skipped_dup"):
                assert old["derive_external_ref"](adapter, body, status) == receipts.derive_external_ref(adapter, body, status)
                counters["external_references"] += 1
    old_final = definitions(contents["services/erp/confirmed_push_reservation.py"], {"_final_request_body"})["_final_request_body"]
    reserved = {"source": "cowork_line", "account_set": "BOOK", "target_intent": {"endpoint_id": "E1", "binding_generation": 0}}
    for body in (None, {}, {"account_set": "OTHER"}, {"target_intent": {"endpoint_id": "wrong"}}, {"payload_version": 1}):
        assert old_final(reserved, {"request_body": body}) == lifecycle.frozen_request(reserved, body)
        counters["frozen_requests"] += 1

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    for adapter in ("mrerp", "express"):
        for success in (False, True):
            for error in errors:
                captured = {}
                result = {"success": success, "error_msg": error, "http_status": 200 if success else 500}
                def finalize(reservation, response, *, retry_delay_sec):
                    captured["delay"] = retry_delay_sec
                    return old_retry["classify_push_status"](response["success"], response["error_msg"])
                def unknown(*args):
                    captured["unknown"] = True
                noop = lambda *a, **k: None
                old = definitions(contents["services/erp/confirmed_push.py"], {"_dispatch_reserved_push"}, {
                    "asyncio": SimpleNamespace(to_thread=inline),
                    "erp_push": SimpleNamespace(push_to_endpoint=lambda *a, **k: result),
                    "confirmed_push_reservation": SimpleNamespace(finalize_reserved_push=finalize, mark_reserved_push_unknown=unknown),
                    "db": SimpleNamespace(**{k: old_retry[k] for k in ("classify_push_status", "is_user_data_error",
                        "get_erp_retry_delay_sec", "counts_as_endpoint_success")}, update_endpoint_stats=noop,
                        update_history_push_status=noop), "notify_success": noop,
                })["_dispatch_reserved_push"]
                reserved = {"endpoint": {"adapter": adapter, "id": "E1"}, "history": {}, "log_id": "L1", "history_id": "H1"}
                observed = asyncio.run(old(reserved, posting_kind=None))
                new = lifecycle.reserved_result(adapter=adapter, result=result)
                old_status = "failed" if observed["status"] == "retrying" else observed["status"]
                assert (old_status, captured.get("delay")) == (new["status"], new["retry_delay_sec"]), (adapter, result, observed, new)
                if captured.get("unknown"):
                    assert new["error_msg"] == lifecycle.UNKNOWN_RESULT
                counters["dispatch_results"] += 1
    return {"source_sha": SOURCE_SHA, **counters,
            "limits": "Pure state decisions and SQL parameter effects only; no locks, auth, DB or external write."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source")
    inputs.add_argument("--source-stdin", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(json.load(sys.stdin) if args.source_stdin else args.source), ensure_ascii=False))
