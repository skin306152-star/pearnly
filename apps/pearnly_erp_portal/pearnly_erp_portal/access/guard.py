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
    if _is_superuser(user):
        return True
    from pearnly_core.shell import entitlements
    from pearnly_erp_portal.access import menu

    features = set(entitlements())
    held = current_roles(user)
    disabled = {menu.role(key) for key, spec in menu.CATALOG.items() if spec[0] and spec[0] not in features}
    return rules.can(held - disabled, capability)


def require(capability: str, user=None) -> None:
    """没有能力就拒绝；未登录与无权限分别报错，便于前端区分。"""
    session_user = frappe.session.user
    if not session_user or session_user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    if not has(capability, user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    _require_pos_for_cashier(capability, user)


def _require_pos_for_cashier(capability: str, user=None) -> None:
    """收银台是**公司级**开关（用户 2026-09-24："不勾选就不显示 pos、记录、交接班"）。

    关掉之后，这家公司的收银**业务**一律不放行 —— 判据在 `masters.company.require_pos`，
    这里挑的是"每一个收银入口都会经过的那一道"（收银域的服务端入口都先 `guard.require("cashier.*")`），
    所以不用挨个接口去加。惰性 import：`masters.company` 反过来依赖本模块。
    """
    if not str(capability or "").startswith(("cashier.", "receipts.", "shifts.")):
        return
    from pearnly_erp_portal.masters import company as companies

    companies.require_pos(user=user)


def scope_filters(user=None) -> dict:
    """列表与汇总的取数范围：管理员看全部，员工只看本人录入。"""
    if _is_superuser(user) or rules.record_scope(current_roles(user)) == rules.ALL_RECORDS:
        return {}
    return {"owner": user or frappe.session.user}
