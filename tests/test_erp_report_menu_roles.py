"""报表跟上「按菜单发角色」（10-30：库存岗员工打不开盘点差异看板，报表只给了 ERP Stock，员工现在只有 ERP Menu … 角色）。

Run with Frappe installed. No Site, Redis or network.
- 每张产品自带报表（Report JSON）只要给了旧的岗位角色（`access.menu.CATALOG` 的第二列），就同时要给至少一个菜单角色；
- 盘点差异看板给盘点菜单的角色。
页面（Page）一律 Desk User、靠导航按菜单角色放行（hooks 的 `pearnly_navigation` permissions），不在这里查。
"""
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ReportMenuRoles(unittest.TestCase):
    def setUp(self):
        from pearnly_erp_portal.access import menu

        self.menu = menu
        self.legacy = {spec[1] for spec in menu.CATALOG.values()}
        self.reports = {}
        for path in ROOT.glob("apps/*/*/*/report/*/*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("doctype") == "Report":
                self.reports[doc["name"]] = {row["role"] for row in doc.get("roles") or []}

    def test_a_report_with_a_legacy_role_also_names_a_menu_role(self):
        for name, roles in self.reports.items():
            with self.subTest(report=name):
                if roles & self.legacy:
                    self.assertTrue(roles & set(self.menu.roles()), f"{name}: {sorted(roles)}")

    def test_the_count_variance_report_opens_for_the_stocktake_menu(self):
        self.assertIn(self.menu.role("stock-count"), self.reports["ERP Stock Count Variance"])


if __name__ == "__main__":
    unittest.main()
