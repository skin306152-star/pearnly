"""工作协调的权限判据：专用接口与原生资源共用同一份。

能用 = 站点是 Cowork、账号启用、勾了「工作协调」（`Cowork Work`）或是产品管理员（`Cowork Admin`）。
工作协调不单独售卖（用户 2026-09-27），所以不看开通功能。板上的人都能看、能改所有卡片；
原生资源只给读，写一律走本域的专用接口（见 `documents`）。
"""
import frappe
from frappe import _
from pearnly_cowork.access import scope

ROLE = "Cowork Work"
ADMIN_ROLE = "Cowork Admin"
CARD = "Cowork Work Card"
LABEL = "Cowork Work Label"
ACTIVITY = "Cowork Work Activity"
NOTIFICATION = "Cowork Work Notification"
COLUMN = "Cowork Work Column"
FIELD = "Cowork Work Field"
MIRROR = "Cowork Work Mirror"
RULE = "Cowork Work Rule"
TYPES = (CARD, LABEL, ACTIVITY, NOTIFICATION, COLUMN, FIELD, MIRROR, RULE)


def allowed(user=None):
    user = user or frappe.session.user
    return bool(user and user != "Guest" and frappe.conf.get("pearnly_product") == "cowork"
                and frappe.db.get_value("User", user, "enabled")
                and {ROLE, ADMIN_ROLE}.intersection(frappe.get_roles(user)))


def require():
    if not allowed():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def administrator(user=None):
    return ADMIN_ROLE in frappe.get_roles(user or frappe.session.user)


def has_permission(doc, ptype=None, user=None, **kwargs):
    # 返回 False 会让框架退回 DocShare；这里直接抛，撤掉的权限不能靠旧分享找回来。
    if (not allowed(user) or ptype not in scope.READ_TYPES
            or not scope.contains(doc.workspace, user=user)):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return True


def notification_permission(doc, ptype=None, user=None, **kwargs):
    # 提醒只给收件人本人看。
    has_permission(doc, ptype, user)
    if doc.user != (user or frappe.session.user):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return True


def reject_share(doc, method=None):
    if doc.share_doctype in TYPES:
        frappe.throw(_("Assign Cowork access through company and client permissions."))


def _query(doctype, user=None):
    scope._reject_share_fallback(doctype, user)
    if not allowed(user):
        return "1=0"
    return f"`tab{doctype}`.workspace IN (SELECT name FROM `tabCowork Workspace` WHERE enabled=1)"


def card_query(user=None):
    return _query(CARD, user)


def label_query(user=None):
    return _query(LABEL, user)


def activity_query(user=None):
    return _query(ACTIVITY, user)


def column_query(user=None):
    return _query(COLUMN, user)


def field_query(user=None):
    return _query(FIELD, user)


def mirror_query(user=None):
    return _query(MIRROR, user)


def rule_query(user=None):
    return _query(RULE, user)


def notification_query(user=None):
    clause = _query(NOTIFICATION, user)
    if clause == "1=0":
        return clause
    return f"{clause} AND `tab{NOTIFICATION}`.user={frappe.db.escape(user or frappe.session.user)}"
