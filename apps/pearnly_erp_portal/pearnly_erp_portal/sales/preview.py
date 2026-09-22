"""Read-only PDF page images for the sales viewer, under File scope."""
import base64

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.sales import access, files


def page_image(file, page=1):
    guard.require("sales.read")
    attached = frappe.get_doc("File", file)
    attached.check_permission("read")
    context = None
    if attached.attached_to_doctype == "ERP Sales Document":
        context = access.get_document(attached.attached_to_name)
        if not any(row.file == attached.name for row in context.attachments):
            frappe.throw(_("Not permitted"), frappe.PermissionError)
    elif attached.attached_to_doctype == "ERP LINE Intake":
        from pearnly_erp_portal.integrations import intake

        context = intake._get(attached.attached_to_name)
        if context.direction != "sales" or context.source_file != attached.file_url:
            frappe.throw(_("Not permitted"), frappe.PermissionError)
    attached = files.upload({"file": file}, context)
    content = attached.get_content()
    if isinstance(content, str):
        content = content.encode("utf-8")
    if len(content) > 20 * 1024 * 1024 or not content.startswith(b"%PDF-"):
        frappe.throw(_("Invalid file"))
    try:
        number = int(page)
    except (ValueError, TypeError):
        frappe.throw(_("Invalid value"))
    # Already pinned in the platform image; no new renderer/dependency or paid IO.
    import pymupdf

    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass or not 1 <= number <= document.page_count:
                frappe.throw(_("Invalid value"))
            source = document[number - 1]
            scale = min(2, 1800 / max(source.rect.width, source.rect.height, 1))
            image = source.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            return {"page": number, "pages": document.page_count,
                    "image": "data:image/png;base64," + base64.b64encode(image.tobytes("png")).decode("ascii")}
    except (pymupdf.FileDataError, RuntimeError):
        frappe.throw(_("Invalid file"))
