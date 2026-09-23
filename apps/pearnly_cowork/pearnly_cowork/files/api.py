"""Original-file upload/download boundaries, without product UI or OCR execution."""
import frappe
from frappe import _
from frappe.core.doctype.access_log.access_log import make_access_log
from frappe.core.api.file import zip_files as native_zip_files
from pearnly_cowork.access import scope
from pearnly_cowork.files import access, service


@frappe.whitelist(methods=["POST"])
def upload(workspace, request_key, client=None):
    scope.require("intake")
    scope.require_scope(workspace, client)
    incoming = frappe.request.files.get("file") if frappe.request else None
    if not incoming:
        frappe.throw(_("Select a file to upload."))
    content = incoming.stream.read(service.MAX_BYTES + 1)
    for attempt in range(3):
        try:
            return service.create(workspace, incoming.filename, content, request_key, client)
        except (frappe.QueryDeadlockError, frappe.DuplicateEntryError):
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist()
def metadata(name):
    return service.project(service.get_source(name))


@frappe.whitelist()
def original(name):
    filename, data = service.original(name)
    make_access_log(doctype="Cowork Source File", document=name, file_type="original")
    frappe.local.response.update(type="download", filename=filename, filecontent=data,
                                  display_content_as="attachment", content_type="application/octet-stream")
    frappe.local.cowork_private_response = True


@frappe.whitelist()
def page(history_name, physical_page):
    data = service.page(history_name, physical_page)
    make_access_log(doctype="Cowork OCR History", document=history_name, file_type="page_png")
    frappe.local.response.update(type="download", filename="invoice-page.png", filecontent=data,
                                  display_content_as="inline", content_type="image/png")
    frappe.local.cowork_private_response = True


def protect_response(response):
    if getattr(frappe.local, "cowork_private_response", False):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"


@frappe.whitelist()
def guarded_zip_files(files):
    # The native ZIP builder opens its ZipFile before get_content; denying
    # there leaves an unclosed archive on Python 3.14. Preflight at the public
    # boundary, then retain native behavior for ordinary files. get_content
    # remains guarded for other server-side callers.
    names = frappe.parse_json(files)
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        frappe.throw(_("Select valid files to download."))
    for name in names:
        if access.is_protected(frappe.get_doc("File", name)):
            frappe.throw(_("Use the Cowork original-file service to access this file."), frappe.PermissionError)
    return native_zip_files(files)
