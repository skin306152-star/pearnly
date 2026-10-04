# -*- coding: utf-8 -*-
"""LINE 绑定即登录（多产品共用）：已绑定 LINE 的人在 LINE 里点链接免密进网页端。

做法（用户 2026-09-26 定，2026-10-04 改）：身份服务（Keycloak）每个 LINE Login 频道一个隐藏的
LINE 登录入口，会话仍由身份服务签发、照常登记。身份服务里**每个 LINE 一个专用身份**（控制面建、
没有密码、不属于任何租户），所以同一个 LINE 绑在几个租户的账号上互不冲突。本站只做三件事：

1. **关联同步**：绑定变化 → 经计费站点的建号服务登记"本站这个账号绑着这个 LINE"，控制面据此
   维护专用身份（站点永不持有身份服务管理凭据）。失败留 `failed`，定时对账补齐。
2. **登录认人**：ID token 带了登录来源（`pearnly_idp`）就必须是本站的 LINE 入口，再按
   `pearnly_line_user_id` 找本站**生效**的绑定认人 —— 不看身份服务里的账号是谁。所以 LINE 只能
   进绑定了它的站点，解绑、改绑、停用之后身份服务里残留的会话也进不来。
3. **会话作废**：绑定不再对应时，立即作废本站经 LINE 登录的会话（密码登录的不动）。

站点上是否启用由开通步骤写入的 `pearnly_line_login_idp` 决定，且必须等于按本站 LIFF 所属
LINE Login 频道算出的别名（一个频道一个入口，见 `line_login_contract`）。
"""

from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import now

from pearnly_core.account import line_login_contract as contract

BINDING = "Pearnly LINE Binding"
SESSION = "Pearnly Session"
IDENTITY = "Pearnly Identity"


# ── 配置 ────────────────────────────────────────────────────────────────────


def login_channel() -> str:
    """本站 LIFF 所属的 LINE Login 频道号（没配 LIFF 就空）。"""
    return contract.login_channel_of(frappe.conf.get("pearnly_line_liff_id"))


def site_alias() -> str:
    """本站的 LINE 登录入口别名；算不出来就空。"""
    channel = login_channel()
    return contract.idp_alias(channel) if channel else ""


def enabled_alias() -> str:
    """已开通的别名：开通步骤写入的值必须与算出来的一致，否则视为未开通。"""
    alias = site_alias()
    return alias if alias and frappe.conf.get("pearnly_line_login_idp") == alias else ""


# ── 入口 ────────────────────────────────────────────────────────────────────


def _local_path(to) -> str:
    to = str(to or "/")
    if not to.startswith("/") or to.startswith("//") or "\\" in to or any(c in to for c in "\r\n"):
        return "/"
    return to


def in_line_app() -> bool:
    """LINE 内置浏览器的 User-Agent 带 `Line/<版本>`。只在这里才直接走 LINE 登录。"""
    if not getattr(frappe.local, "request", None):
        return False
    return " Line/" in (frappe.get_request_header("User-Agent") or "")


ENTER_METHOD = "pearnly_core.account.line_login.enter"


def entry_url(path: str) -> str:
    """发到 LINE 里的网页端链接一律经这里：LINE 内置浏览器里未登录时直接走 LINE 登录。"""
    return (frappe.utils.get_url().rstrip("/") + "/api/method/" + ENTER_METHOD + "?"
            + urlencode({"to": _local_path(path)}))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def enter(to=None):
    """LINE 卡片与菜单的链接入口：已登录直接去；LINE 里未登录走 LINE 登录；别处照常登录。"""
    target = _local_path(to)
    alias = enabled_alias()
    if frappe.session.user != "Guest" or not alias or not in_line_app():
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = target
        return
    from pearnly_core import identity

    identity.begin_login_via(target, idp_hint=alias)


# ── 登录核对（identity.callback 调用） ────────────────────────────────────────


def is_line_login(claims: dict) -> bool:
    return bool(claims.get(contract.CLAIM_IDP))


def resolve_line_login(claims: dict) -> tuple:
    """LINE 登录按本站生效的绑定认人，返回 (本地账号, 登录来源)；不通过抛 PermissionError。

    身份服务认出的是这个 LINE 的专用身份，不是本站账号，所以这里不按 subject 找人。
    """
    source = claims.get(contract.CLAIM_IDP)
    line_user_id = claims.get(contract.CLAIM_LINE_USER)
    alias = enabled_alias()
    user = ""
    if alias and source == alias and contract.valid_line_user_id(line_user_id):
        user = frappe.db.get_value(BINDING, {"line_user_id": line_user_id, "status": "active"}, "user") or ""
    if not user or active_line_of(user) != line_user_id:
        frappe.throw(_("This LINE account is not bound to this account on this site. Sign in with your password."),
                     frappe.PermissionError)
    return user, {"identity_provider": source, "line_user_id": line_user_id}


