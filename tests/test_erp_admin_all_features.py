"""管理员拥有本租户已开通的全部功能（用户 11-15）：判据在 `guard.has` 一处（`rules.admin_held`）。

起因：团队管理把管理员画成全勾、不可改，权限判据却看角色。开户时发齐当时已有的功能角色，之后新增的功能（11-11 收款、
任务管理）只补了功能码、没给管理员补角色，于是「画着勾却没有」——11-13 线上收款对管理员没生效（6 / 7 个管理员缺收款员角色），
任务管理另靠单项特判放行。现在：每个已开通功能，管理员的全部能力都为真；没开通的功能，管理员也没有。

Run with Frappe installed (candidate image). No Site or database: roles and features are stand-ins.
"""
import unittest
from unittest.mock import patch

import frappe

from pearnly_erp_portal.access import guard, menu, rules
from pearnly_erp_portal.work import access as work_access

ALL = sorted({spec[0] for spec in menu.CATALOG.values() if spec[0]} | set(rules.FEATURE_ROLES))
PEOPLE = {
    # 线上老租户管理员：只有产品管理员角色（没补到新功能角色）
    "boss@x": {rules.ADMIN_ROLE, rules.BASE_ROLE},
    "seller@x": {"ERP Sales", menu.role("sales")},
}


class AdminHoldsOpenedFeatures(unittest.TestCase):
    def use(self, features):
        for p in (patch.object(frappe, "get_roles", lambda user=None: sorted(PEOPLE[user])),
                  patch.object(frappe, "conf", {"pearnly_features": list(features), "pearnly_product": "erp"},
                               create=True),
                  patch.object(frappe, "session", type("S", (), {"user": "boss@x"})(), create=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_every_capability_of_every_opened_feature(self):
        self.use(ALL)
        capabilities = set(rules.CAPABILITY_FEATURES) | {cap for spec in menu.CATALOG.values() for cap in spec[2]}
        for capability in sorted(capabilities):
            with self.subTest(capability=capability):
                self.assertTrue(guard.has(capability, user="boss@x"))

    def test_nothing_of_a_feature_that_is_not_opened(self):
        for code, role in rules.FEATURE_ROLES.items():
            off = {c for c, r in rules.FEATURE_ROLES.items() if r == role}
            self.use([c for c in ALL if c not in off])
            for capability, codes in rules.CAPABILITY_FEATURES.items():
                if set(codes) <= off:
                    with self.subTest(feature=code, capability=capability):
                        self.assertFalse(guard.has(capability, user="boss@x"))

    def test_employees_keep_only_what_they_were_given(self):
        self.use(ALL)
        self.assertTrue(guard.has("sales.write", user="seller@x"))
        for capability in ("purchase.read", "collection.write", "tasks.use", "team.manage", "stock.write"):
            with self.subTest(capability=capability):
                self.assertFalse(guard.has(capability, user="seller@x"))

    def test_collection_and_tasks_need_no_special_case(self):
        self.assertFalse(hasattr(rules, "ADMIN_FEATURE_CAPABILITIES"))
        self.assertFalse(hasattr(menu, "ADMIN_PAGES"))
        self.use(ALL)
        with patch.object(frappe, "db", type("D", (), {"get_value": staticmethod(lambda *a, **k: 1)})(), create=True):
            self.assertTrue(work_access.allowed("boss@x"))
            self.use([c for c in ALL if c != "tasks"])
            self.assertFalse(work_access.allowed("boss@x"))


class AdminTablePermissions(unittest.TestCase):
    def test_migrate_adds_only_the_missing_admin_rows_of_opened_features(self):
        """列表取数走原生 get_list：已开通功能的表，管理员没有权限行的补一条；已有的（开户发的公司资料可写等）不动；没开通的不补。"""
        from pearnly_erp_portal.access import setup

        granted = []
        existing = {"Custom DocPerm": [{"parent": "ERP Company", "permlevel": 0}],
                    "DocPerm": [{"parent": "Purchase Document", "permlevel": 0}]}

        def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
            if doctype == "DocType":
                return list(filters["name"][1])
            return [frappe._dict(row) for row in existing.get(doctype, []) if row["parent"] in filters["parent"][1]]

        read, write = {"read": 1}, {"read": 1, "write": 1}
        with patch.object(frappe, "conf", {"pearnly_features": ["purchase", "stocktake"]}, create=True), \
                patch.object(frappe, "get_all", get_all), \
                patch.object(setup, "_grant", lambda doctype, role, perms, permlevel=0: granted.append((doctype, permlevel, perms))):
            setup._grant_admin_opened(read, write, ("ERP Product", "ERP Company"))
        tables = {(doctype, level) for doctype, level, _perms in granted}
        self.assertNotIn(("ERP Company", 0), tables)          # 已有行不改
        self.assertNotIn(("Purchase Document", 0), tables)    # 标准表里已有也不改
        self.assertIn(("ERP Supplier", 0), tables)            # 采购页读的表补上
        self.assertIn(("ERP Stock Count", 0), tables)         # 盘点开通了
        self.assertIn(("ERP Stock Count Item", 1), tables)
        self.assertNotIn(("ERP Sale", 0), tables)             # 收银没开通不补
        self.assertEqual(dict(((d, l), p) for d, l, p in granted)[("ERP Stock Count", 0)], write)


if __name__ == "__main__":
    unittest.main()
