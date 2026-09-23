"""Pure target contracts; fixture rows are synthetic, not a real ERP directory."""
from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError, replace
import json
import unittest

from pearnly_cowork.targets import TargetAccess, TargetEvidence, TargetSelectionError, freeze_target
from pearnly_cowork.targets import choice, choices, evidence, identity, projection, selection

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
NEW_REQUEST_ID = "22222222-2222-4222-8222-222222222222"


def endpoint(adapter="mrerp", **changes):
    value = {"id": "endpoint-1", "enabled": True, "adapter": adapter, "binding_generation": 0,
             "config": ({"comidyear": "6", "seldb": "1"} if adapter == "mrerp" else
                        {"account_set": r"C:\ERP\OLD", "express_root": r"C:\ERP"})}
    value.update(changes)
    return value


def refresh(adapter="mrerp", **changes):
    return {"id": REQUEST_ID, "status": "succeeded", "account_set_key": "@endpoint", "adapter": adapter,
            "result_revision": 7, "head_status": "fresh", "head_revision": 7, "snapshot_adapter": adapter, **changes}


def snapshot(adapter="mrerp", **changes):
    rows = ([{"source_id": "7:2", "label": "บริษัท ปี 7", "active": True,
              "attributes": {"comidyear": "7", "seldb": "2"}}] if adapter == "mrerp" else
            [{"source_id": r"c:\erp\new", "label": "ปีใหม่", "active": True,
              "attributes": {"path": r"C:\ERP\NEW", "root": r"C:\ERP", "writable": True,
                             "mapping": {"ar_acc": "1101"}}}])
    return {"snapshot_id": "snapshot-7", "adapter": adapter, "account_sets": rows,
            "head_snapshot_id": "snapshot-7", "head_revision": 7, "head_status": "fresh", **changes}


def evidence_args(adapter="mrerp", **changes):
    return {"tenant_id": "tenant-1", "user_id": "user-1", "endpoint_id": "endpoint-1", "adapter": adapter,
            "selected_account_set_key": "7:2" if adapter == "mrerp" else r"C:\ERP\NEW",
            "bound_account_set_key": "6:1" if adapter == "mrerp" else r"C:\ERP\OLD",
            "request_id": REQUEST_ID, "revision": 7,
            "latest_refresh": refresh(adapter), "snapshot": snapshot(adapter), **changes}


def access(**changes):
    return replace(TargetAccess("tenant-1", "user-1", "endpoint-1", 42, True), **changes)


def rows(adapter="mrerp", **changes):
    return replace(TargetEvidence("tenant-1", "user-1", "endpoint-1", refresh(adapter), snapshot(adapter)), **changes)


def observation(**changes):
    return {"adapter": "express", "observed_at": "2026-09-22T00:00:00+07:00",
            "collector": {"kind": "companion", "device_id": "synthetic-device"},
            "account_sets": [{"source_id": "B", "label": "บัญชี B"}, {"source_id": "A"}],
            "masters": {"products": [{"source_id": "P-1", "attributes": {"price": 12.5}}]},
            "form_schema": {"fields": [{"key": "posting_kind", "type": "select", "options": [{"source_id": "stock"}]}]},
            "capabilities": {"actions": [{"key": "push", "enabled": False, "block_reason": "synthetic"}]}, **changes}


