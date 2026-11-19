"""替写的切换时刻（旧件闸，主会话 11-17）：`pearnly_core.cc_desktop.writer_rule.stamp_since` 与 Cowork 的迁移补丁。

- 新开户站点没有这项配置 → 新规则对全部件生效（`route_contracts.stand_in_open` 没配回真）；
- 存量站点迁移那一刻补丁写上此刻 → 之前排的件不替、之后排的替；已有值不动（两产品的同名补丁谁先跑都一样，重跑不变）。
"""
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
from pearnly_core.cc_desktop.wire import route_contracts as rc  # noqa: E402


def _load(frappe):
    modules = {"frappe": frappe, "frappe.installer": frappe.installer}
    saved = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location("writer_rule_under_test",
                                                      ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/writer_rule.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class StampSince(unittest.TestCase):
    def setUp(self):
        self.site = {}
        frappe = types.ModuleType("frappe")
        frappe.conf = {}
        frappe.get_site_config = lambda: dict(self.site)
        frappe.installer = types.ModuleType("frappe.installer")
        frappe.installer.update_site_config = lambda key, value: self.site.__setitem__(key, value)
        self.frappe = frappe
        self.rule = _load(frappe)
        patcher = mock.patch.dict(sys.modules, {"frappe": frappe, "frappe.installer": frappe.installer})  # 函数里才导入
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_site_without_the_setting_lets_every_job_stand_in(self):
        self.assertTrue(rc.stand_in_open(datetime(2020, 1, 1), self.site.get(rc.WRITER_RULE_SINCE)))

    def test_migration_stamps_the_moment_once_and_old_jobs_stay_with_their_submitter(self):
        moment = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)
        self.rule.stamp_since(moment)
        self.assertEqual(self.site, {rc.WRITER_RULE_SINCE: "2026-11-18T03:00:00Z"})
        self.assertEqual(self.frappe.conf[rc.WRITER_RULE_SINCE], "2026-11-18T03:00:00Z")
        since = self.site[rc.WRITER_RULE_SINCE]
        self.assertFalse(rc.stand_in_open(datetime(2026, 11, 18, 2, 59), since))  # 迁移前排的：不替
        self.assertTrue(rc.stand_in_open(datetime(2026, 11, 18, 3, 1), since))    # 迁移后排的：替
        self.rule.stamp_since(datetime(2026, 12, 1, tzinfo=timezone.utc))        # 重跑 / 另一个产品的补丁：不动
        self.assertEqual(self.site[rc.WRITER_RULE_SINCE], "2026-11-18T03:00:00Z")

    def test_the_cowork_patch_calls_the_shared_stamp_and_is_registered(self):
        text = (ROOT / "apps/pearnly_cowork/pearnly_cowork/patches/v0_0_29_writer_rule_since.py").read_text(encoding="utf-8")
        self.assertIn("writer_rule.stamp_since()", text)
        patches = (ROOT / "apps/pearnly_cowork/pearnly_cowork/patches.txt").read_text(encoding="utf-8")
        self.assertIn("pearnly_cowork.patches.v0_0_29_writer_rule_since", patches.split("[post_model_sync]", 1)[1])

    def test_the_erp_patch_calls_the_same_stamp_and_is_registered(self):
        text = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/patches/v1_33_writer_rule_since.py").read_text(encoding="utf-8")
        self.assertIn("writer_rule.stamp_since()", text)
        patches = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/patches.txt").read_text(encoding="utf-8")
        self.assertIn("pearnly_erp_portal.patches.v1_33_writer_rule_since", patches.split("[post_model_sync]", 1)[1])


if __name__ == "__main__":
    unittest.main()
