# -*- coding: utf-8 -*-
"""会话登记与撤权。

设计（与计划合同一致）：

- **不另造一套令牌**。登录会话就是 Frappe 原生 session；这里只做"登记"，
  把每个会话记下来，用于展示、审计和撤权。
- **不同产品、不同设备互不踢**。除非显式"退出全部"或改密，否则新登录不
  影响已有会话。
- 撤权分三种：退出本产品（只结束本站）、退出全部（含身份服务会话）、
  改密后撤销（保留当前、其余作废）。

身份服务侧的会话用 ID token 里的 `sid` 关联；撤权时按 `sid` 精确终止，
不去动别人的会话。
"""

import frappe
import requests
from frappe import _


def _realm_base() -> str:
    return str(frappe.conf.get("pearnly_oidc_issuer") or "").rstrip("/")


def _admin_url(path: str) -> str:
    base, realm = _realm_base().split("/realms/", 1)
    return f"{base}/admin/realms/{realm}{path}"


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
        frappe.throw(_("Could not get the admin token"))
    return resp.json()["access_token"]


def _kill_keycloak_session(sid: str) -> bool:
    resp = requests.delete(
        _admin_url(f"/sessions/{sid}"),
        headers={"Authorization": "Bearer " + _admin_token()},
        timeout=15,
    )
    return resp.status_code in (204, 200, 404)


# ── 登记（由 hooks 的 on_session_creation 触发）────────────────────────────


def register_session(login_manager=None):
    """记录本次登录。identity 回调会把身份服务会话 id 暂存在 frappe.local。"""
    session = getattr(frappe.local, "session", None)
    sid = getattr(session, "sid", None) if session else None
    if not sid:
        return

    kc = getattr(frappe.local, "pearnly_kc", None) or {}
    user = frappe.session.user

    values = {
        "user": user,
        "session_id": sid,
        "keycloak_sid": kc.get("sid"),
        # 经 LINE 登录的会话记下来源：绑定变了就只作废这些（见 account/line_login.py）。
        "identity_provider": kc.get("identity_provider"),
        "line_user_id": kc.get("line_user_id"),
        "deployment": frappe.local.site,
        "user_agent": (frappe.get_request_header("User-Agent") or "")[:255],
        "ip_address": (frappe.local.request_ip or "")[:64],
        "status": "active",
        "last_seen_at": frappe.utils.now(),
    }

    existing = frappe.db.get_value("Pearnly Session", {"session_id": sid}, "name")
    if existing:
        frappe.db.set_value("Pearnly Session", existing, values, update_modified=False)
    else:
        doc = frappe.get_doc({"doctype": "Pearnly Session", "created_at": frappe.utils.now(), **values})
        doc.insert(ignore_permissions=True)
    frappe.db.commit()


def _my_rows(user: str) -> list[dict]:
    return frappe.get_all(
        "Pearnly Session",
        filters={"user": user, "status": "active"},
        fields=["name", "session_id", "keycloak_sid", "deployment"],
        limit_page_length=0,
    )


def _mark_revoked(names: list[str]):
    for name in names:
        frappe.db.set_value(
            "Pearnly Session", name, {"status": "revoked", "revoked_at": frappe.utils.now()},
            update_modified=False,
        )
    frappe.db.commit()


def _drop_local_sessions(user: str, keep_current: bool = False):
    """让本地会话立刻失效。

    用框架自带的 frappe.sessions.clear_sessions —— 它会同时清数据库记录与缓存，
    比手写删除可靠（手写时踩过 DocType 解析问题）。
    """
    from frappe.sessions import clear_sessions

    clear_sessions(user=user, keep_current=keep_current, force=True)


# ── 对外接口 ────────────────────────────────────────────────────────────────


@frappe.whitelist()
def my_sessions():
    """当前账号的活跃会话列表（供产品界面展示"登录设备"）。"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    return frappe.get_all(
        "Pearnly Session",
        filters={"user": user, "status": "active"},
        fields=["name", "deployment", "user_agent", "ip_address", "created_at", "last_seen_at"],
        order_by="last_seen_at desc",
        limit_page_length=0,
    )


@frappe.whitelist(methods=["POST"])
def logout_here():
    """退出本产品：只结束当前站点的这个会话。"""
    sid = frappe.session.sid
    frappe.db.set_value(
        "Pearnly Session", {"session_id": sid},
        {"status": "revoked", "revoked_at": frappe.utils.now()}, update_modified=False,
    )
    frappe.db.commit()
    frappe.local.login_manager.logout()
    return {"ok": True}


@frappe.whitelist(methods=["POST"])
def logout_everywhere(keep_current: bool = False):
    """退出全部：本站所有会话 + 身份服务侧对应会话一起作废。

    产品接入用这个接口；`keep_current` 供"改密后撤销其它会话"复用。
    """
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)

    current_sid = frappe.session.sid
    rows = _my_rows(user)

    for row in rows:
        if keep_current and row.session_id == current_sid:
            continue
        if row.keycloak_sid:
            _kill_keycloak_session(row.keycloak_sid)

    targets = [r.name for r in rows if not (keep_current and r.session_id == current_sid)]
    _mark_revoked(targets)
    _drop_local_sessions(user, keep_current=keep_current)

    if not keep_current:
        frappe.local.login_manager.logout()
    return {"ok": True, "revoked": len(targets)}
