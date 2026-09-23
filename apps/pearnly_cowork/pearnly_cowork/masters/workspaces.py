"""Native workspace writes. One validation path for native, single and bulk creation."""
import frappe
from frappe import _

from pearnly_cowork.access import scope
from . import workspace_fields as fields

DOCTYPE = "Cowork Workspace"


class DuplicateWorkspaceTax(frappe.ValidationError):
    pass


def require_manager():
    scope.require("master_data")
    if not scope.administrator():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def lock():
    # Per-Site native metadata row exists before any workspace does. Every
    # workspace create/tax-change shares this transaction lock, across actors.
    frappe.db.get_value("DocType", DOCTYPE, "name", for_update=True)


def tax_taken(tax_id, *, exclude=None, current=False):
    key = fields.tax_identity(tax_id)
    if not key:
        return False
    rows = frappe.db.sql("SELECT name,tax_id FROM `tabCowork Workspace` WHERE enabled=1" +
        (" LOCK IN SHARE MODE" if current else ""), as_dict=True)
    return any(row.name != exclude and fields.tax_identity(row.tax_id) == key for row in rows)


def validate(doc):
    require_manager()
    try:
        clean = fields.values({field: doc.get(field) for field in fields.LIMITS})
    except fields.WorkspaceInputError as error:
        frappe.throw(_(str(error)))
    doc.update(clean)
    previous = doc.get_doc_before_save()
    # Existing duplicates are not silently merged or blocked from unrelated edits.
    # New records, tax changes and reactivation must pass the same live check.
    check_tax = doc.is_new() or not previous or not previous.enabled or previous.tax_id != doc.tax_id
    if check_tax and doc.enabled and fields.tax_identity(doc.tax_id):
        lock()
        if tax_taken(doc.tax_id, exclude=doc.name if not doc.is_new() else None, current=True):
            frappe.throw(_("workspace.tax_id_duplicate"), DuplicateWorkspaceTax)


def create(display_name, tax_id=None, address=None, branch=None, phone=None):
    require_manager()
    doc = frappe.get_doc({"doctype": DOCTYPE, "display_name": display_name, "tax_id": tax_id,
        "address": address, "branch": branch, "phone": phone, "enabled": 1}).insert()
    return {"name": doc.name, "display_name": doc.display_name}
