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
# 退出登录：这次登录认领的手机 / 浏览器推送订阅一并删掉（不经这里结束的会话，发送前核对，见 assistant/push.py）。
on_logout = "pearnly_core.assistant.push.forget_session"

# 统一登录入口：业务站点的 `/`、`/login`、`/app`、`/desk` 对未登录访客一律
# 送去身份服务 —— 站点上没有业务账号的密码，用 Frappe 自带登录页必然失败。
before_request = [
    # CC 桌面版共用机器通道（ADR-025）：带 ``pcc_`` 令牌的精确机器请求，在 Frappe 的 OAuth 校验之前收走令牌（不是机器请求原样放过）
    "pearnly_core.cc_desktop.http_auth.before_request",
    # LINE 的 LIFF 深链先落站点根 `/?liff.state=<站内路径>`：跳到那个站内地址（放在登录拦截之前，路径才不会丢）
    "pearnly_core.login_redirect.follow_liff_state",
    # 未登录访客：站点的门一律送去身份服务（站点上没有业务账号的密码）
    "pearnly_core.login_redirect.redirect_guests_to_identity",
    # 已登录用户打开根路径：直接去自己的工作台，不要先过一屏"桌面"
    "pearnly_core.login_redirect.send_root_to_home",
    # 对话页（CC）的请求带着页面当前的界面语言：这次请求的文案按它，不按账号里存的另一种
    "pearnly_core.account.preferences.use_page_language",
    # 对话页（CC）的接口出错时不回 Python 堆栈（系统设置「显示完整错误」默认开时，系统用户的拒收也带整段堆栈）
    "pearnly_core.assistant.api.no_traceback",
    # 原件层的登记文件（ADR-024）：打开它的私有文件地址时，核对能看后转到存放处的签名下载地址
    "pearnly_core.originals.registered.redirect_download",
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
# 独立网页（如 ERP 的 LINE 盘点页）引用共用脚本时带内容版本号：`{{ versioned_asset("pearnly_core", "scan/reader.js") }}`。
jinja = {"methods": ["pearnly_core.desk.assets.versioned_asset"]}
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
        "on_update": "pearnly_core.desk.permissions.sync_profile_share",
    },
    # iPhone 照片（HEIC）上传时转成 JPEG 再存，三个产品的原生上传都经过这里（`desk/heic.py`）。
    "File": {
        "before_insert": "pearnly_core.desk.heic.convert_file",
    },
    # CC 桌面版共用机器通道的四张表不许分享（ADR-025，`cc_desktop.documents`）
    "DocShare": {
        "before_validate": "pearnly_core.cc_desktop.documents.reject_share",
    },
}
# CC 桌面版共用机器通道的机器认证（ADR-025）：冒充 cmd=login 先拒；按令牌认电脑、切到签发人身份。
before_login = ["pearnly_core.cc_desktop.http_auth.before_login"]
auth_hooks = ["pearnly_core.cc_desktop.http_auth.authenticate_request"]
# 原件层的登记文件（ADR-024，``originals.registered``）：删掉最后一条时连存放处那份一起删。
extend_doctype_class = {"File": ["pearnly_core.originals.registered.RegisteredFile"]}

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
# 不 fork 的识别进程按内存高水位温和重启（见 `execution/recycle.py`）。
after_job = ["pearnly_core.execution.recycle.after_job"]

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
boot_session = [
    "pearnly_core.desk.navigation.extend_boot",
    # 站点货币：符号从服务端给（前端自己去读 `Currency` 表会撞权限弹窗，见该文件抬头）。
    "pearnly_core.desk.currency.boot_session",
    # 上传前缩照片的参数（所有租户都开，`pearnly_core/desk/upload.py`）。
    "pearnly_core.desk.upload.boot_session",
    # 本站点 LIFF ID：盘点扫码在 LINE 普通内置浏览器里转 LIFF 打开（那里 LINE 会弹原生相机授权）。
    "pearnly_core.desk.line_boot.boot_session",
    # 整份翻译表不进按用户缓存的启动信息（每人约 1MB，挤满 redis-cache），返回前按语言放回（`extend_bootinfo` 同名模块）。
    "pearnly_core.desk.boot_messages.boot_session",
]
# 页面文案的版本指纹：每次启动都给（不进按用户缓存的启动信息），前端据此把文案包存在浏览器里。
extend_bootinfo = ["pearnly_core.browser_text.extend_bootinfo", "pearnly_core.desk.boot_messages.extend_bootinfo"]
app_include_css = [versioned_asset("pearnly_core", "desk/product.css"), versioned_asset("pearnly_core", "desk/scan.css")]
# 顺序即依赖：`errors.js` 最先（页面报错回传，后面的组件出错也抓得到）；`product.js` 先立起 `frappe.pearnly`，后面的共用组件都挂在它上面。
app_include_js = [versioned_asset("pearnly_core", "desk/errors.js"), versioned_asset("pearnly_core", "desk/product.js"), versioned_asset("pearnly_core", "desk/select.js"), versioned_asset("pearnly_core", "desk/link.js"), versioned_asset("pearnly_core", "desk/list.js"), versioned_asset("pearnly_core", "desk/viewer.js"), versioned_asset("pearnly_core", "desk/home.js"), versioned_asset("pearnly_core", "desk/sidebar.js"), versioned_asset("pearnly_core", "scan/reader.js"), versioned_asset("pearnly_core", "desk/scan.js")]

