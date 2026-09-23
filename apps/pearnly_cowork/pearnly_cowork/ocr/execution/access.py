"""Internal batch and provider evidence never use generic customer CRUD."""
import frappe
from frappe import _
from pearnly_cowork.access import scope
from pearnly_cowork.files import access as files

DOCTYPE = "Cowork OCR Batch"
WORK = "Pearnly OCR Work"


def require_batch(doc, *, write=False):
    scope.require("intake") if write else scope.require_read()
    scope.require_scope(doc.workspace, doc.client)
    source = frappe.get_doc(files.SOURCE, doc.source_file)
    if source.workspace != doc.workspace or (source.client or "") != (doc.client or ""):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    files.require_full(source)
    return source


def has_permission(doc, ptype=None, user=None, **kwargs):
    frappe.throw(_("Use the Cowork OCR service to access recognition tasks."), frappe.PermissionError)


def query(user=None):
    scope._reject_share_fallback(DOCTYPE, user)
    return "1=0"


def work_permission(doc, ptype=None, user=None, **kwargs):
    if doc.product == "cowork" or str(doc.kind or "").startswith("cowork."):
        frappe.throw(_("Use the Cowork OCR service to access recognition tasks."), frappe.PermissionError)
    return True  # Frappe ANDs hooks with normal role/controller permissions.


def work_query(user=None):
    # Frappe ORs DocShare after query conditions. Only Cowork evidence shares
    # must be denied here; another product's legacy shares are not ours to alter.
    shares = frappe.db.sql("""SELECT s.name FROM `tabDocShare` s
        INNER JOIN `tabPearnly OCR Work` w ON w.name=s.share_name
        WHERE s.share_doctype=%s AND (s.user=%s OR s.everyone=1)
          AND (w.product='cowork' OR w.kind LIKE 'cowork.%%') LIMIT 1""",
        (WORK, user or frappe.session.user))
    if shares:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return "`tabPearnly OCR Work`.`product` != 'cowork' AND `tabPearnly OCR Work`.`kind` NOT LIKE 'cowork.%'"


def reject_share(doc, method=None):
    protected = doc.share_doctype == DOCTYPE
    if doc.share_doctype == WORK:
        row = frappe.db.get_value(WORK, doc.share_name, ["product", "kind"], as_dict=True)
        protected = row and (row.product == "cowork" or str(row.kind or "").startswith("cowork."))
    if protected:
        frappe.throw(_("Recognition tasks cannot be shared separately from their originals."), frappe.PermissionError)
