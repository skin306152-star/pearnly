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
AVAILABLE = {route for item in DECLARATIONS for route in item['available']}


class NativeDeskContract(unittest.TestCase):
    def test_existing_menu_inventory_is_preserved(self):
        # Fixed product contract, independent of the new navigation implementation.
        # ERP 现在是 19 项：2026-09-23 撤掉「公司主档」（"不要公司资料公司主档"），同一天又把
        # **仓库**从公司页里拿出来单独成页（"仓库单独拿出，放在公司下方，仓库做成单独页"）。
        # Cowork 8 项（用户 2026-09-26）：录入工作台、三个对账中心、库存盘点、工作协调 + 首页、团队管理；
        # 识别记录、推送日志、客户、公司资料、集成、使用教程不做。
        self.assertEqual([len(PAGES[p]) for p in ('dms','cowork','erp')], [4,8,19])
        dms = {label: feature for label, _, _, feature in PAGES['dms']}
        self.assertIsNone(dms['nav.connect'])
        self.assertEqual(dms['nav.roster'], 'roster')
        cowork = {route: feature for _, route, _, feature in PAGES['cowork']}
        # 对账中心拆成三个侧栏入口（银行 / 收入 / 销项税报告核查，用户 2026-09-26）。
        self.assertEqual(cowork, {'pearnly-home': None, 'pearnly-intake': 'intake',
                                  'pearnly-recon-bank': 'recon', 'pearnly-recon-income': 'recon',
                                  'pearnly-recon-vat': 'recon', 'pearnly-stocktake': 'stocktake',
                                  'pearnly-work': None, 'pearnly-team': None})

    def test_every_offered_route_has_native_page_without_platform_manager_role(self):
        metadata = {json.loads(p.read_text())['name']: json.loads(p.read_text())
                    for p in (ROOT/'apps').glob('*/*/*/page/*/*.json')}
        for items in PAGES.values():
            for _, name, _, _ in items:
                if name not in AVAILABLE:
                    continue  # The catalog also records planned pages, which stay hidden.
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
            for key in re.findall(r'''["']((?:home|roster|connect|intake|purchase|shell|account|product|stock|trade|company|team|supplier|client|sales)\.[\w.]+)["']''', p.read_text()):
                if key.endswith('.'):
                    continue
                self.assertIn(key, rows, (p, key))
                for lang in ('th','zh','en','ja'):
                    self.assertTrue(rows[key][lang], (key,lang))

    def test_desk_page_text_namespaces_are_allowed(self):
        # 页面上的 `t("company.xxx")` 是**整包**取词：命名空间没登记在
        # `browser_text.APP_BY_PREFIX` 里，接口直接报错，整页只显示键名。
        # （2026-09-23：公司页的 `company.` 就是这么漏的，所以把它钉在这里。）
        allowed = set(re.findall(r'^\s*"([a-z]+\.)":',
                                 (ROOT/'apps/pearnly_core/pearnly_core/browser_text.py').read_text(),
                                 re.MULTILINE))
        self.assertIn('company.', allowed)
        for p in (ROOT/'apps').glob('*/*/public/desk/*.js'):
            for namespace in sorted(set(re.findall(r'''\bt\(\s*["']([a-z]+)\.[\w.]+["']''',
                                                   p.read_text()))):
                with self.subTest(page=p.name, namespace=namespace):
                    self.assertIn(namespace + '.', allowed)

    def test_only_working_pages_are_offered(self):
        source = MODULE.read_text()
        available = {route for item in DECLARATIONS for route in item['available']}
        # 实际能打开的页面（三个产品各自声明，2026-09-23 对齐一次）：供应商、客户、
        # 收银那几个入口与公司页都是前面几批加的，这里补上，别让清单停在旧版本。
        self.assertEqual(available, {'pearnly-home','pearnly-connect','pearnly-roster','pearnly-records','pearnly-intake','pearnly-purchase',
            'pearnly-products', 'pearnly-inventory', 'pearnly-stock-count',
            'pearnly-stock-in', 'pearnly-stock-out', 'pearnly-stock-card', 'pearnly-sales', 'pearnly-cashier',
            'pearnly-suppliers', 'pearnly-clients', 'pearnly-pos-receipts', 'pearnly-pos-shifts',
            'pearnly-team', 'pearnly-company', 'pearnly-warehouses',
            # Cowork 库存盘点（上传盘点，2026-09-26）。
            'pearnly-stocktake', 'pearnly-recon-bank', 'pearnly-recon-income', 'pearnly-recon-vat',
            # Cowork 工作协调（团队看板，2026-09-27）。
            'pearnly-work'})
        # The visible list, the saved sidebar and the personal order all filter
        # on the product's own list instead of one shared route set.
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
        self.frappe.log_error = Mock()
        self.utils = types.ModuleType("frappe.utils")
        self.page_filter = Mock(side_effect=lambda offered: offered)
        self.frappe.get_attr = Mock(return_value=self.page_filter)
        # A DMS site installs core + DMS, so only that product's menu is declared.
        self.frappe.get_hooks = Mock(return_value=[declaration('pearnly_dms')])
        self.shell = types.ModuleType("pearnly_core.shell")
        self.shell.entitlements = Mock(return_value=["roster", "push_record"])
        self.shell.PRODUCT_LABELS = {"dms": "DMS", "cowork": "Cowork", "erp": "ERP"}
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": self.utils,
                                               "pearnly_core.shell": self.shell})
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

    def test_erp_grouping_and_count_permissions_survive_product_split(self):
        self.frappe.get_hooks.return_value = [declaration('pearnly_erp_portal')]
        self.frappe.conf['pearnly_product'] = 'erp'
        self.shell.entitlements.return_value = ['products', 'warehouses', 'stocktake', 'purchase']
        self.frappe.has_permission.side_effect = lambda doctype, perm: doctype in {'ERP Product', 'ERP Stock Count'}
        self.assertEqual(self.namespace['visible_pages']('erp'),
                         ['pearnly-home', 'pearnly-products', 'pearnly-stock-count'])
        self.frappe.get_attr.assert_called_once_with('pearnly_erp_portal.masters.nav.filter_pages')
        self.assertIn('pearnly-inventory', self.page_filter.call_args.args[0])
        self.frappe.log_error.assert_not_called()
        self.assertEqual(self.namespace['available_pages']('cowork'), set())
        groups = self.namespace['navigation_groups']('erp')
        self.assertEqual([g[0] for g in groups], ['nav.group.goodsStock', 'nav.group.purchasing',
                                                 'nav.group.sales', 'nav.group.master'])
        boot = self.boot()
        boot.workspace_sidebar_item = {'pearnly erp': {'items': [
            {'link_to': route} for route in self.namespace['available_pages']('erp')]}}
        self.namespace['extend_boot'](boot)
        links = [r['link_to'] for r in boot.workspace_sidebar_item['pearnly erp']['items'] if r.get('link_to')]
        self.assertEqual(links, ['pearnly-home', 'pearnly-products', 'pearnly-stock-count'])
        headings = [r for r in boot.workspace_sidebar_item['pearnly erp']['items'] if r.get('type') == 'Section Break']
        self.assertEqual([r['label'] for r in headings], ['nav.group.goodsStock'])

    def test_product_page_filter_is_applied_before_native_permissions(self):
        self.frappe.get_hooks.return_value = [declaration('pearnly_erp_portal')]
        self.shell.entitlements.return_value = ['products', 'cashier']
        self.frappe.has_permission.side_effect = lambda doctype, perm: doctype == 'ERP Sale'
        self.page_filter.side_effect = lambda offered: {
            route: value for route, value in offered.items() if value[3] != 'cashier'}
        self.assertEqual(self.namespace['visible_pages']('erp'), ['pearnly-home'])
        self.frappe.get_attr.assert_called_once_with('pearnly_erp_portal.masters.nav.filter_pages')
        offered = self.page_filter.call_args.args[0]
        self.assertTrue({'pearnly-products', 'pearnly-cashier', 'pearnly-pos-receipts',
                         'pearnly-pos-shifts'}.issubset(offered))
        self.assertNotIn('pearnly-inventory', offered)  # Unpurchased features never reach the filter.
        self.frappe.has_permission.assert_any_call('ERP Product', 'read')
        self.assertNotIn(('ERP Sale', 'read'), [call.args for call in self.frappe.has_permission.call_args_list])
        self.frappe.log_error.assert_not_called()

    def test_shared_sidebar_keeps_filtered_pages_and_each_boot_filters_them(self):
        # The shared document is the pool every boot picks from; a data-dependent filter
        # (no company with a cashier yet) must not strip POS from it for good.
        self.frappe.get_hooks.return_value = [declaration('pearnly_erp_portal')]
        self.frappe.conf['pearnly_product'] = 'erp'
        self.shell.entitlements.return_value = ['sales', 'cashier']
        self.page_filter.side_effect = lambda offered: {
            route: value for route, value in offered.items() if value[3] != 'cashier'}
        shared = [row['link_to'] for row in self.namespace['_sidebar_items']('erp', {'sales', 'cashier'})
                  if row.get('link_to')]
        self.assertTrue({'pearnly-cashier', 'pearnly-pos-receipts', 'pearnly-pos-shifts'}.issubset(shared))
        self.page_filter.assert_not_called()
        self.frappe.get_roles.return_value = ['ERP Admin']
        self.frappe.has_permission.side_effect = lambda doctype, perm: doctype == 'ERP Sales Document'
        boot = self.boot()
        boot.workspace_sidebar_item = {'pearnly erp': {'items': [{'link_to': route} for route in shared]}}
        self.namespace['extend_boot'](boot)
        links = [r['link_to'] for r in boot.workspace_sidebar_item['pearnly erp']['items'] if r.get('link_to')]
        self.assertNotIn('pearnly-cashier', links)
        self.assertIn('pearnly-sales', links)

    def test_page_filter_import_or_execution_failure_is_closed(self):
        self.frappe.get_hooks.return_value = [declaration('pearnly_erp_portal')]
        self.shell.entitlements.return_value = ['products', 'warehouses']
        self.frappe.has_permission.side_effect = lambda doctype, perm: doctype == 'ERP Product'
        for stage in ('import', 'execute'):
            with self.subTest(stage=stage):
                self.frappe.log_error.reset_mock()
                self.frappe.get_attr.side_effect = ImportError('fixture') if stage == 'import' else None
                self.page_filter.side_effect = RuntimeError('fixture') if stage == 'execute' else (lambda offered: offered)
                self.assertEqual(self.namespace['visible_pages']('erp'), [])
                self.frappe.has_permission.assert_not_called()
                self.frappe.log_error.assert_called_once_with(title='erp page_filter failed')

    def test_empty_product_filter_stays_empty(self):
        self.frappe.get_hooks.return_value = [declaration('pearnly_erp_portal')]
        self.page_filter.side_effect = lambda offered: {}
        self.assertEqual(self.namespace['visible_pages']('erp'), [])
        self.frappe.log_error.assert_not_called()

    def test_operator_does_not_see_admin_pages_even_when_site_bought_them(self):
        boot = self.boot()
        # Stale stored grouping must not leave a flat item with a missing parent.
        boot.workspace_sidebar_item["pearnly dms"]["items"][0].update(child=1, indent=1)
        self.namespace["extend_boot"](boot)
        self.assertEqual(boot.pearnly_pages, ["pearnly-home"])
        self.assertEqual(boot.workspace_sidebar_item["pearnly dms"]["items"],
                         [{"link_to": "pearnly-home", "child": 0, "indent": 0}])
        self.frappe.get_attr.assert_not_called()  # DMS declares no product page filter.

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
        self.assertEqual(self.namespace["save_sidebar_order"](json.dumps(routes)),
                         {"routes": routes, "pages": ["pearnly-home", "pearnly-connect",
                                                        "pearnly-records", "pearnly-roster"]})
        self.frappe.defaults.set_user_default.assert_called_once_with("pearnly_sidebar_order_dms", json.dumps(routes))
        self.frappe.get_roles.return_value = ["DMS Operator"]
        with self.assertRaises(ValueError):
            self.namespace["save_sidebar_order"](routes)

    def test_saving_sidebar_order_preserves_user_menu_page_access(self):
        self.frappe.conf['pearnly_product'] = 'erp'
        self.frappe.get_hooks.return_value = [declaration('pearnly_erp_portal')]
        self.frappe.get_roles.return_value = ['ERP Admin']
        self.shell.entitlements.return_value = ['products']
        self.frappe.has_permission.side_effect = lambda doctype, perm: doctype in {
            'ERP Product', 'ERP Company', 'ERP Warehouse'}
        routes = ['pearnly-products', 'pearnly-home']
        self.assertEqual(self.namespace['save_sidebar_order'](routes),
                         {'routes': routes, 'pages': ['pearnly-home', 'pearnly-products',
                                                      'pearnly-company', 'pearnly-warehouses', 'pearnly-team']})
        self.frappe.defaults.set_user_default.assert_called_once_with('pearnly_sidebar_order_erp', json.dumps(routes))
        with self.assertRaises(ValueError):
            self.namespace['save_sidebar_order'](routes + ['pearnly-team'])
        self.assertEqual(self.frappe.defaults.set_user_default.call_count, 1)

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
