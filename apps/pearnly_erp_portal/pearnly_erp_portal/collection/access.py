"""收款记录的原生读权限（hooks ``has_permission`` / ``permission_query_conditions``）。

写一律只经 `collection.service` / `collection.push`（``ignore_permissions``），原生入口只给读：本人能用的公司里，能看收款
（``collection.view``：ERP 管理员、销售岗位）看全部，收款员（``collection.write``）只看自己录的。凭证文件挂在收款记录上，
私有文件的取图按这条判（Frappe 私有文件按所挂单据的读权限）。
"""
import frappe

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company as companies

DOCTYPE = "ERP Collection"
READ_TYPES = ("read", "print", "export", "report")


def record_permission(doc, ptype=None, user=None):
    if (ptype or "read") not in READ_TYPES:
        return False
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    from pearnly_erp_portal.collection import service

    return service.can_read(doc, user=user)


def record_query(user=None):
    user = user or frappe.session.user
    if user == "Administrator":
        return ""
    allowed = companies.allowed_companies(user)
    if not allowed:
        return "1=0"
    scope = f"`tab{DOCTYPE}`.company in ({', '.join(frappe.db.escape(name) for name in allowed)})"
    if guard.has("collection.view", user=user):
        return scope
    if guard.has("collection.write", user=user):
        return f"{scope} and `tab{DOCTYPE}`.collector = {frappe.db.escape(user)}"
    return "1=0"
