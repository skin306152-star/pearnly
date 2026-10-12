"""One account language, persisted through native Keycloak and Frappe profiles."""
import frappe
from frappe import _
from pearnly_core.account import oidc_session

LANGUAGES = {"th": "th", "zh": "zh-CN", "en": "en", "ja": "ja"}


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
    # Keycloak locale cookie is on another host; pass the same locale explicitly
    # on the next authorization request instead of trusting that stale cookie.
    frappe.local.cookie_manager.set_cookie("pearnly_locale", LANGUAGES[language])
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
    frappe.local.cookie_manager.set_cookie("pearnly_locale", LANGUAGES[doc.language])


def _sync_or_defer(language):
    # Frappe sessions can outlive an OIDC session. Language is a profile preference,
    # so remember the choice and synchronize it on the next verified SSO login.
    pending = None if oidc_session.update_locale(LANGUAGES[language]) else language
    frappe.defaults.set_user_default("pearnly_pending_locale", pending)


def finish_login_locale(user, claim_locale, chosen=None):
    # chosen：这次登录前在入口页上主动点的语言（2026-10-12 用户：登录页选了英文，进去却是账号里存的中文），
    # 当作这个账号的新语言，与产品里改语言同一条路写回身份服务；其次是之前没同步成功的选择，最后才是账号里存的。
    pending = chosen if chosen in LANGUAGES else frappe.defaults.get_user_default("pearnly_pending_locale", user=user)
    if pending in LANGUAGES:
        _sync_or_defer(pending)
        locale = LANGUAGES[pending]
    else:
        locale = claim_locale
    apply_login_locale(user, locale)
    if locale in LANGUAGES.values():
        frappe.local.cookie_manager.set_cookie("pearnly_locale", locale)
