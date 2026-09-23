from pearnly_core.desk.assets import versioned_asset

app_name = "pearnly_core"
app_title = "Pearnly Core"
app_publisher = "Pearnly"
app_description = "统一身份、授权上下文与审计核心"
app_email = "hello@pearnly.com"
app_license = "mit"

# 产品账户设置沿用原生 User 表单，自有扩展只衔接统一身份。

# 业务前端的入口：`/pearnly-app/<任意子路径>` 都交给同一个壳页，
# 子路径由前端自己按地址渲染（单页应用）。没有这条规则时，
# 直接把地址贴给别人（深链接）会 404 —— Frappe 只认精确路径。
website_route_rules = [
    {"from_route": "/pearnly-app/<path:app_path>", "to_route": "pearnly-app"},
    {"from_route": "/pearnly-app", "to_route": "pearnly-app"},
]

# 会话登记：每次登录后记一条，用于展示、审计与撤权。
on_session_creation = "pearnly_core.sessions.register_session"

# 统一登录入口：业务站点的 `/`、`/login`、`/app`、`/desk` 对未登录访客一律
# 送去身份服务 —— 站点上没有业务账号的密码，用 Frappe 自带登录页必然失败。
before_request = [
    # 未登录访客：站点的门一律送去身份服务（站点上没有业务账号的密码）
    "pearnly_core.login_redirect.redirect_guests_to_identity",
    # 已登录用户打开根路径：直接去自己的工作台，不要先过一屏"桌面"
    "pearnly_core.login_redirect.send_root_to_home",
]

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "pearnly_core",
# 		"logo": "/assets/pearnly_core/logo.png",
# 		"title": "Pearnly Core",
# 		"route": "/pearnly_core",
# 		"has_permission": "pearnly_core.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/pearnly_core/css/pearnly_core.css"
# app_include_js = "/assets/pearnly_core/js/pearnly_core.js"

# include js, css files in header of web template
# web_include_css = "/assets/pearnly_core/css/pearnly_core.css"
# web_include_js = "/assets/pearnly_core/js/pearnly_core.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "pearnly_core/public/scss/website"

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
# app_include_icons = "pearnly_core/public/icons.svg"

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
# 	"methods": "pearnly_core.utils.jinja_methods",
# 	"filters": "pearnly_core.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "pearnly_core.install.before_install"
# after_install = "pearnly_core.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "pearnly_core.uninstall.before_uninstall"
# after_uninstall = "pearnly_core.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "pearnly_core.utils.before_app_install"
# after_app_install = "pearnly_core.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "pearnly_core.utils.before_app_uninstall"
# after_app_uninstall = "pearnly_core.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "pearnly_core.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "pearnly_core.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["pearnly_core.search.awesomebar_results"]

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

doc_events = {
    # 登录后直接进产品工作台，不要"桌面"那一屏。
    # 开通时会给已存在的账号设一遍默认工作区；这个钩子管**之后新增**的员工账号，
    # 否则他们又会先落到桌面上。理由与取舍见 pearnly_core/workspace.py。
    "User": {
        "before_validate": "pearnly_core.account.profile.prepare_profile",
        "before_save": "pearnly_core.account.preferences.sync_profile_language",
        "onload": "pearnly_core.account.profile.load_details",
        "after_insert": "pearnly_core.workspace.apply_to_new_user",
    },
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"pearnly_core.tasks.all"
# 	],
# 	"daily": [
# 		"pearnly_core.tasks.daily"
# 	],
# 	"hourly": [
# 		"pearnly_core.tasks.hourly"
# 	],
# 	"weekly": [
# 		"pearnly_core.tasks.weekly"
# 	],
# 	"monthly": [
# 		"pearnly_core.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "pearnly_core.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "pearnly_core.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "pearnly_core.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "pearnly_core.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["pearnly_core.utils.before_request"]
# after_request = ["pearnly_core.utils.after_request"]

# Job Events
# ----------
# before_job = ["pearnly_core.utils.before_job"]
# after_job = ["pearnly_core.utils.after_job"]

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
# 	"pearnly_core.auth.validate"
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

# Product Desk uses the same native UI runtime as the management Site.
boot_session = "pearnly_core.desk.navigation.extend_boot"
app_include_css = [versioned_asset("pearnly_core", "desk/product.css")]
app_include_js = [versioned_asset("pearnly_core", "desk/product.js"), versioned_asset("pearnly_core", "desk/select.js"), versioned_asset("pearnly_core", "desk/list.js"), versioned_asset("pearnly_core", "desk/home.js"), versioned_asset("pearnly_core", "desk/sidebar.js")]
after_migrate = [
    "pearnly_core.desk.navigation.ensure_navigation",
    "pearnly_core.desk.branding.ensure_site_favicon",
    "pearnly_core.account.layout.ensure_profile_fields",
]

# Keep the native user form; do not add a second account settings page.
doctype_js = {"User": "public/desk/user.js"}

# Product account preferences never expose the framework user directory.
has_permission = {"User": "pearnly_core.desk.permissions.user_permission"}
permission_query_conditions = {"User": "pearnly_core.desk.permissions.user_query"}

# Native clear-cache must not discard still-active identity sessions/state.
persistent_cache_keys = ["pearnly_oidc_session:*", "pearnly_oidc_state:*"]

# Durable OCR backlog repair; second-level ETA delivery uses execution.dispatch.
scheduler_events = {"cron": {"* * * * *": ["pearnly_core.execution.work.sweep"]},
                    "daily": ["pearnly_core.execution.work.prune_checkpoints"]}
