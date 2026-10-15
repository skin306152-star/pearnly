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
# 个人信息页（共用层的原生 User 表单）里追加 LINE 绑定块。
doctype_js = {"User": "public/desk/user-line.js"}
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


# 录入工作台这一条链的页面脚本：工作台（列表 + 目标弹窗）、目标/连接状态、
# 上传识别二级页。按内容版本号加载，改完刷新即生效。
app_include_js = [
    versioned_asset("pearnly_cowork", "desk/choice.js"),
    versioned_asset("pearnly_cowork", "desk/targets.js"),
    versioned_asset("pearnly_cowork", "desk/intake.js"),
    versioned_asset("pearnly_cowork", "desk/intake-choices.js"),
    versioned_asset("pearnly_cowork", "desk/intake-upload.js"),
    versioned_asset("pearnly_cowork", "desk/team.js"),
    versioned_asset("pearnly_cowork", "desk/post-file.js"),
    versioned_asset("pearnly_cowork", "desk/stocktake-entry.js"),
    versioned_asset("pearnly_cowork", "desk/stocktake.js"),
    versioned_asset("pearnly_cowork", "desk/recon.js"),
    versioned_asset("pearnly_cowork", "desk/line-binding.js"),
    versioned_asset("pearnly_cowork", "desk/work.js"),
    # 自动录入规则页（录入工作台子路由 pearnly-intake/folders，2026-10-07）
    versioned_asset("pearnly_cowork", "desk/intake-folders.js"),
]
app_include_css = [versioned_asset("pearnly_cowork", "desk/intake.css"), versioned_asset("pearnly_cowork", "desk/recon.css"),
                   versioned_asset("pearnly_cowork", "desk/work.css")]

