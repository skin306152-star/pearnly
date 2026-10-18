# -*- coding: utf-8 -*-
"""控制面的本地自举：建登录账号、配开户目标。

控制面**只在本机跑、只自己用、永远不上服务器**，所以登录不接统一身份，
就用一个本地账号（earn / earn）。业务站点的统一身份不受影响。

放在 App 里而不是写在脚本里，是因为 `bench console` 会吞多行脚本（踩过），
单行调用一个函数才可靠。
"""

import frappe
from frappe import _

# 登录名 earn、密码 earn（用户指定）。
#
# 两点必须知道（都从源码核实过，不是猜的）：
#
# 1. User 的 `name`（主键）就是邮箱，所以**新建账号必须给一个合法邮箱**，
#    写 `earn` 会被 `InvalidEmailAddressError` 挡下。邮箱只是主键，不要求真实可用。
# 2. Frappe **原生支持用用户名登录**，但要打开开关：
#    System Settings → `allow_login_using_user_name`。
#    源码 `frappe/core/doctype/user/user.py:826 find_by_credentials`：
#    默认按邮箱查，再按开关追加 mobile_no / username。
#    另有 `allow_login_using_mobile_number` 可启用手机号登录。
#
# 所以这里：邮箱用 earn@pearnly.local，username 设成 earn，并打开用户名登录开关，
# 最终用 `earn` / `earn` 就能登进来。
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
        changed = False
        if settings.enable_password_policy:
            settings.enable_password_policy = 0
            changed = True
        # 允许用用户名登录（Frappe 原生能力，见文件头注释）
        if not settings.allow_login_using_user_name:
            settings.allow_login_using_user_name = 1
            changed = True
        if changed:
            settings.save(ignore_permissions=True)
            frappe.db.commit()
    except Exception:
        # 关不掉也不该让建号流程整个失败；下面建号时若被策略挡下会直接报错
        pass


def open_login_identifiers() -> dict:
    """把**开户/登录标识**相关的原生开关全部打开。

    这三项合起来就是 Frappe 原生支持的全部登录标识（源码
    `frappe/core/doctype/user/user.py:826 find_by_credentials`）：

    | 开关 | 作用 |
    |---|---|
    | （邮箱） | 永远支持，User 的主键就是邮箱 |
    | `allow_login_using_user_name` | 额外支持用**用户名**登录 |
    | `allow_login_using_mobile_number` | 额外支持用**手机号**登录 |

    ⚠️ **刻意没有打开**的几个（打开会把本机控制面弄坏，不是漏做）：

    - `disable_user_pass_login`：打开就**完全无法登录**。
    - `enable_two_factor_auth`：本机没配邮件/短信，开了会把自己锁在外面。
    - `login_with_email_link`：同上，本机发不出邮件。
    - `deny_multiple_sessions`：一个账号同时只能有一个会话，与"多设备/多产品互不踢"直接冲突。
    - `enable_password_policy`：会让 earn 这种密码过不了强度检查。
    """
    # 用 db.set_value 直接写：System Settings 走 doc.save() 在本站点没写进去（试过）。
    values = {
        "allow_login_using_user_name": 1,
        "allow_login_using_mobile_number": 1,
    }
    for field, value in values.items():
        frappe.db.set_value("System Settings", "System Settings", field, value)
    frappe.db.commit()
    frappe.clear_cache()
    return {field: frappe.db.get_single_value("System Settings", field) for field in values}


def ensure_target(
    # 默认目标名会**写进记录**（Pearnly Target 的主键），按 Frappe 惯例用英文：
    # 记录名是数据，不做翻译。
    target_name: str = "Local sample",
    base_url: str = "http://keycloak.localhost:8105",
    realm: str = "pearnly-business",
    admin_client_id: str = "pearnly-admin",
    admin_client_secret: str | None = None,
    password_check_client_id: str = "pearnly-password-check",
    password_check_client_secret: str | None = None,
    is_default: int = 1,
    notes: str | None = None,
    cowork_realm: str = "pearnly-cowork",
    cowork_admin_client_secret: str | None = None,
    cowork_password_check_client_secret: str | None = None,
) -> str:
    """配一个开户目标。

    **生产目标等服务器就绪后加一条即可**：把 base_url 换成服务器上的身份服务地址，
    代码不用改。

    除了建站用的管理客户端，还要存**改密用的密码校验客户端**（两者都是 realm 级，
    同 realm 下所有租户共用一套）。存齐了开通流程才会把它们一起下发到租户站点，
    否则开出来的租户**能登录、改不了密码** —— 这个坑真踩过（样板站手工配过，
    真租户站一直缺）。
    """
    if not admin_client_secret:
        frappe.throw(_("Admin client secret is missing"))  # 环境变量 PEARNLY_BIZ_ADMIN_SECRET
    if not password_check_client_secret:
        frappe.throw(_("Password-check client secret is missing"))  # 环境变量 PEARNLY_BIZ_PASSWORD_CHECK_SECRET

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
    doc.password_check_client_id = password_check_client_id
    doc.password_check_client_secret = password_check_client_secret
    # Cowork 自己的账号池（用户 2026-09-26 与 ERP / DMS 分池）：给了它的两个密钥才配，
    # 不给就保持原样 —— 没配的目标开不了 Cowork（开户时拒绝），不会悄悄落进业务账号池。
    if cowork_admin_client_secret and cowork_password_check_client_secret:
        doc.cowork_realm = cowork_realm
        doc.cowork_admin_client_id = admin_client_id
        doc.cowork_admin_client_secret = cowork_admin_client_secret
        doc.cowork_password_check_client_id = password_check_client_id
        doc.cowork_password_check_client_secret = cowork_password_check_client_secret
    doc.notes = notes or doc.notes
    if existing:
        doc.save(ignore_permissions=True)
        result = "UPDATED"
    else:
        doc.insert(ignore_permissions=True)
        result = "CREATED"
    frappe.db.commit()
    return result
