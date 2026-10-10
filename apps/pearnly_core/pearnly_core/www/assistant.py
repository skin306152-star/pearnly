"""智能体对话页 `/assistant`（ADR-019）：三个产品的租户站点共用。

访客由 `login_redirect` 送去 CC 入口（登录后经本站统一登录口回到本页）；这里的跳转只是兜底，规则同一处。
站点只属于一个产品：开户给的是哪个产品，登录后就只看到这个产品的形象与工具。本机管理后台没有产品，不提供此页。
"""
import frappe

from pearnly_core import login_redirect
from pearnly_core.identity import assistant_entry_url

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        location, mark = login_redirect.assistant_gate(frappe.request.cookies, assistant_entry_url())
        if mark:
            login_redirect.set_entry_mark(frappe.local.cookie_manager.set_cookie)
        frappe.local.flags.redirect_location = location
        raise frappe.Redirect
    if not str(frappe.conf.get("pearnly_product") or "").strip():
        raise frappe.PermissionError
    context.no_cache = 1
    context.csrf_token = frappe.sessions.get_csrf_token()
    context.lang = frappe.local.lang or "th"
