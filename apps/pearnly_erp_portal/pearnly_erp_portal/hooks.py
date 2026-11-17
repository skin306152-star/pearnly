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

# 单据打印（领料单 / 收货单，`printing`）：Print Format 里用 `pearnly_print_context(doc)` 取抬头、明细、勾选
jinja = {"methods": ["pearnly_erp_portal.printing.context.pearnly_print_context"]}

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
                  "ERP Sales Document": "pearnly_erp_portal.sales.access.document_permission"}
permission_query_conditions = {"Purchase Document": "pearnly_erp_portal.purchasing.access.query_conditions",
                               "ERP Sales Document": "pearnly_erp_portal.sales.access.query_conditions"}

# 个人信息页（原生 User 表单）里 ERP 自己那一块（LINE 绑定）；页面本身归共用层。
doctype_js = {"User": "public/desk/user-line.js"}

app_include_js = [
    versioned_asset("pearnly_erp_portal", "desk/transactions.js"),
    versioned_asset("pearnly_erp_portal", "desk/serials.js"),
    versioned_asset("pearnly_erp_portal", "desk/line-binding.js"),
    versioned_asset("pearnly_erp_portal", "desk/company-menu.js"),
    versioned_asset("pearnly_erp_portal", "desk/company-connect.js"),
    versioned_asset("pearnly_erp_portal", "desk/entry-choice.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-fields.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-layout.js"),
    versioned_asset("pearnly_erp_portal", "desk/erp-originals.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-entry.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-viewer.js"),
    versioned_asset("pearnly_erp_portal", "desk/purchase-form.js"),
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
    versioned_asset("pearnly_erp_portal", "desk/sales.js"),
    versioned_asset("pearnly_erp_portal", "desk/collection-new.js"),
    versioned_asset("pearnly_erp_portal", "desk/collections.js"),
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
    versioned_asset("pearnly_erp_portal", "desk/work.js"),
]

app_include_css = [
    versioned_asset("pearnly_erp_portal", "desk/purchase.css"),
    versioned_asset("pearnly_erp_portal", "desk/sales.css"),
    versioned_asset("pearnly_erp_portal", "desk/pos/official-pos.css"),
    versioned_asset("pearnly_erp_portal", "desk/cashier.css"),
    versioned_asset("pearnly_erp_portal", "desk/work.css"),
    versioned_asset("pearnly_erp_portal", "desk/company.css"),
]

# LINE 事件处理：传输层（pearnly_core.line）验签与幂等之后，交给这里处理。
# 绑定、菜单与录单前的选择、收单据 → 识别 → 预览卡 → 入账，见 integrations/handlers.py。
pearnly_line_handlers = ["pearnly_erp_portal.integrations.handlers.handle"]
# 开通时（`pearnly_core.provisioning.ensure_product_workspace`）建好默认公司：没有公司，取当前公司会报错。
pearnly_tenant_ready = ["pearnly_erp_portal.masters.company.ensure_default"]
# LINE 收单的识别走共用 OCR 队列（容量、计费与不重复扣费都在共用层）。
pearnly_ocr_handlers = {"erp.line_intake": "pearnly_erp_portal.integrations.intake.run",
                        # CC 对话收单（盘点 2026-11-08 P0-2）：同 LINE 收单一条链路，不回 LINE
                        "erp.cc_intake": "pearnly_erp_portal.integrations.intake.run_chat"}
pearnly_ocr_failure_handlers = {"erp.line_intake": "pearnly_erp_portal.integrations.intake.ocr_failed",
                                "erp.cc_intake": "pearnly_erp_portal.integrations.intake.chat_failed"}
