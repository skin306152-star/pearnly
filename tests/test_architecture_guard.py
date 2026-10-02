"""Prove that representative architecture regressions fail the offline gate."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-architecture.py"
spec = importlib.util.spec_from_file_location("architecture_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ArchitectureGuard(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.package = self.root / "apps/pearnly_dms/pearnly_dms"
        self.package.mkdir(parents=True)
        (self.package.parent / "pyproject.toml").write_text("")
        for name in ("__init__.py", "hooks.py", "modules.txt", "patches.txt"):
            (self.package / name).write_text("")
        (self.package / "contracts").mkdir()
        (self.package / "contracts/models.py").write_text("from dataclasses import dataclass\n")
        docs = self.root / "docs/development"
        docs.mkdir(parents=True)
        (docs / "architecture-policy.json").write_text(json.dumps({"root_modules": {"pearnly_dms": ["__init__.py", "hooks.py", "dms_models.py"]}}))
        (docs / "dms-module-map.json").write_text(json.dumps({"modules": {"pearnly_dms.dms_models": "pearnly_dms.contracts.models"}, "compatibility": {}}))

    def test_custom_ui_is_rejected(self):
        page = self.root / "frontend/src/pages/Example.vue"
        page.parent.mkdir(parents=True)
        page.write_text('<template><input /><div class="pn-error">Error</div></template>')
        self.assertTrue(any("official" in error for error in guard.check(self.root)))
        page.write_text('<template><FormControl /><Alert :title="error" /></template>')
        self.assertEqual(guard.check(self.root), [])

    def test_product_vue_route_is_rejected_but_liff_is_retained(self):
        router = self.root / "frontend/src/router.ts"
        router.parent.mkdir(parents=True)
        router.write_text('routes: [{path: "/home"}]')
        self.assertTrue(any("native Desk" in error for error in guard.check(self.root)))
        router.write_text('routes: [{path: "/dms/editor"}]')
        self.assertEqual(guard.check(self.root), [])

    def test_desk_cannot_replace_native_dialog_with_browser_confirm(self):
        js = self.package / "public/desk/test.js"
        js.parent.mkdir(parents=True)
        js.write_text('window.confirm("sure");')
        self.assertTrue(any("native frappe" in error for error in guard.check(self.root)))
        js.write_text('frappe.confirm("sure", action);')
        self.assertEqual(guard.check(self.root), [])

    def test_valid_domain_package(self):
        self.assertEqual(guard.check(self.root), [])

    def test_new_root_business_module_rejected(self):
        (self.package / "new_business.py").write_text("def create(): pass\n")
        self.assertTrue(any("domain package" in x for x in guard.check(self.root)))

    def test_pure_contract_cannot_import_framework(self):
        (self.package / "contracts/models.py").write_text("import frappe\n")
        self.assertTrue(any("pure and independent" in x for x in guard.check(self.root)))

    def test_internal_import_cannot_use_legacy_path(self):
        (self.package / "contracts/models.py").write_text("from pearnly_dms import dms_models\n")
        self.assertTrue(any("internal code" in x for x in guard.check(self.root)))

    def test_new_cross_domain_dependency_requires_review(self):
        policy = self.root / "docs/development/architecture-policy.json"
        data = json.loads(policy.read_text())
        data["dms_domain_dependencies"] = {"contracts": [], "editor": []}
        policy.write_text(json.dumps(data))
        (self.package / "contracts/models.py").write_text("from pearnly_dms.editor.api import create\n")
        self.assertTrue(any("domain dependency" in x for x in guard.check(self.root)))

    def test_new_cowork_cross_domain_dependency_requires_review(self):
        cowork = self.root / "apps/pearnly_cowork/pearnly_cowork"
        (cowork / "ocr").mkdir(parents=True)
        (cowork / "reconciliation").mkdir()
        (cowork.parent / "pyproject.toml").write_text("")
        for name in ("__init__.py", "hooks.py", "modules.txt", "patches.txt"):
            (cowork / name).write_text("")
        policy = self.root / "docs/development/architecture-policy.json"
        data = json.loads(policy.read_text())
        data["cowork_domain_dependencies"] = {"ocr": [], "reconciliation": []}
        policy.write_text(json.dumps(data))
        (cowork / "ocr/runner.py").write_text("from pearnly_cowork.reconciliation import income_parse\n")
        failures = guard.check(self.root)
        self.assertTrue(any("pearnly_cowork ocr -> reconciliation" in x for x in failures), failures)
        data["cowork_domain_dependencies"] = {"ocr": ["reconciliation"], "reconciliation": []}
        policy.write_text(json.dumps(data))
        self.assertEqual(guard.check(self.root), [])

    def test_compatibility_module_cannot_accumulate_business_logic(self):
        mapping = self.root / "docs/development/dms-module-map.json"
        data = json.loads(mapping.read_text())
        data["compatibility"] = {"pearnly_dms.dms_models": {"module": "pearnly_dms.contracts.models", "exports": ["Thing"]}}
        mapping.write_text(json.dumps(data))
        (self.package / "dms_models.py").write_text("from pearnly_dms.contracts.models import Thing\ndef new_business(): pass\n")
        self.assertTrue(any("compatibility entry" in x for x in guard.check(self.root)))


class ProductDeclarations(unittest.TestCase):
    """ADR-009: a product declares its own catalog, menu and glossary."""

    APPS = {"pearnly_cowork": "cowork", "pearnly_erp_portal": "erp", "pearnly_dms": "dms"}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "scripts").mkdir()
        self.write_tool_apps()
        for app, product in self.APPS.items():
            self.write_product(app, product)

    def write_tool_apps(self):
        for app in ("pearnly_core", "pearnly_control"):
            package = self.root / "apps" / app / app
            package.mkdir(parents=True)
            (package.parent / "pyproject.toml").write_text("")
            for name in ("__init__.py", "hooks.py", "modules.txt", "patches.txt"):
                (package / name).write_text("")
        desk = self.root / "apps/pearnly_core/pearnly_core/desk"
        desk.mkdir()
        (desk / "navigation.py").write_text('"""Shared menu assembly."""\n')
        policy = self.root / "docs/development/architecture-policy.json"
        policy.parent.mkdir(parents=True)
        policy.write_text(json.dumps({"root_modules": {}}))
        (policy.parent / "dms-module-map.json").write_text(
            json.dumps({"modules": {}, "compatibility": {}}))

    def write_product(self, app, product, catalog=True, glossary=True):
        package = self.root / "apps" / app / app
        package.mkdir(parents=True, exist_ok=True)
        (package.parent / "pyproject.toml").write_text("")
        for name in ("__init__.py", "modules.txt", "patches.txt"):
            (package / name).write_text("")
        (package / "hooks.py").write_text(
            'pearnly_navigation = [{"product": "%s"}]\n' % product)
        if catalog:
            (package / "features.json").write_text(
                json.dumps({"product": product, "features": []}))
        if glossary:
            (package / "i18n").mkdir(exist_ok=True)
            (package / "i18n/messages.tsv").write_text("en\tzh\tth\tja\tkey\n")

    def test_a_product_declares_its_own_catalog_menu_and_glossary(self):
        self.assertEqual(guard.check(self.root), [])

    def test_a_missing_catalog_is_reported(self):
        (self.root / "apps/pearnly_cowork/pearnly_cowork/features.json").unlink()
        self.assertTrue(any("feature catalog" in error for error in guard.check(self.root)))

    def test_a_catalog_naming_another_product_is_reported(self):
        path = self.root / "apps/pearnly_erp_portal/pearnly_erp_portal/features.json"
        path.write_text(json.dumps({"product": "cowork", "features": []}))
        self.assertTrue(any("must name product erp" in error for error in guard.check(self.root)))

    def test_a_product_without_its_own_menu_is_reported(self):
        package = self.root / "apps/pearnly_dms/pearnly_dms"
        (package / "hooks.py").write_text("app_name = 'pearnly_dms'\n")
        self.assertTrue(any("pearnly_navigation" in error for error in guard.check(self.root)))

    def test_a_product_without_its_own_glossary_is_reported(self):
        (self.root / "apps/pearnly_dms/pearnly_dms/i18n/messages.tsv").unlink()
        self.assertTrue(any("own glossary" in error for error in guard.check(self.root)))

    def test_the_retired_shared_glossary_is_reported(self):
        (self.root / "scripts/i18n").mkdir(parents=True)
        (self.root / "scripts/i18n/messages.tsv").write_text("app\ten\tzh\tth\tja\tkey\n")
        self.assertTrue(any("glossary is per product" in error for error in guard.check(self.root)))

    def test_product_menu_entries_in_the_shared_layer_are_reported(self):
        desk = self.root / "apps/pearnly_core/pearnly_core/desk/navigation.py"
        desk.write_text('PAGES = {"dms": [("nav.home", "pearnly-home", "home", None)]}\n')
        self.assertTrue(any("product menu entries" in error for error in guard.check(self.root)))



class OcrReplay(unittest.TestCase):
    """回放压测只许在测试租户打开：认的站点名不许放宽，开关键不许进生产配置与别的代码。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.module = self.root / guard.REPLAY_MODULE
        self.module.parent.mkdir(parents=True)
        self.module.write_text('CONFIG = "pearnly_ocr_replay"\nTEST_SITES = re.compile(r"%s")\n' % guard.REPLAY_SITES)

    def errors(self):
        return guard.check_ocr_replay(self.root)

    def test_the_registered_module_and_tests_may_name_the_switch(self):
        test = self.root / "apps/pearnly_core/pearnly_core/execution/test_replay.py"
        test.write_text('conf = {"pearnly_ocr_replay": {"enabled": 1}}\n')
        self.assertEqual(self.errors(), [])

    def test_widening_the_test_sites_is_rejected(self):
        self.module.write_text('TEST_SITES = re.compile(r"[a-z0-9.-]+")\n')
        self.assertTrue(any("do not widen TEST_SITES" in error for error in self.errors()))

    def test_the_switch_in_production_config_or_product_code_is_rejected(self):
        for relative in ("infrastructure/production/runtime.env.example", "apps/pearnly_cowork/pearnly_cowork/hooks.py",
                         "scripts/set-site-config.py"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pearnly_ocr_replay = 1\n")
        flagged = {error.split(":")[0] for error in self.errors()}
        self.assertEqual(flagged, {"infrastructure/production/runtime.env.example",
                                   "apps/pearnly_cowork/pearnly_cowork/hooks.py", "scripts/set-site-config.py"})


if __name__ == "__main__":
    unittest.main()
