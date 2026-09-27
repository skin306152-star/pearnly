"""Pure report contracts; no Site, provider, clock or database access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from pearnly_cowork.connections.reports import (
    CACHE_KEYS, MAPPING_KEYS, PATCH_KEYS, SELECTED_KEYS, ReportError, plan_report,
)


NOW = datetime(2026, 9, 21, 4, 5, 6, tzinfo=timezone.utc)
BOOK = r"E:\DATA\BOOK"
KEY = r"e:\data\book"
ROOT = r"D:\APP"


def body(**extra):
    return {"account_set": BOOK, "account_dir": BOOK, "express_root": ROOT,
            "account_sets": [{"code": "BOOK", "path": BOOK, "root": ROOT, "writable": True}],
            **extra}


class CompanionReportTests(unittest.TestCase):
    def test_missing_fields_preserve_cache_and_read_server_method(self):
        previous = {"reported_products": [{"code": "OLD"}], "method": "direct", "account_set": BOOK}
        plan = plan_report({"method": "rpa", "complete": True}, previous, NOW)
        self.assertEqual(plan.patch, {"agent_last_seen_at": NOW.isoformat()})
        self.assertEqual(plan.response, {"connected": True, "method": "direct", "account_set": BOOK,
                                        "account_sets_received": 0, "accounts_received": 0})
        self.assertEqual(plan.observations, [])

    def test_offline_short_circuits_all_report_components(self):
        plan = plan_report(body(offline="false", mapping=["malformed"], catalog={}), {}, NOW)
        self.assertTrue(plan.offline)  # Source truthiness is intentionally retained.
        self.assertEqual(plan.patch, {"agent_last_seen_at": "1970-01-01 00:00:00+00"})
        self.assertEqual(plan.response, {"connected": False})
        self.assertEqual(plan.observations, [])
        self.assertIsNone(plan.completion)
        self.assertEqual(plan.lease_selection, "")

    def test_refresh_selection_does_not_fall_back_to_displayed_previous_account(self):
        plan = plan_report({}, {"account_set": BOOK}, NOW)
        self.assertEqual(plan.response["account_set"], BOOK)
        self.assertEqual(plan.lease_selection, "")

    def test_refresh_selection_uses_current_account_dir_when_account_set_is_absent(self):
        current = r"E:\DATA\OTHER"
        plan = plan_report({"account_dir": f" {current} "}, {"account_set": BOOK}, NOW)
        self.assertEqual(plan.response["account_set"], BOOK)
        self.assertEqual(plan.lease_selection, current)
        preferred = plan_report({"account_set": " NEW ", "account_dir": current}, {}, NOW)
        self.assertEqual(preferred.lease_selection, "NEW")

    def test_empty_scan_clears_raw_cache_but_never_publishes_empty_directory(self):
        for value in ([], {}, "bad", [{"name": "name-only"}]):
            with self.subTest(value=value):
                plan = plan_report(body(account_sets=value, master_refresh_scope="endpoint",
                                        master_refresh_request_id="R"), {}, NOW)
                self.assertEqual(plan.observations, [])
                self.assertEqual(plan.projection_error, {"scope": "endpoint", "account_set": None,
                                                        "error_code": "ERR_ACCOUNT_SET_EMPTY"})
                self.assertEqual(plan.completion["error_code"], "ERR_ACCOUNT_SET_EMPTY")
        self.assertEqual(plan_report(body(account_sets=[]), {}, NOW).patch["reported_account_sets"], [])

    def test_raw_and_projection_account_set_sanitizers_are_separate(self):
        plan = plan_report(body(account_sets=[{"path": BOOK, "root": ROOT, "writable": "false"},
                                              {"name": " name-only ", "name_en": " English "}]), {}, NOW)
        self.assertEqual(plan.patch["reported_account_sets"],
                         [{"name": " name-only ", "name_en": " English ", "writable": False}])
        projected = plan.observations[0]["account_sets"]
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["source_id"], KEY)
        self.assertEqual(projected[0]["attributes"]["writable"], "false")

    def test_absent_scan_compatibility_choice_has_no_root_proof(self):
        plan = plan_report({"account_dir": BOOK, "express_root": ROOT}, {}, NOW)
        self.assertNotIn("account_dir", plan.patch)
        self.assertEqual(plan.observations[0]["account_sets"][0]["attributes"],
                         {"path": BOOK, "writable": True})

    def test_catalog_empty_is_distinct_from_missing_or_invalid(self):
        for value in (None, [], "bad"):
            plan = plan_report(body(catalog=value), {}, NOW)
            self.assertNotIn("reported_products", plan.patch)
            self.assertEqual(len(plan.observations), 1)
        plan = plan_report(body(catalog={}), {}, NOW)
        self.assertEqual(plan.patch["reported_products"], [])
        self.assertEqual(plan.patch["reported_customers"], [])
        self.assertNotIn("catalog_fingerprint", plan.patch)
        self.assertNotIn("reported_stock_acc_groups", plan.patch)
        self.assertEqual(plan.observations[1]["masters"], {"products": [], "customers": [], "accounts": []})

    def test_catalog_present_accounts_absent_keeps_raw_but_clears_projected_accounts(self):
        previous = {"reported_accounts": [{"code": "OLD"}]}
        plan = plan_report(body(catalog={}), previous, NOW)
        self.assertNotIn("reported_accounts", plan.patch)
        self.assertEqual(plan.observations[1]["masters"]["accounts"], [])

    def test_catalog_name_only_is_raw_only_and_fingerprint_replaces_as_component(self):
        plan = plan_report(body(catalog={"products": [{"name": "Coffee"}],
                                         "fingerprint": {"stock_master_count": -1, "stcrd_lines": "bad"}}),
                           {"catalog_fingerprint": {"stcrd_lines": 10}}, NOW)
        self.assertEqual(plan.patch["reported_products"], [{"name": "Coffee"}])
        self.assertEqual(plan.observations[1]["masters"]["products"], [])
        self.assertEqual(plan.patch["catalog_fingerprint"], {"stock_master_count": 0})

    def test_stock_groups_aliases_and_legacy_bool_rules(self):
        plan = plan_report({"catalog": {"isacc_candidates": [
            {"CODE": " A ", "DESC": " Stock ", "ACCNUM01": " 11 ", "fit": "false", "used_by": -2}]}}, {}, NOW)
        self.assertEqual(plan.patch["reported_stock_acc_groups"],
                         [{"fit": True, "acccod": "A", "name": "Stock", "stock_acc": "11", "used_by": 0}])
        self.assertEqual(plan_report({"catalog": {"isacc_candidates": "bad"}}, {}, NOW)
                         .patch["reported_stock_acc_groups"], [])

    def test_selected_and_mapping_only_merge_nonempty_source_keys(self):
        plan = plan_report({"account_set": " B ", "account_dir": "", "account_set_row": "2",
                            "mapping": {"revenue_acc": " 41 ", "ar_acc": "", "system_url": "evil"}},
                           {"account_set": "A", "account_dir": BOOK, "ar_acc": "13"}, NOW)
        self.assertEqual({k: v for k, v in plan.patch.items() if k in SELECTED_KEYS | MAPPING_KEYS},
                         {"account_set": "B", "account_set_row": 2, "revenue_acc": "41"})
        for value in (None, {}, [], ""):
            self.assertNotIn("ar_acc", plan_report({"mapping": value}, {"ar_acc": "13"}, NOW).patch)
        with self.assertRaises(ReportError) as caught:
            plan_report({"mapping": ["bad"]}, {}, NOW)
        self.assertEqual(caught.exception.code, "companion.report_invalid")

    def test_capability_and_device_limits_are_source_limits(self):
        plan = plan_report(body(max_payload_version=True, companion_version="v" * 21, device="d" * 61),
                           {"companion_version": "old"}, NOW)
        self.assertEqual(plan.patch["max_payload_version"], 1)
        self.assertNotIn("companion_version", plan.patch)
        self.assertEqual(plan.patch["agent_device_name"], "d" * 60)
        self.assertEqual(plan.observations[0]["collector"]["adapter_version"], "v" * 21)

    def test_raw_limits_do_not_truncate_projection_input(self):
        plan = plan_report({"account_sets": [{"code": str(i)} for i in range(51)],
                            "accounts": [{"code": str(i)} for i in range(3001)]}, {}, NOW)
        self.assertEqual(plan.response["account_sets_received"], 50)
        self.assertEqual(plan.response["accounts_received"], 3000)
        self.assertEqual(len(plan.observations[0]["account_sets"]), 51)

    def test_refresh_account_can_differ_from_selected_but_must_be_registered(self):
        value = body(catalog={}, master_refresh_request_id="R", master_refresh_scope="account_set",
                     master_refresh_account_set=r"E:\DATA\OTHER")
        plan = plan_report(value, {}, NOW)
        self.assertEqual(len(plan.observations), 1)
        self.assertIsNone(plan.completion)
        value["account_sets"].append({"code": "OTHER", "path": r"E:\DATA\OTHER"})
        plan = plan_report(value, {}, NOW)
        self.assertEqual(plan.observations[1]["account_set_key"], r"e:\data\other")
        self.assertEqual(plan.completion, {"request_id": "R", "scope": "account_set",
                                          "account_set": r"e:\data\other", "error_code": None})
        # 这次心跳带的主档属于被刷新的那份账套（收心跳时另存，不盖默认账套的缓存）。
        self.assertEqual(plan.refreshed_account, r"e:\data\other")
        self.assertEqual(plan_report(body(catalog={}), {}, NOW).refreshed_account, "")
        # 读取失败（例如重启后还没扫完账套）或没带目录：心跳里的主档不是那份账套的。
        failed = dict(value, master_refresh_error="ERR_ACCOUNT_SET_UNAVAILABLE")
        self.assertEqual(plan_report(failed, {}, NOW).refreshed_account, "")
        no_catalog = {k: v for k, v in value.items() if k != "catalog"}
        self.assertEqual(plan_report(no_catalog, {}, NOW).refreshed_account, "")

    def test_reported_refresh_error_can_coexist_with_valid_component_publication(self):
        plan = plan_report(body(catalog={}, master_refresh_request_id="R", master_refresh_scope="endpoint",
                                master_refresh_error=" ERR_SCAN "), {}, NOW)
        self.assertEqual(len(plan.observations), 2)
        self.assertIsNone(plan.projection_error)
        self.assertEqual(plan.completion["error_code"], "ERR_SCAN")
        self.assertIsNone(plan_report({"master_refresh_request_id": "R", "master_refresh_scope": "endpoint",
                                      "master_refresh_error": "ERR_SCAN"}, {}, NOW).completion)

    def test_unknown_identity_preparation_and_settings_cannot_enter_patch(self):
        plan = plan_report({"tenant": "foreign", "connection": "foreign", "enabled": True,
                            "method": "direct", "complete": True, "preparation": {}, "mappings": {},
                            "supplier_profiles": {}, "bank_index": [], "agent_token_hash": "secret",
                            "account_set": BOOK, "autonomy": "full", "posting_profile": {}}, {}, NOW)
        self.assertTrue(set(plan.patch) <= PATCH_KEYS)
        self.assertEqual(PATCH_KEYS, CACHE_KEYS | SELECTED_KEYS | MAPPING_KEYS)
        self.assertEqual(set(plan.patch), {"account_set", "agent_last_seen_at"})

    def test_time_and_input_must_be_injected_and_valid(self):
        for invalid in (NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=7))), "now", None):
            with self.subTest(invalid=invalid), self.assertRaises(ReportError):
                plan_report({}, {}, invalid)
        for value in ([], "bad", None):
            with self.subTest(value=value), self.assertRaises(ReportError):
                plan_report(value, {}, NOW)

    def test_input_and_output_mutation_do_not_share_nested_state(self):
        value = body(catalog={"products": [{"code": "P"}]})
        previous = {"reported_products": [{"code": "OLD"}]}
        before = deepcopy((value, previous))
        plan = plan_report(value, previous, NOW)
        self.assertEqual((value, previous), before)
        plan.patch["reported_products"][0]["code"] = "EDIT"
        plan.observations[0]["account_sets"][0]["attributes"]["code"] = "EDIT"
        self.assertEqual((value, previous), before)


if __name__ == "__main__":
    unittest.main()
