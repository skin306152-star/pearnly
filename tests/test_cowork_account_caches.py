"""推到非默认 Express 账套也要先做库存检查（用户 2026-09-27，选 A：资料没到不拦推送）。

主档按账套存：第 2 批（ADR-021）起是那份账套自己的账套资料（`pool.facts`；之前是那份账套的目录快照，更早是上报记录里的
``_account_caches``）。不盖、也不记成电脑所选账套的；推送准备、存货科目组按所选账套取；资料属于账套，不属于读它的那把
密钥或那台电脑。端到端（租、读完、各个读的人、自动请读）见 `test_cowork_any_set_catalog.py`。不连库、不依赖 Frappe。
"""
import json
import pathlib
import sys
import types
import unittest
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cowork_pool_fake as fake  # noqa: E402

fake.install()

from pearnly_cowork.connections import collector, model, pool, preparation  # noqa: E402

NOW = datetime(2026, 9, 27, 12, 0, 0)
DEFAULT = r"S:\2569\EXP69\6902ASC"
OTHER = r"S:\2568\EXP68\68LJLAND"
SETS = [{"code": "ASC", "path": DEFAULT, "root": r"S:\2569\EXP69"},
        {"code": "LJ", "path": OTHER, "root": r"S:\2568\EXP68"}]
OTHER_CACHE = {"products": [{"code": "LJ-1"}], "customers": [], "fingerprint": {"stock_master_count": 0},
               "isacc_candidates": [{"code": "LJ", "fit": True}]}


class AccountCacheTest(unittest.TestCase):
    def setUp(self):
        fake.DB.clear()
        self.doc = fake.Doc(doctype="Cowork ERP Connection", name="CONN", workspace="W", client="", adapter="express",
                            enabled=1, binding_generation=0, record_revision=1, projection_heads="{}",
                            report_json=model.dumps({"_account_caches": {"x": {}}}), config_json="{}")
        fake.DB.docs["Cowork ERP Connection"] = {"CONN": self.doc}
        self.pc = fake.Computer(self.doc, "PC", credential="CRED", generation=3)
        self.pc.report({"account_set": DEFAULT, "account_sets": SETS, "accounts": [{"code": "1100"}],
                        "catalog": {"products": [{"code": "ASC-1"}], "customers": [],
                                    "isacc_candidates": [{"code": "ASC", "fit": True}]},
                        "companion_version": "1.2.2", "max_payload_version": 2}, NOW)

    def read_other(self):
        from pearnly_cowork.connections import refresh, state

        kind, key, head = state.scope(OTHER, "express")
        refresh.create(self.doc, kind, key, head, actor="owner@example.com", identity="R1", signature="s", now=NOW)
        leased = self.pc.lease(NOW)
        self.pc.report({"account_set": DEFAULT, "account_sets": SETS, "accounts": [{"code": "5300"}],
                        "catalog": OTHER_CACHE, "companion_version": self.pc.state["companion_version"],
                        "master_refresh_request_id": leased["request_id"], "master_refresh_scope": "account_set",
                        "master_refresh_account_set": leased["account_set_key"]}, NOW)

    def test_another_account_keeps_its_own_masters_and_leaves_the_default_untouched(self):
        self.pc.version = "1.3.0"
        self.pc.state["companion_version"] = "1.3.0"
        self.read_other()
        self.assertEqual(pool.facts("CONN", OTHER)["reported_products"], [{"code": "LJ-1"}])
        self.assertEqual(pool.facts("CONN", DEFAULT)["reported_products"], [{"code": "ASC-1"}])
        self.assertTrue(pool.facts("CONN", DEFAULT)["_routine"])
        self.assertFalse(pool.facts("CONN", OTHER)["_routine"])
        self.assertNotIn("catalog_seen_at", pool.facts("CONN", OTHER))  # 每次都变的时间不进资料

    def test_the_old_shared_cache_is_dropped_from_the_report(self):
        report = json.loads(self.doc.report_json)
        self.assertNotIn("_account_caches", report)
        self.assertNotIn("reported_products", report)
        self.assertEqual(collector._slim({"_account_caches": {}, "update_blocked": None, "x": 1}),
                         {"update_blocked": None})

    def test_preparation_uses_the_account_sets_masters_whichever_key_read_them(self):
        self.pc.state["companion_version"] = "1.3.0"
        self.read_other()
        loaded = types.SimpleNamespace(doc=types.SimpleNamespace(name="CONN"), revision=1,
                                       access=types.SimpleNamespace(user_id="owner@example.com"))
        target = types.SimpleNamespace(account_set=OTHER, root_key=r"S:\2568\EXP68", endpoint_id="CONN",
                                       user_id="owner@example.com", tenant_id="testcowork.pearnly.com",
                                       binding_generation=0)
        facts, evidence = preparation.collect(loaded, target)
        reported = facts["reported_config"]
        self.assertEqual(reported["catalog_fingerprint"], {"stock_master_count": 0})
        self.assertEqual(reported["reported_stock_acc_groups"][0]["acccod"], "LJ")
        self.assertEqual((reported["max_payload_version"], reported["companion_version"]), (2, "1.3.0"))
        self.assertEqual(evidence["credential"]["credential_id"], "CRED")

    def test_stock_group_candidates_come_from_the_same_source_as_push_preparation(self):
        """选存货科目组的候选（用户 2026-09-28）：每份账套用它自己的资料；没读过的算「不知道」（None），不当成「没有组」。"""
        self.pc.state["companion_version"] = "1.3.0"
        self.read_other()
        self.assertEqual(preparation.reported_stock_groups(self.doc, DEFAULT)[0]["acccod"], "ASC")
        self.assertEqual(preparation.reported_stock_groups(self.doc, OTHER)[0]["acccod"], "LJ")
        self.assertIsNone(preparation.reported_stock_groups(self.doc, r"s:\none"))
        # 换了密钥（重新配对、换一台电脑读）：资料仍是这份账套的
        self.doc.report_credential = "NEW"
        self.assertEqual(preparation.reported_stock_groups(self.doc, OTHER)[0]["acccod"], "LJ")


if __name__ == "__main__":
    unittest.main()
