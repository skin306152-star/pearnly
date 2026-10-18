"""Private sales evidence; only owned uploads can be attached."""
import hashlib

import frappe
from frappe import _
from pearnly_core.originals import registered

from pearnly_erp_portal.sales import access

DOCTYPE = "ERP Sales Document"
UPLOAD_KINDS = {"bill", "payment_proof"}


def upload(data, document=None):
    name = data.get("file")
    if not name:
        name = frappe.db.get_value("File", {"file_url": data.get("file_url"),
                                          "owner": frappe.session.user}, "name")
    if not name:
        frappe.throw(_("File not found"))
    file = frappe.get_doc("File", name)
    file.check_permission("read")
    same = document and file.attached_to_doctype == document.doctype \
        and file.attached_to_name == document.name
    if not file.is_private or (not same and (file.owner != frappe.session.user or file.attached_to_name)):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if not (file.file_url or "").startswith("/private/files/"):
        frappe.throw(_("Invalid value"))
    return file


def bind(doc, attachments):
    for data in attachments or []:
        kind = data.get("kind", "bill")
        if kind not in UPLOAD_KINDS:
            frappe.throw(_("Invalid value"))
        file = upload(data, doc)
        if any(row.file == file.name for row in doc.get("attachments", [])):
            continue
        file.db_set({"attached_to_doctype": doc.doctype, "attached_to_name": doc.name},
                    update_modified=False)
        doc.append("attachments", {"file": file.name, "file_url": file.file_url,
                   "file_name": registered.filename(file), "kind": kind})


def add(name, attachments):
    doc = access.get_document(name, write=True, lock=True)
    if doc.status == "void":
        frappe.throw(_("Document has been cancelled"))
    bind(doc, attachments)
    doc.flags.sales_action = True
    doc.save()
    return {"name": doc.name}


def remove(name, file):
    doc = access.get_document(name, write=True, lock=True)
    if doc.status == "void":
        frappe.throw(_("Document has been cancelled"))
    row = next((row for row in doc.attachments if row.file == file), None)
    if not row:
        frappe.throw(_("File not found"))
    doc.remove(row)
    doc.flags.sales_action = True
    doc.save()
    attached = frappe.get_doc("File", file)
    if attached.attached_to_doctype != doc.doctype or attached.attached_to_name != doc.name:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    frappe.delete_doc("File", file, ignore_permissions=True)
    return {"name": doc.name}


def content_hash(file):
    """原件内容的 sha256（原件在原件层的取登记里的，不取回整份）。"""
    return registered.sha256(file)


def combined_hash(hashes):
    return hashes[0] if len(hashes) == 1 else hashlib.sha256("|".join(sorted(hashes)).encode()).hexdigest()


def evidence_hash(doc):
    """单据留底指纹：明细行与附件都算进来，重复提交同一个件会被拒。"""
    hashes = [content_hash(upload({"file": row.file}, doc)) for row in doc.attachments]
    return combined_hash(hashes) if hashes else None
