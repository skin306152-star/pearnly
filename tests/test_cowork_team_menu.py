"""Cowork 团队管理勾选目录（`access.menu`）：只发买了的功能、站点上真有的角色，平台与管理员角色不外发。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "pearnly_cowork"))

from pearnly_cowork.access import menu  # noqa: E402


class MenuCatalogTest(unittest.TestCase):
    ALL = set(menu.roles()) | {"Cowork Admin", "System Manager"}

    def test_order_and_groups_follow_catalog(self):
        self.assertEqual(menu.offered({"intake", "recon", "stocktake"}, self.ALL), (
            "Cowork Intake", "Cowork Bank Reconciliation", "Cowork Stocktake",
            "Cowork Work", "Cowork Profile", "Cowork LINE"))

    def test_unpurchased_feature_is_not_offered(self):
        offered = menu.offered({"intake"}, self.ALL)
        self.assertNotIn("Cowork Stocktake", offered)
        self.assertNotIn("Cowork Bank Reconciliation", offered)
        self.assertIn("Cowork Work", offered)

    def test_missing_role_is_not_offered(self):
        self.assertNotIn("Cowork LINE", menu.offered({"intake"}, self.ALL - {"Cowork LINE"}))

    def test_admin_and_platform_roles_never_offered(self):
        offered = set(menu.offered({"intake", "recon", "stocktake"}, self.ALL))
        self.assertFalse(offered & {"Cowork Admin", "System Manager", "Desk User"})


if __name__ == "__main__":
    unittest.main()
