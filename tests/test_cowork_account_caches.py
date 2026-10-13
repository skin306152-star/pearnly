"""推到非默认 Express 账套也要先做库存检查（用户 2026-09-27，选 A：资料没到不拦推送）。

按单个账套刷新带回的主档存进那份账套自己的目录快照（2026-10-13 起，不再塞在上报记录的 `_account_caches` 里只留 10 份），
不盖、也不记成小助手默认账套的；推送准备按所选账套取。端到端（租、读完、各个读的人、自动请读）见
`test_cowork_any_set_catalog.py`（不依赖 Frappe）；这里在 bench 的 Python 下核对单个函数。
"""
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

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
    def test_another_account_keeps_its_own_masters_and_leaves_the_default_untouched(self):
        report = {"reported_products": [{"code": "ASC-1"}]}
        kept, own = collector._keep_account_caches(report, plan({**OTHER_CACHE, "agent_last_seen_at": "x"}, OTHER),
                                                   "CRED", 3, NOW)
        self.assertEqual(kept.patch, {"agent_last_seen_at": "x"})  # 别的账套的主档不进公共位置
        self.assertEqual(report, {"reported_products": [{"code": "ASC-1"}]})
        self.assertEqual(own["reported_products"], [{"code": "LJ-1"}])
        self.assertEqual((own["_credential"], own["_generation"]), ("CRED", 3))
        self.assertNotIn("catalog_seen_at", own)  # 每次都变的时间不进快照（内容没变就不新建一份）

    def test_default_account_refresh_and_routine_reports_stay_as_before(self):
        for refreshed in ("", r"s:\2569\exp69\6902asc"):
            report = {}
            original = plan(OTHER_CACHE, refreshed)
            kept, own = collector._keep_account_caches(report, original, "CRED", 3, NOW)
            self.assertIs(kept, original)
            self.assertIsNone(own)

    def test_the_old_shared_cache_is_dropped_from_the_report(self):
        report = {"_account_caches": {f"s:\\old{i}": {"_seen_at": f"2026-01-{i + 1:02d}"} for i in range(12)}}
        collector._keep_account_caches(report, plan({}, ""), "CRED", 3, NOW)
        self.assertNotIn("_account_caches", report)

    def test_preparation_uses_the_masters_only_from_the_current_credential(self):
        doc = frappe._dict(report_credential="CRED", report_generation=3)
        report = {"max_payload_version": 2, "companion_version": "1.2.2"}
        own = {**OTHER_CACHE, "_credential": "CRED", "_generation": 3}
        with mock.patch.object(preparation, "account_reported", lambda doc, account: own if account == OTHER else {}):
            reported = preparation._account_cache(report, OTHER, doc)
            self.assertEqual(reported["catalog_fingerprint"], {"stock_master_count": 0})
            self.assertEqual(reported["reported_stock_acc_groups"], [{"code": "LJ"}])
            self.assertEqual((reported["max_payload_version"], reported["companion_version"]), (2, "1.2.2"))
            self.assertNotIn("catalog_seen_at", reported)
            # 换了凭据 / 代次（重新配对）后，旧的不算；没有这份账套的就空着（不拦推送）。
            self.assertEqual(preparation._account_cache(report, OTHER, frappe._dict(report_credential="NEW",
                                                                                  report_generation=3)), {})
            self.assertEqual(preparation._account_cache(report, r"s:\none", doc), {})

    def test_stock_group_candidates_come_from_the_same_source_as_push_preparation(self):
        """选存货科目组的候选（用户 2026-09-28）：默认账套用记在它名下的公共缓存，其他账套用它自己的那份；
        没读过、或出自旧凭据的都算「不知道」（None），不当成「没有组」。"""
        default = r"s:\2569\exp69\6902asc"
        binding = {"account_set": default, "root_key": r"s:\2569\exp69", "credential_id": "CRED",
                   "generation": 3, "issuer": "x", "online": True}
        report = {"_preparation_binding": binding, "reported_stock_acc_groups": [{"code": "ASC"}],
                  "_preparation_origins": {"reported_stock_acc_groups": {k: v for k, v in binding.items() if k != "online"}}}
        doc = frappe._dict(report_credential="CRED", report_generation=3, report_json=json.dumps(report))
        own = {**OTHER_CACHE, "_credential": "CRED", "_generation": 3}
        with mock.patch.object(preparation, "account_reported", lambda doc, account: own if account == OTHER else {}):
            self.assertEqual(preparation.reported_stock_groups(doc, default), [{"code": "ASC"}])
            self.assertEqual(preparation.reported_stock_groups(doc, OTHER), [{"code": "LJ"}])
            self.assertIsNone(preparation.reported_stock_groups(doc, r"s:\none"))
            stale = frappe._dict(doc, report_generation=4)
            self.assertIsNone(preparation.reported_stock_groups(stale, default))
            self.assertIsNone(preparation.reported_stock_groups(stale, OTHER))
            report["_preparation_origins"] = {}  # 公共缓存里的组不是这份账套的（别的账套那次刷新留下的）
            self.assertIsNone(preparation.reported_stock_groups(frappe._dict(doc, report_json=json.dumps(report)),
                                                                default))


if __name__ == "__main__":
    unittest.main()