# 过期没点的预览卡连源文件一起清掉（不做草稿）。
scheduler_events = {
    "hourly": ["pearnly_erp_portal.integrations.intake.expire_stale",
               # Express 当账本的公司：主档与库存从 Express 回读（没接 Express 的站点什么都不做）
               "pearnly_erp_portal.express.masters.sync_all",
               # MR.ERP 当账本的公司：服务端整读主档比对、只写变化行；长期没人用的不读（开关关着什么都不做）
               "pearnly_erp_portal.mrerp.masters.sync_all"],
    # 写进 Express 的单：停下的、会自己好的再准备一次（结果由写账队列直接交回，不轮询；没有要看的就一条查询）
    "cron": {"* * * * *": ["pearnly_erp_portal.express.push.poll",
                           # 排着的 Express 主档同步：按顺序发起读取、读好了排后台任务落库（没有排着的就一条查询）
                           "pearnly_erp_portal.express.masters.advance",
                           # 收款：发起过的未收清发票读取读好了就落进快照（没有发起过就一次缓存读）
                           "pearnly_erp_portal.collection.snapshot.advance",
                           # 收款写进 MR.ERP：等账号的再排、进程死掉的「写入中」转结果不明（开关关着直接返回）
                           "pearnly_erp_portal.collection.mrerp.poll"],
             # Express 主档有改动就跟上：请电脑看一眼主档表的文件属性，变了才整套同步（每家一件很轻的请求）
             "*/10 * * * *": ["pearnly_erp_portal.express.masters.check_marks"],
             # 任务管理：按卡片的提醒时间发到期提醒、到期后发一次过期提醒（同一到期时刻每人各一次）
             "*/5 * * * *": ["pearnly_erp_portal.work.reminders.run"],
             # 接了 Express 的公司：昨天及更早的收银小票按天合成现销日汇总写进 Express（出入库第一期 B3a）
             "20 1 * * *": ["pearnly_erp_portal.express.pos_day.nightly"]},
}

# CC 桌面版直连 ERP（ADR-025）：员工用 ERP 账号在 CC 窗口里登录，这台电脑接上本站的共用机器通道，替他把单写进 Express。
pearnly_cc_desktop = {
    "allowed": "pearnly_erp_portal.express.desktop.allowed",
    "job_changed": "pearnly_erp_portal.express.desktop.job_changed",
    # 电脑报来的账套名单变了：按账套自动建公司、排主档同步（`express.companies`）
    "books_changed": "pearnly_erp_portal.express.companies.books_changed",
    # 领单前兜底：来源单据已作废的写账件撤掉、不交电脑（11-13）
    "still_wanted": "pearnly_erp_portal.express.desktop.still_wanted",
    # 替写（用户 11-17 定稿，两产品同一条）：别人的电脑能不能替这件写——对这家公司有权限、签发有效
    "stand_in": "pearnly_erp_portal.express.desktop.stand_in",
    # express_ar_open（11-11 收款）：一本账套的未收清发票（共用层接受这一种由 CC 桌面版那一批加）
    "kinds": ["express_masters", "express_marks", "express_doc_check", "express_ar_open"],
}
# MR.ERP 登录在 CC 桌面版里授权（ADR-026 第 7 条）：ERP 接 MR.ERP 当账本（`mrerp`）开着才声明；关着时与以前一样回 unsupported
from pearnly_erp_portal import mrerp as _mrerp  # noqa: E402

if _mrerp.MRERP_LEDGER_ENABLED:
    pearnly_cc_desktop.update(mrerp_login_scope="pearnly_erp_portal.mrerp.connection.login_scope",
                              mrerp_login_store="pearnly_erp_portal.mrerp.connection.login_store")

# 产品自己的启动数据（多公司：能切哪几家、当前是哪家）。官方扩展点，见 boot.py。
boot_session = "pearnly_erp_portal.masters.boot.boot_session"

