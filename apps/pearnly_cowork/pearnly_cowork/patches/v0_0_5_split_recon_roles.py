"""对账中心拆成三个可单独勾选的角色（用户 2026-09-27）：银行对账 / 收入对账 / 销项税报告核查。

原来一个 `Cowork Bank Reconciliation` 管三种对账；拆开后银行对账沿用它，收入对账、销项税核查各用新角色
（随各自 DocType 的权限行生成）。已经勾了对账中心的人三种都保留：补上两个新角色，谁也不因升级丢权限。
重复执行不重复加（`add_roles` 跳过已有角色）。
"""
import frappe

OLD = "Cowork Bank Reconciliation"
NEW = ("Cowork Income Reconciliation", "Cowork VAT Check")


def execute():
    from pearnly_core.provisioning import _ensure_role

    for role in NEW:
        _ensure_role(role)
    users = set(frappe.get_all("Has Role", filters={"role": OLD, "parenttype": "User"}, pluck="parent"))
    for name in sorted(users - {"Administrator", "Guest"}):
        user = frappe.get_doc("User", name)
        user.flags.ignore_permissions = True
        user.add_roles(*NEW)
        frappe.clear_cache(user=name)
