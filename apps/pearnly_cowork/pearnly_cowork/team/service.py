"""Cowork 用户账号与功能权限的服务端实现（照 ERP `pearnly_erp_portal.team.service`）。

所有判断在服务端：只有 Cowork Admin 能管团队；只发本租户买了、站点上真有的功能角色；
管理员本人与平台账号不可改。账号就是框架的 `User`，权限就是 `Role`，不另存一份。

与 ERP 的差别：
- Cowork 不做公司隔离（用户 2026-09-26），新用户不分配公司，只勾功能权限。
- 勾选项是 `access.menu.CATALOG`（录入 / 三种对账 / 盘点 / 工作协调 / 个人信息 / LINE）。

写 `User` 时显式 `ignore_permissions`：核心的 User 权限钩子只让非超管访问自己那条，
判据因此放在本模块（与 ERP、开通侧 `ensure_local_user` 同一做法）。
"""

import re

import frappe
from frappe import _

from pearnly_cowork.access import menu

NOT_PERMITTED = "Not permitted"
ALREADY_EXISTS = "Already exists"
ROLE_REQUIRED = "Choose at least one job role"
EMAIL_REQUIRED = "Enter a valid email"
USERNAME_REQUIRED = "Enter the user name"
USERNAME_CHARSET = "Username can only have letters and numbers"
USERNAME_TAKEN = "Username {0} already exists"
USERNAME_RE = re.compile(r"^[A-Za-z0-9]+$")
ALL_RECORDS_ROLES = (menu.ADMIN_ROLE, "System Manager")


def _is_admin(user=None) -> bool:
    user = user or frappe.session.user
    return user == "Administrator" or bool(set(ALL_RECORDS_ROLES) & set(frappe.get_roles(user)))


def require_admin():
    if frappe.conf.get("pearnly_product") != "cowork" or not _is_admin():
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)


def assignable_roles() -> tuple:
    from pearnly_core.shell import entitlements

    present = frappe.get_all("Role", filters={"name": ["in", list(menu.roles())]}, pluck="name")
    return menu.offered(entitlements(), present)


def role_catalog() -> list:
    """勾选框：能发的角色 + 显示名 + 分组。显示名取产品导航声明里的页面名，不再写一份。"""
    from pearnly_core.desk.navigation import _declarations

    spec = _declarations().get("cowork") or {}
    labels = {route: label for label, route, _icon, _feature in spec.get("pages", [])}
    labels["user"] = spec.get("profile_label", "My Profile")
    groups = {route: label for label, routes in spec.get("permission_groups", []) for route in routes}
    routes = {role: route for _feature, role, route in menu.CATALOG.values()}
    catalog = []
    for role in assignable_roles():
        if role == menu.LINE_ROLE:
            catalog.append({"name": role, "label": "LINE", "group": _(groups.get("user", "User Settings"))})
            continue
        route = routes[role]
        catalog.append({"name": role, "label": _(labels.get(route) or role),
                        "group": _(groups.get(route, "General"))})
    return catalog


def _clean_username(username, *, exclude=None) -> str:
    value = str(username or "").strip()
    if not value:
        frappe.throw(_(USERNAME_REQUIRED))
    if not USERNAME_RE.fullmatch(value):
        frappe.throw(_(USERNAME_CHARSET))
    taken = frappe.db.sql(
        "select name from `tabUser` where lower(username) = lower(%s) and name != %s limit 1",
        (value, exclude or ""), pluck=True)
    if taken:
        frappe.throw(_(USERNAME_TAKEN).format(value))
    return value


def _clean_password(password) -> str:
    # 密码不设规则（与 ERP 同一口径）；只挡空值——空密码的账号谁都进不去。
    value = str(password or "")
    if not value:
        frappe.throw(_("Invalid value"))
    return value


def _identity_configured() -> bool:
    return bool(frappe.conf.get("pearnly_oidc_issuer"))


def _local_password_allowed() -> bool:
    # 接了统一身份的站点不写本地密码（本地密码等于旁路）。
    return not _identity_configured() and not frappe.conf.get("pearnly_native_account")


def _issuer() -> str:
    return str(frappe.conf.get("pearnly_oidc_issuer") or "").rstrip("/")


def _bound_subject(user: str) -> str:
    return frappe.db.get_value("Pearnly Identity", {"user": user, "issuer": _issuer(), "status": "active"},
                               "subject")


def _bind_member(user: str, subject: str):
    # 只在 team.manage 通过、且私有建号接口返回之后到这里；不接管已有身份。
    if frappe.db.exists("Pearnly Identity", {"issuer": _issuer(), "subject": subject}):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    frappe.get_doc({"doctype": "Pearnly Identity", "issuer": _issuer(), "subject": subject,
                    "user": user, "status": "active", "invited_by": frappe.session.user}).insert(
        ignore_permissions=True)


def _clean_roles(roles, *, existing=()) -> list:
    if isinstance(roles, str):
        roles = frappe.parse_json(roles)
    if not isinstance(roles, (list, tuple)):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    allowed = set(assignable_roles())
    # 功能被关掉时已发的角色不能被"保存一次"悄悄抹掉。
    retained = set(existing) - allowed
    if not roles and not retained:
        frappe.throw(_(ROLE_REQUIRED))
    cleaned = sorted(retained)
    for role in roles:
        role = str(role).strip()
        if role not in allowed and role not in retained:
            frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
        if role not in cleaned:
            cleaned.append(role)
    return cleaned


