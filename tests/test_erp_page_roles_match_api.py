"""ERP 侧栏 / 路由放行的角色与接口判据一致（主会话 11-09 审定修法 A）。

起因：testerp 的 e2e 账号只有 ERP Admin + 两个菜单岗位，侧栏照样显示库存、收发存等 14 个业务页，点进去接口按
`access/rules.py`（「ERP 管理员不含功能权限」）拒绝，页面显示「无法加载」。原因是 hooks 给每个菜单页的页面权限都加了 ERP Admin。
修法：页面放行的角色由 `access.menu.page_roles` 给——本菜单岗位、旧功能角色；功能页再加 ERP Admin（用户 11-15：管理员拥有
本租户已开通的全部功能，`rules.admin_held`；没开通的功能页侧栏按功能码不出）。下面逐页断言：放行的每个角色单独拿着
（管理员按功能全开通时的隐含岗位算），都过得了这一页接口的读能力。
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

ALL_FEATURES = {spec[0] for spec in menu.CATALOG.values() if spec[0]} | set(rules.FEATURE_ROLES)
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
                    held = rules.admin_held({role}, ALL_FEATURES)
                    self.assertTrue(rules.can(held, caps[0]), f"{key}: 侧栏放行 {role}，接口 {caps[0]} 却拒绝")

    def test_admin_is_admitted_on_every_page(self):
        # 业务页随功能开通放行管理员，管理页（首页、个人、团队、公司）本来就是管理员的
        self.assertEqual({key for key in menu.CATALOG if rules.ADMIN_ROLE in menu.page_roles(key)}, set(menu.CATALOG))

    def test_admin_holds_every_capability_of_every_opened_feature(self):
        held = rules.admin_held({rules.ADMIN_ROLE}, ALL_FEATURES)
        for key, spec in menu.CATALOG.items():
            for capability in spec[2]:
                with self.subTest(page=key, capability=capability):
                    self.assertTrue(rules.can(held, capability))
        for capability in rules.CAPABILITY_FEATURES:
            with self.subTest(capability=capability):
                self.assertTrue(rules.can(held, capability))

    def test_admin_holds_nothing_of_a_feature_that_is_not_opened(self):
        for code, role in rules.FEATURE_ROLES.items():
            shared = {c for c, r in rules.FEATURE_ROLES.items() if r == role}
            with self.subTest(feature=code):
                self.assertNotIn(role, rules.admin_held({rules.ADMIN_ROLE}, ALL_FEATURES - shared))
        self.assertEqual(rules.admin_held({"ERP Sales"}, ALL_FEATURES), {"ERP Sales"})  # 员工不因此多拿

    def test_every_table_a_feature_declares_is_readable_by_the_admin_once_opened(self):
        """主会话 11-15：新增功能的表必须对 ERP Admin 有原生权限（列表走原生 get_list）。features.json 里每个功能声明的表
        （子表除外）都要在迁移收尾给管理员补的范围里：本功能各菜单页的表 + 公共依赖；收款的表在 setup 里单独给管理员。"""
        import json

        app = ACCESS.parent
        setup = (ACCESS / "setup.py").read_text(encoding="utf-8")
        deps = ("ERP Product", "ERP Product Category", "ERP Company", "ERP Warehouse", "ERP POS Terminal")
        self.assertIn("dependencies = (" + ", ".join(f'"{name}"' for name in deps) + ")", setup)  # 与 setup.sync_roles 同一份
        for item in json.loads((app / "features.json").read_text(encoding="utf-8"))["features"]:
            # 同一岗位的功能（库存、收发存报表、盘点都是 ERP Stock）开通时一起发，声明的表按这一组看
            group = {code for code, role in rules.FEATURE_ROLES.items() if role == rules.FEATURE_ROLES.get(item["code"])}
            covered = set(deps) | {doctype for spec in menu.CATALOG.values() if spec[0] in group | {item["code"]}
                                   for doctype in (*spec[3], *spec[4])}
            if item["code"] == "collection":
                self.assertIn("for role in (rules.COLLECTOR_ROLE, rules.ADMIN_ROLE):", setup)
                covered.add("ERP Collection")
            for doctype in item.get("doctypes") or ():
                folder = app / "pearnly_erp_portal" / "doctype" / doctype.lower().replace(" ", "_")
                meta = json.loads((folder / f"{folder.name}.json").read_text(encoding="utf-8")) if folder.is_dir() else {}
                if meta.get("istable"):
                    continue
                with self.subTest(feature=item["code"], doctype=doctype):
                    self.assertIn(doctype, covered)

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
