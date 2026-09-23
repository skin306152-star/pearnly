"""员工账号与岗位的服务端实现。

所有判断在服务端完成：谁可以管团队、能发哪些角色、能改谁。界面只负责展示。

为什么写用户时显式 `ignore_permissions`：核心给 `User` 注册的
`pearnly_core.desk.permissions.user_permission` 规定——产品站点上非超管只能访问
**自己**那条用户记录（个人设置自助，花名册由各自产品管）。该钩子
在文档级检查里对任何"别人的 User"返回 False，于是租户管理员连新增员工都会被框架挡下。
产品自己的判据因此放在本模块：只有带 `team.manage` 的账号（ERP Admin）能进来，员工
只能拿到本站点已开通的功能角色，老板本人与平台账号不可改。这与开通侧
`ensure_local_user` 的做法一致（那里同样用 `ignore_permissions` 建账号）。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard, rules

NOT_PERMITTED = "Not permitted"
ALREADY_EXISTS = "Already exists"
ROLE_REQUIRED = "Choose at least one job role"
EMAIL_REQUIRED = "Enter a valid email"
FULL_NAME_REQUIRED = "Enter the user name"
PASSWORD_TOO_SHORT = "The password must be at least {0} characters"

# 密码最短位数：复用框架那条闸（`frappe.core.doctype.user.user` 的强度检查），
# 这里只在下发前先挡一道，别让"保存成功但账号登不上"这种事发生。
MIN_PASSWORD = 8

# 岗位的中文/泰文/日文说法：术语表是唯一来源，键写在 ERP 的术语表里。
ROLE_KEYS = {
    "ERP Products": "team.role.products",
    "ERP Stock": "team.role.stock",
    "ERP Purchase": "team.role.purchase",
    "ERP Sales": "team.role.sales",
    "ERP Cashier": "team.role.cashier",
}


def role_catalog() -> list[dict]:
    """**能发给员工的岗位**（= 本租户买了、站点上真有的功能角色）+ 每个的中文说法。

    这不是一张配置表：名单来自 `rules.FEATURE_ROLES`，只保留本站点实际存在的角色。
    没开通的功能没有角色，也就不会出现在勾选框里。
    """
    from pearnly_core import browser_text

    return [{"name": role, "label": browser_text.label(ROLE_KEYS.get(role, role))}
            for role in assignable_roles()]


def _clean_password(password) -> str:
    """下发/重置密码前的闸：太短直接拒，别让账号建出来却登不上。"""
    value = str(password or "")
    if len(value) < MIN_PASSWORD:
        frappe.throw(_(PASSWORD_TOO_SHORT).format(MIN_PASSWORD))
    return value


def _identity_configured() -> bool:
    return bool(frappe.conf.get("pearnly_oidc_issuer"))


def _local_password_allowed() -> bool:
    """这个站点要不要写本地密码。

    接了统一身份的业务站点**不写**：业务用户只从身份服务登录，本地密码等于旁路
    （本仓合同，见 `pearnly_core.provisioning.ensure_local_user` 的第 2 条）。
    没有身份服务的站点（实验室 / 离线演示）或显式开了 `pearnly_native_account` 的
    站点才写 —— 否则账号建出来根本登不上。
    """
    return not _identity_configured() or bool(frappe.conf.get("pearnly_native_account"))


def _identity_subject(email: str, full_name: str, secret: str) -> str:
    """在身份服务里建号（或改密码），返回 subject。**建不出来就抛错**，不静默降级。

    静默降级的后果最坏：账号在本地建好了、员工却永远登不上，而且没人知道为什么。
    """
    from pearnly_core import keycloak_admin

    subject = keycloak_admin.find_user_id_by_email(email)
    if subject:
        # 已经有身份：这次当成"重置密码"，不再建第二个身份。
        keycloak_admin.set_password(subject, secret)
        return subject
    return keycloak_admin.create_user(email, first_name=full_name, password=secret)


def _bound_subject(user: str) -> str:
    return frappe.db.get_value("Pearnly Identity", {"user": user, "status": "active"}, "subject")


def present_roles() -> list[str]:
    """本站点上真实存在的 ERP 功能角色（= 这个租户买了的岗位）。"""
    return frappe.get_all(
        "Role", filters={"name": ["in", list(rules.FEATURE_ROLES.values())]}, pluck="name"
    )


def assignable_roles() -> tuple[str, ...]:
    return rules.assignable_roles(present_roles())


def _clean_roles(roles) -> list[str]:
    if isinstance(roles, str):
        roles = frappe.parse_json(roles)
    if not isinstance(roles, (list, tuple)):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    allowed = set(assignable_roles())
    if not roles:
        # 旧线上同样的口径：员工必须至少有一个功能，不能发一个空岗位。
        frappe.throw(_(ROLE_REQUIRED))
    cleaned: list[str] = []
    for role in roles:
        role = str(role).strip()
        # 平台角色、管理员角色，以及本站点没有的角色，一律拒绝。
        if role not in allowed:
            frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
        if role not in cleaned:
            cleaned.append(role)
    return cleaned


def _employee(email: str):
    """取一个**可以管理**的员工；老板本人与平台账号不在此列。"""
    if not email or not frappe.db.exists("User", email):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    user = frappe.get_doc("User", email)
    if set(rules.ALL_RECORDS_ROLES) & {r.role for r in user.roles} or email == "Administrator":
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    return user


def list_members() -> list[dict]:
    guard.require("team.manage")
    roles = set(assignable_roles())
    if not roles:
        return []
    emails = sorted(set(frappe.get_all(
        "Has Role", filters={"role": ["in", list(roles)], "parenttype": "User"}, pluck="parent"
    )))
    members = []
    for email in emails:
        if email == "Administrator":
            continue
        user = frappe.get_doc("User", email)
        members.append({
            # `name` 是给列表框架用的行标识，**必须有**：框架在 `BaseList.prepare_data()`
            # 末尾有一句 `this.data = this.data.uniqBy(d => d.name)`，少了这个字段，
            # 整页的人会被当成同一行合并成一行（实测：8 个人只画出 1 行、计数 1 / 8）。
            # 值就是邮箱 —— 与框架里 `User.name` 同一个值，勾选与打开行也按它找行。
            "name": email,
            "email": email,
            "full_name": user.full_name or email,
            "enabled": bool(user.enabled),
            "roles": sorted(r.role for r in user.roles if r.role in roles),
            "last_login": user.last_login,
            "is_admin": bool(set(rules.ALL_RECORDS_ROLES) & {r.role for r in user.roles}),
        })
    return members


def add_member(email: str, full_name: str = "", roles=None, password=None) -> dict:
    """新增员工：姓名 + 邮箱（就是登录名）+ 初始密码 + 勾选的岗位。

    账号本身是**框架的 `User`**（复用 Frappe/ERPNext 的用户与权限体系），我们只做三件事：
    建账号、发"本站点确实存在"的功能角色、给一个初始密码让员工能马上登录。
    平台角色（System Manager）与产品管理员角色（ERP Admin）永不外发（`_clean_roles`）。
    """
    guard.require("team.manage")
    email = (email or "").strip().lower()
    if "@" not in email:
        frappe.throw(_(EMAIL_REQUIRED))
    if not str(full_name or "").strip():
        frappe.throw(_(FULL_NAME_REQUIRED))
    if frappe.db.exists("User", email):
        frappe.throw(_(ALREADY_EXISTS))
    wanted = _clean_roles(roles)
    secret = _clean_password(password)
    # 统一身份里的号先建好：建不出来就整件事失败，不留"本地有号、人却登不上"的坑。
    subject = _identity_subject(email, str(full_name).strip(), secret) if _identity_configured() else None
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": str(full_name).strip(),
        "send_welcome_email": 0,
        "user_type": "System User",
        "roles": [{"role": rules.BASE_ROLE}, *({"role": role} for role in wanted)],
    })
    # 初始密码由老板当面给员工，不走"邀请邮件"那条路（本租户不接邮件通道）。
    if _local_password_allowed():
        user.new_password = secret
    user.insert(ignore_permissions=True)
    if subject:
        from pearnly_core import identity

        identity.bind_identity(user=user.name, subject=subject)
    frappe.db.commit()
    return {"email": user.name, "roles": sorted(wanted)}


def set_member_password(email: str, password=None) -> dict:
    """重置员工密码（员工忘了密码时老板可以重置；旧密码不需要知道）。

    接了身份服务的站点改的是**身份服务里**那个密码；没有身份服务的站点改本地密码。
    """
    guard.require("team.manage")
    user = _employee(email)
    secret = _clean_password(password)
    if _identity_configured():
        from pearnly_core import keycloak_admin

        subject = _bound_subject(user.name) or keycloak_admin.find_user_id_by_email(user.name)
        if not subject:
            frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
        keycloak_admin.set_password(subject, secret)
    if _local_password_allowed():
        user.new_password = secret
        user.save(ignore_permissions=True)
    frappe.db.commit()
    return {"email": user.name}


def set_member_roles(email: str, roles=None) -> dict:
    """改岗位：只动 ERP 功能角色，其它角色（如 Desk User）保持不动。

    这里**只有岗位**可改：用户名（发号时填的那个名字，就是登录用的身份）和邮箱是
    开户时定死的，租户里谁都不改（用户 2026-09-23："后台管理发号后，用户名和邮箱是
    绑死这个租户的，唯一关系，用户只可以自己改密码，员工也是一样"）。
    """
    guard.require("team.manage")
    wanted = _clean_roles(roles)
    user = _employee(email)
    known = set(rules.FEATURE_ROLES.values())
    # 只动子表、最后自己 save（`ignore_permissions`）：框架的 `add_roles/remove_roles`
    # 会各自 `save()` 一次、且不带 `ignore_permissions`，在租户站点上会被产品自己的
    # User 权限钩子挡下（非超管只能改自己那条）。直接给 `user.roles` 赋一串 dict 也不行，
    # Frappe 16 落库时拿到 dict 会炸。功能角色全摘掉再按勾选补回来，Desk User 不动。
    for row in list(user.roles):
        if row.role in known:
            user.roles.remove(row)
    # `held` 必须在**摘掉之后**再看：先记再摘的话，"已有岗位"里含着刚被摘掉的那些，
    # 于是"原样保存同一批岗位"会把岗位整个点没（2026-09-23 实测：编辑一个采购账号、
    # 什么都不改直接保存 → 采购岗位消失，这个账号也从员工名单里消失）。
    held = {row.role for row in user.roles}
    for role in wanted:
        if role not in held:
            user.append("roles", {"role": role})
    user.save(ignore_permissions=True)
    frappe.db.commit()
    # 角色刚变，清掉该账号的会话缓存；否则它当前会话里仍按旧岗位判权限。
    frappe.clear_cache(user=user.name)
    return {"email": user.name, "roles": sorted(wanted)}


def set_member_active(email: str, enabled) -> dict:
    """停用/启用员工。停用后旧会话再次提交同样会被服务端拒绝。"""
    guard.require("team.manage")
    user = _employee(email)
    user.enabled = 1 if enabled in (True, 1, "1", "true", "True") else 0
    user.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=user.name)
    return {"email": user.name, "enabled": bool(user.enabled)}
