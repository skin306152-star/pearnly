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
import time
from urllib.parse import quote, urlencode

import frappe
import jwt
import requests
from frappe import _
from pearnly_core.account import oidc_session, preferences
from pearnly_core.execution import state as pearnly_state

STATE_TTL = 600  # 秒
# 用过的 state 再留一会儿：LINE 内置浏览器会把同一个回调发两次（2026-10-04 生产日志：
# 同一秒 499 + 417），第二次不报错，而是续上登录。
CONSUMED_TTL = 120
DISCOVERY_TTL = 3600
# 智能体入口（ADR-019）的正式地址；测试环境用站点配置 pearnly_assistant_entry_url 指到 testai。
ASSISTANT_ENTRY = "https://fatcc.com"   # 智能体（CC）入口，2026-10-10 起用独立域名


# ── 配置 ────────────────────────────────────────────────────────────────────


def assistant_entry_url() -> str:
    return str(frappe.conf.get("pearnly_assistant_entry_url") or ASSISTANT_ENTRY).rstrip("/")


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
    return _begin_login(redirect_to)


@frappe.whitelist()
def begin_password_change():
    if frappe.session.user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    return _begin_login("/desk/user/" + quote(frappe.session.user, safe=""), password_change=True)


def begin_login_via(redirect_to, idp_hint):
    """直接走某个外部登录入口（目前只有 LINE，见 account/line_login.py）。"""
    return _begin_login(redirect_to, idp_hint=idp_hint)


def end_session_endpoint() -> str:
    return _discovery()["end_session_endpoint"]