# Product-owned menu, implemented routes, permissions and native sidebar groups (ADR-009).
pearnly_navigation = [{'product': 'erp',
  'app': 'pearnly_erp_portal',
  # 字段说明收进标题旁的说明图标（共用层 `pearnly_core/public/desk/field-help.js`，用户 11-02）
  'field_help': 'icon',
  "profile_label": "My Profile",
  # 仓库不写在这里（岳父 11-08）：它在侧栏「物料与库存」组，岗位勾选跟着侧栏归到同一组
  "permission_groups": [("General", ["pearnly-home", "pearnly-tasks"]),
                        ("User Settings", ["user", "pearnly-team", "pearnly-company"])],
  # 产品自己的**页面过滤器**（共用层只负责调）：收银台是公司级开关，这家公司没开收银，
  # 那三个收银页与收银岗位都不出现。见 `masters/nav.py` 与 ADR-009 的声明约定。
  'page_filter': 'pearnly_erp_portal.masters.nav.filter_pages',
  'pages': [('nav.home', 'pearnly-home', 'house', None),
            ('nav.inventory', 'pearnly-inventory', 'warehouse', 'warehouses'),
            ('nav.products', 'pearnly-products', 'package', 'products'),
            ('nav.stockIn', 'pearnly-stock-in', 'download', 'warehouses'),
            ('nav.stockOut', 'pearnly-stock-out', 'upload', 'warehouses'),
            ('nav.stockTransfer', 'pearnly-stock-transfer', 'arrow-right-left', 'warehouses'),
            # 物料调整（岳父 11-06）：账面与实物对不上时正负混录，盘点差异也生成它（号 IA）
            ('nav.stockAdjust', 'pearnly-stock-adjust', 'sliders-horizontal', 'warehouses'),
            ('nav.stockCard', 'pearnly-stock-card', 'list-checks', 'stockcard'),
            ('nav.stockCount', 'pearnly-stock-count', 'boxes', 'stocktake'),
            ('nav.purchase', 'pearnly-purchase', 'shopping-cart', 'purchase'),
            ('nav.suppliers', 'pearnly-suppliers', 'store', 'purchase'),
            ('nav.sales', 'pearnly-sales', 'receipt-text', 'sales'),
            # 收款（11-11 / 11-14）：一个「收款」权限——本公司全部收款、新建、改 / 撤回还没写进 Express 的（不分谁录的），
            # LINE 确认卡【编辑】也打开这一页；看「收款」功能开没开（用户 11-14）
            ('erp.nav.collections', 'pearnly-collections', 'receipt-text', 'collection'),
            ('nav.cashier', 'pearnly-cashier', 'shopping-cart', 'cashier'),
            ('nav.posReceipts', 'pearnly-pos-receipts', 'receipt-text', 'cashier'),
            ('nav.posShifts', 'pearnly-pos-shifts', 'clipboard-list', 'cashier'),
            ('nav.erpClients', 'pearnly-clients', 'user', 'clients'),
            # 任务管理（看板，2026-10-23 从 Cowork 复制）：侧栏里「销售系统」下方单独一项（见下面 groups）。
            ('erp.nav.work', 'pearnly-tasks', 'square-kanban', 'tasks'),
            ('nav.company', 'pearnly-company', 'building-2', None),
            ('nav.warehouses', 'pearnly-warehouses', 'warehouse', None),
            ('nav.team', 'pearnly-team', 'users-round', None)],
  'available': ['pearnly-home',
                'pearnly-products',
                'pearnly-inventory',
                'pearnly-stock-in',
                'pearnly-stock-out',
                'pearnly-stock-transfer',
                'pearnly-stock-adjust',
                'pearnly-stock-card',
                'pearnly-stock-count',
                'pearnly-purchase',
                'pearnly-suppliers',
                'pearnly-sales',
                'pearnly-collections',
                'pearnly-my-collections',
                'pearnly-clients',
                'pearnly-cashier',
                'pearnly-pos-receipts',
                'pearnly-pos-shifts',
                'pearnly-tasks',
                'pearnly-team',
                'pearnly-company',
                'pearnly-warehouses'],
  'permissions': {'pearnly-products': {'doctype': 'ERP Product'},
                  'pearnly-inventory': {'doctype': 'ERP Stock Balance'},
                  'pearnly-stock-in': {'doctype': 'ERP Stock Entry'},
                  'pearnly-stock-out': {'doctype': 'ERP Stock Entry'},
                  'pearnly-stock-transfer': {'doctype': 'ERP Stock Entry'},
                  'pearnly-stock-adjust': {'doctype': 'ERP Stock Entry'},
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
              # 顺序（岳父 11-06）：库存、物料、入库、出库、调拨、物料调整、收发存报表、盘点；
              # 仓库（岳父 11-08）：从左下角用户菜单移进这一组，放在库存与物料之间（"仓库属于物料相关"）
              ['pearnly-inventory',
               'pearnly-warehouses',
               'pearnly-products',
               'pearnly-stock-in',
               'pearnly-stock-out',
               'pearnly-stock-transfer',
               'pearnly-stock-adjust',
               'pearnly-stock-card',
               'pearnly-stock-count']),
             ('nav.group.purchasing',
              'shopping-cart',
              ['pearnly-purchase', 'pearnly-suppliers']),
             ('nav.group.sales',
              'receipt-text',
              ['pearnly-sales',
               'pearnly-collections',
               'pearnly-cashier',
               'pearnly-pos-receipts',
               'pearnly-pos-shifts',
               'pearnly-clients']),
             # 标题为 None：不是分组，是放在这个位置的单独一项（用户 2026-10-23："放在销售系统的下方"）。
             (None, None, ['pearnly-tasks'])],
  # 「选择框里的『新建』去哪儿」（用户 2026-09-23："每个输入框加新建入口，和官方原生一样，
  # 点新建跳转到我们已有的对应位置，不要把用户带到官方原生的地方去了"）。
  # 键是**被选的那个主档 doctype**，值是**我们自己的那一页**：官方 Link 控件默认把
  # 「新建 X」送到官方快速录入、「高级搜索」送到官方选择器 —— 都会把用户带到我们没有的
  # 功能上。没登记的 doctype 不由这里管（我们的表单也不给它入口），所以用户不会掉进官方表单。
  # 前端据此把这两项接到我们的页面（`pearnly_core/public/desk/link.js`）。
  'links': {'ERP Warehouse': 'pearnly-warehouses',
            'ERP Product': 'pearnly-products',
            # 物料分类没有自己的页：选择框里新建 / 改名原地开小弹窗（`products.js` 的 link_editors），高级搜索落在物料页
            'ERP Product Category': 'pearnly-products',
            'ERP Supplier': 'pearnly-suppliers',
            'ERP Customer': 'pearnly-clients',
            'ERP Company': 'pearnly-company'},
  # 左下角那颗用户按钮里的项（用户 2026-09-23："我点左下角，左侧出现 1.个人信息
  # 2.团队管理"）。写的是**路由**，词与图标仍从上面的 pages 取，不在这儿再写一份；
  # 服务端按与侧栏同一套判据筛（页面已实现 + 当前用户能进），所以只有产品管理员
  # 的菜单里会出现「团队管理」。
  # 公司带 `before_profile`：用户 2026-09-23 口径"公司……做在个人信息上方"，
  # 所以它排在「个人信息」上面，团队管理仍排在下面。
  # 仓库原来也在这里，岳父 11-08 移到侧栏「物料与库存」组（移，不复制）。
  'user_menu': [{'route': 'pearnly-company', 'before_profile': True},
                'pearnly-team'],
  # 页头入口声明（回归方案 A 节，10-26）：每页页头允许出现的入口（语义码）。我们自己的页面由 scripts/check-entries.py 核对代码里
  # 加的每个按钮都在这里；框架原生视图（Form/User、报表）运行时只放行这里列的（core desk/actions.js）；"*" 是各页共用的。
  'actions': {
    "*": ["selection"],
    "pearnly-cashier": ["trade.closeShift", "trade.openShift", "trade.newInvoice", "trade.recentOrders"],
    "pearnly-clients": ["client.new"],
    "pearnly-company": ["company.new", "company.connect.add", "Cancel", "company.save", "company.syncExpress"],
    "pearnly-home": ["home.ledger.export", "home.topup", "home.plans.title", "Delete", "home.billing"],
    "pearnly-inventory": ["stock.action.export", "stock.opening.title", "preview", "Cancel", "submit", "Edit"],
    "pearnly-products": ["product.action.new", "product.action.import", "product.action.submit"],
    "pearnly-purchase": ["Submit", "Express", "Payment", "Amend", "Cancel", "substitute_receipt", "wht_cert", "Print", "New", "Delete", "Export"],
    "pearnly-sales": ["Submit", "Express", "Collection", "Cancel", "Print", "New", "Delete", "Export"],
    "pearnly-collections": ["Export", "New"],
    "pearnly-stock-count": ["stock.count.new", "stock.count.start", "stock.count.close", "stock.count.adjust", "nav.stockAdjust",
                            "stock.action.export", "Express", "Print"],
    "pearnly-stock-in": ["trade.newIn", "trade.newOut", "trade.newTransfer", "trade.newAdjust", "trade.action.submit"],
    "pearnly-stock-out": ["trade.newIn", "trade.newOut", "trade.newTransfer", "trade.newAdjust", "trade.action.submit"],
    "pearnly-stock-transfer": ["trade.newIn", "trade.newOut", "trade.newTransfer", "trade.newAdjust", "trade.action.submit"],
    "pearnly-stock-adjust": ["trade.newIn", "trade.newOut", "trade.newTransfer", "trade.newAdjust", "trade.action.submit"],
    "pearnly-stock-card": ["trade.export.action"],
    "pearnly-suppliers": ["supplier.new"],
    "pearnly-tasks": ["erp.work.notify.title", "erp.work.board.create", "erp.work.board.settings", "erp.work.labels.manage", "erp.work.fields.manage", "erp.work.rules.manage", "erp.work.board.members", "erp.work.archived.title"],
    "pearnly-team": ["team.new", "Save", "Cancel"],
    "pearnly-warehouses": ["company.newWarehouse"],
    "Form/User": ["Save"],
    # 盘点差异报表页头不放原生「导出」（主会话 11-08）：租户账号本来就没有它的导出权限、看不到；导出走盘点单页自己的「导出」
    # （账面 / 实盘 / 差异 / 差异金额 / 仓库 / 仓位都在）。
  },
  # 允许的全局快捷键：Ctrl+S 保存 / 主按钮。其余原生全局快捷键（搜索栏、全局搜索、快捷键表、清缓存重载、控制台、换主题……）不注册；
  # 挂在我们自己页面上的（收银台）照常。
  'shortcuts': ['ctrl+s'],
  # 除我们自己的页面（pearnly-*）与本人个人页外允许打开的框架视图（标准路由前两段）；其余框架路由外壳换成首页。
  # 盘点差异看板是原生报表页（从盘点详情进）。
  'routes': ['query-report/ERP Stock Count Variance']}]

