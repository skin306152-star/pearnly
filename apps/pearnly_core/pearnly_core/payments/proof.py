"""Private native File upload and bounded transfer-proof transport."""
import base64
from io import BytesIO

import frappe
from frappe import _

MAX_BYTES = 10 * 1024 * 1024


def validate_content(content):
    if not content or len(content) > MAX_BYTES:
        frappe.throw(_("Upload a transfer slip up to 10 MB"))
    if content.startswith(b"%PDF-"):
        return ".pdf"
    try:
        from PIL import Image
        with Image.open(BytesIO(content)) as image:
            kind = image.format
            image.verify()
        if kind in ("PNG", "JPEG"):
            return ".png" if kind == "PNG" else ".jpg"
    except Exception:
        pass
    frappe.throw(_("Use a JPG, PNG or PDF transfer slip"))


@frappe.whitelist(methods=["POST"])
def upload():
    from pearnly_core.billing_www import _require_admin
    user = _require_admin()
    upload = frappe.request.files.get("file")
    content = getattr(frappe.local, "uploaded_file", None)
    if content is None:
        content = upload.stream.read(MAX_BYTES + 1) if upload else b""
    suffix = validate_content(content)
    doc = frappe.get_doc({"doctype": "File", "file_name": "transfer-slip" + suffix,
        "content": content, "attached_to_doctype": "User", "attached_to_name": user,
        "is_private": 1}).insert(ignore_permissions=True)
    return {"doctype": "File", "name": doc.name, "file_url": doc.file_url,
            "file_name": doc.file_name, "is_private": 1}


def payload(file_url):
    """Never fetch a caller URL or read another user's private attachment."""
    name = frappe.db.get_value("File", {"file_url": file_url, "is_private": 1,
        "owner": frappe.session.user, "attached_to_doctype": "User",
        "attached_to_name": frappe.session.user}, "name") if file_url else None
    if not name:
        frappe.throw(_("Upload a transfer slip before submitting"))
    doc = frappe.get_doc("File", name)
    content = doc.get_content(encodings=())
    suffix = validate_content(content)
    return {"content": base64.b64encode(content).decode(), "filename": "transfer-slip" + suffix}
