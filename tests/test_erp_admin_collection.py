"""管理员默认有收款（主会话 11-14）：网页 / LINE / CC 三处录收款与团队管理里管理员「全勾」一致。

11-13 晚 testerp：管理员团队管理弹窗里收款打着勾（管理员行一律画成全勾、不能改），LINE 菜单却没有收款格——
`collection.write` 只认收款岗位。现在管理员默认就有，但仍跟着功能开通走：没开通收款的站点管理员也没有。

Run with Frappe installed (candidate image). No Site, database or LINE: roles and features are stand-ins.
"""
import pathlib
import unittest
from unittest.mock import patch

import frappe

from pearnly_erp_portal.access import guard, rules
from pearnly_erp_portal.integrations import line_menu

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALL = ["purchase", "sales", "stocktake", "warehouses", "products", "collection"]
PEOPLE = {
    "boss@x": {rules.ADMIN_ROLE, rules.BASE_ROLE},
    "collector@x": {rules.COLLECTOR_ROLE, rules.LINE_ROLE},
    "seller@x": {"ERP Sales", rules.LINE_ROLE},
}


class AdminCollects(unittest.TestCase):
    def use(self, features):
        for p in (patch.object(frappe, "get_roles", lambda user=None: sorted(PEOPLE[user])),
                  patch.object(frappe, "conf", {"pearnly_features": features}, create=True),
                  patch.object(frappe, "session", type("S", (), {"user": "boss@x"})(), create=True)):
            p.start(); self.addCleanup(p.stop)

    def test_admin_records_collections_when_the_feature_is_on(self):
        self.use(ALL)
        self.assertTrue(guard.has("collection.write", user="boss@x"))
        self.assertTrue(guard.has("collection.view", user="boss@x"))
        self.assertTrue(guard.has("collection.write", user="collector@x"))
        self.assertFalse(guard.has("collection.write", user="seller@x"))

    def test_nobody_records_collections_when_the_feature_is_off(self):
        self.use([code for code in ALL if code != "collection"])
        for user in PEOPLE:
            self.assertFalse(guard.has("collection.write", user=user), user)

    def test_admin_line_menu_has_the_collection_tile(self):
        self.use(ALL)
        self.assertIn("collection", line_menu.wanted_variant("boss@x").split("+"))
        self.use([code for code in ALL if code != "collection"])
        self.assertNotIn("collection", line_menu.wanted_variant("boss@x").split("+"))

    def test_admin_opens_my_collections(self):
        hooks = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py").read_text(encoding="utf-8")
        self.assertIn('"pearnly-my-collections"] = {"any_role": [_rules.COLLECTOR_ROLE, _rules.ADMIN_ROLE]}', hooks)


if __name__ == "__main__":
    unittest.main()
