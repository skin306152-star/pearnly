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
    versioned_asset("pearnly_erp_portal", "desk/purchase-fields.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-layout.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-entry.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-viewer.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-form.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-intake.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase.js"),
    versioned_asset("pearnly_erp_portal", "desk/products.js"),
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
    versioned_asset("pearnly_erp_portal", "desk/cashier.js"),
    versioned_asset("pearnly_erp_portal", "desk/stocktake-entry.js"),
    versioned_asset("pearnly_erp_portal", "desk/stocktake.js"),
    versioned_asset("pearnly_erp_portal", "desk/scan.js"),
]

app_include_css = [
    versioned_asset("pearnly_erp_portal", "desk/purchase.css"),
    versioned_asset("pearnly_erp_portal", "desk/sales.css"),
    versioned_asset("pearnly_erp_portal", "desk/scan.css"),
]

# LINE 事件处理：传输层（pearnly_core.line）验签与幂等之后，交给这里处理。
# 本批只做账号绑定；LINE 录单（发单据 → 识别 → 入账）尚未迁，见 integrations/handlers.py。
pearnly_line_handlers = ["pearnly_erp_portal.integrations.handlers.handle"]

# Product-owned menu, implemented routes, permissions and native sidebar groups (ADR-009).
pearnly_navigation = [{'product': 'erp',
  'app': 'pearnly_erp_portal',
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
            ('nav.erpClients', 'pearnly-clients', 'user', 'clients'),
            ('nav.companyMaster', 'pearnly-company-master', 'users', None),
            ('nav.company', 'pearnly-company', 'contact', None),
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
                'pearnly-sales',
                'pearnly-cashier'],
  'permissions': {'pearnly-products': {'doctype': 'ERP Product'},
                  'pearnly-inventory': {'doctype': 'ERP Stock Balance'},
                  'pearnly-stock-in': {'doctype': 'ERP Stock Entry'},
                  'pearnly-stock-out': {'doctype': 'ERP Stock Entry'},
                  'pearnly-stock-card': {'doctype': 'ERP Stock Movement'},
                  'pearnly-stock-count': {'doctype': 'ERP Stock Count'},
                  'pearnly-purchase': {'doctype': 'Purchase Document'},
                  'pearnly-sales': {'doctype': 'ERP Sales Document'},
                  'pearnly-cashier': {'doctype': 'ERP Sale'}},
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
             ('nav.group.sales', 'receipt-text', ['pearnly-sales', 'pearnly-cashier', 'pearnly-clients']),
             ('nav.group.master',
              'building-2',
              ['pearnly-company-master', 'pearnly-company', 'pearnly-team', 'pearnly-integrations'])]}]