# Product-owned leaf roles; lookup reads never decide which management menu is visible.
# 放行的角色与接口判据同一口径（`menu.page_roles`，主会话 11-09）：管理员没有岗位时不再看到点进去会被拒的业务页。
from pearnly_erp_portal.access import menu as _menu_permissions
for _key, _spec in _menu_permissions.CATALOG.items():
    pearnly_navigation[0]["permissions"][_menu_permissions.route(_key)] = {
        "any_role": _menu_permissions.page_roles(_key)}
# 盘点生成的物料调整单盘点员也能打开、提交（主会话 11-06 审定 #2）：有「盘点」菜单的人进得了物料调整页（列表只出他能看的单）
pearnly_navigation[0]["permissions"][_menu_permissions.route("stock-adjust")]["any_role"].append(_menu_permissions.role("stock-count"))
# Existing coarse-role employees retain their previously available home and profile.
for _key in ("home", "profile"):
    pearnly_navigation[0]["permissions"][_menu_permissions.route(_key)]["any_role"] += [
        "ERP Products", "ERP Stock", "ERP Purchase", "ERP Sales", "ERP Cashier"]
# LINE 只在本人的「个人信息」里绑（用户 2026-09-25），所以勾了 LINE 的人一定进得去那一页。
from pearnly_erp_portal.access import rules as _rules
pearnly_navigation[0]["permissions"][_menu_permissions.route("profile")]["any_role"].append(_rules.LINE_ROLE)
# 收款页（11-11 / 11-14）：与接口同一判据——「收款」岗位与 ERP 管理员（用户 11-14：只有一个收款权限）
pearnly_navigation[0]["permissions"]["pearnly-collections"] = {"any_role": [_rules.COLLECTOR_ROLE, _rules.ADMIN_ROLE]}
# 旧地址「我的收款」（11-14 去掉）：已发出的 LINE 确认卡【编辑】还指向 /desk/pearnly-my-collections/<记录>，留一个不进菜单的页
# 转到「收款」页的那一笔（`page/pearnly_my_collections`），同一判据
pearnly_navigation[0]["permissions"]["pearnly-my-collections"] = {"any_role": [_rules.COLLECTOR_ROLE, _rules.ADMIN_ROLE]}
# 任务管理的默认看板（团队可见、默认三列）在装好 / 升级后建好。
after_install = ["pearnly_erp_portal.access.setup.sync_roles", "pearnly_erp_portal.team.login_settings.apply",
                 "pearnly_erp_portal.work.service.ensure_on_migrate"]
