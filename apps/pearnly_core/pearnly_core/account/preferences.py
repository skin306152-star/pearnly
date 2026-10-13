"""One account language, persisted through native Keycloak and Frappe profiles.

对话页（CC）的界面语言另按这台设备记住的走（规则见 `ui_language`）：记在本站的 `pearnly_locale`
（身份服务的写法，下次登录也按它向身份服务要登录页语言），对话页的请求带着页面当前的语言（`use_page_language`）。
"""
import frappe
from frappe import _
from pearnly_core.account import oidc_session, ui_language

LANGUAGES = {"th": "th", "zh": "zh-CN", "en": "en", "ja": "ja"}
# 这台设备的界面语言（浏览器能留多久就留多久：Chrome 最长 400 天）；退出登录不清
DEVICE_COOKIE = "pearnly_locale"
DEVICE_DAYS = 400
# 对话页每个请求带着页面当前的界面语言（frontend/assistant/src/lib/frappe.ts）
PAGE_LANGUAGE_HEADER = "X-Pearnly-Lang"


def apply_login_locale(user, locale):
    language = next((k for k, v in LANGUAGES.items() if locale in (k, v)), None)
    if language:
        doc = frappe.get_doc("User", user)
        if doc.language != language:
            doc.language = language
            previous = getattr(frappe.flags, "pearnly_locale_synced", False)
            frappe.flags.pearnly_locale_synced = True
            try:
                doc.save(ignore_permissions=True)
            finally:
                frappe.flags.pearnly_locale_synced = previous


@frappe.whitelist(methods=["POST"])
def set_language(language):
    if frappe.session.user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    if language not in LANGUAGES:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if frappe.conf.get("pearnly_native_account"):
        _sync_or_defer(language)
    previous = getattr(frappe.flags, "pearnly_locale_synced", False)
    frappe.flags.pearnly_locale_synced = True
    try:
        apply_login_locale(frappe.session.user, language)
    finally:
        frappe.flags.pearnly_locale_synced = previous
    frappe.local.lang = language
    # 在这台设备上自己选的：记住（对话页按它）。Keycloak locale cookie is on another host; the same
    # cookie passes the locale explicitly on the next authorization request instead of that stale cookie.
    remember_device(language)
    return {"language": language}


def sync_profile_language(doc, method=None):
    """Native User form and the product language dialog share one identity preference."""
    if (not frappe.conf.get("pearnly_product") or not frappe.conf.get("pearnly_native_account")
            or frappe.session.user != doc.name or doc.is_new()
            or not doc.has_value_changed("language")
            or getattr(doc.flags, "pearnly_profile_locale_synced", False)
            or getattr(frappe.flags, "pearnly_locale_synced", False)):
        return
    if doc.language not in LANGUAGES:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    _sync_or_defer(doc.language)
    remember_device(doc.language)


def _sync_or_defer(language):
    # Frappe sessions can outlive an OIDC session. Language is a profile preference,
    # so remember the choice and synchronize it on the next verified SSO login.
    pending = None if oidc_session.update_locale(LANGUAGES[language]) else language
    frappe.defaults.set_user_default("pearnly_pending_locale", pending)


def finish_login_locale(user, claim_locale, shown=None, picked=False):
    """登录回调里定语言（规则见 `ui_language`）。shown：登录页这次显示的语言（CC 入口 pearnly_control.assistant_entry 带来），
    picked：那是用户在登录页上点选的。

    这次登录给这台设备定了新语言（登录页上点的，或这台设备第一次登录时登录页显示的）：记在这台设备上，也当作账号的新语言，
    与产品里改语言同一条路写回身份服务（2026-10-12 用户：登录页选了英文，进去却是账号里存的中文；2026-10-13：没点、
    只是按系统语言显示的泰文，新设备上进去同样要是泰文）。其次是之前没同步成功的选择，最后才是账号里存的。"""
    choice = sign_in_choice(shown, picked)
    pending = choice or frappe.defaults.get_user_default("pearnly_pending_locale", user=user)
    if pending in LANGUAGES:
        _sync_or_defer(pending)
        locale = LANGUAGES[pending]
    else:
        locale = claim_locale
    apply_login_locale(user, locale)
    device = choice or device_language()
    if device:
        remember_device(device)  # 新定的记下；已记住的续期


def device_language():
    """这台设备记住的界面语言（对话页有的那几种），没有给空串。"""
    request = frappe.request
    return ui_language.normalize(request.cookies.get(DEVICE_COOKIE)) if request else ""


def remember_device(language):
    frappe.local.cookie_manager.set_cookie(DEVICE_COOKIE, LANGUAGES[language], max_age=DEVICE_DAYS * 86400,
                                           httponly=True)


def sign_in_choice(shown, picked=False):
    """这次登录给这台设备定下的新语言（`ui_language.sign_in_choice`）；空串表示照旧。"""
    return ui_language.sign_in_choice(device_language(), shown, picked)


def interface_language(user=None):
    """打开对话页时的界面语言：这台设备记住的 → 账号语言 → 系统（浏览器）语言 → 英文。"""
    request = frappe.request
    account = frappe.get_cached_value("User", user or frappe.session.user, "language")
    return ui_language.interface(device_language(), account,
                                 request.headers.get("Accept-Language") if request else "")


def use_page_language():
    """before_request：对话页的请求带着页面当前的界面语言，这次请求的提示、工具步骤说明、术语、工具结果都按它，
    不按账号里存的另一种（只认对话页有的语言）。后台任务、提醒照旧按账号语言。"""
    request = frappe.request
    language = request.headers.get(PAGE_LANGUAGE_HEADER) if request else None
    if language in ui_language.UI_LANGUAGES:
        frappe.local.lang = language
