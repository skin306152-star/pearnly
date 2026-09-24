from pearnly_core.desk.assets import versioned_asset

app_name = "pearnly_erp_portal"
app_title = "Pearnly ERP Portal"
app_publisher = "Pearnly"
app_description = "ERP 门户产品：物料与库存、采购、销售、收发存"
app_email = "hello@pearnly.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "pearnly_erp_portal",
# 		"logo": "/assets/pearnly_erp_portal/logo.png",
# 		"title": "Pearnly ERP Portal",
# 		"route": "/pearnly_erp_portal",
# 		"has_permission": "pearnly_erp_portal.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/pearnly_erp_portal/css/pearnly_erp_portal.css"
# app_include_js = "/assets/pearnly_erp_portal/js/pearnly_erp_portal.js"

# include js, css files in header of web template
# web_include_css = "/assets/pearnly_erp_portal/css/pearnly_erp_portal.css"
# web_include_js = "/assets/pearnly_erp_portal/js/pearnly_erp_portal.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "pearnly_erp_portal/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "pearnly_erp_portal/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "pearnly_erp_portal.utils.jinja_methods",
# 	"filters": "pearnly_erp_portal.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "pearnly_erp_portal.install.before_install"
# after_install = "pearnly_erp_portal.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "pearnly_erp_portal.uninstall.before_uninstall"
# after_uninstall = "pearnly_erp_portal.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "pearnly_erp_portal.utils.before_app_install"
# after_app_install = "pearnly_erp_portal.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "pearnly_erp_portal.utils.before_app_uninstall"
# after_app_uninstall = "pearnly_erp_portal.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "pearnly_erp_portal.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "pearnly_erp_portal.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["pearnly_erp_portal.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"pearnly_erp_portal.tasks.all"
# 	],
# 	"daily": [
# 		"pearnly_erp_portal.tasks.daily"
# 	],
# 	"hourly": [
# 		"pearnly_erp_portal.tasks.hourly"
# 	],
# 	"weekly": [
# 		"pearnly_erp_portal.tasks.weekly"
# 	],
# 	"monthly": [
# 		"pearnly_erp_portal.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "pearnly_erp_portal.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "pearnly_erp_portal.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "pearnly_erp_portal.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "pearnly_erp_portal.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["pearnly_erp_portal.utils.before_request"]
# after_request = ["pearnly_erp_portal.utils.after_request"]

# Job Events
# ----------
# before_job = ["pearnly_erp_portal.utils.before_job"]
# after_job = ["pearnly_erp_portal.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"pearnly_erp_portal.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []


has_permission = {"Purchase Document": "pearnly_erp_portal.purchasing.access.document_permission",
                  "ERP Sales Document": "pearnly_erp_portal.sales.access.document_permission",
                  "ERP LINE Intake": "pearnly_erp_portal.integrations.intake.has_permission"}
permission_query_conditions = {"Purchase Document": "pearnly_erp_portal.purchasing.access.query_conditions",
                               "ERP Sales Document": "pearnly_erp_portal.sales.access.query_conditions",
                               "ERP LINE Intake": "pearnly_erp_portal.integrations.intake.permission_query"}

