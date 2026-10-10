"""智能体对话页 `/assistant`（ADR-019）：三个产品的租户站点共用。

登录走统一登录口（`login_redirect` 把访客送去身份服务，登录后回到本页）；这里的跳转只是兜底。
站点只属于一个产品：开户给的是哪个产品，登录后就只看到这个产品的形象与工具。本机管理后台没有产品，不提供此页。
"""
import frappe

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/api/method/pearnly_core.identity.begin_login?redirect_to=/assistant"
        raise frappe.Redirect
    if not str(frappe.conf.get("pearnly_product") or "").strip():
        raise frappe.PermissionError
    context.no_cache = 1
    context.csrf_token = frappe.sessions.get_csrf_token()
    context.lang = frappe.local.lang or "th"