after_migrate = ["pearnly_erp_portal.access.setup.sync_roles", "pearnly_erp_portal.team.login_settings.apply",
                 "pearnly_erp_portal.work.service.ensure_on_migrate"]

has_permission.update({
    "ERP Stock Entry": "pearnly_erp_portal.inventory.access.entry_permission",
    "ERP Company": "pearnly_erp_portal.masters.access.company_permission",
    "ERP Warehouse": "pearnly_erp_portal.masters.access.warehouse_permission",
})
permission_query_conditions["ERP Stock Entry"] = "pearnly_erp_portal.inventory.access.entry_query"
for _doctype in ("ERP Stock Balance", "ERP Stock Movement", "ERP Stock Opening", "ERP Stock Count",
                 "ERP Stock Count Item", "ERP Stock Count Entry", "ERP Serial No", "ERP Serial Movement"):
    has_permission[_doctype] = "pearnly_erp_portal.inventory.access.record_permission"
    permission_query_conditions[_doctype] = "pearnly_erp_portal.inventory.access.record_query"
for _doctype in ("ERP Sale", "ERP Shift"):
    has_permission[_doctype] = "pearnly_erp_portal.cashier.access.record_permission"
    permission_query_conditions[_doctype] = "pearnly_erp_portal.cashier.access.record_query"