# Native resource APIs and Desk lists share the same backend scope.
permission_query_conditions = {
    "Cowork Workspace Import": "pearnly_cowork.masters.workspace_import.deny",
    "Cowork Invoice Category": "pearnly_cowork.history.categories.native_query",
    "Cowork Supplier Category Memory": "pearnly_cowork.history.categories.native_query",
    "Cowork Name Memory": "pearnly_cowork.history.names.native_query",
    "Cowork Push Notification": "pearnly_cowork.push.native.documents.native_query",
    "Cowork Bank Evidence": "pearnly_cowork.reconciliation.bank_evidence.deny",
    "Cowork Supplier Posting Profile": "pearnly_cowork.masters.push_inputs.deny",
    "Cowork ERP Mapping": "pearnly_cowork.masters.push_inputs.deny",
    "Cowork Recon Preset": "pearnly_cowork.reconciliation.preset_store.deny",
    "Cowork Recon Rerun": "pearnly_cowork.recon_center.rerun.deny",
    "Cowork Express Choice": "pearnly_cowork.push.choices.deny_query",
    "Cowork POS Layout": "pearnly_cowork.pos_import.layouts.deny_query",
    "Cowork Companion Delivery": "pearnly_cowork.companion.delivery_documents.native_query",
    "Cowork Express Agent Credential": "pearnly_cowork.companion.documents.native_query",
    "Cowork CC Desktop Device": "pearnly_cowork.companion.device_documents.native_query",
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
    "File": ["pearnly_cowork.files.access.file_query", "pearnly_cowork.work.access.file_query"],
    "Cowork Stocktake Task": "pearnly_cowork.stocktake.access.task_query",
    "Cowork Stocktake Item": "pearnly_cowork.stocktake.access.item_query",
    "Cowork Stocktake Entry": "pearnly_cowork.stocktake.access.entry_query",
    "Cowork Stocktake Operation": "pearnly_cowork.stocktake.access.operation_query",
    "Cowork Bank Reconciliation": "pearnly_cowork.reconciliation.bank_recon_access.task_query",
    "Cowork Bank Reconciliation Row": "pearnly_cowork.reconciliation.bank_recon_access.row_query",
    "Cowork Income Reconciliation": "pearnly_cowork.reconciliation.income_recon_access.task_query",
    "Cowork Income Reconciliation Row": "pearnly_cowork.reconciliation.income_recon_access.row_query",
    "Cowork VAT Check": "pearnly_cowork.reconciliation.vat_check_access.task_query",
    "Cowork Tax Draft": "pearnly_cowork.tax.access.task_query",
    "Cowork WHT Choice": "pearnly_cowork.tax.memory.deny_query",
    "Cowork WHT Certificate": "pearnly_cowork.history.certificates.deny_query",
    "Cowork Work Board": "pearnly_cowork.work.access.board_query",
    "Cowork Work Card": "pearnly_cowork.work.access.card_query",
    "Cowork Work Label": "pearnly_cowork.work.access.label_query",
    "Cowork Work Activity": "pearnly_cowork.work.access.activity_query",
    "Cowork Work Notification": "pearnly_cowork.work.access.notification_query",
    "Cowork Work Column": "pearnly_cowork.work.access.column_query",
    "Cowork Work Field": "pearnly_cowork.work.access.field_query",
    "Cowork Work Mirror": "pearnly_cowork.work.access.mirror_query",
    "Cowork Work Rule": "pearnly_cowork.work.access.rule_query",
    "Cowork Folder Intake Rule": "pearnly_cowork.folder_intake.access.deny_query",
    "Cowork Folder Intake Run": "pearnly_cowork.folder_intake.access.deny_query",
    "Cowork Folder Intake File": "pearnly_cowork.folder_intake.access.deny_query",
    "Cowork Company Folder": "pearnly_cowork.folder_intake.access.deny_query",
    "Cowork Company": "pearnly_cowork.companies.access.deny_query",
    "Cowork Company Note": "pearnly_cowork.companies.access.deny_query",
    "Cowork Companion Request": "pearnly_cowork.companion.request_access.deny_query",
    "Cowork Express Readback": "pearnly_cowork.readback.access.deny_query",
}
has_permission = {
    "Cowork Workspace Import": "pearnly_cowork.masters.workspace_import.deny",
    "Cowork Invoice Category": "pearnly_cowork.history.categories.deny",
    "Cowork Supplier Category Memory": "pearnly_cowork.history.categories.deny",
    "Cowork Name Memory": "pearnly_cowork.history.names.deny",
    "Cowork Push Notification": "pearnly_cowork.push.native.documents.native_permission",
    "Cowork Bank Evidence": "pearnly_cowork.reconciliation.bank_evidence.deny",
    "Cowork Supplier Posting Profile": "pearnly_cowork.masters.push_inputs.deny",
    "Cowork ERP Mapping": "pearnly_cowork.masters.push_inputs.deny",
    "Cowork Recon Preset": "pearnly_cowork.reconciliation.preset_store.deny",
    "Cowork Recon Rerun": "pearnly_cowork.recon_center.rerun.deny",
    "Cowork Express Choice": "pearnly_cowork.push.choices.deny",
    "Cowork POS Layout": "pearnly_cowork.pos_import.layouts.deny",
    "Cowork Companion Delivery": "pearnly_cowork.companion.delivery_documents.native_permission",
    "Cowork Express Agent Credential": "pearnly_cowork.companion.documents.native_permission",
    "Cowork CC Desktop Device": "pearnly_cowork.companion.device_documents.native_permission",
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
    "File": ["pearnly_cowork.files.access.file_permission", "pearnly_cowork.work.access.file_permission"],
    "Cowork Stocktake Task": "pearnly_cowork.stocktake.access.has_permission",
    "Cowork Stocktake Item": "pearnly_cowork.stocktake.access.has_permission",
    "Cowork Stocktake Entry": "pearnly_cowork.stocktake.access.has_permission",
    "Cowork Stocktake Operation": "pearnly_cowork.stocktake.access.has_permission",
    "Cowork Bank Reconciliation": "pearnly_cowork.reconciliation.bank_recon_access.has_permission",
    "Cowork Bank Reconciliation Row": "pearnly_cowork.reconciliation.bank_recon_access.has_permission",
    "Cowork Income Reconciliation": "pearnly_cowork.reconciliation.income_recon_access.has_permission",
    "Cowork Income Reconciliation Row": "pearnly_cowork.reconciliation.income_recon_access.has_permission",
    "Cowork VAT Check": "pearnly_cowork.reconciliation.vat_check_access.has_permission",
    "Cowork Tax Draft": "pearnly_cowork.tax.access.has_permission",
    "Cowork WHT Choice": "pearnly_cowork.tax.memory.deny",
    "Cowork WHT Certificate": "pearnly_cowork.history.certificates.deny",
    "Cowork Work Board": "pearnly_cowork.work.access.has_permission",
    "Cowork Work Card": "pearnly_cowork.work.access.has_permission",
    "Cowork Work Label": "pearnly_cowork.work.access.has_permission",
    "Cowork Work Activity": "pearnly_cowork.work.access.has_permission",
    "Cowork Work Notification": "pearnly_cowork.work.access.notification_permission",
    "Cowork Work Column": "pearnly_cowork.work.access.has_permission",
    "Cowork Work Field": "pearnly_cowork.work.access.has_permission",
    "Cowork Work Mirror": "pearnly_cowork.work.access.has_permission",
    "Cowork Work Rule": "pearnly_cowork.work.access.has_permission",
    "Cowork Folder Intake Rule": "pearnly_cowork.folder_intake.access.deny",
    "Cowork Folder Intake Run": "pearnly_cowork.folder_intake.access.deny",
    "Cowork Folder Intake File": "pearnly_cowork.folder_intake.access.deny",
    "Cowork Company Folder": "pearnly_cowork.folder_intake.access.deny",
    "Cowork Company": "pearnly_cowork.companies.access.deny",
    "Cowork Company Note": "pearnly_cowork.companies.access.deny",
    "Cowork Companion Request": "pearnly_cowork.companion.request_access.deny",
    "Cowork Express Readback": "pearnly_cowork.readback.access.deny",
}
doc_events = {"Pearnly OCR Work": {
    "before_insert": "pearnly_cowork.ocr.execution.work_guard.mutation",
    "before_validate": "pearnly_cowork.ocr.execution.work_guard.mutation",
    "validate": "pearnly_cowork.ocr.execution.work_guard.mutation",
    "on_trash": "pearnly_cowork.ocr.execution.work_guard.deny_delete",
}, "DocShare": {
    "before_validate": ["pearnly_cowork.masters.workspace_import.reject_share", "pearnly_cowork.history.categories.reject_share", "pearnly_cowork.history.names.reject_share", "pearnly_cowork.masters.push_inputs.reject_share", "pearnly_cowork.reconciliation.bank_evidence.reject_share", "pearnly_cowork.access.scope.reject_share",
        "pearnly_cowork.companion.delivery_documents.reject_share",
        "pearnly_cowork.companion.documents.reject_share",
        "pearnly_cowork.companion.device_documents.reject_share",
        "pearnly_cowork.connections.access.reject_share", "pearnly_cowork.push.native.documents.reject_share",
        "pearnly_cowork.ocr.execution.access.reject_share", "pearnly_cowork.ocr.feedback.access.reject_share",
        "pearnly_cowork.files.access.reject_share", "pearnly_cowork.stocktake.access.reject_share",
        "pearnly_cowork.reconciliation.bank_recon_access.reject_share",
        "pearnly_cowork.reconciliation.income_recon_access.reject_share",
        "pearnly_cowork.reconciliation.vat_check_access.reject_share",
        "pearnly_cowork.tax.access.reject_share", "pearnly_cowork.tax.memory.reject_share",
        "pearnly_cowork.history.certificates.reject_share",
        "pearnly_cowork.reconciliation.preset_store.reject_share",
        "pearnly_cowork.recon_center.rerun.reject_share",
        "pearnly_cowork.work.access.reject_share", "pearnly_cowork.push.choices.reject_share",
        "pearnly_cowork.pos_import.layouts.reject_share",
        "pearnly_cowork.folder_intake.access.reject_share", "pearnly_cowork.companion.request_access.reject_share",
        "pearnly_cowork.readback.access.reject_share", "pearnly_cowork.companies.access.reject_share"],
    "validate": ["pearnly_cowork.masters.workspace_import.reject_share", "pearnly_cowork.history.categories.reject_share", "pearnly_cowork.history.names.reject_share", "pearnly_cowork.masters.push_inputs.reject_share", "pearnly_cowork.reconciliation.bank_evidence.reject_share", "pearnly_cowork.access.scope.reject_share",
        "pearnly_cowork.companion.delivery_documents.reject_share",
        "pearnly_cowork.companion.documents.reject_share",
        "pearnly_cowork.companion.device_documents.reject_share",
        "pearnly_cowork.connections.access.reject_share", "pearnly_cowork.push.native.documents.reject_share",
        "pearnly_cowork.ocr.execution.access.reject_share", "pearnly_cowork.ocr.feedback.access.reject_share",
        "pearnly_cowork.files.access.reject_share", "pearnly_cowork.stocktake.access.reject_share",
        "pearnly_cowork.reconciliation.bank_recon_access.reject_share",
        "pearnly_cowork.reconciliation.income_recon_access.reject_share",
        "pearnly_cowork.reconciliation.vat_check_access.reject_share",
        "pearnly_cowork.tax.access.reject_share", "pearnly_cowork.tax.memory.reject_share",
        "pearnly_cowork.history.certificates.reject_share",
        "pearnly_cowork.reconciliation.preset_store.reject_share",
        "pearnly_cowork.recon_center.rerun.reject_share",
        "pearnly_cowork.work.access.reject_share", "pearnly_cowork.push.choices.reject_share",
        "pearnly_cowork.pos_import.layouts.reject_share",
        "pearnly_cowork.folder_intake.access.reject_share", "pearnly_cowork.companion.request_access.reject_share",
        "pearnly_cowork.readback.access.reject_share", "pearnly_cowork.companies.access.reject_share"],
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

pearnly_ocr_handlers = {"cowork.invoice": "pearnly_cowork.ocr.execution.workflow.run",
                        # 销项税报告核查的图片 / PDF 发票（同一个识别引擎，结果不进识别记录）
                        "cowork.vat_check": "pearnly_cowork.recon_center.invoice_ocr.run",
                        # 对账三类文档（银行账单 / 总账 / 销项税报告）要模型读的：扫描件、照片、要模型兜底的 PDF
                        "cowork.recon_document": "pearnly_cowork.recon_center.document_ocr.run"}
# LINE 发来的单据：识别落成票后推卡、最终失败时回一句（不是从 LINE 来的原件什么都不做）。
cowork_ocr_recognized = ["pearnly_cowork.line.intake.recognized", "pearnly_cowork.folder_intake.events.recognized"]
# 推送时这张票已定的选择（push/application/service.py 的 `_fixed`）：定时任务的票推往该任务的连接与账套时带任务默认处理，
# 网页复核页、手机、CC 重推同一口径；取不到回 {}（2026-10-12）。
cowork_push_fixed_choices = ["pearnly_cowork.folder_intake.push.fixed_for"]
# 要人选费用科目时的历史建议（push/application/service.py 的 `_suggest`）：这本账套近 12 个月这家供应商常用的科目，
# 经按需请求请电脑读、按连接 + 账套存；取不到回 none、不抛错（科目建议 B，2026-10-15）。
cowork_push_account_history = ["pearnly_cowork.readback.history.for_push"]
# 票的推送状态 / 原因变了（history/push_state.py 投影推送账本时）：定时任务的票在提交后马上刷新那份文件的结果与结果表格，
# 不等每 10 分钟的跟进（2026-10-13）。
cowork_push_state_changed = ["pearnly_cowork.folder_intake.events.pushed"]
pearnly_ocr_failure_handlers = {"cowork.invoice": "pearnly_cowork.line.intake.ocr_failed"}
# 续跑时遇到上一个进程留下的「结果不明」调用，由对账的识别任务自己处理：账单 / 总账 / 税报按有限次重发，发票当成这一页
# 读不出交引擎按页兜底；不整份作废（2026-09-30 压测第 18 轮）。
pearnly_ocr_resume_unknown = ["cowork.vat_check", "cowork.recon_document"]
# 识别有了结论：等它的对账放回队列接着跑（对账等识别时不占进程）；任何一份结束都腾出识别名额，放等名额的对账
# （recon_center/waiting.py，2026-10-01）。
pearnly_ocr_finished_handlers = {kind: "pearnly_cowork.recon_center.waiting.work_finished"
                                 for kind in ("cowork.invoice", "cowork.vat_check", "cowork.recon_document")}
# 识别交给 Cloud Tasks（站点配置 pearnly_cowork_ocr_tasks，默认关）时，读的过程中的进度由 Cloud Run 回调送来，
# 按类别交给这里登记的函数更新页面 / 看板（ocr/remote/callback.py，2026-10-01）。
cowork_remote_progress = {"cowork.invoice": "pearnly_cowork.ocr.execution.workflow.remote_progress",
                          "cowork.recon_document": "pearnly_cowork.recon_center.document_ocr.remote_progress"}

# Product-owned menu, implemented routes, permissions and native sidebar groups (ADR-009).
# 菜单（用户 2026-09-26）：录入工作台、对账中心、库存盘点、工作协调；团队管理放左下角用户菜单。
# 识别记录、推送日志、客户、公司资料、集成、使用教程不做。库存盘点照搬 ERP、只去掉盘点在库（2026-09-26）；
# 工作协调是团队看板（2026-09-27）：不单独售卖（功能码 None），勾了「工作协调」或管理员就能进。
# 显示名 2026-09-27 改为「任务管理」，图标 square-kanban，与 LINE 菜单格子同一个图标。
pearnly_navigation = [{'product': 'cowork',
  'app': 'pearnly_cowork',
  'profile_label': 'My Profile',
  # 团队管理勾选框的分组：业务功能一组，个人信息 + LINE 一组（与 ERP「用户菜单」同一口径）。
  'permission_groups': [('General', ['pearnly-intake', 'pearnly-recon-bank', 'pearnly-recon-income', 'pearnly-recon-vat',
                                     'pearnly-stocktake', 'pearnly-work']),
                        ('User Settings', ['user'])],
  'pages': [('nav.home', 'pearnly-home', 'house', None),
            ('nav.intake', 'pearnly-intake', 'scan', 'intake'),
            # 对账中心拆成三个侧栏入口（用户 2026-09-26）；团队管理里也是三个可单独勾选的角色（2026-09-27）。
            ('cowork.nav.reconBank', 'pearnly-recon-bank', 'landmark', 'recon'),
            ('cowork.nav.reconIncome', 'pearnly-recon-income', 'receipt', 'recon'),
            ('cowork.nav.reconVat', 'pearnly-recon-vat', 'file-check', 'recon'),
            ('nav.stocktake', 'pearnly-stocktake', 'boxes', 'stocktake'),
            ('cowork.nav.work', 'pearnly-work', 'square-kanban', None),
            ('nav.team', 'pearnly-team', 'users-round', None)],
  'available': ['pearnly-home', 'pearnly-intake', 'pearnly-recon-bank', 'pearnly-recon-income', 'pearnly-recon-vat',
                'pearnly-stocktake', 'pearnly-work', 'pearnly-team'],
  'permissions': {'pearnly-intake': {'doctype': 'Intake Record'},
                  'pearnly-recon-bank': {'any_role': ['Cowork Bank Reconciliation', 'Cowork Admin']},
                  'pearnly-recon-income': {'any_role': ['Cowork Income Reconciliation', 'Cowork Admin']},
                  'pearnly-recon-vat': {'any_role': ['Cowork VAT Check', 'Cowork Admin']},
                  'pearnly-stocktake': {'any_role': ['Cowork Stocktake', 'Cowork Admin']},
                  'pearnly-work': {'any_role': ['Cowork Work', 'Cowork Admin']},
                  # 能管本产品用户的是产品管理员（判据在 `team.service.require_admin`）。
                  'pearnly-team': {'any_role': ['Cowork Admin']},
                  # 个人信息：勾了「个人信息」或「LINE」的人都进得去（LINE 在个人信息里绑）。
                  'user': {'any_role': ['Cowork Profile', 'Cowork LINE', 'Cowork Admin']}},
  'user_menu': ['pearnly-team']}]

# 团队管理 / 个人信息 / LINE 三个没有 DocType 挂着的角色，装好、升级后确保存在；
# 站点内部那一份归属（Cowork 不做公司隔离）也一并确保存在。
after_install = ["pearnly_cowork.team.setup.sync_roles", "pearnly_cowork.access.scope.ensure_on_migrate",
                 "pearnly_cowork.work.service.ensure_on_migrate"]
after_migrate = ["pearnly_cowork.team.setup.sync_roles", "pearnly_cowork.access.scope.ensure_on_migrate",
                 "pearnly_cowork.work.service.ensure_on_migrate"]
boot_session = "pearnly_cowork.line.boot.boot_session"
# 文件夹自动录入的站点总闸放进启动信息（不进按用户缓存的 boot_session，改总闸后刷新页面即生效）。
pearnly_line_handlers = ["pearnly_cowork.line.handlers.handle"]


# MR.ERP 推送的兜底巡检：丢失的排队任务重新排上、过期的执行转结果不明（见 push.mrerp.runner.sweep）。
# 工作协调看板：每 5 分钟按卡片的"提醒时间"发到期提醒、到期后发一次过期提醒（同一到期时刻每人各一次）。
scheduler_events = {"cron": {"*/5 * * * *": ["pearnly_cowork.push.mrerp.runner.sweep",
                                             "pearnly_cowork.work.reminders.run",
                                             # 部署 / 重启时被杀的对账续跑（recon_center/jobs.py）
                                             "pearnly_cowork.recon_center.jobs.resume_abandoned",
                                             # 定时任务的前一天提醒与完成简报（folder_intake/schedule.py）
                                             "pearnly_cowork.folder_intake.schedule.notices"],
                             "* * * * *": [
                                 # 等识别的对账：提醒万一没送到、时限到了、开始读了、名额腾出来了，放回队列（recon_center/waiting.py）
                                 "pearnly_cowork.recon_center.waiting.tick",
                                 # 对账按租户限流：清掉不在跑却占着名额的，补位（recon_center/slots.py）
                                 "pearnly_cowork.recon_center.slots.tick",
                                 # 文件夹自动录入的后台推进（folder_intake/schedule.py）
                                 "pearnly_cowork.folder_intake.schedule.tick"]},
                    # 对账预设问题留着的原文件：过期没人答的删掉（recon_center/rerun.py）。
                    "daily": ["pearnly_cowork.recon_center.rerun.expire"]}

# 后台 worker 父进程预先导入的模块（`pearnly_core/execution/worker.py`）：对账长任务与每分钟的定时任务。
# 任务照旧每次 fork，只是不再每个任务重新导入；导入不能开连接、不能依赖 Site。
# 智能体工具（ADR-019 / ADR-009）：Cowork 自己声明，共用层汇总并以当前用户身份在服务端执行。
pearnly_assistant_tools = ["pearnly_cowork.assistant.tools.TOOLS", "pearnly_cowork.assistant.company_tools.TOOLS"]
# Cowork 自己的说话规矩（账套按公司名说，不说文件夹代码）：共用层接在共用说明后面
pearnly_assistant_guidance = ["pearnly_cowork.assistant.tools.GUIDANCE",
                              "pearnly_cowork.assistant.company_tools.GUIDANCE"]
# 工具进行中的说法（对话底部的工作状态行，如「正在识别单据」）：共用层汇总，译文在本 App 术语表
pearnly_assistant_step_names = ["pearnly_cowork.assistant.step_names.STEP_NAMES"]
# CC 按公司主档组织对话（ADR-020）：共用层取这个模块的 KIND / PERIOD / SCOPE / allowed / view / context
pearnly_assistant_projects = ["pearnly_cowork.companies.provider"]
# 每 5 分钟的提醒入口（`folder_intake.schedule.notices`）顺带叫的：「待处理」早上汇总（ADR-020，补充 乙）。
# 经 hooks 声明而不是 folder_intake 直接导入 companies（companies 依赖 folder_intake，反过来会成环）。
cowork_scheduled_notices = ["pearnly_cowork.companies.pending.morning"]
# 公司主档页「记住的选择」里来自 Express 历史的「供应商 → 科目」（推送时会直接用的那些）：读与删（删了这家供应商不再
# 按历史用）。存放在 readback 域（科目建议 B），companies 不导入它（readback 依赖 push，companies 也依赖 push，各走各的）。
cowork_company_history_choices = ["pearnly_cowork.readback.history.company_choices"]
cowork_company_history_forget = ["pearnly_cowork.readback.history.company_forget"]
# 推送前核对分店（补充 甲.3）：票面没有本方分店时，CC 对话指定的分店（`project_scope`，只在推往这家公司的账套时算）。
# push 域不导入 companies（companies 依赖 push）。
cowork_push_work_branch = ["pearnly_cowork.companies.provider.work_branch"]
# 定时任务的提醒 / 完成简报属于哪家公司（点开到这家的主档页）：folder_intake 不导入 companies（反过来依赖）。
cowork_rules_company = ["pearnly_cowork.companies.service.company_of_rules"]

pearnly_worker_preload = [
    "pearnly_cowork.recon_center.jobs",
    "pearnly_cowork.recon_center.document_ocr",
    "pearnly_cowork.recon_center.vat_check",
    "pearnly_cowork.recon_center.sheets",
    "pearnly_cowork.recon_center.waiting",
    "pearnly_cowork.recon_center.slots",
    "pearnly_cowork.recon_center.rerun",
    "pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation",
    "pearnly_cowork.ocr.enterprise.text_layer",
    "pearnly_cowork.push.mrerp.runner",
    "pearnly_cowork.work.reminders",
    "pdfminer.high_level",
]
