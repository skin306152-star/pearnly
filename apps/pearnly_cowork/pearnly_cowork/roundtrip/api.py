"""Native download endpoint for the explicit Thai review-workbook protocol."""
import frappe
from frappe import _
from frappe.core.doctype.access_log.access_log import make_access_log

from pearnly_cowork.roundtrip import service
from pearnly_cowork.roundtrip.export import ExportError


@frappe.whitelist(methods=["POST"])
def export(names):
    try:
        content = service.export(names)
    except ExportError as exc:
        frappe.throw(_(exc.code))
    make_access_log(doctype="Cowork OCR History", file_type="review_workbook")
    frappe.local.response.update(type="download", filename="cowork-review.xlsx", filecontent=content,
        display_content_as="attachment", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    frappe.local.cowork_private_response = True
