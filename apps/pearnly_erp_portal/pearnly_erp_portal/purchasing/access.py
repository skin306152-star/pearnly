"""Record ownership also applies to native forms, files and PDF downloads."""
import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company as companies


def get_document(name, *, write=False, lock=False):
    if lock:
        # 先加锁、再做任何普通读（11-28 B）：读视图从拿到锁之后才建，后面的 get_doc 读到的是别人刚提交的值；
        # 先读（哪怕只是核权限）再锁，关快照隔离后 get_doc 是旧副本，保存时把别人刚写的账本栏盖回去
        frappe.db.sql("select name from `tabPurchase Document` where name=%s for update", (name,))
    guard.require("purchase.write" if write else "purchase.read")
    doc = frappe.get_doc("Purchase Document", name)
    if document_permission(doc, "write" if write else "read", frappe.session.user) is False:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    doc.check_permission("write" if write else "read")
    return doc


def document_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    if not guard.has("purchase.read" if ptype in ("read", "print", "export", "select") else "purchase.write", user):
        return False
    if doc.get("company") and doc.company not in companies.allowed_companies(user):
        return False
    scope = guard.scope_filters(user)
    if scope and doc.owner != user:
        return False
    return True


def query_conditions(user=None):
    user = user or frappe.session.user
    conditions = ["`tabPurchase Document`.`company` = " + frappe.db.escape(companies.active_company(user))]
    if guard.scope_filters(user):
        conditions.append("`tabPurchase Document`.`owner` = " + frappe.db.escape(user))
    return " and ".join(conditions)
