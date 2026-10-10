"""智能体入口站点 fatcc.com 的登录流程（ADR-019，只在 `pearnly_assistant_entry` 站点运行）。

1. 用户填用户名或邮箱 → 问计费服务这个账号在哪个账号池（不透露公司与站点；查不到就用默认账号池，
   照常进密码页、登录失败，不提示「没有这个账号」）。
2. 跳到该账号池的登录服务，用入口专用的公开客户端（PKCE，不带密钥）与 ai 专用密码页样式，账号已填好。
3. 回调：校验 state、nonce、签名、签发方、受众 → 用已验证的身份问计费服务能进哪些站点。
4. 一个站点直接进；多个让用户选一次。最后交给站点自己的统一登录口：账号池里已有登录状态，不再要密码。

记住上次：浏览器里留账号池与站点（不是登录凭据）。下次打开先静默确认登录状态，还在就直接进。
"""
import secrets
import time

import frappe
import jwt
import requests
from frappe import _

from pearnly_control.assistant_entry import contracts

STATE_TTL = 600
PICK_TTL = 600
DISCOVERY_TTL = 3600
REMEMBER_DAYS = 180
ISSUER_COOKIE = "pearnly_ai_issuer"
SITE_COOKIE = "pearnly_ai_site"
LANG_COOKIE = "pearnly_locale"
_jwks: dict = {}


def enabled() -> bool:
    return bool(frappe.conf.get("pearnly_assistant_entry"))


# 入口站点只开放这几个地址：入口页与三个登录接口。Frappe 自带的登录页、后台、通用接口一律拒绝（同计费站点的做法）。
ALLOWED_PATHS = frozenset({"/", "/ai"} | {f"/api/method/pearnly_control.assistant_entry.api.{name}"
                                          for name in ("start", "callback", "choose")})


def allowed(path: str) -> bool:
    return str(path or "") in ALLOWED_PATHS


def guard_entry_site():
    if not enabled():
        return
    request = getattr(frappe.local, "request", None)
    if request and (not allowed(request.path) or frappe.form_dict.get("cmd")):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def _require():
    if not enabled():
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def base_url() -> str:
    return str(frappe.conf.get("pearnly_assistant_entry_url") or "").rstrip("/")


def redirect_uri() -> str:
    return base_url() + "/api/method/pearnly_control.assistant_entry.api.callback"


def lang() -> str:
    request = getattr(frappe.local, "request", None)
    cookie = request.cookies.get(LANG_COOKIE) if request else ""
    header = request.headers.get("Accept-Language") if request else ""
    return contracts.lang(cookie or header)


def _billing(action: str, **payload) -> dict:
    url = str(frappe.conf.get("pearnly_assistant_entry_billing_url") or "").rstrip("/")
    token = frappe.conf.get("pearnly_assistant_entry_token") or ""
    if not url or not token:
        frappe.throw(_("The sign-in service is not available. Try again later."))
    try:
        response = requests.post(url + "/api/method/pearnly_control.assistant_entry.billing.resolve",
                                 json={"action": action, "payload": payload}, timeout=10, allow_redirects=False,
                                 headers={"X-Pearnly-Assistant-Entry": token,
                                          "Host": frappe.conf.get("pearnly_assistant_entry_billing_host") or "billing.internal"})
        response.raise_for_status()
        return response.json()["message"]
    except (requests.RequestException, ValueError, KeyError):
        frappe.throw(_("The sign-in service is not available. Try again later."))


def _discovery(issuer: str) -> dict:
    key = f"pearnly_ai_discovery:{issuer}"
    cached = frappe.cache.get_value(key)
    if cached:
        return cached
    doc = requests.get(issuer + "/.well-known/openid-configuration", timeout=10).json()
    frappe.cache.set_value(key, doc, expires_in_sec=DISCOVERY_TTL)
    return doc


def _store():
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()


def _authorize(issuer: str, *, login_hint="", silent=False) -> str:
    from pearnly_core.identity import _pkce_pair

    verifier, challenge = _pkce_pair()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    _store().set_value(f"pearnly_ai_state:{state}", {"issuer": issuer, "verifier": verifier, "nonce": nonce,
                                                    "silent": silent}, expires_in_sec=STATE_TTL)
    return contracts.authorize_url(issuer, redirect_uri=redirect_uri(), state=state, nonce=nonce, challenge=challenge,
                                   login_hint=login_hint, locale=lang(), silent=silent)


