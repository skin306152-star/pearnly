"""服务端权限判据。

业务校验与权限一律在服务端完成：菜单显隐、前端禁用态只是减负，不是授权。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import rules


def _is_superuser(user=None) -> bool:
    """框架超级用户。开通、迁移与测试都走它，但产品界面不会发给客户。"""
    return (user or frappe.session.user) == "Administrator"


def current_roles(user=None) -> set:
    if _is_superuser(user):
        return {rules.SYSTEM_ADMIN_ROLE, rules.ADMIN_ROLE}
    return set(frappe.get_roles(user or frappe.session.user))


def has(capability: str, user=None) -> bool:
    return _is_superuser(user) or rules.can(current_roles(user), capability)


def require(capability: str, user=None) -> None:
    """没有能力就拒绝；未登录与无权限分别报错，便于前端区分。"""
    session_user = frappe.session.user
    if not session_user or session_user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    if not has(capability, user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def scope_filters(user=None) -> dict:
    """列表与汇总的取数范围：管理员看全部，员工只看本人录入。"""
    if _is_superuser(user) or rules.record_scope(current_roles(user)) == rules.ALL_RECORDS:
        return {}
    return {"owner": user or frappe.session.user}
