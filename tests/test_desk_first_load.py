"""网页首次打开：部署后预热、页面文案指纹，以及 Desk 脚本逐个加载（不打包）。在生产镜像里跑（用镜像里的 frappe），不连站点、数据库、redis。

被测代码取检出的源码（/candidate），不取镜像里那份。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
for name in [name for name in sys.modules if name == "pearnly_core" or name.startswith("pearnly_core.")]:
    del sys.modules[name]

import frappe  # noqa: E402

from pearnly_core import browser_text  # noqa: E402
from pearnly_core.desk import warmup  # noqa: E402

assert Path(warmup.__file__).is_relative_to(ROOT), warmup.__file__


class NoBundleTest(unittest.TestCase):
    """Desk 脚本逐个加载、不打包（2026-10-01：打包后 POS 的扫码库 onScan 没挂到全局，收银台初始化报错；各产品脚本按
    「单独加载、共用全局」写的不止这一处，不能逐页证明打包无害，撤回）。"""

    def test_hooks_load_each_desk_file(self):
        for app in ("pearnly_core", "pearnly_cowork", "pearnly_dms", "pearnly_erp_portal"):
            source = (ROOT / "apps" / app / app / "hooks.py").read_text()
            self.assertNotIn(".bundle.js", source, app)
            self.assertNotIn("desk_bundle", source, app)
        self.assertNotIn("build-desk-bundles", (ROOT / "infrastructure/images/frappe.Dockerfile").read_text())


class TextVersionTest(unittest.TestCase):
    def test_fingerprint_follows_the_language_packs(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = {}

            def app_path(app, *parts):
                path = Path(folder, app, *parts)
                paths[path] = True
                return str(path)

            Path(folder, "pearnly_core", "translations").mkdir(parents=True)
            pack = Path(folder, "pearnly_core", "translations", "th.csv")
            pack.write_text("home.title,หน้าแรก\n")
            with patch.object(frappe, "get_app_path", app_path):
                browser_text.clear_cache()
                first = browser_text.text_version()
                self.assertEqual(browser_text.text_version(), first)
                pack.write_text("home.title,หน้าหลัก\n")
                self.assertEqual(browser_text.text_version(), first)  # 进程内只算一次，与语言包缓存同生命周期
                browser_text.clear_cache()
                self.assertNotEqual(browser_text.text_version(), first)
            browser_text.clear_cache()

    def test_boot_gets_the_fingerprint_and_never_breaks_on_failure(self):
        boot = frappe._dict()
        with patch.object(browser_text, "text_version", return_value="abc"):
            browser_text.extend_bootinfo(boot)
        self.assertEqual(boot.pearnly_text_version, "abc")
        boot = frappe._dict()
        with patch.object(browser_text, "text_version", side_effect=RuntimeError("x")), \
                patch.object(frappe, "log_error") as logged:
            browser_text.extend_bootinfo(boot)
        self.assertNotIn("pearnly_text_version", boot)
        logged.assert_called_once()

    def test_core_declares_the_official_extend_bootinfo_hook(self):
        source = (ROOT / "apps/pearnly_core/pearnly_core/hooks.py").read_text()
        self.assertIn('extend_bootinfo = ["pearnly_core.browser_text.extend_bootinfo"]', source)


class WarmupTest(unittest.TestCase):
    def setUp(self):
        warmup._started = None
        frappe.local.site, frappe.local.sites_path = "demo.localhost", "/sites"
        self.events = []
        self.patches = [
            patch.object(warmup, "recent_users", return_value=["a@x", "b@x", "c@x"]),
            patch.object(warmup, "product_doctypes", return_value=["Cowork Bank Reconciliation"]),
            patch.object(warmup, "warm_meta", side_effect=lambda doctypes: len(doctypes)),
            patch.object(warmup, "_fresh_context", side_effect=lambda *a: self.events.append(("fresh",) + a)),
            patch.object(frappe, "log_error", side_effect=lambda **kw: self.events.append(("log", kw["title"]))),
            patch.object(frappe, "db", SimpleNamespace(commit=lambda: None), create=True),
            patch.object(frappe.local, "conf", frappe._dict(), create=True),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        warmup._started = None

    def test_each_user_gets_a_fresh_context_and_one_failure_does_not_stop_the_rest(self):
        def boot(user):
            self.events.append(("boot", user))
            if user == "b@x":
                raise RuntimeError("broken user")
            return user != "c@x"

        with patch.object(warmup, "warm_boot", side_effect=boot):
            warmup.run()
        self.assertEqual([event[0] for event in self.events],
                         ["fresh", "boot", "fresh", "boot", "log", "fresh", "boot", "fresh"])
        self.assertTrue(all(event[1:] == ("demo.localhost", "/sites") for event in self.events if event[0] == "fresh"))

    def test_never_raises_because_bench_execute_would_run_it_again(self):
        with patch.object(warmup, "recent_users", side_effect=RuntimeError("db down")), \
                patch.object(warmup, "warm_boot") as boot:
            warmup.run()
        boot.assert_not_called()
        self.assertEqual(self.events, [("log", "pearnly desk warmup")])

    def test_budget_is_shared_across_sites(self):
        with patch.object(warmup, "warm_boot", return_value=True) as boot, \
                patch.object(warmup.time, "monotonic", side_effect=[0, 0, 1, 2, 3, 4]):
            warmup.run()
        self.assertEqual(boot.call_count, 3)
        boot.reset_mock()
        with patch.object(warmup, "warm_boot", return_value=True) as boot, \
                patch.object(warmup.time, "monotonic", side_effect=[warmup.BUDGET_SECONDS + 1] * 3):
            warmup.run()  # 第二个站点：上一个站点开始时算起已经超时
        boot.assert_not_called()

    def test_cached_boot_is_left_alone_and_new_boot_uses_the_official_builder(self):
        cache = {}
        fake_cache = SimpleNamespace(hget=lambda key, user: cache.get((key, user)),
                                     hset=lambda key, user, value: cache.__setitem__((key, user), value))
        with patch.object(frappe, "cache", fake_cache, create=True), \
                patch.object(frappe, "set_user") as set_user, \
                patch("frappe.boot.get_bootinfo", return_value={"user": "built"}):
            self.assertTrue(warmup.warm_boot("a@x"))
            self.assertFalse(warmup.warm_boot("a@x"))
        set_user.assert_called_once_with("a@x")
        self.assertEqual(cache, {("bootinfo", "a@x"): {"user": "built"}})


if __name__ == "__main__":
    unittest.main()
