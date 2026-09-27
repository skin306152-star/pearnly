"""Pure target contracts; fixture rows are synthetic, not a real ERP directory."""
from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError, replace
import json
import unittest

from pearnly_cowork.targets import TargetAccess, TargetEvidence, TargetSelectionError, freeze_target
from pearnly_cowork.targets import account_match, catalog_groups, choice, choices, evidence, identity, projection, selection

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

    def test_nondefault_receipt_when_given_must_be_valid(self):
        for request_id, revision, reason in ((None, 7, "proof_missing"), (REQUEST_ID, None, "proof_missing"),
                                             ("bad", 7, "proof_malformed"),
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

    def test_same_data_directory_different_program_root_is_rejected(self):
        snap = snapshot("express")
        snap["account_sets"][0].update(source_id=r"c:\erp\old")
        result = evidence.validate_selection(**evidence_args("express", selected_account_set_key=r"C:\ERP\OLD",
                    selected_root_key=r"C:\OTHER", bound_root_key=r"C:\ERP", request_id=None, revision=None,
                    snapshot=snap))
        self.assertEqual(result["reason"], "root_mismatch")

    def test_without_receipt_the_current_directory_decides(self):
        """用户 2026-09-27：网页用服务端存着的目录选，推送不再要一张刚刷新的回执；
        所选账套在当前这份目录里、可用、可写、程序目录对得上就行（写入前助手再核一次）。"""
        args = {"request_id": None, "revision": None, "latest_refresh": None}
        ok = evidence.validate_selection(**evidence_args(**args))
        self.assertEqual((ok["ok"], ok["reason"]), (True, "current_snapshot"))
        # 刷新进行中 / 刷新失败时，当前这份目录照样算数。
        for status in ("pending", "error"):
            result = evidence.validate_selection(**evidence_args(**args, snapshot=snapshot(head_status=status)))
            self.assertTrue(result["ok"])
        for change, expected in (({"snapshot": None}, "snapshot_not_found"),
                                 ({"snapshot": snapshot(head_snapshot_id="other")}, "snapshot_superseded"),
                                 ({"snapshot": snapshot(adapter="express")}, "adapter_mismatch"),
                                 ({"snapshot": snapshot(account_sets=[])}, "account_not_found"),
                                 ({"user_id": ""}, "context_invalid")):
            result = evidence.validate_selection(**evidence_args(**{**args, **change}))
            self.assertEqual((result["ok"], result["reason"]), (False, expected))
            self.assertEqual(result["error_code"], evidence.CATALOG_REFRESH_INVALID)

    def test_without_receipt_any_year_folder_in_the_directory_can_be_chosen(self):
        """Express 的年度是数据文件夹：同一条连接可以选别的年度文件夹里的账套，不钉死绑定的那个。"""
        snap = snapshot("express")
        snap["account_sets"][0]["attributes"]["root"] = r"\\server\ACCOUNT\68EXP"
        result = evidence.validate_selection(**evidence_args("express", request_id=None, revision=None,
                                                             latest_refresh=None, snapshot=snap,
                                                             bound_root_key=r"\\server\ACCOUNT\69EXP"))
        self.assertEqual((result["ok"], result["root_key"]), (True, r"\\server\account\68exp"))
        snap["account_sets"][0]["attributes"]["writable"] = False
        result = evidence.validate_selection(**evidence_args("express", request_id=None, revision=None,
                                                             latest_refresh=None, snapshot=snap))
        self.assertEqual(result["reason"], "account_not_writable")

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
            freeze_target(endpoint(), access=access(), evidence=rows(snapshot=snapshot(account_sets=[])),
                          account_set_key="7:2", trusted_account_config={"comidyear": "7", "seldb": "2"})
        self.assertEqual(caught.exception.code, "catalog_refresh_invalid")
        # 不带回执、账套在当前目录里：照当前目录冻结目标。
        frozen = freeze_target(endpoint(), access=access(), evidence=rows(), account_set_key="7:2")
        self.assertEqual(frozen.account_set, "7:2")

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



def express_row(path, name, root_label, row, company="", writable=True, active=True):
    return {"source_id": path.lower(), "label": name, "active": active,
            "attributes": {"path": path, "root": "\\\\srv\\ACCOUNT\\" + root_label, "root_label": root_label,
                           "row": row, "company": company, "writable": writable}}


class CatalogGroupTests(unittest.TestCase):
    """用户 2026-09-27：Express 照真实用法选——年度 = 数据文件夹，账套 = 登录列表原样。"""

    def test_express_year_folders_newest_first_and_login_list_order_kept(self):
        rows = [
            express_row(r"S:\2559\EXP59\49LJLAND", "149LJLAND  LJLAND 2549  AUDITED BY YING", "59EXP", 4),
            express_row(r"S:\2559\EXP59\46LJLAND", "146LJLAND  LJLAND 2546 AUDITED BY YING", "59EXP", 1),
            express_row(r"S:\2569\EXP69\69LJLAND", "69LJLAND LJ LAND 2569-2570", "68EXP", 54, "บริษัท แอล เจ แลนด์ จำกัด"),
            express_row(r"S:\2568\EXP68\68LJLAND", "68LJLAND LJ LAND LOCKED 2568", "68EXP", 21, "บริษัท แอล เจ แลนด์ จำกัด"),
            express_row(r"D:\ExpressI\DEMO", "Z.DEMO", "ExpressI", 1),
        ]
        groups = catalog_groups.groups("express", rows)
        self.assertEqual([group["label"] for group in groups], ["68EXP", "59EXP", "ExpressI"])
        self.assertEqual([item["label"] for item in groups[0]["items"]],
                         ["68LJLAND LJ LAND LOCKED 2568", "69LJLAND LJ LAND 2569-2570"])
        self.assertEqual(groups[0]["items"][0]["description"], "บริษัท แอล เจ แลนด์ จำกัด")
        self.assertEqual([item["value"] for item in groups[1]["items"]],
                         [r"s:\2559\exp59\46ljland", r"s:\2559\exp59\49ljland"])

    def test_express_hides_unwritable_and_inactive_and_tells_same_names_apart(self):
        rows = [
            express_row(r"S:\2568\EXP68\68NLT", "68NLT", "68EXP", 1),
            express_row(r"S:\2568\EXP68\68NLTB", "68NLT", "68EXP", 2),
            express_row(r"S:\2558\EXP58\58ASIASP", "58ASIASP", "68EXP", 3, writable=False),
            express_row(r"S:\2568\EXP68\OLD", "OLD", "68EXP", 4, active=False),
        ]
        items = catalog_groups.groups("express", rows)[0]["items"]
        self.assertEqual([item["label"] for item in items], ["68NLT (68nlt)", "68NLT (68nltb)"])

    def test_same_named_year_folders_show_the_distinguishing_path(self):
        a = express_row(r"S:\2563\EXP63\63A", "63A", "63EXP", 1)
        b = express_row(r"S:\2563\EXP63\63B", "63B", "63EXP", 1)
        a["attributes"]["root"] = "\\\\srv\\ACCOUNT\\63EXP"
        b["attributes"]["root"] = "\\\\srv\\ACCOUNT\\64EXP\\63EXP"
        c = express_row(r"S:\2564\EXP64\64C", "64C", "64EXP", 1)
        self.assertEqual([group["label"] for group in catalog_groups.groups("express", [a, b, c])],
                         ["64EXP", "63EXP", "64EXP\\63EXP"])

    def test_mrerp_stays_company_then_year(self):
        rows = [{"source_id": "15:1", "label": "2567", "attributes": {"company": "A", "company_name": "Alpha"}},
                {"source_id": "16:1", "label": "2568", "attributes": {"company": "A", "company_name": "Alpha"}}]
        self.assertEqual(catalog_groups.groups("mrerp", rows), [
            {"value": "A", "label": "A · Alpha", "items": [{"value": "16:1", "label": "2568", "description": ""},
                                                          {"value": "15:1", "label": "2567", "description": ""}]}])
        self.assertEqual(catalog_groups.groups("other", rows), [])



OWN_TAX = "0105551234567"


def book(path, name, root_label, *, tax=OWN_TAX, start=None, end=None, next_end=None, branch=None, writable=True):
    attributes = {"path": path, "root": "\\\\srv\\ACCOUNT\\" + root_label, "root_label": root_label,
                  "tax_id": tax, "writable": writable}
    if start:
        attributes.update(period_start=start, period_end=end, next_end=next_end)
    if branch is not None:
        attributes["branch"] = branch
    return {"source_id": path.lower(), "label": name, "active": True, "attributes": attributes}


class AccountMatchTests(unittest.TestCase):
    """用户 2026-09-27：LINE 里不选账套，识别后按本方税号 + 单据日期（会计期间）自动挑，挑不出就留给编辑页。"""

    def pick(self, rows, fields, direction="purchase", recent=()):
        return account_match.suggest("express", rows, direction=direction, fields=fields, recent=recent)

    def test_own_tax_id_and_fiscal_period_pick_the_book(self):
        rows = [book(r"S:\2568\EXP68\68ACME", "68ACME", "68EXP", start="2024-11-01", end="2025-10-31", next_end="2026-10-31"),
                # 69 年的账登记在 68EXP 里：看会计期间，不看文件夹名。
                book(r"S:\2569\EXP69\69ACME", "69ACME", "68EXP", start="2025-11-01", end="2026-10-31", next_end="2027-10-31"),
                book(r"S:\2569\EXP69\69OTHER", "69OTHER", "69EXP", tax="0105559999999",
                     start="2025-11-01", end="2026-10-31", next_end="2027-10-31")]
        result = self.pick(rows, {"buyer_tax": "0-1055-51234-56-7", "date": "2026-03-15"})
        self.assertEqual((result["account_set_key"], result["reason"]), (r"s:\2569\exp69\69acme", "tax_date"))
        # 销项看卖方税号。
        result = self.pick(rows, {"seller_tax": OWN_TAX, "buyer_tax": "0105559999999", "date": "2025-01-10"}, "sales")
        self.assertEqual(result["account_set_key"], r"s:\2568\exp68\68acme")

    def test_next_year_slot_is_used_only_when_no_book_covers_the_date(self):
        rows = [book(r"S:\2568\EXP68\68ACME", "68ACME", "68EXP", start="2024-11-01", end="2025-10-31", next_end="2026-10-31")]
        self.assertEqual(self.pick(rows, {"buyer_tax": OWN_TAX, "date": "2026-02-01"})["account_set_key"], r"s:\2568\exp68\68acme")
        self.assertEqual(self.pick(rows, {"buyer_tax": OWN_TAX, "date": "2027-02-01"})["reason"], "no_match")

    def test_books_without_periods_fall_back_to_the_year_folder(self):
        rows = [book(r"S:\2568\EXP68\68ACME", "68ACME", "68EXP"), book(r"S:\2569\EXP69\69ACME", "69ACME", "69EXP")]
        self.assertEqual(self.pick(rows, {"buyer_tax": OWN_TAX, "date": "2026-05-01"})["account_set_key"], r"s:\2569\exp69\69acme")

    def test_several_books_for_one_company_use_recent_then_leave_it_to_the_user(self):
        rows = [book(r"S:\2568\EXP68\68AUD", "68ACME AUDITED", "68EXP", start="2025-01-01", end="2025-12-31", next_end="2026-12-31"),
                book(r"S:\2568\EXP68\68DATA", "68ACME DATA", "68EXP", start="2025-01-01", end="2025-12-31", next_end="2026-12-31")]
        fields = {"buyer_tax": OWN_TAX, "date": "2025-06-01"}
        self.assertEqual(self.pick(rows, fields)["reason"], "ambiguous")
        self.assertEqual(self.pick(rows, fields, recent=[r"s:\other", r"s:\2568\exp68\68data"])["account_set_key"],
                         r"s:\2568\exp68\68data")

    def test_branch_narrows_only_when_books_report_it(self):
        rows = [book(r"S:\HQ", "ACME HQ", "68EXP", branch="00000"), book(r"S:\B1", "ACME สาขา 1", "68EXP", branch="00001")]
        self.assertEqual(self.pick(rows, {"buyer_tax": OWN_TAX, "buyer_branch": "สาขาที่ 00001", "date": "2025-06-01"})
                         ["account_set_key"], r"s:\b1")
        self.assertEqual(self.pick(rows, {"buyer_tax": OWN_TAX, "buyer_branch": "สำนักงานใหญ่", "date": "2025-06-01"})
                         ["account_set_key"], r"s:\hq")
        self.assertEqual(self.pick(rows, {"buyer_tax": OWN_TAX, "date": "2025-06-01"})["reason"], "ambiguous")

    def test_no_guessing_without_tax_or_match_and_unwritable_books_skipped(self):
        rows = [book(r"S:\A", "A", "68EXP", writable=False)]
        self.assertEqual(self.pick(rows, {"date": "2025-06-01"})["reason"], "no_tax")
        self.assertEqual(self.pick(rows, {"buyer_tax": OWN_TAX})["reason"], "no_tax_match")

    def test_mrerp_single_or_year_labelled_book(self):
        rows = [{"source_id": "15:1", "label": "2567", "attributes": {}}, {"source_id": "16:1", "label": "2568", "attributes": {}}]
        self.assertEqual(account_match.suggest("mrerp", rows[:1], direction="purchase", fields={})["account_set_key"], "15:1")
        self.assertEqual(account_match.suggest("mrerp", rows, direction="purchase", fields={"date": "2025-03-01"})
                         ["account_set_key"], "16:1")
        self.assertEqual(account_match.suggest("mrerp", rows, direction="purchase", fields={})["reason"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
