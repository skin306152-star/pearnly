# -*- coding: utf-8 -*-
"""开通时要在**目标租户站点**上做的事。

为什么放在 App 里而不是写成脚本：`bench console` 会吞多行脚本（连报错都不给），
踩过好几次。这里每个函数都能用**一行** `bench execute` 调起来：

    bench --site <site> execute pearnly_core.provisioning.ensure_local_user \
        --kwargs '{"email": "...", "full_name": "...", "subject": "...", "deployment_id": "..."}'
"""

import frappe
from frappe import _

from pearnly_core import identity


def ensure_local_user(email: str, full_name: str, subject: str,
                      deployment_id: str | None = None,
                      role: str = "System Manager") -> dict:
    """建本地账号并绑定身份。幂等。

    三条合同约束：
    1. **不按邮箱合并到已有账号**（P1.04）：已存在就复用，不再新建；
    2. **不给本地密码**：业务用户只走统一身份登录，不允许本地密码旁路；
    3. 已经绑过的身份不重复绑，视为成功（重跑开通不会卡在这一步）。
    """
    email = (email or "").strip().lower()
    if not email:
        frappe.throw(_("邮箱不能为空"))

    created = "exists"
    if not frappe.db.exists("User", email):
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": (full_name or email.split("@")[0])[:140],
            "enabled": 1,
            "send_welcome_email": 0,
            "user_type": "System User",
        })
        user.insert(ignore_permissions=True)
        created = "created"
    else:
        user = frappe.get_doc("User", email)

    if role and role not in [r.role for r in user.roles]:
        user.add_roles(role)

    bound = "already"
    existing = frappe.db.exists(
        "Pearnly Identity", {"issuer": identity.issuer(), "subject": subject}
    )
    if not existing:
        identity.bind_identity(user=email, subject=subject, deployment_id=deployment_id)
        bound = "bound"

    frappe.db.commit()
    return {"user": email, "created": created, "bound": bound, "role": role}


def finalize_site() -> dict:
    """建站收尾：把 desk 首页从 setup-wizard 摘出来、标记设置已完成。

    新建的站点默认带着 `desktop:home_page = setup-wizard`，而"设置向导"页面在
    设置已完成时又会跳回 /desk —— 两头互踢会造成**整页无限重载（闪屏）**。
    这是踩过的坑，详见 docs/p4/03-desk-unstable.md；开通流程必须包含这一步，
    否则新开的租户一登录就落到向导页。

    真值说明（也是最容易改错的地方）：
      "设置已完成"的真正开关是 `Installed Application.is_setup_complete`
      （`frappe/__init__.py:is_setup_complete()` 读的就是它），
      System Settings 上那个同名值只是它的回写，单改那个没有任何效果。
    """
    from frappe.desk.doctype.desktop_icon.desktop_icon import clear_desktop_icons_cache

    frappe.db.sql(
        "update `tabInstalled Application` set is_setup_complete=1 "
        "where app_name in (%s, %s)",
        ("frappe", "erpnext"),
    )
    frappe.db.set_single_value("System Settings", "setup_complete", 1)
    frappe.db.set_default("desktop:home_page", "desktop")

    # 业务站点关掉"本地密码登录"这条旁路。
    # 为什么必须关：站点上的 Administrator 是有密码的，而业务账号在站点上
    # 根本没有密码 —— 不关就等于留了一条"用站点本地密码登录"的绕行路，
    # 与 P3 合同"业务用户无独立本地密码登录旁路"直接冲突。
    # 这个开关同时管住网页表单和 /api/method/login（frappe/auth.py:149）。
    # 控制面是有意用本地账号的（ADR-004），所以用 pearnly_product 区分。
    if str(frappe.conf.get("pearnly_product") or "").strip():
        frappe.db.set_single_value("System Settings", "disable_user_pass_login", 1)
        # 免密码的"邮箱链接登录"也不在统一登录口内，一并关掉
        frappe.db.set_single_value("System Settings", "login_with_email_link", 0)
    frappe.db.commit()

    frappe.clear_cache()
    clear_desktop_icons_cache()

    home_page = frappe.db.get_default("desktop:home_page")
    done = frappe.is_setup_complete()
    if home_page != "desktop" or not done:
        frappe.throw(
            f"收尾没成功：desktop:home_page={home_page!r} is_setup_complete={done}"
        )
    return {"home_page": home_page, "is_setup_complete": done}
