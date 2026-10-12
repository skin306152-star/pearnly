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
            "Cowork Intake", "Cowork Bank Reconciliation", "Cowork Income Reconciliation",
            "Cowork VAT Check", "Cowork Stocktake", "Cowork Work", "Cowork Profile", "Cowork LINE"))

    def test_three_reconciliations_are_separate_roles(self):
        # 用户 2026-09-27：对账中心拆成银行 / 收入 / 销项税核查三项，可单独勾选，各进各的页面。
        routes = {spec[1]: spec[2] for spec in menu.CATALOG.values() if spec[0] == "recon"}
        self.assertEqual(routes, {"Cowork Bank Reconciliation": "pearnly-recon-bank",
                                  "Cowork Income Reconciliation": "pearnly-recon-income",
                                  "Cowork VAT Check": "pearnly-recon-vat"})

    def test_unpurchased_feature_is_not_offered(self):
        offered = menu.offered({"intake"}, self.ALL)
        self.assertNotIn("Cowork Stocktake", offered)
        self.assertFalse({"Cowork Bank Reconciliation", "Cowork Income Reconciliation",
                          "Cowork VAT Check"} & set(offered))
        self.assertIn("Cowork Work", offered)

    def test_tax_drafts_role_is_offered_only_when_bought(self):
        # 报税草稿（2026-10-13）：没有页面（入口是 CC 工具），团队管理里照样单独勾选
        self.assertEqual(menu.CATALOG["tax"], ("tax_drafts", "Cowork Tax Filing", None))
        self.assertNotIn("Cowork Tax Filing", menu.offered({"intake", "recon"}, self.ALL))
        offered = menu.offered({"intake", "recon", "tax_drafts"}, self.ALL)
        self.assertEqual(offered.index("Cowork Tax Filing"), offered.index("Cowork VAT Check") + 1)

    def test_missing_role_is_not_offered(self):
        self.assertNotIn("Cowork LINE", menu.offered({"intake"}, self.ALL - {"Cowork LINE"}))

    def test_admin_and_platform_roles_never_offered(self):
        offered = set(menu.offered({"intake", "recon", "stocktake"}, self.ALL))
        self.assertFalse(offered & {"Cowork Admin", "System Manager", "Desk User"})


if __name__ == "__main__":
    unittest.main()
