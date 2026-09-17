# 发号页：控制面用，要求登录（平台 realm）。
import frappe

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        # 走**统一身份**入口，不要用 Frappe 自带的 /login：
        # 平台账号只存在于身份服务里，本站没有它的密码，用自带登录页必然报
        # "Invalid credentials"。
        frappe.local.flags.redirect_location = (
            "/api/method/pearnly_core.identity.begin_login?redirect_to=/pearnly-issue"
        )
        raise frappe.Redirect

    from frappe.sessions import get_csrf_token

    # CSRF 必须在这里显式取：模板里直接写 frappe.session.csrf_token 会渲染成
    # 字符串 "None"，前端拿它请求必然失败（踩过）。
    context.csrf_token = get_csrf_token()
    context.operator = frappe.session.user
