from pearnly_core.desk.assets import versioned_asset

app_name = "pearnly_cowork"
app_title = "Pearnly Cowork"
app_publisher = "Pearnly"
app_description = "Cowork 产品：录入工作台、识别记录、推送日志、对账中心、库存盘点"
app_email = "hello@pearnly.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "pearnly_cowork",
# 		"logo": "/assets/pearnly_cowork/logo.png",
# 		"title": "Pearnly Cowork",
# 		"route": "/pearnly_cowork",
# 		"has_permission": "pearnly_cowork.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/pearnly_cowork/css/pearnly_cowork.css"
# app_include_js = "/assets/pearnly_cowork/js/pearnly_cowork.js"

# include js, css files in header of web template
# web_include_css = "/assets/pearnly_cowork/css/pearnly_cowork.css"
# web_include_js = "/assets/pearnly_cowork/js/pearnly_cowork.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "pearnly_cowork/public/scss/website"

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
# app_include_icons = "pearnly_cowork/public/icons.svg"

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
# 	"methods": "pearnly_cowork.utils.jinja_methods",
# 	"filters": "pearnly_cowork.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "pearnly_cowork.install.before_install"
# after_install = "pearnly_cowork.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "pearnly_cowork.uninstall.before_uninstall"
# after_uninstall = "pearnly_cowork.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "pearnly_cowork.utils.before_app_install"
# after_app_install = "pearnly_cowork.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "pearnly_cowork.utils.before_app_uninstall"
# after_app_uninstall = "pearnly_cowork.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "pearnly_cowork.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "pearnly_cowork.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["pearnly_cowork.search.awesomebar_results"]

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
# 		"pearnly_cowork.tasks.all"
# 	],
# 	"daily": [
# 		"pearnly_cowork.tasks.daily"
# 	],
# 	"hourly": [
# 		"pearnly_cowork.tasks.hourly"
# 	],
# 	"weekly": [
# 		"pearnly_cowork.tasks.weekly"
# 	],
# 	"monthly": [
# 		"pearnly_cowork.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "pearnly_cowork.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "pearnly_cowork.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "pearnly_cowork.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "pearnly_cowork.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["pearnly_cowork.utils.before_request"]
# after_request = ["pearnly_cowork.utils.after_request"]

# Job Events
# ----------
# before_job = ["pearnly_cowork.utils.before_job"]
# after_job = ["pearnly_cowork.utils.after_job"]

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
# 	"pearnly_cowork.auth.validate"
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


app_include_js = [versioned_asset("pearnly_cowork", "desk/intake.js")]

