# -*- coding: utf-8 -*-
"""控制面的本地自举：建登录账号、配开户目标。

控制面**只在本机跑、只自己用、永远不上服务器**，所以登录不接统一身份，
就用一个本地账号（earn / earn）。业务站点的统一身份不受影响。

放在 App 里而不是写在脚本里，是因为 `bench console` 会吞多行脚本（踩过），
单行调用一个函数才可靠。
"""

import frappe
from frappe import _

# 登录密码按要求写死成 earn。
#
# 登录名只能是**邮箱**：Frappe 按邮箱查账号，写 `earn` 会被
# `InvalidEmailAddressError` 挡下，而且这一版不支持用用户名登录
# （试过：设了 username=earn，用 usr=earn 仍然 401）。
# 所以登录名是 earn@pearnly.local，密码 earn。
LOCAL_ADMIN = "earn@pearnly.local"
LOCAL_USERNAME = "earn"
LOCAL_PASSWORD = "earn"


def ensure_local_admin(email: str = LOCAL_ADMIN, password: str = LOCAL_PASSWORD) -> str:
    """建或重置本地登录账号。返回 CREATED / UPDATED。

    会**在本站点关掉密码强度策略**：控制面只在本机跑、只自己用，密码按用户要求
    就是 earn。开着强度策略的话，这种密码会被 "Common words are easy to guess" 挡下。
    业务站点不受影响（各自的 System Settings 是分开的）。
    """
    _relax_password_policy()

    if not frappe.db.exists("User", email):
        doc = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "username": LOCAL_USERNAME,
            "first_name": LOCAL_USERNAME,
            "enabled": 1,
            "send_welcome_email": 0,
            "new_password": password,
        })
        doc.insert(ignore_permissions=True)
        doc.add_roles("System Manager")
        result = "CREATED"
    else:
        from frappe.utils.password import update_password

        update_password(email, password)
        frappe.db.set_value("User", email, "enabled", 1)
        result = "UPDATED"
    frappe.db.commit()
    return result


def _relax_password_policy() -> None:
    try:
        settings = frappe.get_single("System Settings")
        if settings.enable_password_policy:
            settings.enable_password_policy = 0
            settings.save(ignore_permissions=True)
            frappe.db.commit()
    except Exception:
        # 关不掉也不该让建号流程整个失败；下面建号时若被策略挡下会直接报错
        pass


def ensure_target(
    target_name: str = "本地样板",
    base_url: str = "http://keycloak.localhost:8105",
    realm: str = "pearnly-business",
    admin_client_id: str = "pearnly-admin",
    admin_client_secret: str | None = None,
    is_default: int = 1,
    notes: str | None = None,
) -> str:
    """配一个开户目标。

    **生产目标等服务器就绪后加一条即可**：把 base_url 换成服务器上的身份服务地址，
    代码不用改。
    """
    if not admin_client_secret:
        frappe.throw(_("必须给管理客户端密钥（环境变量 PEARNLY_BIZ_ADMIN_SECRET）"))

    existing = frappe.db.get_value("Pearnly Target", {"target_name": target_name}, "name")
    if existing:
        doc = frappe.get_doc("Pearnly Target", existing)
    else:
        doc = frappe.get_doc({"doctype": "Pearnly Target", "target_name": target_name})
    doc.is_default = is_default
    doc.base_url = base_url
    doc.realm = realm
    doc.admin_client_id = admin_client_id
    doc.admin_client_secret = admin_client_secret
    doc.notes = notes or doc.notes
    if existing:
        doc.save(ignore_permissions=True)
        result = "UPDATED"
    else:
        doc.insert(ignore_permissions=True)
        result = "CREATED"
    frappe.db.commit()
    return result
