"""推到非默认 Express 账套也要先做库存检查（用户 2026-09-27，选 A：资料没到不拦推送）。

按单个账套刷新带回的主档另存到这份账套名下，不盖、也不记成小助手默认账套的；推送准备按所选账套取。
"""
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

import frappe  # noqa: E402

from pearnly_cowork.connections import collector, preparation  # noqa: E402
from pearnly_cowork.connections.reports import ReportPlan  # noqa: E402

NOW = datetime(2026, 9, 27, 12, 0, 0)
DEFAULT = r"S:\2569\EXP69\6902ASC"
OTHER = r"s:\2568\exp68\68ljland"
OTHER_CACHE = {"reported_products": [{"code": "LJ-1"}], "catalog_fingerprint": {"stock_master_count": 0},
               "reported_stock_acc_groups": [{"code": "LJ"}], "reported_accounts": [{"code": "1100"}],
               "catalog_seen_at": NOW.isoformat()}


def plan(patch, refreshed=""):
    return ReportPlan(False, dict(patch), [], None, None, {}, lease_selection=DEFAULT, refreshed_account=refreshed)


class AccountCacheTest(unittest.TestCase):
    def test_another_account_keeps_its_own_cache_and_leaves_the_default_untouched(self):
        report = {"reported_products": [{"code": "ASC-1"}]}
        kept = collector._keep_account_caches(report, plan({**OTHER_CACHE, "agent_last_seen_at": "x"}, OTHER),
                                              "CRED", 3, NOW)
        self.assertEqual(kept.patch, {"agent_last_seen_at": "x"})  # 别的账套的主档不进公共位置
        self.assertEqual(report["reported_products"], [{"code": "ASC-1"}])
        cache = report["_account_caches"][OTHER]
        self.assertEqual(cache["reported_products"], [{"code": "LJ-1"}])
        self.assertEqual((cache["_credential"], cache["_generation"]), ("CRED", 3))

    def test_default_account_refresh_and_routine_reports_stay_as_before(self):
        for refreshed in ("", r"s:\2569\exp69\6902asc"):
            report = {}
            original = plan(OTHER_CACHE, refreshed)
            self.assertIs(collector._keep_account_caches(report, original, "CRED", 3, NOW), original)
            self.assertNotIn("_account_caches", report)

    def test_only_the_most_recent_accounts_are_kept(self):
        report = {"_account_caches": {f"s:\\old{i}": {"_seen_at": f"2026-01-{i + 1:02d}"} for i in range(12)}}
        collector._keep_account_caches(report, plan(OTHER_CACHE, OTHER), "CRED", 3, NOW)
        self.assertEqual(len(report["_account_caches"]), collector._MAX_ACCOUNT_CACHES)
        self.assertIn(OTHER, report["_account_caches"])

    def test_preparation_uses_the_cache_only_from_the_current_credential(self):
        doc = frappe._dict(report_credential="CRED", report_generation=3)
        report = {"_account_caches": {OTHER: {**OTHER_CACHE, "_credential": "CRED", "_generation": 3}},
                  "max_payload_version": 2, "companion_version": "1.2.2"}
        reported = preparation._account_cache(report, OTHER, doc)
        self.assertEqual(reported["catalog_fingerprint"], {"stock_master_count": 0})
        self.assertEqual(reported["reported_stock_acc_groups"], [{"code": "LJ"}])
        self.assertEqual((reported["max_payload_version"], reported["companion_version"]), (2, "1.2.2"))
        self.assertNotIn("catalog_seen_at", reported)
        # 换了凭据 / 代次（重新配对）后，旧缓存不算；没有这份账套的缓存就空着（不拦推送）。
        self.assertEqual(preparation._account_cache(report, OTHER, frappe._dict(report_credential="NEW",
                                                                              report_generation=3)), {})
        self.assertEqual(preparation._account_cache(report, r"s:\none", doc), {})


    def test_stock_group_candidates_come_from_the_same_source_as_push_preparation(self):
        """选存货科目组的候选（用户 2026-09-28）：默认账套用记在它名下的公共缓存，其他账套用它自己的缓存；
        没读过、或出自旧凭据的都算「不知道」（None），不当成「没有组」。"""
        default = r"s:\2569\exp69\6902asc"
        binding = {"account_set": default, "root_key": r"s:\2569\exp69", "credential_id": "CRED",
                   "generation": 3, "issuer": "x", "online": True}
        report = {"_preparation_binding": binding, "reported_stock_acc_groups": [{"code": "ASC"}],
                  "_preparation_origins": {"reported_stock_acc_groups": {k: v for k, v in binding.items() if k != "online"}},
                  "_account_caches": {OTHER: {**OTHER_CACHE, "_credential": "CRED", "_generation": 3}}}
        doc = frappe._dict(report_credential="CRED", report_generation=3, report_json=json.dumps(report))
        self.assertEqual(preparation.reported_stock_groups(doc, default), [{"code": "ASC"}])
        self.assertEqual(preparation.reported_stock_groups(doc, OTHER), [{"code": "LJ"}])
        self.assertIsNone(preparation.reported_stock_groups(doc, r"s:\none"))
        stale = frappe._dict(doc, report_generation=4)
        self.assertIsNone(preparation.reported_stock_groups(stale, default))
        self.assertIsNone(preparation.reported_stock_groups(stale, OTHER))
        report["_preparation_origins"] = {}  # 公共缓存里的组不是这份账套的（别的账套那次刷新留下的）
        self.assertIsNone(preparation.reported_stock_groups(frappe._dict(doc, report_json=json.dumps(report)), default))


if __name__ == "__main__":
    unittest.main()
