"""网页首次打开：打包、部署后预热、页面文案指纹。在生产镜像里跑（用镜像里的 frappe），不连站点、数据库、redis。

被测代码取检出的源码（/candidate），不取镜像里那份。
"""
import importlib
import json
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
for name in [name for name in sys.modules if name == "pearnly_core" or name.startswith("pearnly_core.")]:
    del sys.modules[name]

import frappe  # noqa: E402

from pearnly_core import browser_text  # noqa: E402
from pearnly_core.desk import assets, warmup  # noqa: E402

assert Path(assets.__file__).is_relative_to(ROOT), assets.__file__
build = importlib.util.spec_from_file_location("build_desk_bundles", ROOT / "scripts" / "build-desk-bundles.py")
builder = importlib.util.module_from_spec(build)
build.loader.exec_module(builder)


class DeskBundleTest(unittest.TestCase):
    """构建脚本写下的源文件摘要与运行时的判断必须对得上；对不上一律退回逐个加载。"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.apps, self.sites_assets = root / "apps", root / "assets"
        self.public = self.apps / "demo_app" / "demo_app" / "public"
        (self.public / "desk").mkdir(parents=True)
        (self.public / "desk" / "a.js").write_text("window.a = 1;\n")
        (self.public / "desk" / "b.js").write_text("window.b = 2;\n")
        (self.public / "desk" / "skin.css").write_text(".x{color:red}\n")
        (self.apps / "demo_app" / "demo_app" / "hooks.py").write_text(
            'DESK_JS = ["desk/a.js", "desk/b.js"]\nDESK_CSS = ["desk/skin.css"]\n')
        self.sites_assets.mkdir()
        self.patches = [patch.object(assets, "_public", lambda app: self.public),
                        patch.object(assets, "_assets_dir", lambda: self.sites_assets)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def publish(self, bundle, built_path):
        (self.sites_assets / built_path).parent.mkdir(parents=True, exist_ok=True)
        (self.sites_assets / built_path).write_text("built")
        index = self.sites_assets / "assets.json"
        data = json.loads(index.read_text()) if index.exists() else {}
        data[bundle] = f"/assets/{built_path}"
        index.write_text(json.dumps(data))

    def test_build_entry_keeps_order_and_runtime_accepts_it(self):
        self.assertTrue(builder.prepare(self.apps, "demo_app"))
        self.assertEqual((self.public / "js" / "demo_app_desk.bundle.js").read_text(),
                         'import "../desk/a.js";\nimport "../desk/b.js";\n')
        # 样式按 Frappe 的搜索路径引用、不带 .css（带了 Sass 会原样留成普通 CSS 引用）
        self.assertEqual((self.public / "scss" / "demo_app_desk.bundle.scss").read_text(),
                         '@import "demo_app/public/desk/skin";\n')
        files = ["desk/a.js", "desk/b.js"]
        self.publish("demo_app_desk.bundle.js", "demo_app/dist/js/demo_app_desk.bundle.ABC.js")
        self.publish("demo_app_desk.bundle.css", "demo_app/dist/css/demo_app_desk.bundle.DEF.css")
        self.assertEqual(assets.desk_bundle("demo_app", "demo_app_desk.bundle.js", files), ["demo_app_desk.bundle.js"])
        self.assertEqual(assets.desk_bundle("demo_app", "demo_app_desk.bundle.css", ["desk/skin.css"]),
                         ["demo_app_desk.bundle.css"])

    def individual(self, files):
        return [f"/assets/demo_app/{relative}?v={sha256((self.public / relative).read_bytes()).hexdigest()[:16]}"
                for relative in files]

    def test_falls_back_to_individual_files_whenever_the_bundle_is_not_this_source(self):
        files = ["desk/a.js", "desk/b.js"]
        bundle = "demo_app_desk.bundle.js"
        # 没构建过
        self.assertEqual(assets.desk_bundle("demo_app", bundle, files), self.individual(files))
        builder.prepare(self.apps, "demo_app")
        # 构建了入口、但 assets.json 里没有登记
        self.assertEqual(assets.desk_bundle("demo_app", bundle, files), self.individual(files))
        self.publish(bundle, "demo_app/dist/js/demo_app_desk.bundle.ABC.js")
        self.assertEqual(assets.desk_bundle("demo_app", bundle, files), [bundle])
        # 实时源码改了一个文件（测试机挂源码）：摘要对不上
        (self.public / "desk" / "b.js").write_text("window.b = 3;\n")
        self.assertEqual(assets.desk_bundle("demo_app", bundle, files), self.individual(files))
        builder.prepare(self.apps, "demo_app")
        self.assertEqual(assets.desk_bundle("demo_app", bundle, files), [bundle])
        # 清单变了（加文件、换顺序）
        self.assertNotEqual(assets.desk_bundle("demo_app", bundle, list(reversed(files))), [bundle])
        # 登记了但包文件不在
        (self.sites_assets / "demo_app/dist/js/demo_app_desk.bundle.ABC.js").unlink()
        self.assertEqual(assets.desk_bundle("demo_app", bundle, files), self.individual(files))

    def test_real_product_lists_exist_and_have_no_duplicates(self):
        apps = ROOT / "apps"
        found = 0
        for app in builder.APPS:
            hooks = apps / app / app / "hooks.py"
            if not hooks.is_file():
                continue
            for name, files in builder.lists(hooks).items():
                found += 1
                self.assertEqual(len(files), len(set(files)), (app, name))
                for relative in files:
                    self.assertTrue((apps / app / app / "public" / relative).is_file(), (app, relative))
                    self.assertEqual(Path(relative).suffix, ".js" if name == "DESK_JS" else ".css", (app, relative))
        self.assertGreaterEqual(found, 6)


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
