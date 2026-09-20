"""Keep cloud account passwords at the identity provider, never the local User store."""
import frappe
from frappe import _
import re

from pearnly_core.account import oidc_session

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")


def product_identity():
    return frappe.conf.get("pearnly_product") and frappe.conf.get("pearnly_native_account")


def reject_local_password(doc, method=None):
    if (frappe.conf.get("pearnly_product") and frappe.conf.get("pearnly_native_account")
            and doc.get("new_password")):
        frappe.throw(_("account.identityPasswordOnly"))


def prepare_profile(doc, method=None):
    reject_local_password(doc, method)
    if not product_identity() or doc.is_new() or doc.name != frappe.session.user:
        return
    if doc.email != doc.name:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if not getattr(frappe.flags, "pearnly_profile_synced", False) and doc.has_value_changed("first_name"):
        doc.first_name = (doc.first_name or "").strip()
        if not USERNAME_RE.fullmatch(doc.first_name):
            frappe.throw(_("account.usernameFormat"))
        # Fail before updating the identity service if a legacy local username collides.
        if frappe.db.exists("User", {"username": doc.first_name, "name": ["!=", doc.name]}):
            frappe.throw(_("account.usernameTaken"))
        doc.username = doc.first_name
        doc.middle_name = doc.last_name = ""


def sync_username(doc, method=None):
    if (not product_identity() or doc.is_new() or doc.name != frappe.session.user
            or getattr(frappe.flags, "pearnly_profile_synced", False)
            or not doc.has_value_changed("first_name")):
        return
    from pearnly_core.account.preferences import LANGUAGES
    previous = doc.get_doc_before_save()
    locale = LANGUAGES.get(doc.language) if doc.has_value_changed("language") else None
    if not oidc_session.update_profile(username=doc.first_name,
            expected_username=previous.first_name, email=doc.name, locale=locale):
        frappe.throw(_("account.signInToRename"), frappe.AuthenticationError)
    # The language hook follows this hook. Both changes used one native account update.
    doc.flags.pearnly_profile_locale_synced = bool(locale)
    if locale:
        frappe.defaults.set_user_default("pearnly_pending_locale", None)
        frappe.local.cookie_manager.set_cookie("pearnly_locale", locale)


def apply_login_username(user, claims):
    """Each product projects the same verified identity without renaming its email key."""
    username = str(claims.get("preferred_username") or "").strip()
    if not username or not product_identity():
        return
    doc = frappe.get_doc("User", user)
    if doc.first_name == username and not doc.middle_name and not doc.last_name:
        return
    previous = getattr(frappe.flags, "pearnly_profile_synced", False)
    frappe.flags.pearnly_profile_synced = True
    try:
        doc.first_name = username
        doc.middle_name = doc.last_name = ""
        # Email-style legacy usernames remain valid at Keycloak. Do not let Frappe
        # strip their @ or invent a replacement local login identifier.
        if USERNAME_RE.fullmatch(username):
            doc.username = username
        doc.save(ignore_permissions=True)
    finally:
        frappe.flags.pearnly_profile_synced = previous


def load_details(doc, method=None):
    if not product_identity() or doc.name != frappe.session.user:
        return
    # Native User permits self-reading without granting the User DocType role.
    # Its virtual fields are filtered by field permissions; pass read-only site
    # context through the native onload channel instead of widening User access.
    doc.set_onload("pearnly_account", {
        "company": frappe.conf.get("pearnly_company_name") or frappe.conf.get("pearnly_workspace") or "",
        "domain": frappe.conf.get("host_name") or "",
    })
