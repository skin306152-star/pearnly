"""Hash-bound offline target differential; legacy database reads are row fixtures.

PYTHONPATH=apps/pearnly_cowork:tests python3.14 scripts/cowork-targets-source-parity.py --source SNAPSHOT
No legacy application import, Frappe, database, credential lookup or network.
"""
import argparse
import ast
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace

from pearnly_cowork.targets import choice, choices, evidence, identity, projection, selection, freeze_target
from test_cowork_targets import REQUEST_ID, NEW_REQUEST_ID, access, rows as target_evidence, endpoint, evidence_args, observation, refresh, snapshot

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "selected_account.py": "c6c339870c0da419633212324b782e21ef8ae5d2f99b76992761ef9c050c5c17",
    "target_catalog_evidence.py": "643d8e0e1efd90e17b6a9e186ec6ed67bef3b68c52b81e090ec33b2526209419",
    "line_target_choice.py": "1ed3f97de9fe39eff593fe8bbac1bd9b6f78ab7efca8d5118f1c883bf603d22e",
    "target_projection_contract.py": "44e380ac443ac1ffa289ce387ed3c6465ff6b4edfaf5994a5c4561e800a8a7c2",
    "line_target_projection.py": "3aa6d8f8fd5da0093c962424822fbd4fc710f04a64c1b3d83ac8199a2eac9507",
    "express_account_identity.py": "6271c1277c12bffbad09a02514ca29255bdb68f776106d5232f4f2241b8eaa4b",
    "target_projection_store.py": "cfb97b650fb45595073ecc576f34e167d233fb6ed3ccd9bcaa3c0b6a136be63d",
}


class SourceConflict(ValueError):
    def __init__(self, status_code, *, detail):
        self.status_code, self.detail = status_code, detail


class Cursor:
    def __init__(self, rows): self.rows = deepcopy(rows)
    def execute(self, *_args): pass
    def fetchone(self): return self.rows.pop(0) if self.rows else None
    def __enter__(self): return self
    def __exit__(self, *_args): return False


def load_sources(root=None, source_texts=None):
    trees = {}
    for name, expected in HASHES.items():
        relative = "services/erp/" + name
        if source_texts is None:
            data = (Path(root) / relative).read_bytes()
        else:
            value = source_texts.get(relative)
            if not isinstance(value, str):
                raise ValueError("Missing source text: " + relative)
            data = value.encode("utf-8")
        assert hashlib.sha256(data).hexdigest() == expected, name
        tree = ast.parse(data.decode())
        tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom)
            and (n.module or "").split(".")[0] in {"services", "core", "fastapi"})]
        trees[name] = tree

    def execute(name, **injected):
        namespace = {"__name__": __name__, **injected}
        exec(compile(trees[name], name, "exec"), namespace)
        return SimpleNamespace(**namespace)

    old_identity = execute("express_account_identity.py")
    old_choice = execute("line_target_choice.py")
    old_choices = execute("line_target_projection.py", normalize_express_account_key=old_identity.normalize_express_account_key)
    old_projection = execute("target_projection_contract.py")
    store_tree = trees["target_projection_store.py"]
    store_tree.body = [node for node in store_tree.body if isinstance(node, ast.FunctionDef) and node.name == "_json"]
    old_store = execute("target_projection_store.py", Any=object, json=__import__("json"))
    fixture = SimpleNamespace(state=None, rows=[])
    old_evidence = execute("target_catalog_evidence.py", ENDPOINT_SCOPE_KEY="@endpoint",
        normalize_express_account_key=old_identity.normalize_express_account_key,
        db=SimpleNamespace(get_cursor_rls=lambda **kw: Cursor(fixture.rows)))
    old_selection = execute("selected_account.py", HTTPException=SourceConflict,
        line_target_projection=old_choices, target_catalog_evidence=old_evidence,
        normalize_express_account_key=old_identity.normalize_express_account_key,
        reported_account_set_roots=old_identity.reported_account_set_roots,
        endpoint_with_account_choice=old_choice.endpoint_with_account_choice,
        load_state=lambda **kw: deepcopy(fixture.state), load_state_with_cursor=lambda *a, **kw: deepcopy(fixture.state))
    return SimpleNamespace(identity=old_identity, choice=old_choice, choices=old_choices,
        projection=old_projection, evidence=old_evidence, selection=old_selection, store=old_store, fixture=fixture)


