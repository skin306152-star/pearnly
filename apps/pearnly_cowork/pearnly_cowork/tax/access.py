"""报税草稿的权限：站点是 Cowork、买了 ``tax_drafts``、账号启用、有「Cowork Tax Filing」或 Cowork Admin。

照对账的写法（`reconciliation.recon_access`），功能码不同所以不复用那个类。Cowork 不做公司隔离：工作空间只核引用一致。
ภ.ง.ด.3 / 53 草稿由识别记录算出，读它还要读识别记录的权限（`scope.READ_FEATURES`），与出草稿时同一道。
草稿表只经 `tax.service` 写；通用的删除、分享、提交一律拒绝（删除走 `tax.service.delete`）。
"""
import frappe
from frappe import _

from pearnly_core.shell import entitlements
from pearnly_cowork.access import scope

TASK = "Cowork Tax Draft"
ROLE = "Cowork Tax Filing"
FEATURE = "tax_drafts"
ADMIN_ROLE = "Cowork Admin"
ROLES = {ROLE, ADMIN_ROLE}
VAT, WHT = "vat_return", "withholding"


def allowed(user=None) -> bool:
    user = user or frappe.session.user
    return bool(user and user != "Guest" and frappe.conf.get("pearnly_product") == "cowork"
                and FEATURE in entitlements() and frappe.db.get_value("User", user, "enabled")
                and ROLES.intersection(frappe.get_roles(user)))


def require(workspace=None):
    if not allowed() or (workspace is not None and not scope.contains(workspace)):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def has_permission(doc, ptype=None, user=None, **kwargs):
    # False 会让 Frappe 退回 DocShare 判断；拒绝要抛错才是最终结论。
    if (not allowed(user) or ptype in {"delete", "share", "submit", "cancel", "amend"}
            or not scope.contains(doc.workspace, user=user)
            or (doc.kind == WHT and not scope.allowed_any(scope.READ_FEATURES, user=user))):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return True


def reject_share(doc, method=None):
    if doc.share_doctype == TASK:
        frappe.throw(_("Assign Cowork access through company and client permissions."))


def task_query(user=None):
    user = user or frappe.session.user
    if frappe.get_all("DocShare", filters={"share_doctype": TASK}, or_filters={"user": user, "everyone": 1},
                      pluck="name", limit_page_length=1):
        frappe.throw(_("Remove legacy document shares before accessing Cowork records."), frappe.PermissionError)
    if not allowed(user):
        return "1=0"
    condition = f"`tab{TASK}`.workspace IN (SELECT name FROM `tabCowork Workspace` WHERE enabled=1)"
    if not scope.allowed_any(scope.READ_FEATURES, user=user):
        condition += f" AND `tab{TASK}`.kind = '{VAT}'"
    return condition
