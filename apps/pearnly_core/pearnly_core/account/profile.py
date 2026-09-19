"""Keep cloud account passwords at the identity provider, never the local User store."""
import frappe
from frappe import _


def reject_local_password(doc, method=None):
    if (frappe.conf.get("pearnly_product") and frappe.conf.get("pearnly_native_account")
            and doc.get("new_password")):
        frappe.throw(_("account.identityPasswordOnly"))
