"""Transfer details from legacy production ec2e5c957e6b; native private evidence."""
import base64
import binascii

import frappe
from frappe import _

# Public receiving details shown by the actual legacy production frontend.
# An explicit site setting may replace all four fields together.
LEGACY_ACCOUNT = {
    "bank": "ธนาคาร กรุงเทพ",
    "branch": "สาขาโชคชัย 4 ลาดพร้าว",
    "account_number": "230-0-91368-4",
    "account_name": "บจ. มิสเตอร์ อี อาร์ พี",
}


def account():
    values = frappe.conf.get("pearnly_collection_account") or LEGACY_ACCOUNT
    return {key: str(values.get(key) or "") for key in LEGACY_ACCOUNT}


def decode_proof(proof):
    from pearnly_core.payments.proof import MAX_BYTES, validate_content
    if not isinstance(proof, dict) or not isinstance(proof.get("content"), str):
        frappe.throw(_("Upload a transfer slip before submitting"))
    if len(proof["content"]) > ((MAX_BYTES + 2) // 3) * 4:
        frappe.throw(_("Upload a transfer slip up to 10 MB"))
    try:
        content = base64.b64decode(proof["content"], validate=True)
    except (ValueError, binascii.Error):
        frappe.throw(_("Use a JPG, PNG or PDF transfer slip"))
    suffix = validate_content(content)
    return content, suffix


def attach(doc, content, suffix):
    file = frappe.get_doc({"doctype": "File", "file_name": doc.name + suffix,
        "content": content, "attached_to_doctype": doc.doctype, "attached_to_name": doc.name,
        "is_private": 1}).insert(ignore_permissions=True)
    doc.db_set("slip", file.file_url, commit=False)


def evidence(request):
    doc = frappe.get_doc("Pearnly Topup Request", request)
    name = frappe.db.get_value("File", {"attached_to_doctype": doc.doctype,
        "attached_to_name": doc.name, "file_url": doc.slip, "is_private": 1}, "name")
    if not name:
        frappe.throw(_("No transfer slip is attached"))
    file = frappe.get_doc("File", name)
    return {"filename": file.file_name, "content": base64.b64encode(file.get_content(encodings=())).decode()}
