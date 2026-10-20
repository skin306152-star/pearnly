"""Server-side OIDC credentials, scoped to the current native Frappe session."""
import logging

import frappe
import requests
from frappe import _
from pearnly_core.execution import state as pearnly_state

logger = logging.getLogger(__name__)
SESSION_HINT = "pearnly_oidc_id_token"


def cache_key():
    return f"pearnly_oidc_session:{frappe.session.sid}"


def remember(tokens, subject):
    from frappe.sessions import get_expiry_in_seconds
    pearnly_state.store().set_value(cache_key(), {
        "id_token": tokens["id_token"], "refresh_token": tokens.get("refresh_token"),
        "subject": subject,
    }, expires_in_sec=get_expiry_in_seconds())
    # 会话自己再存一份注销凭据：Redis 那份丢了（清缓存、Redis 重建）时，点注销仍能带上 id_token_hint，
    # 不掉到身份服务自带的"您确定要注销吗"确认页（2026-09-25 实际遇到）。
    # 要放进 `frappe.session.data`：框架只把这一层写进 Sessions 表并在下次请求恢复；放在 `frappe.session` 顶层的
    # 键（10-20 前的写法）下一次请求就没了。登录这次请求里会话记录已经先插入，所以这里强制写回一次。
    try:
        _persist_hint(tokens["id_token"])
    except Exception:
        logger.warning("Could not persist the OIDC logout hint in the session", exc_info=True)


def _persist_hint(value):
    data = frappe.session.get("data")
    if data is None or data.get(SESSION_HINT) == value:
        return
    data[SESSION_HINT] = value
    session = getattr(frappe.local, "session_obj", None)
    if session is not None and frappe.session.get("user") not in (None, "Guest"):
        session.update(force=True)


def logout_hint():
    return ((frappe.session.get("data") or {}).get(SESSION_HINT)
            or (pearnly_state.store().get_value(cache_key()) or {}).get("id_token"))


def forget():
    pearnly_state.store().delete_value(cache_key())
    try:
        data = frappe.session.get("data")
        if data is not None:
            data.pop(SESSION_HINT, None)   # 随后就是登出、删会话，不必再写回
    except Exception:
        logger.warning("Could not clear the OIDC logout hint in the session", exc_info=True)


def update_locale(locale):
    """Use the user's own native account API; never tenant admin credentials."""
    return update_profile(locale=locale)


def update_profile(*, locale=None):
    """Update only the current verified subject's locale, preserving other attributes.

    用户名/邮箱由后台发号、绑死这个租户，**不从这里改**（用户 2026-09-23 口径）——
    这条通道只剩"语言偏好"一件事，别的字段一律按当前值原样写回。
    """
    key = cache_key()
    with pearnly_state.store().lock(key + ":refresh", timeout=90, blocking_timeout=5):
        saved = pearnly_state.store().get_value(key) or {}
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
        if locale:
            payload["attributes"]["locale"] = [locale]
        response = requests.post(endpoint, headers=headers, json=payload, timeout=15)
        if response.status_code not in (200, 204):
            frappe.throw(_("Could not update the account language."))
        check = requests.get(endpoint, headers=headers, timeout=15)
        current = check.json() if check.status_code == 200 else {}
        if locale and current.get("attributes", {}).get("locale") != [locale]:
            frappe.throw(_("Could not update the account language."))

        return True
