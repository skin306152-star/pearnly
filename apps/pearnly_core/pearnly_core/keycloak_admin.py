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

**控制面开户要打到服务器**：目标环境在 `Pearnly Target` 里配，不是站点配置。那种
情况下用下面的 `AdminClient`，直接带上地址、realm 与凭据。
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


# ── 按显式配置访问（控制面开户用；目标可以是本机，也可以是服务器）─────────────


class AdminClient:
    """一个目标环境的身份服务管理客户端。

    与上面那组函数的区别：地址、realm、凭据都由调用方给，**不读站点配置**——
    因为控制面要对准服务器，而服务器不是本机站点。
    """

    def __init__(self, base_url: str, realm: str, client_id: str, client_secret: str):
        self.base = (base_url or "").rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        if not (self.base and self.realm and self.client_id and self.client_secret):
            frappe.throw(_("目标环境配置不完整：地址、realm、客户端 ID 与密钥都要填"))

    def token(self) -> str:
        resp = requests.post(
            f"{self.base}/realms/{self.realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            frappe.throw(
                _("连不上目标环境的身份服务（{0}）：{1} {2}").format(
                    self.base, resp.status_code, resp.text[:200]
                )
            )
        return resp.json()["access_token"]

    def _url(self, path: str) -> str:
        return f"{self.base}/admin/realms/{self.realm}{path}"

    @staticmethod
    def _headers(token: str) -> dict:
        return {"Authorization": "Bearer " + token}

    def find_user_id_by_email(self, email: str) -> str | None:
        resp = requests.get(
            self._url("/users"),
            headers=self._headers(self.token()),
            params={"email": email, "exact": "true"},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            frappe.throw(_("查账号失败：{0} {1}").format(resp.status_code, resp.text[:200]))
        rows = resp.json() or []
        return rows[0]["id"] if rows else None

    def create_user(self, email: str, first_name: str, password: str | None = None,
                    last_name: str = "") -> str:
        """建号。`password=None` 表示**先不设密码**，走邀请邮件让用户自己设。

        不设密码时必须立刻发出邀请（`send_invite_email`），否则这个账号登不进来。
        """
        token = self.token()
        payload = {
            "username": email,
            "email": email,
            "firstName": first_name or email.split("@")[0],
            "lastName": last_name or "",
            "enabled": True,
            "emailVerified": True,
            # 姓名必须给，否则 Keycloak 会挂 VERIFY_PROFILE 必填动作，
            # 登录后跳到补资料页而不是回调。
            "requiredActions": [],
        }
        if password:
            payload["credentials"] = [
                {"type": "password", "value": password, "temporary": False}
            ]
        resp = requests.post(self._url("/users"), headers=self._headers(token),
                             json=payload, timeout=TIMEOUT)
        if resp.status_code not in (201, 204):
            frappe.throw(_("在目标环境建账号失败：{0} {1}").format(
                resp.status_code, resp.text[:200]))
        location = resp.headers.get("Location", "")
        if location:
            return location.rstrip("/").rsplit("/", 1)[-1]
        found = self.find_user_id_by_email(email)
        if not found:
            frappe.throw(_("账号已建但取不到 id"))
        return found

    def send_invite_email(self, user_id: str, client_id: str,
                          lifespan_seconds: int = 7 * 24 * 3600) -> None:
        """让身份服务给这个账号发一封「自己设密码」的邀请邮件。

        `client_id` 是身份服务用来拼链接的客户端：邮件里的地址会按它的
        redirect URI 生成。样板阶段所有租户共用同一个客户端；真实租户各自
        建客户端之后应当传租户自己的。

        发不出去时**必须报错**：Keycloak 在 SMTP 认证失败时会返回 500
        并记 `KC-SERVICES0029`（实测过），所以这里不是"发不发得出去都继续"。
        """
        if not client_id:
            frappe.throw(_("没有配「邀请链接用的客户端 ID」，发不出邀请邮件"))
        resp = requests.put(
            self._url(f"/users/{user_id}/execute-actions-email"),
            headers=self._headers(self.token()),
            params={"client_id": client_id, "lifespan": lifespan_seconds},
            json=["UPDATE_PASSWORD"],
            timeout=TIMEOUT,
        )
        if resp.status_code not in (200, 204):
            frappe.throw(
                _("邀请邮件发送失败：{0} {1}").format(resp.status_code, resp.text[:200])
            )


def client_for_target(target_name: str | None = None) -> tuple["AdminClient", str]:
    """按 `Pearnly Target` 取一个管理客户端。返回 (client, 目标名)。

    没指定就取默认目标；一个目标都没有则报错——开户必须知道打到哪。
    """
    filters = {"target_name": target_name} if target_name else {"is_default": 1}
    row = frappe.db.get_value(
        "Pearnly Target", filters,
        ["target_name", "base_url", "realm", "admin_client_id", "admin_client_secret"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("没有可用的开户目标，请先在“开户目标”里配一个"))
    secret = row.admin_client_secret
    if secret:
        try:
            from frappe.utils.password import get_decrypted_password

            secret = get_decrypted_password(
                "Pearnly Target", row.target_name, "admin_client_secret", raise_exception=False
            ) or secret
        except Exception:
            pass
    return AdminClient(row.base_url, row.realm, row.admin_client_id, secret), row.target_name


def invite_client_id_for(target_name: str | None = None) -> str:
    """取该目标用于拼邀请链接的客户端 ID（见 `Pearnly Target.invite_client_id`）。"""
    filters = {"target_name": target_name} if target_name else {"is_default": 1}
    return frappe.db.get_value("Pearnly Target", filters, "invite_client_id") or ""