def active_line_of(user: str) -> str:
    """该账号在本站生效的 LINE（账号停用视为没有）。"""
    if not user or not frappe.db.get_value("User", user, "enabled"):
        return ""
    return frappe.db.get_value(BINDING, {"user": user, "status": "active"}, "line_user_id") or ""


# ── 会话作废 ────────────────────────────────────────────────────────────────


def revoke_stale_line_sessions(users) -> int:
    """作废这些账号里"登录所用 LINE 已不是其生效绑定"的会话。密码登录的会话不动。"""
    from frappe.sessions import delete_session

    revoked = 0
    for user in {u for u in users if u}:
        keep = active_line_of(user)
        for row in frappe.get_all(SESSION, filters={"user": user, "status": "active",
                                                   "identity_provider": ("is", "set")},
                                  fields=["name", "session_id", "line_user_id"]):
            if keep and row.line_user_id == keep:
                continue
            delete_session(row.session_id, user=user, reason="LINE binding changed")
            frappe.db.set_value(SESSION, row.name, {"status": "revoked", "revoked_at": now()},
                                update_modified=False)
            revoked += 1
    return revoked


# ── 关联同步 ────────────────────────────────────────────────────────────────


def binding_changed(line_user_id):
    """`pearnly_line_binding_changed` 钩子：先当场作废失效会话，再排队同步身份服务。"""
    users = set(frappe.get_all(BINDING, filters={"line_user_id": line_user_id}, pluck="user"))
    # 同一个 LINE 之前登录过的别的账号（改绑）也要作废。
    users |= set(frappe.get_all(SESSION, filters={"line_user_id": line_user_id, "status": "active"},
                                pluck="user"))
    revoke_stale_line_sessions(users)
    if not enabled_alias():
        return
    for user in users:
        frappe.db.set_value(BINDING, {"user": user, "status": "active"},
                            {"login_link": "pending", "login_link_at": now()}, update_modified=False)
        frappe.enqueue("pearnly_core.account.line_login.sync_user", queue="short", user=user,
                       enqueue_after_commit=True, job_id=f"pearnly-line-login:{frappe.local.site}:{user}",
                       deduplicate=True)


def subject_of(user: str) -> str:
    from pearnly_core import identity

    return frappe.db.get_value(IDENTITY, {"user": user, "issuer": identity.issuer(), "status": "active"},
                               "subject") or ""


def sync_user(user):
    """把该账号在身份服务里的 LINE 关联对齐到本站当前绑定（幂等）。"""
    from pearnly_core.account import member_client

    revoke_stale_line_sessions([user])
    if not enabled_alias():
        return
    active = active_line_of(user)
    subject = subject_of(user)
    rows = frappe.get_all(BINDING, filters={"user": user}, fields=["name", "line_user_id", "status"])
    try:
        if active and subject:
            member_client.link_line(subject, active, login_channel())
        elif subject:
            member_client.unlink_line(subject, login_channel())
    except Exception:
        # 不记请求与异常细节（含身份标识）；对账会重试。
        frappe.db.rollback()
        for row in rows:
            if row.status == "active":
                frappe.db.set_value(BINDING, row.name, {"login_link": "failed", "login_link_at": now()},
                                    update_modified=False)
        frappe.db.commit()
        return
    for row in rows:
        state = "linked" if (row.status == "active" and row.line_user_id == active and subject) else "unlinked"
        frappe.db.set_value(BINDING, row.name, {"login_link": state, "login_link_at": now()},
                            update_modified=False)
    frappe.db.commit()


def _needs_sync(row) -> bool:
    wanted = "linked" if row.status == "active" and row.enabled else "unlinked"
    if wanted == "unlinked" and not row.login_link:
        return False  # 从未关联过，无需摘除
    return row.login_link != wanted


def reconcile():
    """定时对账：上线时的已有绑定、之前失败的、账号被停用的，都在这里补齐。"""
    if not enabled_alias():
        return
    rows = frappe.db.sql(
        """select b.user, b.status, b.login_link, u.enabled
           from `tabPearnly LINE Binding` b join `tabUser` u on u.name = b.user""", as_dict=True)
    for user in sorted({row.user for row in rows if _needs_sync(row)}):
        sync_user(user)


# ── 会话结束（被拒的 LINE 登录要把身份服务那边的会话也结束掉） ─────────────────


def end_session_url(id_token: str) -> str:
    from pearnly_core import identity

    return identity.end_session_endpoint() + "?" + urlencode({
        "client_id": frappe.conf.get("pearnly_oidc_client_id"),
        "id_token_hint": id_token,
        "post_logout_redirect_uri": str(frappe.conf.get("host_name") or "").rstrip("/") + "/",
    })