def _take(key: str) -> dict:
    store = _store()
    with store.lock(key + ":take", timeout=30, blocking_timeout=10):
        saved = store.get_value(key)
        if saved:
            store.delete_value(key)
    return saved or {}


def _remembered(name: str) -> str:
    request = getattr(frappe.local, "request", None)
    return (request.cookies.get(name) or "") if request else ""


def _remember(name: str, value: str) -> None:
    frappe.local.cookie_manager.set_cookie(name, value, max_age=REMEMBER_DAYS * 86400, secure=True, httponly=True)


def _redirect(location: str) -> None:
    # 地址带一个空的 #：入口第一页把密码放在 # 后面带去登录服务（浏览器不发给服务器）；登录服务已有登录状态时会直接跳回这里，
    # 浏览器跳转时会把原来的 # 一路带下去，显式给空 # 才会在这里断掉，不会跟到公司页面的地址栏里。
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = location if "#" in location else location + "#"


def start(login: str) -> str:
    """第一步：返回该账号所在账号池的登录地址。"""
    _require()
    login = contracts.login(login)
    found = _billing("issuers", login=login).get("issuers") or []
    issuers = contracts.ordered_issuers(found, _remembered(ISSUER_COOKIE))
    issuer = issuers[0] if issuers else contracts.issuer(frappe.conf.get("pearnly_assistant_entry_default_issuer"))
    return _authorize(issuer, login_hint=login)


def resume_url() -> str:
    """打开入口时：记得上次的账号池就先静默确认登录状态；不记得返回空串（显示登录页）。"""
    try:
        return _authorize(contracts.issuer(_remembered(ISSUER_COOKIE)), silent=True)
    except contracts.ContractError:
        return ""


def _verify(code: str, saved: dict) -> dict:
    disc = _discovery(saved["issuer"])
    response = requests.post(disc["token_endpoint"], timeout=15, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
        "client_id": contracts.CLIENT_ID, "code_verifier": saved["verifier"]})
    if response.status_code != 200 or not response.json().get("id_token"):
        frappe.throw(_("Sign-in failed. Try again."))
    id_token = response.json()["id_token"]
    client = _jwks.get(saved["issuer"]) or _jwks.setdefault(saved["issuer"], jwt.PyJWKClient(disc["jwks_uri"]))
    claims = jwt.decode(id_token, client.get_signing_key_from_jwt(id_token).key, algorithms=["RS256"],
                        audience=contracts.CLIENT_ID, issuer=saved["issuer"],
                        options={"require": ["exp", "iat", "sub", "iss"]})
    if claims.get("nonce") != saved["nonce"]:
        frappe.throw(_("Sign-in failed. Try again."))
    return claims


def callback(code=None, state=None, error=None) -> None:
    _require()
    saved = _take(f"pearnly_ai_state:{state}") if state else {}
    if not saved:
        return _redirect(base_url() + "/")
    if error or not code:
        # 静默确认没过（登录状态已过期）或用户在登录页返回：回到入口重新填账号。
        return _redirect(base_url() + "/?fresh=1")
    claims = _verify(code, saved)
    sites = _billing("sites", issuer=saved["issuer"], subject=claims["sub"],
                     username=claims.get("preferred_username") or "").get("sites") or []
    _remember(ISSUER_COOKIE, saved["issuer"])
    if not sites:
        return _redirect(base_url() + "/?none=1")
    if len(sites) == 1:
        return go(sites[0]["site"])
    last = _remembered(SITE_COOKIE)
    if saved.get("silent") and last in {s["site"] for s in sites}:
        return go(last)  # 打开入口时静默确认通过：回到上次进的公司
    token = secrets.token_urlsafe(24)
    _store().set_value(f"pearnly_ai_pick:{token}", {"sites": sites, "at": time.time()}, expires_in_sec=PICK_TTL)
    return _redirect(base_url() + f"/?pick={token}")


def picks(token: str) -> list[dict]:
    if not token:
        return []
    return (_store().get_value(f"pearnly_ai_pick:{token}") or {}).get("sites") or []


def choose(pick: str, site: str) -> None:
    _require()
    site = contracts.site(site)
    if site not in {s["site"] for s in picks(pick)}:
        return _redirect(base_url() + "/?fresh=1")
    _take(f"pearnly_ai_pick:{pick}")
    return go(site)


def go(site: str) -> None:
    _remember(SITE_COOKIE, site)
    return _redirect(contracts.site_entry_url(site))
