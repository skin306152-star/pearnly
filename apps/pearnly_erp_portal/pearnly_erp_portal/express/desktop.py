"""ERP 接 CC 桌面版共用机器通道的声明实现（hooks `pearnly_cc_desktop`，ADR-025）。

- 谁能把自己的电脑连上 ERP：在职员工、有开采购或销售单的权限（CC 窗口里用 ERP 账号登录 ERP 站点时，对话页登记这台电脑）。
  电脑以登记人的身份领单；写谁的单按「在这台电脑上登录过 CC 的人」（用户 10-16「谁的活谁的电脑做」）。
- 写账有了新状态：写回 ERP 单据（`push.job_changed`）。
"""
import frappe

from pearnly_erp_portal.access import guard


def allowed(user) -> bool:
    if not user or user in ("Guest", "Administrator"):
        return False
    if frappe.db.get_value("User", user, "user_type") != "System User":
        return False
    return guard.has("sales.write", user=user) or guard.has("purchase.write", user=user)


def job_changed(view):
    from pearnly_erp_portal.express import push

    push.job_changed(view)
