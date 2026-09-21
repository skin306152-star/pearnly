"""Offline contract checks: preserve product routes and native Page permissions."""
import ast
import csv
import json
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch
import types
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'apps/pearnly_core/pearnly_core/desk/navigation.py'


def declaration(app):
    """Each product declares its own menu in its own hooks; read it as data."""
    source = (ROOT / f'apps/{app}/{app}/hooks.py').read_text()
    assigned = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'pearnly_navigation'
                            for t in node.targets))
    entries = ast.literal_eval(assigned.value)
    assert len(entries) == 1, entries
    return entries[0]


DECLARATIONS = [declaration(app) for app in ('pearnly_dms', 'pearnly_cowork', 'pearnly_erp_portal')]
PAGES = {item['product']: [tuple(page) for page in item['pages']] for item in DECLARATIONS}


class NativeDeskContract(unittest.TestCase):
    def test_existing_menu_inventory_is_preserved(self):
        # Fixed product contract, independent of the new navigation implementation.
        self.assertEqual([len(PAGES[p]) for p in ('dms','cowork','erp')], [4,11,14])
        dms = {label: feature for label, _, _, feature in PAGES['dms']}
        self.assertIsNone(dms['nav.connect'])
        self.assertEqual(dms['nav.roster'], 'roster')
        cowork = {label: feature for label, _, _, feature in PAGES['cowork']}
        for name in ('nav.clients','nav.company','nav.team','nav.integrations'):
            self.assertEqual(cowork[name], 'master_data')

    def test_every_route_has_native_page_without_platform_manager_role(self):
        metadata = {json.loads(p.read_text())['name']: json.loads(p.read_text())
                    for p in (ROOT/'apps').glob('*/*/*/page/*/*.json')}
        for items in PAGES.values():
            for _, name, _, _ in items:
                with self.subTest(page=name):
                    self.assertIn(name, metadata)
                    self.assertEqual(metadata[name]['standard'], 'Yes')
                    self.assertEqual(metadata[name]['roles'], [{'role':'Desk User'}])

    def test_native_semantic_translation_keys_exist_in_all_languages(self):
        # The glossary is kept per product now (apps/<app>/<app>/i18n/messages.tsv).
        rows = {}
        for glossary in (ROOT/'apps').glob('*/*/i18n/messages.tsv'):
            with glossary.open() as handle:
                rows.update({r['key']: r for r in csv.DictReader(handle, delimiter='\t')
                             if r.get('key')})
        self.assertTrue(rows, 'no semantic keys found in any product glossary')
        for p in (ROOT/'apps').glob('*/*/public/desk/*.js'):
            for key in re.findall(r'''["']((?:home|roster|connect|intake|purchase|shell|account)\.[\w.]+)["']''', p.read_text()):
                if key.endswith('.'):
                    continue
                self.assertIn(key, rows, (p, key))
                for lang in ('th','zh','en','ja'):
                    self.assertTrue(rows[key][lang], (key,lang))

    def test_only_working_pages_are_offered(self):
        source = MODULE.read_text()
        available = {route for item in DECLARATIONS for route in item['available']}
        self.assertEqual(available, {'pearnly-home','pearnly-connect','pearnly-roster','pearnly-records','pearnly-intake','pearnly-purchase'})
        # The visible list, the saved sidebar and the personal order all filter
        # on the product's own list instead of one shared route set.
        self.assertEqual(source.count('route in offered'), 3)
        self.assertNotIn('pearnly-', source.split('"""', 2)[-1])

    def test_only_ticket_based_line_editor_and_account_keep_standalone_routes(self):
        source = (ROOT/'frontend/src/router.ts').read_text()
        self.assertEqual(re.findall(r'path:\s*"([^"]+)"', source), ['/dms/account', '/dms/editor'])
        html = (ROOT/'apps/pearnly_core/pearnly_core/www/pearnly-app.html').read_text()
        self.assertIn('!["dms/editor", "dms/account"].includes(route)', html)
        self.assertIn('location.replace("/desk/"', html)


