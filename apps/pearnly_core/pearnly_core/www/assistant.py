"""智能体对话页 `/assistant`（ADR-019）：三个产品的租户站点共用。

登录就是本站会话（后台开户发的账号，经 Keycloak 登录）；没登录先去登录页再回来。
本机管理后台没有产品，不提供此页。
"""
import frappe

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/assistant"
        raise frappe.Redirect
    if not str(frappe.conf.get("pearnly_product") or "").strip():
        raise frappe.PermissionError
    context.no_cache = 1
    context.csrf_token = frappe.sessions.get_csrf_token()
    context.lang = frappe.local.lang or "th"
