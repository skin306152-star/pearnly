# -*- coding: utf-8 -*-
"""统一身份接入：OIDC 授权码登录 + 按 (issuer, subject) 绑定本地账号。

与计划合同一致的三条硬规则：

1. 本地账号由 **(issuer, subject)** 唯一决定。**不按邮箱匹配**——邮箱可被修改、
   可被重复声明，按邮箱匹配会把不同人的账号合并（这是现有实现的缺陷）。
2. **绝不自动开户**。没有绑定记录一律拒绝，必须先被邀请，由控制面写入绑定。
3. 业务站点只接受配好的那一个 issuer。平台 realm 与业务 realm 的 issuer 不同，
   所以平台账号天然进不来，不需要额外拦截代码。

校验项：state（服务端一次性）、nonce、ID token 签名（JWKS）、issuer、audience、
过期时间、PKCE（S256）。缺任何一项都算失败。
"""

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import frappe
import jwt
import requests
from frappe import _

STATE_TTL = 600  # 秒
DISCOVERY_TTL = 3600


# ── 配置 ────────────────────────────────────────────────────────────────────


def _conf(key, required=True):
    value = frappe.conf.get(key)
    if required and not value:
        frappe.throw(_("Site config is missing {0}").format(key))
    return value


def issuer() -> str:
    return str(_conf("pearnly_oidc_issuer")).rstrip("/")


def _discovery() -> dict:
    cached = frappe.cache.get_value("pearnly_oidc_discovery")
    if cached:
        return cached
    url = issuer() + "/.well-known/openid-configuration"
    doc = requests.get(url, timeout=10).json()
    frappe.cache.set_value("pearnly_oidc_discovery", doc, expires_in_sec=DISCOVERY_TTL)
    return doc


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _state_key(state: str) -> str:
    return f"pearnly_oidc_state:{state}"


# ── 第一步：跳转到身份服务 ──────────────────────────────────────────────────


@frappe.whitelist(allow_guest=True)
def begin_login(redirect_to: str | None = None):
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()

    frappe.cache.set_value(
        _state_key(state),
        {"nonce": nonce, "verifier": verifier, "redirect_to": redirect_to},
        expires_in_sec=STATE_TTL,
    )

    params = {
        "client_id": _conf("pearnly_oidc_client_id"),
        "redirect_uri": _conf("pearnly_oidc_redirect_uri"),
        "response_type": "code",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = _discovery()["authorization_endpoint"] + "?" + urlencode(params)
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = url


# ── 第二步：回调、校验、绑定、建会话 ────────────────────────────────────────


@frappe.whitelist(allow_guest=True)
def callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        frappe.throw(_("The identity service returned an error: {0}").format(error))
    if not code or not state:
        frappe.throw(_("Missing code or state"))

    saved = frappe.cache.get_value(_state_key(state))
    # 一次性消费：无论成功失败都作废，防重放
    frappe.cache.delete_value(_state_key(state))
    if not saved:
        frappe.throw(_("state is invalid or has expired"))

    claims = _exchange_and_verify(code, saved)
    subject = claims.get("sub")
    token_issuer = claims.get("iss")
    if not subject:
        frappe.throw(_("The ID token has no sub"))
    if token_issuer != issuer():
        frappe.throw(_("The ID token issuer is not the identity service configured for this site"))

    user = _resolve_user(token_issuer, subject)

    # 把身份服务侧的会话 id 暂存，交由 on_session_creation 记进会话登记表。
    frappe.local.pearnly_kc = {
        "sid": claims.get("sid"),
        "sub": subject,
        "iss": token_issuer,
    }
    _login_as(user)

    frappe.db.set_value(
        "Pearnly Identity",
        {"issuer": token_issuer, "subject": subject},
        "last_login_at",
        frappe.utils.now(),
        update_modified=False,
    )

    # 落点交给首页规则算（通常是租户自己的工作台），不要写死 /desk —— 写死的话
    # 默认工作区形同虚设，每个人登录后都先看到"桌面"那一屏。
    from pearnly_core import workspace as workspace_mod

    redirect_to = workspace_mod.resolve_after_login(saved.get("redirect_to"))
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = redirect_to


def _exchange_and_verify(code: str, saved: dict) -> dict:
    disc = _discovery()
    resp = requests.post(
        disc["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _conf("pearnly_oidc_redirect_uri"),
            "client_id": _conf("pearnly_oidc_client_id"),
            "client_secret": _conf("pearnly_oidc_client_secret"),
            "code_verifier": saved["verifier"],
        },
        timeout=15,
    )
    if resp.status_code != 200:
        frappe.throw(_("Could not exchange the token: {0}").format(resp.text[:300]))
    tokens = resp.json()
    id_token = tokens.get("id_token")
    if not id_token:
        frappe.throw(_("The identity service did not return an id_token"))

    jwks_client = jwt.PyJWKClient(disc["jwks_uri"])
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=_conf("pearnly_oidc_client_id"),
        options={"require": ["exp", "iat", "sub", "iss"]},
    )
    if claims.get("nonce") != saved["nonce"]:
        frappe.throw(_("nonce mismatch"))
    return claims


def _resolve_user(token_issuer: str, subject: str) -> str:
    """按 (issuer, subject) 找本地账号。找不到就是没被邀请——直接拒绝。"""
    name = frappe.db.get_value(
        "Pearnly Identity",
        {"issuer": token_issuer, "subject": subject, "status": "active"},
        "name",
    )
    if not name:
        # 明确拒绝，绝不自动建号；也不按邮箱兜底匹配。
        frappe.throw(
            _("This identity is not registered on this site; ask an admin to invite you and retry (this site does not accept self sign-up)"),
            frappe.PermissionError,
        )
    user = frappe.db.get_value("Pearnly Identity", name, "user")
    if not frappe.db.get_value("User", user, "enabled"):
        frappe.throw(_("The account is disabled"), frappe.PermissionError)
    return user


def _login_as(user: str):
    frappe.local.login_manager.login_as(user)


# ── 供控制面调用：登记绑定（邀请） ──────────────────────────────────────────


@frappe.whitelist()
def bind_identity(user: str, subject: str, issuer_value: str | None = None, deployment_id: str | None = None):
    """把某个身份绑定到本地账号。只允许已登录且有权限的人调用。"""
    frappe.only_for(("System Manager",))
    if not frappe.db.exists("User", user):
        frappe.throw(_("No such local account: {0}").format(user))

    existing = frappe.db.exists(
        "Pearnly Identity", {"issuer": issuer_value or issuer(), "subject": subject}
    )
    if existing:
        frappe.throw(_("This identity is already bound to {0}").format(
            frappe.db.get_value("Pearnly Identity", existing, "user")
        ))

    doc = frappe.get_doc({
        "doctype": "Pearnly Identity",
        "issuer": issuer_value or issuer(),
        "subject": subject,
        "user": user,
        "deployment_id": deployment_id,
        "status": "active",
        "invited_by": frappe.session.user,
    })
    doc.insert()
    frappe.db.commit()
    return doc.name
