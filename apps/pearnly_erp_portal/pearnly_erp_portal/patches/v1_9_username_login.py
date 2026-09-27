"""用户名当登录名：打开框架那两位开关，并给存量账号补上用户名。

用户 2026-09-24 的口径：

· "添加用户必须是英文和数字，不区分大小写，长度不限"；
· "密码可以自由填写，不限制长度，也不限制其它东西"；
· "**这个用户名是可以登录的，和邮箱一样**；邮箱和用户名是唯一绑定关系"；
· "全局可以涉及到姓名的地方，全部改为用户名"。

框架那半边只需要两位设置：

1. `allow_login_using_user_name = 1` —— `User.find_by_credentials()` 才会把 `username`
   也当成登录名（`name`（邮箱）那条一直都在，所以是"邮箱和用户名都能登"）；
2. `enable_password_policy = 0` —— 关掉框架的密码强度闸，让任意密码都能设（我们只挡空密码）。

存量账号（升级前建的、只有邮箱没有用户名的）这里补一个用户名：拿邮箱 @ 前面那段，
只留英文和数字；撞名就往后加序号。**同时把 `first_name` 写成用户名** —— 用户口径是
"全局涉及到姓名的地方全部改为用户名"，框架自己显示名字的地方（顶栏、邮件、"经手人"）
读的就是 `first_name` / `full_name`，不写的话那些地方还是老样子。
**已经建过、但用户名不合规的账号（带下划线/连字符/中日韩字）也一起纠正**：规则是"英文和
数字"，老的 `frappe.scrub` 那套会留下下划线，留着就与口径不一致（同一个列表里两种写法）。
超管 `Administrator` 与 `Guest` 不动。
"""

import re

import frappe

from pearnly_erp_portal.team import login_settings


def _derive(email: str) -> str:
    """邮箱 @ 前面那段，只留英文和数字（空的话给个兜底）。"""
    base = re.sub(r"[^A-Za-z0-9]", "", str(email or "").split("@")[0])
    return base or "user"


def _free_username(base: str, taken: set) -> str:
    if base.lower() not in taken:
        return base
    index = 2
    while f"{base}{index}".lower() in taken:
        index += 1
    return f"{base}{index}"


def _conforms(username: str) -> bool:
    return bool(username) and bool(re.fullmatch(r"[A-Za-z0-9]+", username))


def execute():
    # Unified identities are authoritative. A local migration must not invent a
    # different username or claim to rename the remote login; verified SSO claims
    # project the display name on login. This patch only normalizes offline users.
    if not login_settings.local_login():
        return
    login_settings.apply()

    emails = frappe.get_all("User", filters={"name": ["not in", ["Administrator", "Guest"]]},
                            pluck="name", limit_page_length=0)
    kept = {str(frappe.db.get_value("User", email, "username") or "").lower()
            for email in emails}
    taken = {name for name in kept if name}
    for email in emails:
        row = frappe.db.get_value("User", email, ["username", "first_name"], as_dict=True) or {}
        current = str(row.get("username") or "")
        if _conforms(current):
            username = current
        else:
            # 不合规（空 / 带下划线连字符 / 中日韩字）：按邮箱重算一个，并腾出旧的占位。
            taken.discard(current.lower())
            username = _free_username(_derive(email), taken)
            taken.add(username.lower())
        # 三个都写：用户名是登录名，`first_name`/`full_name` 是全站显示名（框架顶栏、邮件、
        # "经手人"读的就是它俩），口径是"全局涉及到姓名的地方全部改为用户名"。
        # `full_name` 是**单独一列**（`User.set_full_name()` 只在保存时拼），少写它就会出现
        # "列表已经是用户名、侧边栏还是旧名字"（实测）。
        frappe.db.set_value("User", email,
                            {"username": username, "first_name": username, "full_name": username},
                            update_modified=False)
    frappe.db.commit()
    frappe.clear_cache()
