app_name = "pearnly_control"
app_title = "Pearnly Control"
app_publisher = "Pearnly"
app_description = "控制面：开户、公司、产品与功能、域名、角色、账套（仅开发机运行）"
app_email = "skin@pearnly.local"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "pearnly_control",
# 		"logo": "/assets/pearnly_control/logo.png",
# 		"title": "Pearnly Control",
# 		"route": "/pearnly_control",
# 		"has_permission": "pearnly_control.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/pearnly_control/css/pearnly_control.css"
# app_include_js = "/assets/pearnly_control/js/pearnly_control.js"

# include js, css files in header of web template
# web_include_css = "/assets/pearnly_control/css/pearnly_control.css"
# web_include_js = "/assets/pearnly_control/js/pearnly_control.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "pearnly_control/public/scss/website"

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
# app_include_icons = "pearnly_control/public/icons.svg"

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
# 	"methods": "pearnly_control.utils.jinja_methods",
# 	"filters": "pearnly_control.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "pearnly_control.install.before_install"
# after_install = "pearnly_control.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "pearnly_control.uninstall.before_uninstall"
# after_uninstall = "pearnly_control.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "pearnly_control.utils.before_app_install"
# after_app_install = "pearnly_control.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "pearnly_control.utils.before_app_uninstall"
# after_app_uninstall = "pearnly_control.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "pearnly_control.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "pearnly_control.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["pearnly_control.search.awesomebar_results"]

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
# 		"pearnly_control.tasks.all"
# 	],
# 	"daily": [
# 		"pearnly_control.tasks.daily"
# 	],
# 	"hourly": [
# 		"pearnly_control.tasks.hourly"
# 	],
# 	"weekly": [
# 		"pearnly_control.tasks.weekly"
# 	],
# 	"monthly": [
# 		"pearnly_control.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "pearnly_control.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "pearnly_control.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "pearnly_control.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "pearnly_control.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["pearnly_control.utils.before_request"]
# after_request = ["pearnly_control.utils.after_request"]

# Job Events
# ----------
# before_job = ["pearnly_control.utils.before_job"]
# after_job = ["pearnly_control.utils.after_job"]

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
# 	"pearnly_control.auth.validate"
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

# 控制面不用 Frappe 自带登录页：平台账号只存在于身份服务里，本站没有它的密码，
# 用自带登录页必然报 "Invalid credentials"。任何人走到 /login 都直接送进统一身份入口。
#
# 只影响控制站点（本 app 只装在控制站点上）。业务站点暂不动——那边的产品界面还没做，
# 现在还要靠自带页面验证会话与绑定。
website_redirects = [
    {"source": "/login", "target": "/api/method/pearnly_core.identity.begin_login"},
]