# Native resource APIs and Desk lists share the same backend scope.
permission_query_conditions = {
    "Cowork Push Notification": "pearnly_cowork.push.native.documents.native_query",
    "Cowork Bank Evidence": "pearnly_cowork.reconciliation.bank_evidence.deny",
    "Cowork Supplier Posting Profile": "pearnly_cowork.masters.push_inputs.deny",
    "Cowork ERP Mapping": "pearnly_cowork.masters.push_inputs.deny",
    "Cowork Companion Delivery": "pearnly_cowork.companion.delivery_documents.native_query",
    "Cowork Express Agent Credential": "pearnly_cowork.companion.documents.native_query",
    "Cowork ERP Connection": "pearnly_cowork.connections.access.native_query",
    "Cowork ERP Snapshot": "pearnly_cowork.connections.access.native_query",
    "Cowork ERP Refresh": "pearnly_cowork.connections.access.native_query",
    "Cowork Push Record": "pearnly_cowork.push.native.documents.native_query",
    "Cowork Push Attempt": "pearnly_cowork.push.native.documents.native_query",
    "Cowork Push Writer Lock": "pearnly_cowork.push.native.documents.native_query",
    "Cowork OCR Batch": "pearnly_cowork.ocr.execution.access.query",
    "Pearnly OCR Work": "pearnly_cowork.ocr.execution.access.work_query",
    "Cowork OCR Correction": "pearnly_cowork.ocr.feedback.access.native_query",
    "Cowork Workspace": "pearnly_cowork.access.scope.workspace_query",
    "Cowork Client": "pearnly_cowork.access.scope.client_query",
    "Cowork OCR History": "pearnly_cowork.access.scope.history_query",
    "Cowork Source File": "pearnly_cowork.files.access.source_query",
    "File": "pearnly_cowork.files.access.file_query",
    "Cowork Stocktake Task": "pearnly_cowork.stocktake.access.task_query",
    "Cowork Stocktake Item": "pearnly_cowork.stocktake.access.item_query",
    "Cowork Stocktake Entry": "pearnly_cowork.stocktake.access.entry_query",
    "Cowork Stocktake Operation": "pearnly_cowork.stocktake.access.operation_query",
    "Cowork Bank Reconciliation": "pearnly_cowork.reconciliation.bank_recon_access.task_query",
    "Cowork Bank Reconciliation Row": "pearnly_cowork.reconciliation.bank_recon_access.row_query",
}
has_permission = {
    "Cowork Push Notification": "pearnly_cowork.push.native.documents.native_permission",
    "Cowork Bank Evidence": "pearnly_cowork.reconciliation.bank_evidence.deny",
    "Cowork Supplier Posting Profile": "pearnly_cowork.masters.push_inputs.deny",
    "Cowork ERP Mapping": "pearnly_cowork.masters.push_inputs.deny",
    "Cowork Companion Delivery": "pearnly_cowork.companion.delivery_documents.native_permission",
    "Cowork Express Agent Credential": "pearnly_cowork.companion.documents.native_permission",
    "Cowork ERP Connection": "pearnly_cowork.connections.access.native_permission",
    "Cowork ERP Snapshot": "pearnly_cowork.connections.access.native_permission",
    "Cowork ERP Refresh": "pearnly_cowork.connections.access.native_permission",
    "Cowork Push Record": "pearnly_cowork.push.native.documents.native_permission",
    "Cowork Push Attempt": "pearnly_cowork.push.native.documents.native_permission",
    "Cowork Push Writer Lock": "pearnly_cowork.push.native.documents.native_permission",
    "Cowork OCR Batch": "pearnly_cowork.ocr.execution.access.has_permission",
    "Pearnly OCR Work": "pearnly_cowork.ocr.execution.access.work_permission",
    "Cowork OCR Correction": "pearnly_cowork.ocr.feedback.access.native_permission",
    "Cowork Workspace": "pearnly_cowork.access.scope.workspace_permission",
    "Cowork Client": "pearnly_cowork.access.scope.client_permission",
    "Cowork OCR History": "pearnly_cowork.access.scope.history_permission",
    "Cowork Source File": "pearnly_cowork.files.access.source_permission",
    "File": "pearnly_cowork.files.access.file_permission",
    "Cowork Stocktake Task": "pearnly_cowork.stocktake.access.has_permission",
    "Cowork Stocktake Item": "pearnly_cowork.stocktake.access.has_permission",
    "Cowork Stocktake Entry": "pearnly_cowork.stocktake.access.has_permission",
    "Cowork Stocktake Operation": "pearnly_cowork.stocktake.access.has_permission",
    "Cowork Bank Reconciliation": "pearnly_cowork.reconciliation.bank_recon_access.has_permission",
    "Cowork Bank Reconciliation Row": "pearnly_cowork.reconciliation.bank_recon_access.has_permission",
}
doc_events = {"Pearnly OCR Work": {
    "before_insert": "pearnly_cowork.ocr.execution.work_guard.mutation",
    "before_validate": "pearnly_cowork.ocr.execution.work_guard.mutation",
    "validate": "pearnly_cowork.ocr.execution.work_guard.mutation",
    "on_trash": "pearnly_cowork.ocr.execution.work_guard.deny_delete",
}, "DocShare": {
    "before_validate": ["pearnly_cowork.masters.push_inputs.reject_share", "pearnly_cowork.reconciliation.bank_evidence.reject_share", "pearnly_cowork.access.scope.reject_share",
        "pearnly_cowork.companion.delivery_documents.reject_share",
        "pearnly_cowork.companion.documents.reject_share",
        "pearnly_cowork.connections.access.reject_share", "pearnly_cowork.push.native.documents.reject_share",
        "pearnly_cowork.ocr.execution.access.reject_share", "pearnly_cowork.ocr.feedback.access.reject_share",
        "pearnly_cowork.files.access.reject_share", "pearnly_cowork.stocktake.access.reject_share",
        "pearnly_cowork.reconciliation.bank_recon_access.reject_share"],
    "validate": ["pearnly_cowork.masters.push_inputs.reject_share", "pearnly_cowork.reconciliation.bank_evidence.reject_share", "pearnly_cowork.access.scope.reject_share",
        "pearnly_cowork.companion.delivery_documents.reject_share",
        "pearnly_cowork.companion.documents.reject_share",
        "pearnly_cowork.connections.access.reject_share", "pearnly_cowork.push.native.documents.reject_share",
        "pearnly_cowork.ocr.execution.access.reject_share", "pearnly_cowork.ocr.feedback.access.reject_share",
        "pearnly_cowork.files.access.reject_share", "pearnly_cowork.stocktake.access.reject_share",
        "pearnly_cowork.reconciliation.bank_recon_access.reject_share"],
}}
extend_doctype_class = {
    "File": ["pearnly_cowork.files.guard.CoworkFileGuard"],
    "Pearnly OCR Work": ["pearnly_cowork.ocr.execution.work_guard.CoworkWorkGuard"],
}
before_login = ["pearnly_cowork.companion.http_auth.before_login"]
before_request = ["pearnly_cowork.companion.http_auth.before_request"]
auth_hooks = ["pearnly_cowork.companion.http_auth.authenticate_request"]
after_request = ["pearnly_cowork.files.api.protect_response", "pearnly_cowork.companion.http_auth.after_request"]
override_whitelisted_methods = {
    "frappe.core.api.file.zip_files": "pearnly_cowork.files.api.guarded_zip_files",
}

pearnly_ocr_handlers = {"cowork.invoice": "pearnly_cowork.ocr.execution.workflow.run"}