has_permission["ERP POS Terminal"] = "pearnly_erp_portal.cashier.access.terminal_permission"
# 收款记录：原生只读（收款员看自己的、能看收款的看本公司的；写只走 `collection.service`），凭证私有文件跟它判
has_permission["ERP Collection"] = "pearnly_erp_portal.collection.access.record_permission"
permission_query_conditions["ERP Collection"] = "pearnly_erp_portal.collection.access.record_query"
# 主档按公司（`masters.access.master_query`）：从 Express 同步来的档只在那家公司看得到
for _doctype, _query in (("ERP Customer", "customer_query"), ("ERP Supplier", "supplier_query"),
                         ("ERP Product", "product_query")):
    has_permission[_doctype] = "pearnly_erp_portal.masters.access.master_permission"
    permission_query_conditions[_doctype] = f"pearnly_erp_portal.masters.access.{_query}"
permission_query_conditions.update({
    "ERP Company": "pearnly_erp_portal.masters.access.company_list_query",
    "ERP Warehouse": "pearnly_erp_portal.masters.access.warehouse_list_query",
    "ERP POS Terminal": "pearnly_erp_portal.cashier.access.terminal_query",
})
doc_events = {"DocShare": {"validate": ["pearnly_erp_portal.access.sharing.validate_share",
                                        "pearnly_erp_portal.work.access.reject_share"]}}
for _doctype in ("ERP Warehouse", "ERP POS Terminal"):
    doc_events[_doctype] = {"before_validate": "pearnly_erp_portal.masters.access.validate_company_unchanged"}
# 库存单附件：已提交 / 作废的单附件删不掉、挪不走；往库存单上挂文件只走产品接口（`inventory.attachments`）
doc_events["File"] = {_event: "pearnly_erp_portal.inventory.attachments.guard_file" for _event in ("validate", "on_trash")}
doc_events["ERP Stock Entry"] = {
    _event: "pearnly_erp_portal.inventory.access.validate_entry_write"
    for _event in ("before_insert", "before_validate", "on_change", "on_trash", "before_rename", "after_rename")
}
doc_events["ERP Stock Opening"] = {
    _event: "pearnly_erp_portal.inventory.access.validate_opening_write"
    for _event in ("before_insert", "before_validate", "on_change", "on_trash", "before_rename", "after_rename")
}
# 打印：拒打在原生打印渲染前判（`printing.context.before_print`），打印页 / PDF 回 403 而不是模板出错的 417
for _doctype in ("ERP Stock Entry", "Purchase Document", "ERP Sales Document", "ERP Sale", "ERP Shift", "ERP Stock Count"):
    doc_events.setdefault(_doctype, {})["before_print"] = "pearnly_erp_portal.printing.context.before_print"

