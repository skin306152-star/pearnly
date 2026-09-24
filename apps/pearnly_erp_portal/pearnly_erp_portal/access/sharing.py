"""Company-scoped ERP records cannot acquire an alternate DocShare authorization."""
import frappe
from frappe import _

SCOPED_DOCTYPES = (
    "ERP Company", "ERP Warehouse", "ERP POS Terminal", "ERP Stock Entry",
    "ERP Stock Balance", "ERP Stock Movement", "ERP Stock Opening", "ERP Stock Count",
    "ERP Stock Count Item", "ERP Stock Count Entry", "ERP Sale", "ERP Shift",
    "Purchase Document", "ERP Sales Document",
)


def validate_share(doc, method=None):
    if doc.share_doctype in SCOPED_DOCTYPES:
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def remove_legacy_shares():
    """Sharing is not a product capability; preserve unrelated and profile shares."""
    for name in frappe.get_all("DocShare", filters={"share_doctype": ["in", SCOPED_DOCTYPES]}, pluck="name"):
        frappe.delete_doc("DocShare", name, ignore_permissions=True)
