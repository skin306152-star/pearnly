"""权限判断去重（2026-10-01 生产压测：一份发票的识别与存结果约 50 条 SQL 来自重复的权限判断）。

守住：几项一起判（``granted`` / ``allowed_any``）与原来逐项 ``allowed`` 在所有组合下结论相同，且账号只查一次；
归属判断（``contains``）与原来 frappe.db.exists 同样的判据。离线：数据库用替身。"""
import itertools
import unittest
from unittest import mock

import frappe

from pearnly_cowork.access import scope


def original_allowed(feature, user, *, product, features, enabled, roles):
    """改动前 ``scope.allowed`` 的判据（逐项、每次查账号）。"""
    return bool(user and user != "Guest" and product == "cowork" and feature in features and enabled
                and {scope.ROLES[feature], "Cowork Admin"}.intersection(roles))


class Granted(unittest.TestCase):
    def check(self, user, product, features, enabled, roles):
        db = mock.MagicMock()
        db.sql.return_value = ((1,),) if enabled else ((0,),)
        with mock.patch.object(scope.frappe, "db", db), \
                mock.patch.object(scope.frappe, "conf", frappe._dict(pearnly_product=product)), \
                mock.patch.object(scope, "entitlements", return_value=list(features)), \
                mock.patch.object(scope.frappe, "get_roles", return_value=list(roles)), \
                mock.patch.object(scope.frappe, "session", mock.Mock(user=user)):
            asked = (*scope.READ_FEATURES, "master_data")
            got = scope.granted(asked, user)
            expected = {f for f in asked if original_allowed(f, user, product=product, features=features,
                                                             enabled=enabled, roles=roles)}
            self.assertEqual(got, expected)
            self.assertEqual(scope.allowed_any(asked, user), bool(expected))
            for feature in asked:
                self.assertEqual(scope.allowed(feature, user), feature in expected)
        return db

    def test_every_combination_matches_the_one_by_one_answer(self):
        roles_options = [(), ("Cowork Intake",), ("Cowork OCR History",), ("Cowork Master Data",), ("Cowork Admin",),
                         ("Cowork Intake", "Cowork Master Data")]
        feature_options = [(), ("intake",), ("ocr_history",), ("intake", "ocr_history", "master_data")]
        for user, product, features, enabled, roles in itertools.product(
                ("u@example.com", "Guest", ""), ("cowork", "erp"), feature_options, (True, False), roles_options):
            with self.subTest(user=user, product=product, features=features, enabled=enabled, roles=roles):
                self.check(user, product, features, enabled, roles)

    def test_the_account_is_read_once_for_several_features(self):
        db = mock.MagicMock()
        db.sql.return_value = ((1,),)
        with mock.patch.object(scope.frappe, "db", db), \
                mock.patch.object(scope.frappe, "conf", frappe._dict(pearnly_product="cowork")), \
                mock.patch.object(scope, "entitlements", return_value=["intake", "ocr_history", "master_data"]), \
                mock.patch.object(scope.frappe, "get_roles", return_value=["Cowork OCR History"]):
            self.assertTrue(scope.allowed_any((*scope.READ_FEATURES, "master_data"), "u@example.com"))
        self.assertEqual(db.sql.call_count, 1)
        self.assertEqual(db.sql.call_args.args[1], ("u@example.com",))

    def test_nothing_bought_reads_no_account(self):
        db = mock.MagicMock()
        with mock.patch.object(scope.frappe, "db", db), \
                mock.patch.object(scope.frappe, "conf", frappe._dict(pearnly_product="cowork")), \
                mock.patch.object(scope, "entitlements", return_value=[]):
            self.assertFalse(scope.allowed_any(scope.READ_FEATURES, "u@example.com"))
        db.sql.assert_not_called()


class Contains(unittest.TestCase):
    def test_same_rule_as_exists(self):
        rows = {("ws", None): ((1,),), ("client", ("c1", "ws")): ((1,),)}

        def sql(query, values):
            if "Cowork Workspace" in query:
                return rows.get(("ws", None)) if values == ("ws",) else ()
            return rows.get(("client", values), ())
        db = mock.MagicMock(**{"sql.side_effect": sql})
        with mock.patch.object(scope.frappe, "db", db):
            self.assertTrue(scope.contains("ws"))
            self.assertTrue(scope.contains("ws", "c1"))
            self.assertFalse(scope.contains("ws", "c2"))  # 客户不属于它 / 已停用
            self.assertFalse(scope.contains("other"))      # 归属不存在 / 已停用
            self.assertFalse(scope.contains(""))
        self.assertTrue(all("enabled=1" in call.args[0] for call in db.sql.call_args_list))




class ScopeQuery(unittest.TestCase):
    def test_enabled_workspaces_are_listed_as_values_not_a_subquery(self):
        # 2026-10-02 m4 / m5：子查询让 MariaDB 整表扫记录表再排序；按字面值写才走索引顺序读
        db = mock.MagicMock()
        db.escape.side_effect = lambda value: "'" + value.replace("'", "''") + "'"
        with mock.patch.object(scope.frappe, "db", db), \
                mock.patch.object(scope.frappe, "get_all", return_value=["w1", "o'k"]) as get_all:
            clause = scope._scope_query("`tabCowork OCR History`", "u@example.com", client=True)
        get_all.assert_called_once_with("Cowork Workspace", filters={"enabled": 1}, pluck="name", order_by="name asc")
        self.assertIn("`tabCowork OCR History`.`workspace` IN ('w1', 'o''k')", clause)
        self.assertNotIn("SELECT name FROM `tabCowork Workspace`", clause)
        self.assertIn("`tabCowork Client` WHERE enabled=1", clause)        # 客户停用照旧挡

    def test_no_enabled_workspace_sees_nothing(self):
        with mock.patch.object(scope.frappe, "get_all", return_value=[]):
            self.assertEqual(scope._scope_query("`tabCowork OCR History`", "u@example.com", client=True), "1=0")


if __name__ == "__main__":
    unittest.main()