# 任务管理（看板）：原生资源只读、只读得到能看的看板；写一律走 `work` 的专用接口（`work.documents` 写入闸）。
# 卡片附件是原生 File：只给能看那块看板的人（别的文件不受影响）。
for _doctype in ("ERP Work Board", "ERP Work Card", "ERP Work Label", "ERP Work Activity", "ERP Work Column",
                 "ERP Work Field", "ERP Work Mirror", "ERP Work Rule"):
    has_permission[_doctype] = "pearnly_erp_portal.work.access.has_permission"
has_permission["ERP Work Notification"] = "pearnly_erp_portal.work.access.notification_permission"
has_permission["File"] = "pearnly_erp_portal.work.access.file_permission"
permission_query_conditions.update({
    "ERP Work Board": "pearnly_erp_portal.work.access.board_query",
    "ERP Work Card": "pearnly_erp_portal.work.access.card_query",
    "ERP Work Label": "pearnly_erp_portal.work.access.label_query",
    "ERP Work Activity": "pearnly_erp_portal.work.access.activity_query",
    "ERP Work Notification": "pearnly_erp_portal.work.access.notification_query",
    "ERP Work Column": "pearnly_erp_portal.work.access.column_query",
    "ERP Work Field": "pearnly_erp_portal.work.access.field_query",
    "ERP Work Mirror": "pearnly_erp_portal.work.access.mirror_query",
    "ERP Work Rule": "pearnly_erp_portal.work.access.rule_query",
    "File": "pearnly_erp_portal.work.access.file_query",
})

# 代录 Express（共用层 pearnly_core.express_operator，评估 docs/work/2026-10-26-express-operator-product-评估.md）：CC 里「代录」
# 列这家公司还没交进写账队列的采购 / 销售单，交单走 ERP 推送同一条路、载荷加代录标记（载荷 16）。
pearnly_express_operator = {
    "allowed": "pearnly_erp_portal.express.operator.allowed",
    "sources": "pearnly_erp_portal.express.operator.sources",
    # 这个月以外还等着录的单按月计数（代录计划这个月为空时 CC 说清、问要不要改月份，A 线 11-15）
    "elsewhere": "pearnly_erp_portal.express.operator.elsewhere",
    "precheck": "pearnly_erp_portal.express.operator.precheck",
    # 放文件（A 批 1，ADR-027 代录完整）：交 ERP 对话收单识别、识别状态现读；目标只有 Express
    "intake": "pearnly_erp_portal.express.operator.intake",
    "uploads": "pearnly_erp_portal.express.operator.uploads",
    "target": "pearnly_erp_portal.express.operator.target",
    "submit": "pearnly_erp_portal.express.operator.submit",
    "status": "pearnly_erp_portal.express.operator.status",
    "pending": "pearnly_erp_portal.express.operator.pending_documents",
    # 没录成的原因给模型的那句话（express_operator_status，测试机1 11-12：模型把原因码原样说给用户）
    "reason_text": "pearnly_erp_portal.express.cc_tools.reason_words",
    # 任务排着等电脑时在等的本人电脑名称（页面「等待电脑 · 名称」，A 线 11-12）
    "waiting_on": "pearnly_erp_portal.express.operator.waiting_on",
    # 本人电脑此刻能不能写（没电脑 / 够不到 / 只有同事在线 / 关着 → 已有原因码，经 reason_text 出话；11-16 H 组）
    "computer_state": "pearnly_erp_portal.express.operator.computer_state",
}
# 写账前置判定（共用层 pearnly_core.write_precheck，开始前确认卡片，评估 docs/work/2026-11-04-precheck-card.md W3）：一批单写进
# Express 前要人定的项一次收齐（账套、选择项、存货科目组、收银三项、库存单科目），记下走单据页「选一次」同一套核对
pearnly_write_precheck = {
    "allowed": "pearnly_erp_portal.express.precheck.allowed",
    "check": "pearnly_erp_portal.express.precheck.check",
    "remember": "pearnly_erp_portal.express.precheck.remember",
    "search": "pearnly_erp_portal.express.precheck.search",
    "resume": "pearnly_erp_portal.express.precheck.resume",
}
# 实操任务页底部的对话（对话框甲）：查进度、交代跳过 / 重做的工具在共用层，ERP 列上即用；进行中的说法同一个模块
# ERP 版 CC 按公司组织对话（ADR-020，2026-11-02）：公司工具（列出、读资料、经「允许」记 / 删知识库）与说话规矩
# 对话里直写（评估 2026-11-02 §13 第 9 步）：按公司列单、要选的项、记选择、交直写、推送结果、结果不明的核对（`express.cc_tools`）
# 盘点（盘点 2026-11-08 P1-1）：列、看差异、新建、记数、结束、按结果生成调整单草稿（`inventory.cc_tools`）
# 收款（收款第 4 步 11-11）：找客户、未收清发票、方式、草稿与凭证、按写入计划放行、结果、撤回（`collection.cc_tools`，只在直写对话：代录不做收款，用户 11-11）
pearnly_assistant_tools = ["pearnly_core.express_operator.tools.TOOLS", "pearnly_erp_portal.masters.cc_tools.TOOLS",
                           "pearnly_erp_portal.express.cc_tools.TOOLS", "pearnly_erp_portal.inventory.cc_tools.TOOLS",
                           "pearnly_erp_portal.collection.cc_tools.TOOLS"]
