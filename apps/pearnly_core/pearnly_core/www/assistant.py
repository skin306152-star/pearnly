"""智能体对话页 `/assistant`（ADR-019）：三个产品的租户站点共用。

访客由 `login_redirect` 送去 CC 入口（登录后经本站统一登录口回到本页）；这里的跳转只是兜底，规则同一处。
站点只属于一个产品：开户给的是哪个产品，登录后就只看到这个产品的形象与工具。本机管理后台没有产品，不提供此页。
界面语言按这台设备记住的走，没有才用账号语言、系统语言（`pearnly_core.account.ui_language`）。
"""
import frappe

from pearnly_core import login_redirect
from pearnly_core.account import preferences
from pearnly_core.assistant import contracts
from pearnly_core.cc_desktop import cloud_entry
from pearnly_core.identity import assistant_entry_url
from pearnly_core.originals.rules import UPLOAD_MAX_BYTES

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        location, mark = login_redirect.assistant_gate(frappe.request.cookies, assistant_entry_url(), frappe.local.site)
        if mark:
            login_redirect.set_entry_mark(frappe.local.cookie_manager.set_cookie)
        frappe.local.flags.redirect_location = location
        raise frappe.Redirect
    if not str(frappe.conf.get("pearnly_product") or "").strip():
        raise frappe.PermissionError
    context.no_cache = 1
    context.csrf_token = frappe.sessions.get_csrf_token()
    # 一份附件最多多大：页面先比、超了当场说（与 Frappe 请求体上限同一个数，`originals.upload_limits`）
    context.upload_max_bytes = UPLOAD_MAX_BYTES
    # 收哪些类型：页面选中文件时先比，不收的当场拒收、不留失败卡（同服务端校验一个来源，`contracts.upload_name`）
    context.upload_extensions = sorted(contracts.UPLOAD_EXTENSIONS)
    # 云端 Express 的入口：正式部署里先不显示（端到端没走通，`cc_desktop.cloud_entry` 写明什么时候拿掉）
    context.cloud_express = cloud_entry.visible()
    context.lang = frappe.local.lang = preferences.interface_language()
