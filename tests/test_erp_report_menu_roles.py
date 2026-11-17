"""产品报表跟侧栏同一口径放行（10-30 库存岗员工打不开盘点差异看板；11-16 只有 ERP Admin 的管理员也打不开）。

Run with Frappe installed. No Site, Redis or network.
- 每张产品自带报表（Report JSON）按 ``ref_doctype`` 归到 `access.menu.CATALOG` 里写这张表的菜单（没有写的就看读的）——
  归不到任何菜单的是无主报表，失败；
- 报表角色必须覆盖所属菜单的 `menu.page_roles`（本菜单岗位、旧岗位、ERP 管理员），与侧栏 / 路由放行同一口径：
  管理员自动拥有已开通的全部功能（用户 11-15），侧栏放行了他，报表不能再拒。
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
        self.reports = {}
        for path in ROOT.glob("apps/*/*/*/report/*/*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("doctype") == "Report":
                self.reports[doc["name"]] = (doc.get("ref_doctype") or "", {row["role"] for row in doc.get("roles") or []})

    def owners(self, doctype):
        writers = [key for key, spec in self.menu.CATALOG.items() if doctype in spec[3]]
        return writers or [key for key, spec in self.menu.CATALOG.items() if doctype in spec[4]]

    def test_every_product_report_belongs_to_a_menu_and_opens_for_its_page_roles(self):
        self.assertTrue(self.reports)
        for name, (doctype, roles) in self.reports.items():
            with self.subTest(report=name):
                owners = self.owners(doctype)
                self.assertTrue(owners, f"{name}：ref_doctype {doctype!r} 不归任何菜单（access.menu.CATALOG），是无主报表")
                for key in owners:
                    missing = sorted(set(self.menu.page_roles(key)) - roles)
                    self.assertFalse(missing, f"{name}：缺所属菜单 {key} 的放行角色 {missing}")

    def test_the_count_variance_report_opens_for_the_stocktake_menu_and_the_admin(self):
        _doctype, roles = self.reports["ERP Stock Count Variance"]
        self.assertIn(self.menu.role("stock-count"), roles)
        self.assertIn(self.menu.ADMIN, roles)

    def test_a_report_nobody_owns_fails(self):
        self.assertEqual(self.owners("Some Table No Menu Writes"), [])


if __name__ == "__main__":
    unittest.main()