pearnly_assistant_guidance = ["pearnly_erp_portal.masters.cc_tools.GUIDANCE", "pearnly_erp_portal.express.cc_tools.GUIDANCE"]
# CC 工具执行期间「当前公司」是对话挂的那家（`masters.company.acting`，盘点 2026-11-08 ①）：库存、物料、盘点等按当前公司取数的服务都跟着走
pearnly_assistant_tool_context = ["pearnly_erp_portal.masters.cc_tools.acting_filed"]
pearnly_assistant_step_names = ["pearnly_core.express_operator.tools.STEP_NAMES",
                                "pearnly_erp_portal.express.cc_tools.STEP_NAMES", "pearnly_erp_portal.inventory.cc_tools.STEP_NAMES",
                                "pearnly_erp_portal.collection.cc_tools.STEP_NAMES"]
# 对话按 ERP 公司归组：provider 交给共用层（`pearnly_core.assistant.projects`），公司接口前缀与分区也在它上面声明
pearnly_assistant_projects = ["pearnly_erp_portal.masters.cc_projects"]
# 公司建、改名、停用、启用、删：推给能看这家的人，CC 侧栏即时更新（`masters.cc_service`）
doc_events["ERP Company"] = {"on_update": "pearnly_erp_portal.masters.cc_service.company_changed",
                             "on_trash": "pearnly_erp_portal.masters.cc_service.company_removed"}

# 录过的票先核账本（共用入口 `pearnly_core.ledger_tickets.check_present`，A 线 11-11）：Express 经本产品自己的 CC 桌面版请求通道；
# MR.ERP 一侧按公司的 MR.ERP 连接直读 MR.ERP 列表（D 线 11-11，`mrerp.ledger_check`；MR.ERP 账本开关关着时回空）
pearnly_ledger_check = {"express": "pearnly_erp_portal.express.ledger_check.check",
                        "mrerp": "pearnly_erp_portal.mrerp.ledger_check.check"}

# 引导声明（总设计 docs/work/2026-11-14-cc-guided-master-design.md 2.2）：每项业务一份声明，共用引擎 `pearnly_core.assistant.guide` 据此出菜单与卡
pearnly_guides = ["pearnly_erp_portal.express.cc_guides.GUIDES", "pearnly_erp_portal.collection.cc_guide.GUIDES"]
