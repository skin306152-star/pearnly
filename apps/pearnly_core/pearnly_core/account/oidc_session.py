"""Server-side OIDC credentials, scoped to the current native Frappe session."""
import frappe
import requests
from frappe import _


def cache_key():
    return f"pearnly_oidc_session:{frappe.session.sid}"


def remember(tokens, subject):
    from frappe.sessions import get_expiry_in_seconds
    frappe.cache.set_value(cache_key(), {
        "id_token": tokens["id_token"], "refresh_token": tokens.get("refresh_token"),
        "subject": subject,
    }, expires_in_sec=get_expiry_in_seconds())


def logout_hint():
    return (frappe.cache.get_value(cache_key()) or {}).get("id_token")


def forget():
    frappe.cache.delete_value(cache_key())


def update_locale(locale):
    """Use the user's own native account API; never tenant admin credentials."""
    key = cache_key()
    with frappe.cache.lock(key + ":refresh", timeout=90, blocking_timeout=5):
        saved = frappe.cache.get_value(key) or {}
        if not saved.get("refresh_token"):
            return False
        issuer = str(frappe.conf.pearnly_oidc_issuer).rstrip("/")
        response = requests.post(issuer + "/protocol/openid-connect/token", data={
            "grant_type": "refresh_token", "refresh_token": saved["refresh_token"],
            "client_id": frappe.conf.pearnly_oidc_client_id,
            "client_secret": frappe.conf.pearnly_oidc_client_secret,
        }, timeout=15)
        if response.status_code in (400, 401):
            return False
        if response.status_code != 200:
            frappe.throw(_("Could not update the account language."))
        tokens = response.json()
        # Preserve rotation immediately, even if the later profile operation fails.
        remember({"id_token": saved["id_token"],
                  "refresh_token": tokens.get("refresh_token", saved["refresh_token"])}, saved["subject"])
        headers = {"Authorization": "Bearer " + tokens["access_token"], "Accept": "application/json"}
        endpoint = issuer + "/account/"
        response = requests.get(endpoint, headers=headers, timeout=15)
        if response.status_code != 200:
            frappe.throw(_("Could not update the account language."))
        profile = response.json()
        if profile.get("id") != saved["subject"]:
            frappe.throw(_("Not permitted"), frappe.PermissionError)
        payload = {k: profile[k] for k in ("username", "firstName", "lastName", "email", "attributes") if k in profile}
        payload["attributes"] = dict(payload.get("attributes") or {})
        payload["attributes"]["locale"] = [locale]
        response = requests.post(endpoint, headers=headers, json=payload, timeout=15)
        if response.status_code not in (200, 204):
            frappe.throw(_("Could not update the account language."))
        check = requests.get(endpoint, headers=headers, timeout=15)
        if check.status_code != 200 or check.json().get("attributes", {}).get("locale") != [locale]:
            frappe.throw(_("Could not update the account language."))

        return True
