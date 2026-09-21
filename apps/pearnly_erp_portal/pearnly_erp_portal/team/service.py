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
            "email": email,
            "full_name": user.full_name or email,
            "enabled": bool(user.enabled),
            "roles": sorted(r.role for r in user.roles if r.role in roles),
            "last_login": user.last_login,
            "is_admin": bool(set(rules.ALL_RECORDS_ROLES) & {r.role for r in user.roles}),
        })
    return members


def add_member(email: str, full_name: str = "", roles=None) -> dict:
    """新增员工：同一站点账号体系，不发任何平台角色。"""
    guard.require("team.manage")
    email = (email or "").strip().lower()
    if "@" not in email:
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    if frappe.db.exists("User", email):
        frappe.throw(_(ALREADY_EXISTS))
    wanted = _clean_roles(roles)
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": (full_name or email.split("@")[0]).strip(),
        "send_welcome_email": 0,
        "roles": [{"role": rules.BASE_ROLE}, *({"role": role} for role in wanted)],
    })
    user.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"email": user.name, "roles": sorted(wanted)}


def set_member_roles(email: str, roles=None) -> dict:
    """改岗位：只动 ERP 功能角色，其它角色（如 Desk User）保持不动。"""
    guard.require("team.manage")
    wanted = _clean_roles(roles)
    user = _employee(email)
    known = set(rules.FEATURE_ROLES.values())
    kept = [{"role": r.role} for r in user.roles if r.role not in known]
    user.roles = kept + [{"role": role} for role in wanted]
    user.save(ignore_permissions=True)
    frappe.db.commit()
    return {"email": user.name, "roles": sorted(wanted)}


def set_member_active(email: str, enabled) -> dict:
    """停用/启用员工。停用后旧会话再次提交同样会被服务端拒绝。"""
    guard.require("team.manage")
    user = _employee(email)
    user.enabled = 1 if enabled in (True, 1, "1", "true", "True") else 0
    user.save(ignore_permissions=True)
    frappe.db.commit()
    return {"email": user.name, "enabled": bool(user.enabled)}
