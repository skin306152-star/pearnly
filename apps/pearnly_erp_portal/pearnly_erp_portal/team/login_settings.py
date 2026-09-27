"""用户名当登录名：框架那两位开关（口径见补丁 `patches/v1_9_username_login.py`）。

接了统一身份的站点不动（登录在身份服务，用户名登录由它负责）；只管本地登录的站点。
新装站点不会真跑补丁（框架把补丁直接记为已执行），所以安装与每次迁移后也调这里 ——
录制问题 #10：新建的本地站点用用户名登录失败、只能用邮箱。幂等。
"""
import frappe


def local_login() -> bool:
    return not (frappe.conf.get("pearnly_oidc_issuer") or frappe.conf.get("pearnly_native_account"))


def apply():
    if not local_login():
        return
    for field, value in (("allow_login_using_user_name", 1), ("enable_password_policy", 0)):
        if frappe.db.get_single_value("System Settings", field) != value:
            frappe.db.set_single_value("System Settings", field, value)
