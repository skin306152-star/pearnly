"""Keep cloud account passwords at the identity provider, never the local User store.

用户名与邮箱由后台发号、绑死这个租户（用户 2026-09-23："可以用用户名登录，但是不能
修改…后台管理发号后，用户名和邮箱是绑死这个租户的，唯一关系，用户只可以自己改密码"）。
所以这里只有两道守卫：本地密码不收（云站点走身份服务），邮箱不能改；**没有"用户自己
改名"那条路**（`first_name` 在 `account/layout.py` 里设成 DocType 级只读）。
"""
import frappe
from frappe import _
import re

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")


def product_identity():
    return frappe.conf.get("pearnly_product") and frappe.conf.get("pearnly_native_account")


def reject_local_password(doc, method=None):
    if (frappe.conf.get("pearnly_product") and frappe.conf.get("pearnly_native_account")
            and doc.get("new_password")):
        frappe.throw(_("account.identityPasswordOnly"))


def prepare_profile(doc, method=None):
    reject_local_password(doc, method)
    if not product_identity() or doc.is_new() or doc.name != frappe.session.user:
        return
    if doc.email != doc.name:
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def apply_login_username(user, claims):
    """Each product projects the same verified identity without renaming its email key."""
    username = str(claims.get("preferred_username") or "").strip()
    if not username or not product_identity():
        return
    doc = frappe.get_doc("User", user)
    if doc.first_name == username and not doc.middle_name and not doc.last_name:
        return
    previous = getattr(frappe.flags, "pearnly_profile_synced", False)
    frappe.flags.pearnly_profile_synced = True
    try:
        doc.first_name = username
        doc.middle_name = doc.last_name = ""
        # Email-style legacy usernames remain valid at Keycloak. Do not let Frappe
        # strip their @ or invent a replacement local login identifier.
        if USERNAME_RE.fullmatch(username):
            doc.username = username
        doc.save(ignore_permissions=True)
    finally:
        frappe.flags.pearnly_profile_synced = previous


def load_details(doc, method=None):
    # 这一行只给"本站是哪家公司、用哪个域名进来的"，页面上是只读的两格。
    # 原先要求 `pearnly_native_account`（那是**身份服务**那条路的开关，管的是本地密码），
    # 于是没有身份服务的站点上这两格是空的 —— 而框架有一条官方规则：**只读且没有值的字段
    # 整行藏起来**（`hide_empty_read_only_fields`），所以看起来"少了一行"（用户 2026-09-23：
    # "你 erp 这边，少一个域名显示"）。这里改成：只要是产品站点、看的是自己的资料，就给值；
    # 域名用框架自己的 `get_url()`（官方取站点地址的那个函数，本地实验室站点同样有值）。
    if not frappe.conf.get("pearnly_product") or doc.name != frappe.session.user:
        return
    from frappe.utils import get_url
    # Native User permits self-reading without granting the User DocType role.
    # Its virtual fields are filtered by field permissions; pass read-only site
    # context through the native onload channel instead of widening User access.
    doc.set_onload("pearnly_account", {
        "company": frappe.conf.get("pearnly_company_name") or frappe.conf.get("pearnly_workspace") or "",
        "domain": get_url() or "",
    })
