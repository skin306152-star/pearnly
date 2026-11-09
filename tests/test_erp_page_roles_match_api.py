"""ERP 侧栏 / 路由放行的角色与接口判据一致（主会话 11-09 审定修法 A）。

起因：testerp 的 e2e 账号只有 ERP Admin + 两个菜单岗位，侧栏照样显示库存、收发存等 14 个业务页，点进去接口按
`access/rules.py`（「ERP 管理员不含功能权限」）拒绝，页面显示「无法加载」。原因是 hooks 给每个菜单页的页面权限都加了 ERP Admin。
修法：页面放行的角色由 `access.menu.page_roles` 给——本菜单岗位、旧功能角色；只有接口本身认管理员的页（任务管理，
`work.access.allowed` 放行 ERP Admin）才加 ERP Admin。下面逐页断言：放行的每个角色单独拿着，都过得了这一页接口的读能力。
纯逻辑，不需要 Frappe。
"""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
ACCESS = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/access"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


package = ModuleType("pearnly_erp_portal")
package.__path__ = []
access = ModuleType("pearnly_erp_portal.access")
access.__path__ = []
sys.modules.setdefault("pearnly_erp_portal", package)
sys.modules.setdefault("pearnly_erp_portal.access", access)
menu = load("pearnly_erp_portal.access.menu", ACCESS / "menu.py")
access.menu = menu
rules = load("pearnly_erp_portal.access.rules", ACCESS / "rules.py")

# 接口本身认 ERP 管理员的页（与源码同一判据）：任务管理 `work/access.py: allowed = administrator(user) or guard.has(...)`
API_ADMITS_ADMIN = {"tasks"}
# 盘点岗进物料调整页：接口用 count.write 放行盘点生成的调整单（`inventory/documents.py`，主会话 11-06 审定 #2）
EXTRA = {"stock-adjust": (menu.role("stock-count"), "count.write")}


class PageRolesMatchApi(unittest.TestCase):
    def test_every_role_a_page_admits_passes_that_pages_api(self):
        for key, spec in menu.CATALOG.items():
            caps = spec[2]
            if not caps:
                continue
            for role in menu.page_roles(key):
                with self.subTest(page=key, role=role):
                    if role == rules.ADMIN_ROLE and key in API_ADMITS_ADMIN:
                        continue
                    self.assertTrue(rules.can({role}, caps[0]), f"{key}: 侧栏放行 {role}，接口 {caps[0]} 却拒绝")

    def test_admin_is_only_admitted_where_the_api_admits_the_admin(self):
        admitted = {key for key in menu.CATALOG if rules.ADMIN_ROLE in menu.page_roles(key)}
        legacy_admin = {key for key, spec in menu.CATALOG.items() if spec[1] == rules.ADMIN_ROLE}
        self.assertEqual(admitted, legacy_admin | API_ADMITS_ADMIN)
        for key in ("inventory", "stock-card", "warehouses", "sales", "cashier", "suppliers", "clients"):
            self.assertNotIn(rules.ADMIN_ROLE, menu.page_roles(key), key)

    def test_the_stock_count_role_opens_stock_adjust_through_count_write(self):
        role, capability = EXTRA["stock-adjust"]
        self.assertTrue(rules.can({role}, capability))

    def test_the_menu_admin_is_the_rules_admin(self):
        self.assertEqual(menu.ADMIN, rules.ADMIN_ROLE)

    def test_hooks_take_the_page_roles_from_the_menu(self):
        hooks = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py").read_text(encoding="utf-8")
        loop = hooks.split("for _key, _spec in _menu_permissions.CATALOG.items():", 1)[1].split("\n\n", 1)[0]
        self.assertIn("page_roles(_key)", loop)
        self.assertNotIn('"ERP Admin"', loop)


if __name__ == "__main__":
    unittest.main()
