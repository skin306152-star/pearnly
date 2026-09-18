# -*- coding: utf-8 -*-
"""账号自助改密。三个产品共用同一套接口（本 App 是共用层）。

为什么要单独校验"当前密码"：接入统一身份后，**密码存在身份服务里，站点不存密码**。
要支持"输入当前密码再改"，就需要一条校验通道。

⚠️ 与计划的偏离（明确记录）：
计划写的是"禁止密码授权（Resource Owner Password）grant"。登录**没有**用密码授权，
仍然只走授权码 + PKCE。但为了校验"当前密码"，这里开了一个**专用客户端**
`pearnly-password-check`，它只开密码授权、不能发起登录跳转、不能拿服务账号。
用途仅限：服务端拿用户输入的当前密码去身份服务换一次令牌，成功即说明密码正确，
随后立刻丢弃令牌。若将来不接受这一条通道，就把"当前密码"字段去掉，改成
要求用户重新做一次 OIDC 登录（prompt=login）后再改。
"""

import frappe
import requests
from frappe import _

# 密码强度**不卡**（用户 2026-09-18 明确要求："密码不要卡得太严格，纯字母，
# 纯数字都可以，包括首次使用"）。
#
# 所以这里只保证"非空"，长度和复杂度由用户自己决定。这也是两边一致的做法：
# 身份服务侧本来就没有密码策略（realm 的 `passwordPolicy` 为空），
# 卡人的一直是**我们自己**这个下限 —— 放宽后就不会出现"身份服务收下了、
# 我们这边先拒了"的错配。
MIN_LENGTH = 1


def _realm_base() -> str:
    return str(frappe.conf.get("pearnly_oidc_issuer") or "").rstrip("/")


def _identity_for(user: str) -> dict:
    row = frappe.db.get_value(
        "Pearnly Identity",
        {"user": user, "status": "active"},
        ["name", "subject", "issuer"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("当前账号没有登记身份绑定，无法自助改密，请联系管理员"))
    return row


def _admin_token() -> str:
    resp = requests.post(
        _realm_base() + "/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": frappe.conf.get("pearnly_oidc_admin_client_id"),
            "client_secret": frappe.conf.get("pearnly_oidc_admin_client_secret"),
        },
        timeout=15,
    )
    if resp.status_code != 200:
        frappe.throw(_("取管理令牌失败"))
    return resp.json()["access_token"]


def _keycloak_username(subject: str) -> str:
    resp = requests.get(
        _admin_url(f"/users/{subject}"),
        headers={"Authorization": "Bearer " + _admin_token()},
        timeout=15,
    )
    if resp.status_code != 200:
        frappe.throw(_("读取账号资料失败：{0}").format(resp.status_code))
    return resp.json()["username"]


def _realm_name() -> str:
    # issuer 形如 http://host/realms/<name>
    return _realm_base().rsplit("/realms/", 1)[-1]


def _admin_url(path: str) -> str:
    base = _realm_base().split("/realms/")[0]
    return f"{base}/admin/realms/{_realm_name()}{path}"


def _verify_current_password(subject: str, current_password: str) -> None:
    username = _keycloak_username(subject)
    resp = requests.post(
        _realm_base() + "/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": frappe.conf.get("pearnly_oidc_password_check_client_id"),
            "client_secret": frappe.conf.get("pearnly_oidc_password_check_client_secret"),
            "username": username,
            "password": current_password,
            "scope": "openid",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        frappe.throw(_("当前密码不正确"), frappe.AuthenticationError)


@frappe.whitelist(methods=["POST"])
def change_password(current_password=None, new_password=None, confirm_password=None):
    """改自己的密码。任何产品页面都调这一个接口。"""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("未登录"), frappe.AuthenticationError)

    current_password = (current_password or "").strip()
    new_password = (new_password or "").strip()
    confirm_password = (confirm_password or "").strip()

    if not current_password or not new_password or not confirm_password:
        frappe.throw(_("三项都要填"))
    if new_password != confirm_password:
        frappe.throw(_("两次输入的新密码不一致"))
    if len(new_password) < MIN_LENGTH:
        frappe.throw(_("新密码不能为空"))
    if new_password == current_password:
        frappe.throw(_("新密码不能与当前密码相同"))

    identity = _identity_for(user)
    _verify_current_password(identity.subject, current_password)

    resp = requests.put(
        _admin_url(f"/users/{identity.subject}/reset-password"),
        headers={
            "Authorization": "Bearer " + _admin_token(),
            "Content-Type": "application/json",
        },
        json={"type": "password", "value": new_password, "temporary": False},
        timeout=15,
    )
    if resp.status_code not in (200, 204):
        frappe.throw(_("身份服务拒绝改密：{0} {1}").format(resp.status_code, resp.text[:200]))

    frappe.logger("pearnly.identity").info(
        f"password changed user={user} subject={identity.subject}"
    )

    # 改密后撤销其它会话：保留当前这一个，其余（含身份服务侧）全部作废。
    # 这是"改密即踢掉别处登录"的落点；正常登录不会互踢。
    revoked = 0
    try:
        from pearnly_core import sessions

        revoked = sessions.logout_everywhere(keep_current=True).get("revoked", 0)
    except Exception as exc:  # 撤权失败不能掩盖"密码已改成功"这个事实
        frappe.logger("pearnly.identity").warning(f"revoke after password change failed: {exc}")

    return {"ok": True, "revoked_other_sessions": revoked}
