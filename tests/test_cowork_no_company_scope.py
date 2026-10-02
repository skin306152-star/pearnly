"""Cowork 不做公司数据隔离（用户 2026-09-26）：归属只核对引用一致，不按人分公司；站点里没有归属就自动建一份。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

from pearnly_cowork.access import scope  # noqa: E402

APP = root / "apps" / "pearnly_cowork" / "pearnly_cowork"


class _Doc:
    def __init__(self, values):
        self.values = values
        self.flags = mock.Mock(site_workspace=False)
        self.name = "WS-NEW"

    def insert(self, ignore_permissions=False):
        self.inserted = ignore_permissions
        return self


class ScopeWithoutCompanyTest(unittest.TestCase):
    def test_contains_only_checks_references_not_grants(self):
        with mock.patch.object(scope.frappe, "db", mock.Mock(**{"exists.return_value": True})), \
                mock.patch.object(scope.frappe, "get_all", side_effect=AssertionError("no grant lookup")):
            self.assertTrue(scope.contains("WS-1", "CL-1", user="staff@example.com"))

    def test_contains_rejects_missing_or_disabled_reference(self):
        with mock.patch.object(scope.frappe, "db", mock.Mock(**{"sql.return_value": ()})):  # 查不到这一行
            self.assertFalse(scope.contains("WS-1"))
        self.assertFalse(scope.contains(""))

    def test_the_same_check_is_read_once_per_transaction(self):
        # 可重复读：同一事务里再查一遍结果必然相同，记在 Frappe 的事务级缓存里；提交 / 回滚清空缓存后照常现查（停用照旧生效）
        from collections import defaultdict

        def tree():
            return defaultdict(tree)

        rows = {"WS-1": [(1,)]}
        db = mock.Mock(value_cache=tree(), sql=mock.Mock(side_effect=lambda query, values: rows.get(values[0], [])))
        with mock.patch.object(scope.frappe, "db", db):
            for _ in range(5):
                self.assertTrue(scope.contains("WS-1"))
            self.assertEqual(db.sql.call_count, 1)
            self.assertFalse(scope.contains("WS-2"))
            self.assertEqual(db.sql.call_count, 2)
            rows.pop("WS-1")                      # 另一个事务把它停用了
            self.assertTrue(scope.contains("WS-1"))  # 本事务的快照里仍是启用（与再查一遍的结果相同）
            db.value_cache.clear()                # 提交 / 回滚
            self.assertFalse(scope.contains("WS-1"))
            self.assertEqual(db.sql.call_count, 3)

    def test_existing_row_is_reused(self):
        with mock.patch.object(scope.frappe, "get_all", return_value=["WS-OLDEST"]) as get_all, \
                mock.patch.object(scope.frappe, "get_doc", side_effect=AssertionError("must not create")):
            self.assertEqual(scope.default_workspace(), "WS-OLDEST")
        self.assertEqual(get_all.call_args.kwargs["order_by"], "creation asc, name asc")

    def test_missing_row_is_created_once_under_lock(self):
        created = []

        def get_doc(values):
            created.append(_Doc(values))
            return created[-1]

        with mock.patch.object(scope.frappe, "get_all", return_value=[]), \
                mock.patch.object(scope.frappe, "db", mock.Mock()) as db, \
                mock.patch.object(scope.frappe, "get_doc", side_effect=get_doc), \
                mock.patch.object(scope.frappe, "local", mock.Mock(site="testcowork.pearnly.com")):
            self.assertEqual(scope.default_workspace(), "WS-NEW")
        db.get_value.assert_called_once_with("DocType", "Cowork Workspace", "name", for_update=True)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].values["display_name"], "testcowork.pearnly.com")
        self.assertTrue(created[0].flags.site_workspace)
        self.assertTrue(created[0].inserted)


class NoCompanyGrantsLeftTest(unittest.TestCase):
    def test_no_per_user_company_filters_remain(self):
        for path in APP.rglob("*.py"):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('grants("Cowork Workspace"', text, path)
            self.assertNotIn('grants("Cowork Client"', text, path)
            self.assertNotIn("workspace_unresolved", text, path)

    def test_new_members_are_not_assigned_a_company(self):
        text = (APP / "team" / "service.py").read_text(encoding="utf-8")
        self.assertNotIn("User Permission", text)

    def test_migrate_ensures_site_row_and_drops_old_grants(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn("pearnly_cowork.access.scope.ensure_on_migrate", hooks)
        self.assertIn("pearnly_cowork.patches.v0_0_3_drop_company_grants",
                      (APP / "patches.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