def outcome(call):
    try:
        result = call()
        return ("value", asdict(result) if is_dataclass(result) else result)
    except Exception as error:
        if hasattr(error, "detail"):
            return ("conflict", error.status_code, error.detail)
        return ("error", type(error).__name__, getattr(error, "code", str(error)))


def compare(expected, actual, case):
    assert expected == actual, (case, expected, actual)


def run(root=None, source_texts=None):
    old, rng = load_sources(root, source_texts), random.Random(20260922)
    counts = {key: 0 for key in ("identity", "choices", "reset", "selection", "evidence", "receipt", "projection", "composition")}
    for _ in range(1000):
        raw = rng.choice((None, "", "C:/ERP/TEST/", r"c:\ERP\..\ERP\TEST", r"\\server\ERP\公司", r"S:\2569\COMPANY"))
        compare(old.identity.normalize_express_account_key(raw), identity.normalize_express_account_key(raw), raw)
        rows = [{"path": raw, "root": rng.choice((None, r"C:\ERP", r"\\server\program"))},
                {"source_id": raw, "attributes": {"path": raw, "root": "D:/PROGRAM"}}]
        compare(old.identity.reported_account_set_roots(rows), identity.reported_account_set_roots(rows), rows)
        counts["identity"] += 2

    for adapter in ("mrerp", "express"):
        for index in range(250):
            ep = endpoint(adapter)
            state = {"snapshot": snapshot(adapter)} if rng.randrange(2) else None
            if adapter == "express":
                ep["config"].update(reported_account_sets=[{"path": r"C:\ERP\NEW", "writable": bool(index % 2)},
                                                           {"path": "c:/erp/new/", "writable": False}])
            probe = old.selection._projection_probe(ep, state)
            compare(old.choices.account_choices_for_endpoint(ep, probe), choices.account_choices_for_endpoint(ep, probe), (adapter, index))
            counts["choices"] += 1
            target = rng.choice((None, "", "6:1", "7:2", "9:9") if adapter == "mrerp" else
                                (None, "", r"C:\ERP\OLD", r"C:\ERP\NEW", r"D:\OTHER"))
            trusted = rng.choice((None, {}, {"comidyear": "7", "seldb": "2"}) if adapter == "mrerp" else
                                 (None, {}, {"account_set": r"D:\OTHER", "writable": bool(index % 2)}))
            arguments = dict(tenant_id="tenant-1", user_id="user-1", account_set_key=target, trusted_account_config=trusted)
            old.fixture.state = state
            for name in ("resolve_account_choice", "resolve_endpoint_account"):
                compare(outcome(lambda: getattr(old.selection, name)(deepcopy(ep), **arguments)),
                        outcome(lambda: getattr(selection, name)(deepcopy(ep), state=deepcopy(state), **arguments)), (name, adapter, index))
                counts["selection"] += 1
            if adapter == "express":
                compare(old.selection.allowed_express_account_keys(ep, tenant_id="tenant-1", cur=Cursor([])),
                        selection.allowed_express_account_keys(ep, tenant_id="tenant-1", state=state), index)
                counts["selection"] += 1

            config = {"account_set": r"C:\ERP\OLD", "account_dir": r"C:\ERP\OLD", "revenue_acc": "old", "ar_acc": "old",
                      "reported_products": [{"source_id": "old"}], "catalog_fingerprint": "old", "retained": "unchanged"}
            ep = endpoint("express", config=config)
            picked = {"account_set": rng.choice((r"C:\ERP\OLD", "c:/erp/old/", r"C:\ERP\NEW")),
                      "root_key": r"C:\ERP", "mapping": {"ar_acc": "new", "not_a_slot": "ignored"}}
            compare(old.choice.endpoint_with_account_choice(deepcopy(ep), deepcopy(picked)),
                    choice.endpoint_with_account_choice(deepcopy(ep), deepcopy(picked)), ("reset", index))
            counts["reset"] += 1

    refresh_variants = [None, {}, refresh(), refresh(id=NEW_REQUEST_ID), refresh(status="requested"),
                        refresh(account_set_key="7:2"), refresh(adapter="express"), refresh(result_revision=8)]
    snapshot_variants = [None, {}, snapshot(), snapshot(head_status="stale"), snapshot(head_revision=8),
                         snapshot(head_snapshot_id="other"), snapshot(adapter="express"), snapshot(account_sets="bad"),
                         snapshot(account_sets=[]), snapshot(account_sets=[{"source_id": "7:2", "active": False}])]
    # 有意的变化（用户 2026-09-27）：不带刷新回执时按当前目录核对（旧版一律 proof_missing），
    # 见 tests/test_cowork_targets.py 的 test_without_receipt_*；这里只比带回执的路径。
    for ref in refresh_variants:
        for snap in snapshot_variants:
            for request_id, revision in ((REQUEST_ID, 7), ("bad", 7), (REQUEST_ID, True), (REQUEST_ID, "7")):
                args = evidence_args(latest_refresh=ref, snapshot=snap, request_id=request_id, revision=revision)
                rows_args = {key: args.pop(key) for key in ("latest_refresh", "snapshot")}
                compare(old.evidence.validate_selection(Cursor([ref, snap]), **args),
                        evidence.validate_selection(**args, **rows_args), (ref, snap, request_id, revision))
                counts["evidence"] += 1
    for index in range(1500):
        adapter = rng.choice(("express", "mrerp", "other"))
        args = evidence_args(adapter if adapter in {"express", "mrerp"} else "express")
        args["adapter"] = adapter
        if rng.randrange(3) == 0: args["selected_account_set_key"] = args["bound_account_set_key"]
        if rng.randrange(4) == 0: args["bound_root_key"] = r"C:\ERP"
        if rng.randrange(4) == 0: args["selected_root_key"] = r"D:\OTHER"
        if rng.randrange(4) == 0: args["request_id"] = rng.choice((None, "bad", NEW_REQUEST_ID))
        if rng.randrange(4) == 0: args["revision"] = rng.choice((None, True, 8, "7", "", -1))
        if args["request_id"] in (None, "") and args["revision"] in (None, ""):
            continue  # 不带回执：有意的变化，见上
        if rng.randrange(6) == 0: args["user_id"] = ""
        ref, snap = args.pop("latest_refresh"), args.pop("snapshot")
        row = snap["account_sets"][0]
        if rng.randrange(4) == 0: row["active"] = False
        if rng.randrange(4) == 0: row["attributes"]["writable"] = False
        if rng.randrange(4) == 0: row["attributes"]["root"] = r"D:\OTHER"
        if rng.randrange(4) == 0: snap["account_sets"] = __import__("json").dumps(snap["account_sets"])
        compare(old.evidence.validate_selection(Cursor([ref, snap]), **args),
                evidence.validate_selection(**args, latest_refresh=ref, snapshot=snap), ("evidence", index))
        counts["evidence"] += 1

    for index in range(500):
        ref = refresh(**rng.choice(({}, {"id": NEW_REQUEST_ID}, {"head_status": "stale"}, {"head_revision": 8},
                                     {"snapshot_adapter": "express"}, {"status": "failed"})))
        args = dict(tenant_id="tenant-1", user_id="user-1", endpoint_id="endpoint-1", adapter="mrerp",
                    request_id=rng.choice((REQUEST_ID, NEW_REQUEST_ID, "bad", None)), request_revision=rng.choice((7, 8, True)),
                    catalog_revision=rng.choice((7, 8)))
        old.fixture.rows = [ref]
        compare(old.evidence.validate_refresh_receipt(**args), evidence.validate_refresh_receipt(**args, latest_refresh=ref), index)
        counts["receipt"] += 1

    for index in range(500):
        raw = observation()
        raw["adapter"] = rng.choice(("express", "mrerp"))
        rng.shuffle(raw["account_sets"])
        if index % 9 == 0: raw["account_sets"].append({"source_id": "A"})
        if index % 13 == 0: raw["collector"] = {"token": "synthetic"}
        if index % 17 == 0: raw["masters"] = {"unknown": []}
        if index % 19 == 0: raw["observed_at"] = "2026-09-22T00:00:00"
        if index % 23 == 0: raw["form_schema"]["fields"][0]["type"] = "unknown"
        if index % 29 == 0: raw["account_sets"][0]["attributes"] = {"n": float("inf")}
        compare(outcome(lambda: old.projection.normalize_projection(deepcopy(raw))),
                outcome(lambda: projection.normalize_projection(deepcopy(raw))), ("projection", index))
        counts["projection"] += 1
    for adapter in ("mrerp", "express"):
        for encoded in (False, True):
            ep, snap, ref = endpoint(adapter), snapshot(adapter), refresh(adapter)
            key = "7:2" if adapter == "mrerp" else r"C:\ERP\NEW"
            if encoded: snap["account_sets"] = __import__("json").dumps(snap["account_sets"])
            args = dict(tenant_id="tenant-1", user_id="user-1", account_set_key=key)
            expected_proof = old.selection.require_catalog_evidence(ep, **args, request_id=REQUEST_ID,
                revision=7, cur=Cursor([ref, snap]))
            old.fixture.state = {"snapshot": {**snap, "account_sets": old.store._json(snap["account_sets"], [])}}
            expected_choice = old.selection.resolve_account_choice(ep, **args)
            selected = old.choice.endpoint_with_account_choice(ep, expected_choice)["config"]
            actual = freeze_target(ep, access=access(), evidence=target_evidence(adapter, snapshot=snap),
                                   account_set_key=key, request_id=REQUEST_ID, revision=7)
            expected_account = f"{selected['comidyear']}:{selected['seldb']}" if adapter == "mrerp" else selected["account_set"]
            assert actual.account_set == expected_account
            assert actual.root_key == expected_proof["root_key"]
            assert actual.catalog_refresh_request_id == expected_proof["request_id"]
            assert actual.catalog_projection_revision == expected_proof["revision"]
            assert actual.proof_required == expected_proof["proof_required"]
            counts["composition"] += 1

    # Source default selection has no refresh dependency. Its proof may omit
    # root_key, but the explicit server root remains authoritative for default.
    # Only the legacy binding (generation 0) is compared: the managed branch
    # was never enabled and is retired (D9), so a managed binding never freezes.
    for generation in (0,):
        ep = endpoint("express", binding_generation=generation, bound_account_set=r"C:\BOUND",
            config={"account_set": r"C:\OLD", "express_root": "D:/APP/./", "ar_acc": "old"})
        key = None
        args = dict(tenant_id="tenant-1", user_id="user-1", account_set_key=key)
        source_proof = old.selection.require_catalog_evidence(ep, **args, cur=Cursor([]))
        assert source_proof["ok"] and source_proof["proof_required"] is False
        assert source_proof["root_key"] is None
        old.fixture.state = None
        source_choice = old.selection.resolve_account_choice(ep, **args)
        config = old.choice.endpoint_with_account_choice(ep, source_choice)["config"]
        actual = freeze_target(ep, access=access(), evidence=target_evidence("express", latest_refresh=None, snapshot=None),
                               account_set_key=key)
        assert actual.account_set == config["account_set"]
        assert actual.root_key == old.identity.normalize_express_account_key(config["express_root"])
        assert actual.account_config()["express_root"] == config["express_root"]
        assert actual.account_config().get("ar_acc") == config.get("ar_acc")
        assert actual.proof_required is False and actual.catalog_refresh_request_id is None
        assert actual.catalog_projection_revision is None
        counts["composition"] += 1

    # Deliberate native composition guards, separate from source parity counts.
    # The old independent proof and choice calls succeed in every case below.
    unpaired = snapshot("express")
    del unpaired["account_sets"][0]["attributes"]["root"]
    different = snapshot("express", account_sets=[{"source_id": r"E:\DATA\BOOK", "active": True,
        "attributes": {"path": r"E:\DATA\BOOK", "writable": True, "mapping": {"ar_acc": "1101"}}}])
    native_cases = [
        (endpoint("express", config={"account_set": r"D:\DATA\OLD", "express_root": r"D:\APP"}),
         different, r"E:\DATA\BOOK", "frozen_root_mismatch"),
        (endpoint("express", config={"account_set": r"C:\ERP\OLD"}), snapshot("express"), None, "frozen_root_missing"),
        (endpoint("express", config={"account_set": r"C:\ERP\OLD", "reported_account_sets": [
            {"path": r"C:\ERP\OLD", "root": r"C:\ERP"}]}), snapshot("express"), None, "frozen_root_missing"),
        (endpoint("express"), unpaired, r"C:\ERP\NEW", "frozen_root_unproven"),
    ]
    for ep, snap, key, reason in native_cases:
        args = dict(tenant_id="tenant-1", user_id="user-1", account_set_key=key)
        expected_proof = old.selection.require_catalog_evidence(ep, **args, request_id=REQUEST_ID,
            revision=7, cur=Cursor([refresh("express"), snap]))
        assert expected_proof["ok"] is True
        old.fixture.state = {"snapshot": deepcopy(snap)}
        expected_choice = old.selection.resolve_account_choice(ep, **args)
        assert old.choice.endpoint_with_account_choice(ep, expected_choice)["config"]
        actual = outcome(lambda: freeze_target(ep, access=access(), evidence=target_evidence("express", snapshot=snap),
                        account_set_key=key, request_id=REQUEST_ID, revision=7))
        compare(("conflict", 409, {"code": "catalog_refresh_invalid", "reason": reason}), actual, ("native_guard", reason))

    managed = endpoint("express", binding_generation=3, bound_account_set=r"C:\BOUND",
                       config={"account_set": r"C:\OLD", "express_root": r"D:\APP"})
    for key in (r"C:\BOUND", None):
        actual = outcome(lambda: freeze_target(managed, access=access(), evidence=target_evidence("express",
                         latest_refresh=None, snapshot=None), account_set_key=key))
        compare(("conflict", 409, "erp.target_context_invalid"), actual, ("retired_managed", key))
    account_cases = [
        (endpoint(), None, {"comidyear": " ", "seldb": " "}),
    ]
    for ep, key, trusted in account_cases:
        args = dict(tenant_id="tenant-1", user_id="user-1", account_set_key=key, trusted_account_config=trusted)
        source_proof = old.selection.require_catalog_evidence(ep, **args, cur=Cursor([]))
        assert source_proof["ok"] and source_proof["proof_required"] is False
        old.fixture.state = None
        source_choice = old.selection.resolve_account_choice(ep, **args)
        config = old.choice.endpoint_with_account_choice(ep, source_choice)["config"]
        selected_account = (config["account_set"] if ep["adapter"] == "express" else
                            f"{config.get('comidyear') or '6'}:{config.get('seldb') or '1'}")
        assert source_proof["account_set_key"] != selected_account.strip()
        actual = outcome(lambda: freeze_target(ep, access=access(), evidence=target_evidence(ep["adapter"],
                         latest_refresh=None, snapshot=None), account_set_key=key, trusted_account_config=trusted))
        compare(("conflict", 409, {"code": "catalog_refresh_invalid", "reason": "frozen_account_mismatch"}),
                actual, ("native_guard", ep["adapter"], "frozen_account_mismatch"))
    print("PASS source=" + SOURCE_SHA + " " + " ".join(f"{key}={value}" for key, value in counts.items())
          + f" total={sum(counts.values())} intentional_composition_rejections={len(native_cases) + len(account_cases)}"
          + " retired_managed_rejections=2")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source")
    source.add_argument("--source-stdin", action="store_true",
                        help="Read JSON {services/erp/filename.py: sourceText}; verify the same fixed hashes.")
    args = parser.parse_args()
    source_texts = json.load(sys.stdin) if args.source_stdin else None
    if args.source_stdin and not isinstance(source_texts, dict):
        parser.error("--source-stdin requires a JSON object of relative paths to source text")
    run(args.source, source_texts)
