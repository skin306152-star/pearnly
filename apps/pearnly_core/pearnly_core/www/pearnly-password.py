# 改密页要求登录。未登录直接跳登录页，不暴露表单。
import frappe

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/pearnly-password"
        raise frappe.Redirect

    # CSRF 令牌必须在这里显式取。模板里直接写 frappe.session.csrf_token 会渲染成
    # 字符串 "None"（该字段在会话数据里可能还没生成），前端拿它去请求必然失败。
    from frappe.sessions import get_csrf_token

    context.csrf_token = get_csrf_token()
