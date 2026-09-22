from pearnly_core.desk.assets import versioned_asset

app_name = "pearnly_dms"
app_title = "Pearnly DMS"
app_publisher = "Pearnly"
app_description = "DMS 产品：身份证录入、客户匹配、操作手册、推送记录"
app_email = "hello@pearnly.com"
app_license = "mit"

# LINE 事件处理：传输层（pearnly_core.line）验签与幂等之后，交给这里处理。
# ERP / Cowork 将来各自加一行即可，传输层不用改。
pearnly_line_handlers = ["pearnly_dms.line.handlers.handle"]
pearnly_line_binding_changed = ["pearnly_dms.line.identity.binding_changed", "pearnly_dms.menu.rich_menu.request_sync"]

doc_events = {
    "DMS Operator": {
        "on_update": "pearnly_dms.menu.rich_menu.operator_changed",
        "on_trash": "pearnly_dms.menu.rich_menu.operator_changed",
    },
    "Pearnly ERP Endpoint": {
        "on_update": "pearnly_dms.operators.endpoints.sync_shared_connection",
    },
}
schedule_menu_reconciliation = "pearnly_dms.menu.rich_menu.reconcile"
scheduler_events = {"cron": {
    # 主档后台预热：每分钟只看新鲜度，超过 45 秒才派发一次刷新（口径照老产品
    # `dms_master_refresh.sweep`）。没有它，用户点第一下就要现场等一次全量登录。
    "* * * * *": ["pearnly_dms.adapter.warm.sweep"],
    "*/10 * * * *": [schedule_menu_reconciliation],
    "*/5 * * * *": ["pearnly_dms.booking.recovery.reconcile_due",
                      "pearnly_dms.booking.delivery.schedule_due",
                      "pearnly_dms.booking.claim_recovery.schedule_due"],
}}

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "pearnly_dms",
# 		"logo": "/assets/pearnly_dms/logo.png",
# 		"title": "Pearnly DMS",
# 		"route": "/pearnly_dms",
# 		"has_permission": "pearnly_dms.connections.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/pearnly_dms/css/pearnly_dms.css"
# app_include_js = "/assets/pearnly_dms/js/pearnly_dms.js"

# include js, css files in header of web template
# web_include_css = "/assets/pearnly_dms/css/pearnly_dms.css"
# web_include_js = "/assets/pearnly_dms/js/pearnly_dms.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "pearnly_dms/public/scss/website"

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
# app_include_icons = "pearnly_dms/public/icons.svg"

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
# 	"methods": "pearnly_dms.utils.jinja_methods",
# 	"filters": "pearnly_dms.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "pearnly_dms.install.before_install"
# after_install = "pearnly_dms.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "pearnly_dms.uninstall.before_uninstall"
# after_uninstall = "pearnly_dms.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "pearnly_dms.utils.before_app_install"
# after_app_install = "pearnly_dms.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "pearnly_dms.utils.before_app_uninstall"
# after_app_uninstall = "pearnly_dms.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "pearnly_dms.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "pearnly_dms.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["pearnly_dms.search.awesomebar_results"]

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
# 		"pearnly_dms.tasks.all"
# 	],
# 	"daily": [
# 		"pearnly_dms.tasks.daily"
# 	],
# 	"hourly": [
# 		"pearnly_dms.tasks.hourly"
# 	],
# 	"weekly": [
# 		"pearnly_dms.tasks.weekly"
# 	],
# 	"monthly": [
# 		"pearnly_dms.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "pearnly_dms.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "pearnly_dms.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "pearnly_dms.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "pearnly_dms.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["pearnly_dms.utils.before_request"]
# after_request = ["pearnly_dms.utils.after_request"]

# Job Events
# ----------
# before_job = ["pearnly_dms.utils.before_job"]
# after_job = ["pearnly_dms.utils.after_job"]

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
# 	"pearnly_dms.auth.validate"
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

app_include_js = [versioned_asset("pearnly_dms", "desk/connect.js"), versioned_asset("pearnly_dms", "desk/roster.js"), versioned_asset("pearnly_dms", "desk/records.js")]

# Only OCR is resumable here; external order writers retain their own ledger.
pearnly_ocr_handlers = {"dms.id_card": "pearnly_dms.line.out.run_image"}
pearnly_ocr_failure_handlers = {"dms.id_card": "pearnly_dms.line.out.ocr_failed"}

# Product-owned menu, implemented routes, permissions and native sidebar groups (ADR-009).
pearnly_navigation = [{'product': 'dms',
  'app': 'pearnly_dms',
  'pages': [('nav.home', 'pearnly-home', 'house', None),
            ('nav.connect', 'pearnly-connect', 'plug', None),
            ('nav.records', 'pearnly-records', 'list', 'push_record'),
            ('nav.roster', 'pearnly-roster', 'users', 'roster')],
  'available': ['pearnly-home', 'pearnly-connect', 'pearnly-records', 'pearnly-roster'],
  'permissions': {'pearnly-connect': {'any_role': ['DMS Admin', 'System Manager']},
                  'pearnly-records': {'any_role': ['DMS Admin', 'System Manager']},
                  'pearnly-roster': {'any_role': ['DMS Admin', 'System Manager']}}}]