class ProductNavigationAccess(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.conf = {"pearnly_product": "dms"}
        self.frappe._ = lambda value: value
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.session = types.SimpleNamespace(user="operator@example.test")
        self.frappe.defaults = types.SimpleNamespace(get_user_default=Mock(return_value=None), set_user_default=Mock())
        self.frappe.PermissionError = PermissionError
        self.frappe.throw = Mock(side_effect=ValueError)
        self.frappe.get_roles = Mock(return_value=["DMS Operator"])
        self.frappe.has_permission = Mock(return_value=False)
        # A DMS site installs core + DMS, so only that product's menu is declared.
        self.frappe.get_hooks = Mock(return_value=[declaration('pearnly_dms')])
        self.shell = types.ModuleType("pearnly_core.shell")
        self.shell.entitlements = Mock(return_value=["roster", "push_record"])
        self.shell.PRODUCT_LABELS = {"dms": "DMS", "cowork": "Cowork", "erp": "ERP"}
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe, "pearnly_core.shell": self.shell})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.namespace = {}
        exec(compile(MODULE.read_text(), str(MODULE), "exec"), self.namespace)

    def boot(self):
        class Boot(dict):
            __getattr__ = dict.get
            __setattr__ = dict.__setitem__
        return Boot(workspace_sidebar_item={"pearnly dms": {"items": [
            {"link_to": name} for name in ("pearnly-home", "pearnly-connect", "pearnly-roster", "pearnly-records")
        ]}})

    def test_operator_does_not_see_admin_pages_even_when_site_bought_them(self):
        boot = self.boot()
        self.namespace["extend_boot"](boot)
        self.assertEqual(boot.pearnly_pages, ["pearnly-home"])
        self.assertEqual(boot.workspace_sidebar_item["pearnly dms"]["items"], [{"link_to":"pearnly-home"}])

    def test_company_title_uses_onboarding_name_without_changing_sidebar_identity(self):
        self.frappe.conf["pearnly_workspace"] = "Existing Company"
        boot = self.boot()
        self.namespace["extend_boot"](boot)
        self.assertEqual(boot.pearnly_company_name, "Existing Company")
        self.frappe.conf["pearnly_company_name"] = "  บริษัท 示例 <Company>  "
        self.namespace["extend_boot"](boot)
        self.assertEqual(boot.pearnly_company_name, "บริษัท 示例 <Company>")
        self.assertEqual(boot.pearnly_sidebar, "Pearnly DMS")

    def test_admin_still_needs_feature_and_page_implementation(self):
        self.frappe.get_roles.return_value = ["DMS Admin"]
        self.shell.entitlements.return_value = ["push_record"]
        boot = self.boot()
        self.namespace["extend_boot"](boot)
        self.assertEqual(boot.pearnly_pages, ["pearnly-home", "pearnly-connect", "pearnly-records"])

    def test_personal_projection_does_not_change_the_next_admin_menu(self):
        self.namespace["extend_boot"](self.boot())
        self.frappe.get_roles.return_value = ["DMS Admin"]
        boot = self.boot()
        self.namespace["extend_boot"](boot)
        self.assertEqual(len(boot.workspace_sidebar_item["pearnly dms"]["items"]), 4)

    def test_document_page_requires_native_read_permission(self):
        self.frappe.conf["pearnly_product"] = "cowork"
        self.frappe.get_hooks.return_value = [declaration('pearnly_cowork'),
                                              declaration('pearnly_erp_portal')]
        self.assertFalse(self.namespace["_can_open_page"]("cowork", "pearnly-intake"))
        self.frappe.has_permission.assert_called_with("Intake Record", "read")
        self.frappe.has_permission.return_value = True
        self.assertTrue(self.namespace["_can_open_page"]("erp", "pearnly-purchase"))
        self.frappe.has_permission.assert_called_with("Purchase Document", "read")

    def test_an_installed_product_only_offers_its_own_pages(self):
        # The hook is read from installed apps: a DMS site cannot render Cowork pages.
        self.assertEqual(self.namespace["products"](), ["dms"])
        self.assertEqual(self.namespace["available_pages"]("cowork"), set())

    def test_personal_order_filters_old_routes_and_appends_newly_available_pages(self):
        self.frappe.get_roles.return_value = ["DMS Admin"]
        self.frappe.defaults.get_user_default.return_value = '["pearnly-roster", "role", "pearnly-roster"]'
        boot = self.boot()
        self.namespace["extend_boot"](boot)
        self.assertEqual(boot.pearnly_pages, ["pearnly-roster", "pearnly-home", "pearnly-connect", "pearnly-records"])

    def test_save_rejects_added_missing_duplicate_and_non_string_routes(self):
        for routes in (["pearnly-home", "role"], [], ["pearnly-home", "pearnly-home"], [{}], 'invalid json'):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                self.namespace["save_sidebar_order"](routes)
        self.frappe.defaults.set_user_default.assert_not_called()

    def test_save_uses_only_current_user_default_and_current_permissions(self):
        self.frappe.get_roles.return_value = ["DMS Admin"]
        routes = ["pearnly-roster", "pearnly-home", "pearnly-connect", "pearnly-records"]
        self.assertEqual(self.namespace["save_sidebar_order"](json.dumps(routes)), {"routes": routes})
        self.frappe.defaults.set_user_default.assert_called_once_with("pearnly_sidebar_order_dms", json.dumps(routes))
        self.frappe.get_roles.return_value = ["DMS Operator"]
        with self.assertRaises(ValueError):
            self.namespace["save_sidebar_order"](routes)

    def test_guest_cannot_save_order(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(ValueError):
            self.namespace["save_sidebar_order"](["pearnly-home"])

    def test_control_plane_is_not_restricted(self):
        self.frappe.conf = {}
        boot = self.boot()
        original = repr(boot)
        self.namespace["extend_boot"](boot)
        self.assertEqual(repr(boot), original)


if __name__ == '__main__':
    unittest.main()