app_include_js = [
    versioned_asset("pearnly_erp_portal", "desk/transactions.js"),
    versioned_asset("pearnly_erp_portal", "desk/company-menu.js"),
    versioned_asset("pearnly_erp_portal", "desk/entry-choice.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-fields.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-layout.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-entry.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-viewer.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-form.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-intake.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase.js"),
    versioned_asset("pearnly_erp_portal", "desk/products.js"),
    versioned_asset("pearnly_erp_portal", "desk/stock-opening.js"),
    versioned_asset("pearnly_erp_portal", "desk/inventory.js"),
    versioned_asset("pearnly_erp_portal", "desk/stock-entry.js"),
    versioned_asset("pearnly_erp_portal", "desk/stock-card.js"),
    versioned_asset("pearnly_erp_portal", "desk/sales-fields.js"),
    versioned_asset("pearnly_erp_portal", "desk/sales-layout.js"),
    versioned_asset("pearnly_erp_portal", "desk/sales-entry.js"),
    versioned_asset("pearnly_erp_portal", "desk/sales-viewer.js"),
    versioned_asset("pearnly_erp_portal", "desk/sales-form.js"),
    versioned_asset("pearnly_erp_portal", "desk/sales-intake.js"),
    versioned_asset("pearnly_erp_portal", "desk/sales.js"),
    versioned_asset("pearnly_erp_portal", "desk/suppliers.js"),
    versioned_asset("pearnly_erp_portal", "desk/clients.js"),
    versioned_asset("pearnly_erp_portal", "desk/cashier.js"),
    # Pinned upstream components precede our adapter. Versioned Desk assets load once.
    versioned_asset("pearnly_erp_portal", "desk/pos/onscan.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos_number_pad.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos_item_selector.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos_item_details.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos_item_cart.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos_payment.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos_past_order_list.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos_past_order_summary.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos_controller.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/pearnly-pos-shift.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/pearnly-pos-receipt.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos/pearnly-pos-adapter.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos-receipts.js"),
    versioned_asset("pearnly_erp_portal", "desk/pos-shifts.js"),
    versioned_asset("pearnly_erp_portal", "desk/stocktake-entry.js"),
    versioned_asset("pearnly_erp_portal", "desk/stocktake.js"),
    versioned_asset("pearnly_erp_portal", "desk/scan.js"),
    versioned_asset("pearnly_erp_portal", "desk/team.js"),
    versioned_asset("pearnly_erp_portal", "desk/company.js"),
    versioned_asset("pearnly_erp_portal", "desk/warehouses.js"),
]

