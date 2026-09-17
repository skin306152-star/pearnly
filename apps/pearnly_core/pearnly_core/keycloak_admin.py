# -*- coding: utf-8 -*-
"""身份服务的管理侧调用（统一一份，控制面与业务站点都用它）。

之前 `password.py` 与 `sessions.py` 各自写了一份取管理令牌与拼管理地址的代码，
这里收敛成一处，避免两份实现慢慢走偏。

约定：
- 管理凭据只从站点配置读，不落库、不进日志。
- 所有调用都设超时；失败一律抛错，不静默返回空。

**两个 realm 的概念**：业务站点只接业务 realm（平台账号进不来）。控制面不一样：
它自己用平台 realm 登录，但要往**业务 realm** 里建租户账号。所以这里的函数都带
一个 `prefix`，指到哪套配置就用哪套：

    prefix="pearnly_oidc"          → 业务 realm（业务站点的默认）
    prefix="pearnly_business_oidc" → 业务 realm（控制面用它建号）
    prefix="pearnly_oidc"（平台站点）→ 平台 realm
"""

import frappe
import requests
from frappe import _

TIMEOUT = 15


def realm_base(prefix: str = "pearnly_oidc") -> str:
    value = str(frappe.conf.get(f"{prefix}_issuer") or "").rstrip("/")
    if not value:
        frappe.throw(_("站点配置缺少 {0}_issuer").format(prefix))
    return value


def realm_name(prefix: str = "pearnly_oidc") -> str:
    # issuer 形如 http://host/realms/<name>
    return realm_base(prefix).rsplit("/realms/", 1)[-1]


def admin_url(path: str, prefix: str = "pearnly_oidc") -> str:
    base = realm_base(prefix).split("/realms/")[0]
    return f"{base}/admin/realms/{realm_name(prefix)}{path}"


def admin_token(prefix: str = "pearnly_oidc") -> str:
    client_id = frappe.conf.get(f"{prefix}_admin_client_id")
    client_secret = frappe.conf.get(f"{prefix}_admin_client_secret")
    if not client_id or not client_secret:
        frappe.throw(_("站点配置缺少 {0}_admin_client_id / _secret").format(prefix))
    resp = requests.post(
        realm_base(prefix) + "/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        frappe.throw(_("取管理令牌失败：{0}").format(resp.status_code))
    return resp.json()["access_token"]


def _headers(prefix: str = "pearnly_oidc") -> dict:
    return {"Authorization": "Bearer " + admin_token(prefix)}


def find_user_id_by_email(email: str, prefix: str = "pearnly_oidc") -> str | None:
    resp = requests.get(
        admin_url("/users", prefix),
        headers=_headers(prefix),
        params={"email": email, "exact": "true"},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        frappe.throw(_("按邮箱查账号失败：{0}").format(resp.status_code))
    rows = resp.json() or []
    return rows[0]["id"] if rows else None


def create_user(email: str, first_name: str, last_name: str = "", password: str | None = None,
                prefix: str = "pearnly_oidc") -> str:
    """建账号。返回 subject（身份服务里的用户 id）。

    注意：**不按邮箱自动合并**。调用方负责先判断账号是否已存在。
    """
    payload = {
        "username": email,
        "email": email,
        "firstName": first_name or email.split("@")[0],
        "lastName": last_name or "",
        "enabled": True,
        "emailVerified": True,
        # 必须有姓名，否则 Keycloak 会挂 VERIFY_PROFILE 必填动作，
        # 登录后跳到补资料页而不是回调（踩过）。
        "requiredActions": [],
    }
    if password:
        payload["credentials"] = [{"type": "password", "value": password, "temporary": False}]

    resp = requests.post(admin_url("/users", prefix), headers=_headers(prefix),
                         json=payload, timeout=TIMEOUT)
    if resp.status_code not in (201, 204):
        frappe.throw(_("建账号失败：{0} {1}").format(resp.status_code, resp.text[:200]))

    location = resp.headers.get("Location", "")
    if location:
        return location.rstrip("/").rsplit("/", 1)[-1]
    subject = find_user_id_by_email(email, prefix)
    if not subject:
        frappe.throw(_("账号已建但取不到 id"))
    return subject


def set_password(subject: str, password: str, *, temporary: bool = False,
                 prefix: str = "pearnly_oidc") -> None:
    resp = requests.put(
        admin_url(f"/users/{subject}/reset-password", prefix),
        headers={**_headers(prefix), "Content-Type": "application/json"},
        json={"type": "password", "value": password, "temporary": temporary},
        timeout=TIMEOUT,
    )
    if resp.status_code not in (200, 204):
        frappe.throw(_("设置密码失败：{0} {1}").format(resp.status_code, resp.text[:200]))


def kill_session(sid: str, prefix: str = "pearnly_oidc") -> bool:
    resp = requests.delete(
        admin_url(f"/sessions/{sid}", prefix), headers=_headers(prefix), timeout=TIMEOUT
    )
    return resp.status_code in (200, 204, 404)


def verify_password(username: str, password: str, prefix: str = "pearnly_oidc") -> bool:
    """校验某个账号的当前密码。

    走的是**专用客户端**（只开密码授权，不能发起登录跳转、无服务账号）。
    登录本身仍然只走授权码 + PKCE。
    """
    resp = requests.post(
        realm_base(prefix) + "/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": frappe.conf.get(f"{prefix}_password_check_client_id"),
            "client_secret": frappe.conf.get(f"{prefix}_password_check_client_secret"),
            "username": username,
            "password": password,
            "scope": "openid",
        },
        timeout=TIMEOUT,
    )
    return resp.status_code == 200