# 页面里的 `<audio id="sound-…">` 是 Desk 模板按**各 App 的 `sounds` 钩子**生成的
# （`frappe/www/desk.html` 遍历 `hooks["sounds"]`），`frappe.utils.play_sound(name)` 取的就是
# 这些节点里同名的那一个。frappe 自带七个（submit / error / cancel / delete / click / alert / email），
# 官方 POS 收款小键盘那一声 `numpad-touch` 在 **erpnext** 的钩子里 —— 而租户站点不装 erpnext，
# 于是 `play_sound("numpad-touch")` 取不到节点、异常被它自己的 try/catch 吞掉，**看着像没音效**
# （2026-09-23 在实验室站点实测：页面里只有 frappe 那七个）。
# 这里按官方同样的音量补上那一条，**文件用我们自己 App 里的一份**（`public/sounds/`，
# 与上游同一份音频）—— 之前指向 `/assets/erpnext/...`，那要求 bench/镜像里 clone 过 erpnext
# （资产是 bench 级软链，跟站点装不装它无关）：生产镜像底座正好是官方 `frappe/erpnext`，
# 所以当时也能用，但那是一条我们**不拥有**的路径，哪天换成纯 frappe 的基础镜像就会静默没声。
# 用自己的文件之后这条依赖彻底消失（用户 2026-09-23："把那 1.6KB 的 mp3 放进我们自己 App"）。
# 装了 erpnext 的站点会多一个同名节点（框架不去重，取到的仍是第一个、也就是我们这条），行为不变。
sounds = [
    {"name": "numpad-touch", "src": "/assets/pearnly_core/sounds/numpad-touch.mp3", "volume": 0.8},
]
after_migrate = [
    "pearnly_core.desk.navigation.ensure_navigation",
    "pearnly_core.desk.branding.ensure_site_favicon",
    "pearnly_core.desk.branding.ensure_site_app_name",
    "pearnly_core.account.layout.ensure_profile_fields",
    "pearnly_core.account.session_length.ensure_session_expiry",
    "pearnly_core.desk.error_detail.ensure_traceback_hidden",
]

# Keep the native user form; do not add a second account settings page.
doctype_js = {"User": "public/desk/user.js"}

# Product account preferences never expose the framework user directory.
# 导出任务只给本人看（见 ``pearnly_core.exports``）。
has_permission = {"User": "pearnly_core.desk.permissions.user_permission",
                  "Pearnly Export": "pearnly_core.exports.service.has_permission",
                  # CC 桌面版共用机器通道的四张表只经本域写，原生读写一律拒绝（ADR-025）
                  **{doctype: "pearnly_core.cc_desktop.documents.native_permission"
                     for doctype in ("Pearnly CC Device", "Pearnly CC Book", "Pearnly CC Job", "Pearnly CC Request")}}
permission_query_conditions = {"User": "pearnly_core.desk.permissions.user_query",
                               "Pearnly Export": "pearnly_core.exports.service.query_conditions",
                               **{doctype: "pearnly_core.cc_desktop.documents.native_query"
                                  for doctype in ("Pearnly CC Device", "Pearnly CC Book", "Pearnly CC Job",
                                                  "Pearnly CC Request")}}

# Native clear-cache must not discard still-active identity sessions/state.
persistent_cache_keys = ["pearnly_oidc_session:*", "pearnly_oidc_state:*"]

# Durable OCR backlog repair; second-level ETA delivery uses execution.dispatch.
scheduler_events = {"cron": {"* * * * *": ["pearnly_core.execution.work.sweep"],
                             # 导出文件 1 小时过期：每 10 分钟删一次过期的。
                             "*/10 * * * *": ["pearnly_core.exports.service.sweep"]},
                    "hourly": ["pearnly_core.account.line_login.reconcile"],
                    "daily": ["pearnly_core.execution.work.prune_checkpoints"]}

# 导出文件的下载响应不进缓存（与 Cowork 原件下载同一做法）；所有原生下载的中文 / 泰文文件名按 RFC 5987 写进响应头。
after_request = ["pearnly_core.exports.service.protect_response", "pearnly_core.exports.service.encode_filename",
                 "pearnly_core.cc_desktop.http_auth.after_request"]

# LINE 绑定即登录：绑定变化 → 作废失效的 LINE 会话、同步身份服务里的关联（多产品共用）。
pearnly_line_binding_changed = ["pearnly_core.account.line_login.binding_changed"]