app_include_css = [
    versioned_asset("pearnly_erp_portal", "desk/purchase.css"),
    versioned_asset("pearnly_erp_portal", "desk/sales.css"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos.css"),
    versioned_asset("pearnly_erp_portal", "desk/cashier.css"),
    versioned_asset("pearnly_erp_portal", "desk/scan.css"),
]

# LINE 事件处理：传输层（pearnly_core.line）验签与幂等之后，交给这里处理。
# 本批只做账号绑定；LINE 录单（发单据 → 识别 → 入账）尚未迁，见 integrations/handlers.py。
pearnly_line_handlers = ["pearnly_erp_portal.integrations.handlers.handle"]

# 产品自己的启动数据（多公司：能切哪几家、当前是哪家）。官方扩展点，见 boot.py。
boot_session = "pearnly_erp_portal.masters.boot.boot_session"

# Product-owned menu, implemented routes, permissions and native sidebar groups (ADR-009).
pearnly_navigation = [{'product': 'erp',
  'app': 'pearnly_erp_portal',
  "profile_label": "My Profile",
  "permission_groups": [("General", ["pearnly-home"]),
                        ("User Settings", ["user", "pearnly-team", "pearnly-company", "pearnly-warehouses"])],
  # 产品自己的**页面过滤器**（共用层只负责调）：收银台是公司级开关，这家公司没开收银，
  # 那三个收银页与收银岗位都不出现。见 `masters/nav.py` 与 ADR-009 的声明约定。
  'page_filter': 'pearnly_erp_portal.masters.nav.filter_pages',
  'pages': [('nav.home', 'pearnly-home', 'house', None),
            ('nav.products', 'pearnly-products', 'package', 'products'),
            ('nav.inventory', 'pearnly-inventory', 'warehouse', 'warehouses'),
            ('nav.stockIn', 'pearnly-stock-in', 'download', 'warehouses'),
            ('nav.stockOut', 'pearnly-stock-out', 'upload', 'warehouses'),
            ('nav.stockCard', 'pearnly-stock-card', 'list-checks', 'stockcard'),
            ('nav.stockCount', 'pearnly-stock-count', 'boxes', 'stocktake'),
            ('nav.purchase', 'pearnly-purchase', 'shopping-cart', 'purchase'),
            ('nav.suppliers', 'pearnly-suppliers', 'store', 'purchase'),
            ('nav.purchaseSettings', 'pearnly-buy-settings', 'sun', 'purchase'),
            ('nav.sales', 'pearnly-sales', 'receipt-text', 'sales'),
            ('nav.cashier', 'pearnly-cashier', 'shopping-cart', 'cashier'),
            ('nav.posReceipts', 'pearnly-pos-receipts', 'receipt-text', 'cashier'),
            ('nav.posShifts', 'pearnly-pos-shifts', 'clipboard-list', 'cashier'),
            ('nav.erpClients', 'pearnly-clients', 'user', 'clients'),
            ('nav.company', 'pearnly-company', 'building-2', None),
            ('nav.warehouses', 'pearnly-warehouses', 'warehouse', None),
            ('nav.team', 'pearnly-team', 'users-round', None),
            ('nav.integrations', 'pearnly-integrations', 'plug', None)],
  'available': ['pearnly-home',
                'pearnly-products',
                'pearnly-inventory',
                'pearnly-stock-in',
                'pearnly-stock-out',
                'pearnly-stock-card',
                'pearnly-stock-count',
                'pearnly-purchase',
                'pearnly-suppliers',
                'pearnly-sales',
                'pearnly-clients',
                'pearnly-cashier',
                'pearnly-pos-receipts',
                'pearnly-pos-shifts',
                'pearnly-team',
                'pearnly-company',
                'pearnly-warehouses'],
  'permissions': {'pearnly-products': {'doctype': 'ERP Product'},
                  'pearnly-inventory': {'doctype': 'ERP Stock Balance'},
                  'pearnly-stock-in': {'doctype': 'ERP Stock Entry'},
                  'pearnly-stock-out': {'doctype': 'ERP Stock Entry'},
                  'pearnly-stock-card': {'doctype': 'ERP Stock Movement'},
                  'pearnly-stock-count': {'doctype': 'ERP Stock Count'},
                  'pearnly-purchase': {'doctype': 'Purchase Document'},
                  'pearnly-suppliers': {'doctype': 'ERP Supplier'},
                  'pearnly-sales': {'doctype': 'ERP Sales Document'},
                  'pearnly-clients': {'doctype': 'ERP Customer'},
                  'pearnly-cashier': {'doctype': 'ERP Sale'},
                  'pearnly-pos-receipts': {'doctype': 'ERP Sale'},
                  'pearnly-pos-shifts': {'doctype': 'ERP Shift'},
                  # 公司（本租户主体资料）：业务岗位都能读（开单要抬头与税号），
                  # 只有产品管理员写得动；判据在服务端（`company.read` / `company.manage`）。
                  'pearnly-company': {'doctype': 'ERP Company'},
                  # 仓库：库存岗位读得到（出入库要选仓），写由 `stock.manage` 在服务端判。
                  'pearnly-warehouses': {'doctype': 'ERP Warehouse'},
                  # 员工账号与岗位：能管本产品员工的是产品管理员（ERP Admin）。
                  # 用角色判，不新增权限码 —— Frappe 的用户与权限体系就是判据。
                  'pearnly-team': {'any_role': ['ERP Admin']}},
  'groups': [('nav.group.goodsStock',
              'boxes',
              ['pearnly-products',
               'pearnly-inventory',
               'pearnly-stock-in',
               'pearnly-stock-out',
               'pearnly-stock-card',
               'pearnly-stock-count']),
             ('nav.group.purchasing',
              'shopping-cart',
              ['pearnly-purchase', 'pearnly-suppliers', 'pearnly-buy-settings']),
             ('nav.group.sales',
              'receipt-text',
              ['pearnly-sales',
               'pearnly-cashier',
               'pearnly-pos-receipts',
               'pearnly-pos-shifts',
               'pearnly-clients']),
             ('nav.group.master',
              'building-2',
              ['pearnly-integrations'])],
  # 「选择框里的『新建』去哪儿」（用户 2026-09-23："每个输入框加新建入口，和官方原生一样，
  # 点新建跳转到我们已有的对应位置，不要把用户带到官方原生的地方去了"）。
  # 键是**被选的那个主档 doctype**，值是**我们自己的那一页**：官方 Link 控件默认把
  # 「新建 X」送到官方快速录入、「高级搜索」送到官方选择器 —— 都会把用户带到我们没有的
  # 功能上。没登记的 doctype 不由这里管（我们的表单也不给它入口），所以用户不会掉进官方表单。
  # 前端据此把这两项接到我们的页面（`pearnly_core/public/desk/link.js`）。
  'links': {'ERP Warehouse': 'pearnly-warehouses',
            'ERP Product': 'pearnly-products',
            'ERP Supplier': 'pearnly-suppliers',
            'ERP Customer': 'pearnly-clients',
            'ERP Company': 'pearnly-company'},
  # 左下角那颗用户按钮里的项（用户 2026-09-23："我点左下角，左侧出现 1.个人信息
  # 2.团队管理"）。写的是**路由**，词与图标仍从上面的 pages 取，不在这儿再写一份；
  # 服务端按与侧栏同一套判据筛（页面已实现 + 当前用户能进），所以只有产品管理员
  # 的菜单里会出现「团队管理」。
  # 公司带 `before_profile`：用户 2026-09-23 口径"公司……做在个人信息上方"，
  # 所以它排在「个人信息」上面，团队管理仍排在下面。
  'user_menu': [{'route': 'pearnly-company', 'before_profile': True},
                {'route': 'pearnly-warehouses', 'before_profile': True},
                'pearnly-team']}]

# Product-owned leaf roles; lookup reads never decide which management menu is visible.
from pearnly_erp_portal.access import menu as _menu_permissions
for _key, _spec in _menu_permissions.CATALOG.items():
    pearnly_navigation[0]["permissions"][_menu_permissions.route(_key)] = {
        "any_role": [_menu_permissions.role(_key), _spec[1], "ERP Admin"]}
# Existing coarse-role employees retain their previously available home and profile.
for _key in ("home", "profile"):
    pearnly_navigation[0]["permissions"][_menu_permissions.route(_key)]["any_role"] += [
        "ERP Products", "ERP Stock", "ERP Purchase", "ERP Sales", "ERP Cashier"]
after_install = "pearnly_erp_portal.access.setup.sync_roles"
after_migrate = "pearnly_erp_portal.access.setup.sync_roles"

has_permission.update({
    "ERP Stock Entry": "pearnly_erp_portal.inventory.access.entry_permission",
    "ERP Company": "pearnly_erp_portal.masters.access.company_permission",
    "ERP Warehouse": "pearnly_erp_portal.masters.access.warehouse_permission",
})
permission_query_conditions["ERP Stock Entry"] = "pearnly_erp_portal.inventory.access.entry_query"
for _doctype in ("ERP Stock Balance", "ERP Stock Movement", "ERP Stock Opening", "ERP Stock Count",
                 "ERP Stock Count Item", "ERP Stock Count Entry"):
    has_permission[_doctype] = "pearnly_erp_portal.inventory.access.record_permission"
    permission_query_conditions[_doctype] = "pearnly_erp_portal.inventory.access.record_query"
for _doctype in ("ERP Sale", "ERP Shift"):
    has_permission[_doctype] = "pearnly_erp_portal.cashier.access.record_permission"
    permission_query_conditions[_doctype] = "pearnly_erp_portal.cashier.access.record_query"
has_permission["ERP POS Terminal"] = "pearnly_erp_portal.cashier.access.terminal_permission"
permission_query_conditions.update({
    "ERP Company": "pearnly_erp_portal.masters.access.company_list_query",
    "ERP Warehouse": "pearnly_erp_portal.masters.access.warehouse_list_query",
    "ERP POS Terminal": "pearnly_erp_portal.cashier.access.terminal_query",
})
doc_events = {"DocShare": {"validate": "pearnly_erp_portal.access.sharing.validate_share"}}
for _doctype in ("ERP Warehouse", "ERP POS Terminal"):
    doc_events[_doctype] = {"before_validate": "pearnly_erp_portal.masters.access.validate_company_unchanged"}
doc_events["ERP Stock Entry"] = {
    _event: "pearnly_erp_portal.inventory.access.validate_entry_write"
    for _event in ("before_insert", "before_validate", "on_change", "on_trash", "before_rename", "after_rename")
}
doc_events["ERP Stock Opening"] = {
    _event: "pearnly_erp_portal.inventory.access.validate_opening_write"
    for _event in ("before_insert", "before_validate", "on_change", "on_trash", "before_rename", "after_rename")
}