def _manageable(user) -> bool:
    held = {row.role for row in user.roles}
    return (user.name not in ("Administrator", "Guest") and bool(held & set(menu.roles()))
            and not held & set(ALL_RECORDS_ROLES))


def _member(email: str):
    if not email or not frappe.db.exists("User", email):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    user = frappe.get_doc("User", email)
    if not _manageable(user):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    return user


def _line_bindings(emails) -> dict:
    rows = frappe.get_all("Pearnly LINE Binding", filters={"user": ["in", list(emails) or [""]],
                          "status": "active"}, fields=["user", "display_name", "bound_at"])
    return {row.user: row for row in rows}


def list_members() -> list:
    require_admin()
    known = set(menu.roles())
    emails = sorted(set(frappe.get_all("Has Role", filters={
        "role": ["in", [*known, menu.ADMIN_ROLE]], "parenttype": "User"}, pluck="parent")))
    bindings = _line_bindings(emails)
    members = []
    for email in emails:
        if email == "Administrator":
            continue
        user = frappe.get_doc("User", email)
        held = {row.role for row in user.roles}
        admin = bool(held & set(ALL_RECORDS_ROLES))
        members.append({
            # `name` 是列表框架的行标识（`uniqBy(d => d.name)`），缺了整页会并成一行。
            "name": email,
            "email": email,
            "username": user.username or "",
            "display": user.username or email,
            "enabled": bool(user.enabled),
            "roles": sorted(held & known),
            "last_login": user.last_login,
            "is_admin": admin,
            # 管理员本人：全部功能都有，这里只看不改（页面画成全勾、灰色）。
            "locked": not _manageable(user),
            "line": admin or menu.LINE_ROLE in held,
            "line_name": (bindings.get(email) or {}).get("display_name") or "",
            "line_bound_at": (bindings.get(email) or {}).get("bound_at"),
        })
    return members


def add_member(username: str = "", email: str = "", roles=None, password=None) -> dict:
    """添加用户：用户名（英文数字，可登录）+ 邮箱 + 初始密码 + 勾选的功能权限。"""
    require_admin()
    email = (email or "").strip().lower()
    if "@" not in email:
        frappe.throw(_(EMAIL_REQUIRED))
    if frappe.db.exists("User", email):
        frappe.throw(_(ALREADY_EXISTS))
    username = _clean_username(username)
    wanted = _clean_roles(roles)
    secret = _clean_password(password)
    if frappe.conf.get("pearnly_native_account") and not _identity_configured():
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    subject = None
    if _identity_configured():
        # 统一身份里的号先建：建不出来就整件事失败，不留"本地有号、人却登不上"的坑。
        from pearnly_core.account import member_client

        subject = member_client.create(email, username, secret)
    user = frappe.get_doc({
        "doctype": "User", "email": email, "username": username, "first_name": username,
        "send_welcome_email": 0, "user_type": "System User",
        "roles": [{"role": menu.BASE_ROLE}, *({"role": role} for role in wanted)],
    })
    if _local_password_allowed():
        user.new_password = secret
    user.insert(ignore_permissions=True)
    if subject:
        _bind_member(user.name, subject)
    frappe.db.commit()
    return {"email": user.name, "username": user.username, "roles": sorted(wanted)}


def set_member_roles(email: str, roles=None) -> dict:
    """改功能权限：只动目录里的角色，其它角色（如 Desk User）不动。"""
    require_admin()
    user = _member(email)
    held_before = {row.role for row in user.roles}
    known = set(menu.roles())
    wanted = _clean_roles(roles, existing=held_before & known)
    for row in list(user.roles):
        if row.role in known:
            user.roles.remove(row)
    held = {row.role for row in user.roles}
    for role in wanted:
        if role not in held:
            user.append("roles", {"role": role})
    user.save(ignore_permissions=True)
    if menu.LINE_ROLE in held_before and menu.LINE_ROLE not in wanted:
        # 取消 LINE = 立即解绑，这个 LINE 再发消息按未绑定处理。
        from pearnly_core import line_binding

        for line_user_id in frappe.get_all("Pearnly LINE Binding", filters={"user": user.name, "status": "active"},
                                           pluck="line_user_id"):
            line_binding.unbind(line_user_id)
    frappe.db.commit()
    frappe.clear_cache(user=user.name)
    return {"email": user.name, "roles": sorted(wanted)}


def set_member_password(email: str, password=None) -> dict:
    require_admin()
    user = _member(email)
    secret = _clean_password(password)
    if _identity_configured():
        from pearnly_core.account import member_client

        if not _bound_subject(user.name):
            frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
        member_client.reset_password(user.name, secret)
    elif not _local_password_allowed():
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    if _local_password_allowed():
        user.new_password = secret
        user.save(ignore_permissions=True)
    frappe.db.commit()
    return {"email": user.name}


def set_member_active(email: str, enabled) -> dict:
    require_admin()
    user = _member(email)
    user.enabled = 1 if enabled in (True, 1, "1", "true", "True") else 0
    user.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=user.name)
    return {"email": user.name, "enabled": bool(user.enabled)}