class TargetTests(unittest.TestCase):
    def test_identity_keeps_explicit_program_root_pairing(self):
        self.assertEqual(identity.normalize_express_account_key(" C:/ERP/NEW/ "), r"c:\erp\new")
        self.assertEqual(identity.reported_account_set_roots([{"path": r"S:\2569\EXP69\COMPANY"}]), {})
        values = [{"path": r"S:\2569\EXP69\COMPANY", "root": r"\\server\program\69EXP"}]
        self.assertEqual(identity.reported_account_set_roots(values), {r"s:\2569\exp69\company": r"\\server\program\69exp"})

    def test_source_choices_keep_projected_path_and_mapping(self):
        value = choices.account_choices_for_endpoint(endpoint("express"), {"account_sets": snapshot("express")["account_sets"]})
        self.assertEqual(value[0]["key"], r"c:\erp\new")
        self.assertEqual(value[0]["account_dir"], r"C:\ERP\NEW")
        self.assertEqual(value[0]["mapping"], {"ar_acc": "1101"})
        self.assertEqual(choices.account_choices_for_endpoint(endpoint("mrerp")), [])

    def test_legacy_reported_choices_deduplicate_paths_and_retain_current(self):
        ep = endpoint("express", config={"account_set": r"C:\ERP\OLD", "reported_account_sets": [
            {"path": r"C:\ERP\NEW", "writable": True}, {"path": "c:/erp/new/", "writable": False}]})
        result = choices.account_choices_for_endpoint(ep)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["writable"])
        self.assertEqual(result[1]["account_set"], r"C:\ERP\OLD")

    def test_default_target_does_not_require_a_refresh(self):
        result = evidence.validate_selection(**evidence_args(selected_account_set_key="6:1", request_id=None, revision=None,
                                                              latest_refresh=None, snapshot=None))
        self.assertEqual((result["ok"], result["proof_required"], result["reason"]), (True, False, "bound_default"))
        self.assertEqual(selection.resolve_account_choice(endpoint(), tenant_id="tenant-1", user_id="user-1"),
                         {"key": "6:1", "comidyear": "6", "seldb": "1"})

    def test_managed_default_uses_bound_account_as_source_does(self):
        ep = endpoint("express", binding_generation=3, bound_account_set=r"C:\ERP\BOUND", config={})
        selected = selection.resolve_account_choice(ep, tenant_id="tenant-1", user_id="user-1")
        self.assertEqual(selected["key"], r"c:\erp\bound")
        self.assertEqual(selected["account_dir"], r"C:\ERP\BOUND")

    def test_nondefault_requires_valid_request_and_revision(self):
        for request_id, revision, reason in ((None, None, "proof_missing"), ("bad", 7, "proof_malformed"),
                                             (REQUEST_ID, True, "proof_malformed"), (REQUEST_ID, -1, "proof_malformed")):
            result = evidence.validate_selection(**evidence_args(request_id=request_id, revision=revision))
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], reason)
        self.assertTrue(evidence.validate_selection(**evidence_args())["ok"])

    def test_refresh_receipt_rejects_superseded_failed_or_wrong_scope(self):
        cases = [({}, "refresh_not_found"), ({"id": NEW_REQUEST_ID}, "refresh_superseded"),
                 ({"status": "leased"}, "refresh_not_succeeded"), ({"account_set_key": "7:2"}, "refresh_scope_mismatch"),
                 ({"adapter": "express"}, "adapter_mismatch"), ({"result_revision": 6}, "revision_mismatch")]
        for change, expected in cases:
            row = refresh(**change) if change else None
            result = evidence.validate_selection(**evidence_args(latest_refresh=row))
            self.assertEqual(result["reason"], expected)
        result = evidence.validate_refresh_receipt(tenant_id="tenant-1", user_id="user-1", endpoint_id="endpoint-1",
                    adapter="mrerp", request_id=REQUEST_ID, request_revision=7, catalog_revision=8, latest_refresh=refresh())
        self.assertEqual(result["reason"], "revision_mismatch")

    def test_projection_must_be_current_fresh_and_same_adapter(self):
        for change, expected in (({"head_status": "stale"}, "projection_not_fresh"),
                                 ({"head_revision": 8}, "snapshot_superseded"),
                                 ({"head_snapshot_id": "other"}, "snapshot_superseded"),
                                 ({"adapter": "express"}, "adapter_mismatch"),
                                 ({"account_sets": None}, "snapshot_invalid"),
                                 ({"account_sets": []}, "account_not_found")):
            self.assertEqual(evidence.validate_selection(**evidence_args(snapshot=snapshot(**change)))["reason"], expected)

    def test_inactive_read_only_and_root_mismatch_are_not_selectable(self):
        for key, value, expected in (("active", False, "account_inactive"), ("writable", False, "account_not_writable"),
                                     ("root", r"C:\OTHER", "root_mismatch")):
            snap = snapshot("express")
            row = snap["account_sets"][0]
            (row if key == "active" else row["attributes"])[key] = value
            result = evidence.validate_selection(**evidence_args("express", snapshot=snap, bound_root_key=r"C:\ERP"))
            self.assertEqual(result["reason"], expected)

    def test_same_data_directory_different_program_root_requires_proof(self):
        result = evidence.validate_selection(**evidence_args("express", selected_account_set_key=r"C:\ERP\OLD",
                    selected_root_key=r"C:\OTHER", bound_root_key=r"C:\ERP", request_id=None, revision=None))
        self.assertEqual(result["reason"], "proof_missing")

    def test_switching_account_clears_old_mapping_and_catalogue_only(self):
        ep = endpoint("express", config={"account_set": r"C:\ERP\OLD", "account_dir": r"C:\ERP\OLD",
                     "revenue_acc": "old", "reported_products": [{"code": "old"}], "catalog_fingerprint": "old",
                     "username_enc": "do-not-expose", "other_flag": True})
        before = deepcopy(ep)
        result = choice.endpoint_with_account_choice(ep, {"account_set": r"C:\ERP\NEW", "root_key": r"C:\ERP",
                                                          "mapping": {"revenue_acc": "new", "unapproved": "ignored"}})
        self.assertEqual(ep, before)
        self.assertEqual(result["config"]["revenue_acc"], "new")
        self.assertNotIn("reported_products", result["config"])
        self.assertNotIn("catalog_fingerprint", result["config"])
        self.assertNotIn("unapproved", result["config"])
        self.assertTrue(result["config"]["other_flag"])

    def test_forged_choice_is_not_copied_through(self):
        with self.assertRaises(TargetSelectionError) as caught:
            selection.resolve_account_choice(endpoint(), tenant_id="tenant-1", user_id="user-1", account_set_key="9:9")
        self.assertEqual(caught.exception.code, "erp.account_set_unavailable")
        # The legacy trusted LINE fallback remains an explicit server-only input.
        trusted = selection.resolve_account_choice(endpoint(), tenant_id="tenant-1", user_id="user-1",
                    account_set_key="7:2", trusted_account_config={"comidyear": "7", "seldb": "2"})
        self.assertEqual(trusted["key"], "7:2")

    def test_freeze_combines_proof_choice_and_current_access(self):
        result = freeze_target(endpoint("express"), access=access(), evidence=rows("express"),
                               account_set_key=r"C:\ERP\NEW", request_id=REQUEST_ID, revision=7)
        self.assertEqual(result.account_set, r"c:\erp\new")
        self.assertEqual(result.account_config()["ar_acc"], "1101")
        self.assertEqual(result.catalog_projection_revision, 7)
        self.assertEqual(result.root_key, r"c:\erp")
        self.assertEqual(result.workspace_client_id, 42)
        self.assertTrue(result.proof_required)

    def test_frozen_values_are_immutable_and_do_not_include_credentials(self):
        ep = endpoint(config={"username_enc": "secret", "password": "secret", "comidyear": "6", "seldb": "1"})
        result = freeze_target(ep, access=access(), evidence=rows())
        ep["config"]["comidyear"] = "99"
        self.assertEqual(result.account_set, "6:1")
        detached = result.account_config();detached["comidyear"] = "8"
        self.assertEqual(result.account_config()["comidyear"], "6")
        self.assertNotIn("secret", json.dumps(asdict(result)))
        with self.assertRaises(FrozenInstanceError): result.account_set = "other"

    def test_freeze_rejects_bound_proof_root_that_differs_from_inferred_choice_root(self):
        ep = endpoint("express", config={"account_set": r"D:\DATA\OLD", "express_root": r"D:\APP"})
        snap = snapshot("express", account_sets=[{"source_id": r"E:\DATA\BOOK", "active": True,
                "attributes": {"path": r"E:\DATA\BOOK", "writable": True, "mapping": {"ar_acc": "1101"}}}])
        arguments = dict(tenant_id="tenant-1", user_id="user-1", account_set_key=r"E:\DATA\BOOK")
        proof = selection.require_catalog_evidence(ep, **arguments, request_id=REQUEST_ID,
                    revision=7, latest_refresh=refresh("express"), snapshot=snap)
        selected = choice.endpoint_with_account_choice(ep, selection.resolve_account_choice(
                    ep, **arguments, state={"snapshot": snap}))
        self.assertEqual(proof["root_key"], r"d:\app")
        self.assertEqual(selected["config"]["express_root"], r"E:\DATA")
        with self.assertRaises(TargetSelectionError) as caught:
            freeze_target(ep, access=access(), evidence=rows("express", snapshot=snap),
                          account_set_key=r"E:\DATA\BOOK", request_id=REQUEST_ID, revision=7)
        self.assertEqual((caught.exception.code, caught.exception.reason),
                         ("catalog_refresh_invalid", "frozen_root_mismatch"))

    def test_freeze_rejects_missing_either_proof_or_selected_config_root(self):
        configs = [{"account_set": r"C:\ERP\OLD"},
                   {"account_set": r"C:\ERP\OLD", "reported_account_sets": [
                       {"path": r"C:\ERP\OLD", "root": r"C:\ERP"}]}]
        for config in configs:
            with self.subTest(config=config), self.assertRaises(TargetSelectionError) as caught:
                freeze_target(endpoint("express", config=config), access=access(), evidence=rows("express"))
            self.assertEqual(caught.exception.reason, "frozen_root_missing")

    def test_freeze_legacy_default_uses_explicit_server_root_without_directory_refresh(self):
        ep = endpoint("express", config={"account_set": r"C:\OLD", "express_root": "D:/APP/./"})
        proof = selection.require_catalog_evidence(ep, tenant_id="tenant-1", user_id="user-1")
        self.assertFalse(proof["proof_required"])
        self.assertIsNone(proof["root_key"])
        result = freeze_target(ep, access=access(), evidence=rows("express", latest_refresh=None, snapshot=None))
        self.assertEqual(result.account_set, r"c:\old")
        self.assertEqual(result.root_key, r"d:\app")
        self.assertFalse(result.proof_required)
        self.assertIsNone(result.catalog_refresh_request_id)
        self.assertIsNone(result.catalog_projection_revision)
        self.assertEqual(result.account_config()["express_root"], "D:/APP/./")

    def test_freeze_managed_bound_default_uses_explicit_server_root_without_refresh(self):
        ep = endpoint("express", binding_generation=3, bound_account_set=r"C:\BOUND", bound_profile_key="profile-3",
                      config={"account_set": r"C:\OLD", "express_root": r"D:\APP", "ar_acc": "old"})
        result = freeze_target(ep, access=access(), evidence=rows("express", latest_refresh=None, snapshot=None),
                               account_set_key=r"C:\BOUND")
        self.assertEqual(result.account_set, r"c:\bound")
        self.assertEqual(result.root_key, r"d:\app")
        self.assertEqual((result.binding_generation, result.bound_profile_key), (3, "profile-3"))
        self.assertFalse(result.proof_required)
        self.assertIsNone(result.catalog_refresh_request_id)
        self.assertNotIn("ar_acc", result.account_config())

    def test_freeze_rejects_managed_proof_for_one_account_and_final_config_for_another(self):
        ep = endpoint("express", binding_generation=3, bound_account_set=r"C:\BOUND",
                      config={"account_set": r"C:\OLD", "express_root": r"D:\APP"})
        arguments = dict(tenant_id="tenant-1", user_id="user-1", account_set_key=r"C:\BOUND",
                         trusted_account_config={"account_dir": r"C:\BOUND", "root_key": r"D:\APP"})
        proof = selection.require_catalog_evidence(ep, **arguments)
        configured = choice.endpoint_with_account_choice(ep, selection.resolve_account_choice(ep, **arguments))
        self.assertFalse(proof["proof_required"])
        self.assertEqual(proof["account_set_key"], r"c:\bound")
        self.assertEqual(configured["config"]["account_set"], r"C:\OLD")
        self.assertEqual(configured["config"]["account_dir"], r"C:\BOUND")
        with self.assertRaises(TargetSelectionError) as caught:
            freeze_target(ep, access=access(), evidence=rows("express", latest_refresh=None, snapshot=None),
                          account_set_key=arguments["account_set_key"], trusted_account_config=arguments["trusted_account_config"])
        self.assertEqual(caught.exception.reason, "frozen_account_mismatch")

    def test_freeze_compares_mrerp_final_year_database_to_the_proven_key(self):
        with self.assertRaises(TargetSelectionError) as caught:
            freeze_target(endpoint(), access=access(), evidence=rows(latest_refresh=None, snapshot=None),
                          trusted_account_config={"comidyear": " ", "seldb": " "})
        self.assertEqual(caught.exception.reason, "frozen_account_mismatch")

    def test_freeze_mrerp_trims_each_year_database_component_like_source_reservation(self):
        ep = endpoint(config={"comidyear": " 6 ", "seldb": " 1 "})
        result = freeze_target(ep, access=access(), evidence=rows(latest_refresh=None, snapshot=None),
                               trusted_account_config={"comidyear": " 6 ", "seldb": " 1 "})
        self.assertEqual(result.account_set, "6:1")
        self.assertFalse(result.proof_required)

    def test_freeze_does_not_accept_matching_parent_inference_as_explicit_proof(self):
        snap = snapshot("express")
        del snap["account_sets"][0]["attributes"]["root"]
        # Both old layers yield C:\ERP, but only from bound fallback / dirname.
        with self.assertRaises(TargetSelectionError) as caught:
            freeze_target(endpoint("express"), access=access(), evidence=rows("express", snapshot=snap),
                          account_set_key=r"C:\ERP\NEW", request_id=REQUEST_ID, revision=7)
        self.assertEqual(caught.exception.reason, "frozen_root_unproven")

    def test_freeze_accepts_explicit_canonical_roots_without_requiring_raw_spelling_match(self):
        for origin in ("snapshot", "reported", "trusted"):
            ep, snap, trusted = endpoint("express"), snapshot("express"), None
            del snap["account_sets"][0]["attributes"]["root"]
            if origin == "snapshot":
                snap["account_sets"][0]["attributes"]["root"] = "C:/ERP/./"
            elif origin == "reported":
                ep["config"]["reported_account_sets"] = [{"path": "c:/erp/NEW/", "root": "C:/ERP/./"}]
            else:
                trusted = {"account_set": r"C:\ERP\NEW", "root_key": "C:/ERP/./"}
            with self.subTest(origin=origin):
                result = freeze_target(ep, access=access(), evidence=rows("express", snapshot=snap),
                    account_set_key=r"C:\ERP\NEW", trusted_account_config=trusted, request_id=REQUEST_ID, revision=7)
                self.assertEqual(result.root_key, r"c:\erp")
                self.assertEqual(identity.normalize_express_account_key(result.account_config()["express_root"]), result.root_key)

    def test_freeze_adapts_raw_database_json_before_resolving_the_same_snapshot(self):
        snap = snapshot()
        snap["account_sets"] = json.dumps(snap["account_sets"])
        result = freeze_target(endpoint(), access=access(), evidence=rows(snapshot=snap),
                               account_set_key="7:2", request_id=REQUEST_ID, revision=7)
        self.assertEqual(result.account_set, "7:2")
        self.assertIsInstance(snap["account_sets"], str)

    def test_freeze_rejects_denial_cross_context_disabled_and_missing_proof(self):
        for ep, grant, proof, wanted in ((endpoint(), access(authorized=False), rows(), "erp.target_access_denied"),
                    (endpoint(), access(), rows(endpoint_id="other"), "erp.target_context_invalid"),
                    (endpoint(enabled=False), access(), rows(), "erp.endpoint_disabled")):
            with self.assertRaises(TargetSelectionError) as caught:
                freeze_target(ep, access=grant, evidence=proof)
            self.assertEqual(caught.exception.code, wanted)
        with self.assertRaises(TargetSelectionError) as caught:
            freeze_target(endpoint(), access=access(), evidence=rows(), account_set_key="7:2",
                          trusted_account_config={"comidyear": "7", "seldb": "2"})
        self.assertEqual(caught.exception.code, "catalog_refresh_required")

    def test_projection_hash_ignores_observation_order_time_and_collector(self):
        raw = observation(); changed = deepcopy(raw)
        changed["observed_at"] = "2026-09-23T00:00:00Z"
        changed["collector"] = {"kind": "synthetic"}
        changed["account_sets"].reverse()
        first, second = projection.normalize_projection(raw), projection.normalize_projection(changed)
        self.assertEqual(first.source_hash, second.source_hash)
        self.assertEqual(first.component_hashes, second.component_hashes)
        self.assertEqual(first.observed_at.isoformat(), "2026-09-21T17:00:00+00:00")

    def test_projection_retains_full_schema_masters_and_capabilities(self):
        result = projection.normalize_projection(observation())
        self.assertEqual(set(result.masters), set(projection.ENTITY_TYPES))
        self.assertEqual(result.form_schema["fields"][0]["type"], "select")
        self.assertFalse(result.capabilities["actions"][0]["enabled"])
        self.assertEqual(result.collector["device_id"], "synthetic-device")

    def test_projection_rejects_duplicate_sensitive_unbounded_and_bad_clock(self):
        cases = [(observation(account_sets=[{"source_id": "A"}] * 2), "erp.target_projection_duplicate_source_id"),
                 (observation(account_sets=[{"source_id": "A", "attributes": {"api_token": "secret"}}]), "erp.target_projection_sensitive_field"),
                 (observation(account_sets=[{"source_id": str(i)} for i in range(5001)]), "erp.target_projection_too_large"),
                 (observation(observed_at="2026-09-22T00:00:00"), "erp.target_projection_observed_at_invalid"),
                 (observation(collector={"token": "secret"}), "erp.target_projection_collector_invalid"),
                 (observation(masters={"other": []}), "erp.target_projection_unknown_entity")]
        for raw, expected in cases:
            with self.assertRaises(projection.ProjectionContractError) as caught: projection.normalize_projection(raw)
            self.assertEqual(caught.exception.code, expected)


if __name__ == "__main__":
    unittest.main()
