"""ERP 接 CC 桌面版共用机器通道的声明实现（hooks `pearnly_cc_desktop`，ADR-025）。

- 谁能把自己的电脑连上 ERP：在职员工、有开采购或销售单的权限（CC 窗口里用 ERP 账号登录 ERP 站点时，对话页登记这台电脑）。
  电脑以登记人的身份领单；写谁的单按「在这台电脑上登录过 CC 的人」（用户 10-16「谁的活谁的电脑做」）。
- 写账有了新状态：写回 ERP 单据（`push.job_changed`）；收款记录交给 `collection.push.job_changed`。
"""
import frappe

from pearnly_erp_portal.access import guard


def allowed(user) -> bool:
    if not user or user in ("Guest", "Administrator"):
        return False
    if frappe.db.get_value("User", user, "user_type") != "System User":
        return False
    return guard.has("sales.write", user=user) or guard.has("purchase.write", user=user)


def still_wanted(view) -> bool:
    """领单前兜底（共用层 ``still_wanted``）：收款记录照旧；单据交给 `push.still_wanted`。"""
    if view.get("source_doctype") == "ERP Collection":
        return True
    from pearnly_erp_portal.express import push

    return push.still_wanted(view)


def job_changed(view):
    if view.get("source_doctype") == "ERP Collection":
        from pearnly_erp_portal.collection import push as collection_push

        collection_push.job_changed(view)  # 收款（RE）：记回收款记录
        return
    from pearnly_erp_portal.express import push

    push.job_changed(view)
