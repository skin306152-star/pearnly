"""开户收尾跑迁移收尾里的初始化（10-28 主会话按类要求）：内存替身，不连站点。

- 新开户不走 migrate：只在 after_migrate 里建的默认数据（ERP 任务看板等）新站点原来都缺，要等下一次部署迁移或第一次打开页面兜底；
- `run_site_initializers` 跑本仓应用（pearnly_*）登记的 after_migrate，按应用、按登记顺序，不跑框架自己的，回跑了哪些；
- 开户 `set_workspace` 写完已购功能后紧接着跑它（同一进程，功能清单同步进本进程的 conf，按功能建的看板才看得到）。
"""
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()
sys.modules.setdefault("frappe.utils", types.ModuleType("frappe.utils"))
# 开户模块顶上导入身份服务（要 jwt），这里只测初始化，不用它
sys.modules.setdefault("pearnly_core.identity", types.ModuleType("pearnly_core.identity"))


class SiteInitializers(unittest.TestCase):
    def test_runs_this_repositorys_after_migrate_hooks_in_order(self):
        from unittest import mock

        from pearnly_core import provisioning

        hooks = {"frappe": ["frappe.website.after_migrate"],
                 "pearnly_core": ["pearnly_core.a", "pearnly_core.b"],
                 "pearnly_erp_portal": ["pearnly_erp_portal.work.service.ensure_on_migrate"]}
        ran = []
        with mock.patch.object(frappe, "get_installed_apps", lambda: ["frappe", "pearnly_core", "pearnly_erp_portal"], create=True), \
                mock.patch.object(frappe, "get_hooks", lambda name, app_name=None: hooks.get(app_name, []), create=True), \
                mock.patch.object(frappe, "get_attr", lambda path: (lambda: ran.append(path)), create=True), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            done = provisioning.run_site_initializers()
        self.assertEqual(done, ["pearnly_core.a", "pearnly_core.b", "pearnly_erp_portal.work.service.ensure_on_migrate"])
        self.assertEqual(ran, done)  # 框架自己的 after_migrate 不跑

    def test_the_workspace_step_runs_them_after_writing_the_features(self):
        step = (ROOT / "infrastructure/production/site-step.py").read_text(encoding="utf-8")
        block = step[step.index('elif action == "set_workspace":'):step.index('elif action == "verify_site":')]
        self.assertLess(block.index("ensure_product_workspace(json.dumps(p))"), block.index("run_site_initializers()"))
        source = (ROOT / "apps/pearnly_core/pearnly_core/provisioning.py").read_text(encoding="utf-8")
        written = source.index('update_site_config("pearnly_features", codes)')
        self.assertIn('frappe.conf["pearnly_features"] = codes', source[written:written + 400])


if __name__ == "__main__":
    unittest.main()