def _begin_login(redirect_to=None, password_change=False, idp_hint=None):
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()

    pearnly_state.store().set_value(
        _state_key(state),
        {"nonce": nonce, "verifier": verifier, "redirect_to": redirect_to, "idp_hint": idp_hint,
         "expected_user": frappe.session.user if password_change else None},
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
    preferred = frappe.request.cookies.get("pearnly_locale") if frappe.request else None
    if preferred in preferences.LANGUAGES.values():
        params["ui_locales"] = preferred
    if password_change:
        params.update(kc_action="UPDATE_PASSWORD", prompt="login", login_hint=frappe.session.user)
    elif idp_hint:
        params["kc_idp_hint"] = idp_hint
    url = _discovery()["authorization_endpoint"] + "?" + urlencode(params)
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = url


# ── 第二步：回调、校验、绑定、建会话 ────────────────────────────────────────


def _take_state(state: str) -> dict:
    """一次性取出 state：取到的同时换成"已用"标记（锁内完成，并发的重复请求只会看到标记）。"""
    store = pearnly_state.store()
    key = _state_key(state)
    with store.lock(key + ":take", timeout=30, blocking_timeout=10):
        saved = store.get_value(key)
        if saved and not saved.get("consumed"):
            store.set_value(key, {"consumed": True, "redirect_to": saved.get("redirect_to"),
                                  "idp_hint": saved.get("idp_hint")}, expires_in_sec=CONSUMED_TTL)
    return saved or {}


def _resume(saved: dict):
    """重复的回调：已经登录就直接去目标页；还没有就重新走一遍（身份服务有会话，会立刻回来）。"""
    if frappe.session.user != "Guest":
        from pearnly_core import workspace as workspace_mod

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = workspace_mod.resolve_after_login(saved.get("redirect_to"))
        return
    _begin_login(saved.get("redirect_to"), idp_hint=saved.get("idp_hint"))


RETRY_COOKIE = "pearnly_login_retry"
RETRY_SECONDS = 60


def _restart():
    """回跳时 state 已过期（在登录页停了超过 STATE_TTL）或查不到：已经登录就去目标页；没有就重新走一遍登录
    （身份服务那边刚登录过，会立刻回来，不用再输密码）。以前直接报「state 无效或已过期」，用户只看到服务器错误、
    刷新还是同一个死地址（2026-10-11 ris.pearnly.com：登录页停了 12 分钟）。60 秒内只重来一次，免得状态存储出问题时来回跳。"""
    if frappe.session.user != "Guest":
        from pearnly_core import workspace as workspace_mod

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = workspace_mod.resolve_after_login(None)
        return
    if frappe.request and frappe.request.cookies.get(RETRY_COOKIE):
        frappe.throw(_("state is invalid or has expired"))
    frappe.local.cookie_manager.set_cookie(RETRY_COOKIE, "1", max_age=RETRY_SECONDS, httponly=True)
    _begin_login(None)


def _refuse(reason: str):
    """登录被拒：不建本地会话；说明页上的"请登录"先经浏览器注销结束身份服务那边这次会话。

    以前只有 LINE 被拒时才注销（还是直接跳走、看不到原因）；按账号被拒（本站没登记这个身份）
    时会话留在身份服务里，单点登录会一直把同一个身份带回来，连密码登录也进不去
    （2026-10-04 testcowork 实际卡住过）。站点没有身份服务管理凭据，只能走浏览器注销。
    """
    from pearnly_core.account import line_login

    frappe.respond_as_web_page(
        _("Not permitted"), reason, http_status_code=403, indicator_color="red",
        primary_action=line_login.end_session_url(frappe.local.pearnly_verified_tokens["id_token"]),
        primary_label=_("Please sign in"))


@frappe.whitelist(allow_guest=True)
def callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        frappe.throw(_("The identity service returned an error: {0}").format(error))
    if not code or not state:
        frappe.throw(_("Missing code or state"))

    saved = _take_state(state)
    if saved.get("consumed"):
        return _resume(saved)
    if not saved:
        return _restart()

    claims = _exchange_and_verify(code, saved)
    subject = claims.get("sub")
    token_issuer = claims.get("iss")
    if not subject:
        frappe.throw(_("The ID token has no sub"))
    if token_issuer != issuer():
        frappe.throw(_("The ID token issuer is not the identity service configured for this site"))

    # 经 LINE 登录的：身份服务给的是这个 LINE 的专用身份，按本站生效绑定认人（必须是本站入口）。
    # 其余按 (issuer, subject) 找本站登记的账号。不通过就不建本地会话，并结束身份服务那边的会话。
    from pearnly_core.account import line_login

    try:
        if line_login.is_line_login(claims):
            user, source = line_login.resolve_line_login(claims)
        else:
            user, source = _resolve_user(token_issuer, subject), {}
        if saved.get("expected_user") and user != saved["expected_user"]:
            frappe.throw(_("Not permitted"), frappe.PermissionError)
    except frappe.PermissionError as refused:
        return _refuse(str(refused))

    # 把身份服务侧的会话 id 暂存，交由 on_session_creation 记进会话登记表。
    frappe.local.pearnly_kc = {
        "sid": claims.get("sid"),
        "sub": subject,
        "iss": token_issuer,
        **source,
    }
    _login_as(user)
    tokens = frappe.local.pearnly_verified_tokens
    if source:
        # LINE 专用身份不是本站账号：只留注销凭据，不拿它的令牌改资料、不用它的用户名和语言。
        oidc_session.remember({"id_token": tokens["id_token"]}, subject)
        preferences.finish_login_locale(user, None)
        identity_filters = {"issuer": token_issuer, "user": user, "status": "active"}
    else:
        oidc_session.remember(tokens, subject)
        from pearnly_core.account.profile import apply_login_username
        apply_login_username(user, claims)
        preferences.finish_login_locale(user, claims.get("locale"))
        identity_filters = {"issuer": token_issuer, "subject": subject}

    frappe.db.set_value(
        "Pearnly Identity",
        identity_filters,
        "last_login_at",
        frappe.utils.now(),
        update_modified=False,
    )

    # 落点交给首页规则算（通常是租户自己的工作台），不要写死 /desk —— 写死的话
    # 默认工作区形同虚设，每个人登录后都先看到"桌面"那一屏。
    from pearnly_core import workspace as workspace_mod

    redirect_to = workspace_mod.resolve_after_login(saved.get("redirect_to"))
    # OIDC redirects arrive as GET. Native Frappe rolls GET writes back unless
    # explicitly marked: persist the verified profile projection and login audit.
    frappe.flags.commit = True
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = redirect_to


@frappe.whitelist(methods=["POST"])
def sign_out(to=None):
    """After the in-app confirmation, end this session via native OIDC logout.

    从智能体对话页退出（`to=assistant`）回到智能体入口 fatcc.com（ADR-019），其余回本站首页。
    入口地址的跳转已登记在各站点的身份服务客户端（post.logout.redirect.uris）。
    """
    endpoint = _discovery()["end_session_endpoint"]
    back = str(_conf("host_name")).rstrip("/") + "/"
    if to == "assistant":
        back = assistant_entry_url() + "/?fresh=1"
    params = {
        "client_id": _conf("pearnly_oidc_client_id"),
        "post_logout_redirect_uri": back,
    }
    hint = oidc_session.logout_hint()
    if hint:
        params["id_token_hint"] = hint
    # Old sessions without a hint retain the provider's safe confirmation fallback.
    url = endpoint + "?" + urlencode(params)
    oidc_session.forget()
    frappe.local.login_manager.logout()
    return {"redirect_url": url}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def backchannel_logout(logout_token=None):
    """OIDC back-channel logout: a signed provider event, never an admin token."""
    try:
        signing_key = jwt.PyJWKClient(_discovery()["jwks_uri"]).get_signing_key_from_jwt(logout_token)
        claims = jwt.decode(logout_token, signing_key.key, algorithms=["RS256"],
            audience=_conf("pearnly_oidc_client_id"), issuer=issuer(),
            options={"require": ["iat", "jti", "iss", "aud", "sid"]})
        event = "http://schemas.openid.net/event/backchannel-logout"
        if (event not in claims.get("events", {}) or "nonce" in claims
                or not 0 <= time.time() - claims["iat"] <= 120):
            raise ValueError("Invalid logout event")
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        frappe.throw(_("Not permitted"), frappe.AuthenticationError)
    from frappe.sessions import delete_session
    rows = frappe.get_all("Pearnly Session", filters={"keycloak_sid": claims["sid"], "status": "active"},
                          fields=["name", "user", "session_id"])
    for row in rows:
        delete_session(row.session_id, user=row.user)
        frappe.db.set_value("Pearnly Session", row.name, "status", "revoked")
    frappe.db.commit()
    return {"ok": True}


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
    frappe.local.pearnly_verified_tokens = resp.json()
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
