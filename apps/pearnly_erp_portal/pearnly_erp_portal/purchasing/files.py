"""Private purchase evidence; only owned uploads can be attached."""
import hashlib

import frappe
from frappe import _

from pearnly_erp_portal.purchasing import access

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
    same = document and file.attached_to_doctype == document.doctype and file.attached_to_name == document.name
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
        file.db_set({"attached_to_doctype": doc.doctype, "attached_to_name": doc.name}, update_modified=False)
        doc.append("attachments", {"file": file.name, "file_url": file.file_url,
                   "file_name": file.file_name, "kind": kind})


def add(name, attachments):
    doc = access.get_document(name, write=True, lock=True)
    if doc.status == "void":
        frappe.throw(_("Document has been cancelled"))
    bind(doc, attachments)
    doc.flags.purchase_action = True
    doc.save()
    return {"name": doc.name}


def remove(name, file):
    doc = access.get_document(name, write=True, lock=True)
    if doc.status == "void":
        frappe.throw(_("Document has been cancelled"))
    row = next((row for row in doc.attachments if row.file == file), None)
    if not row:
        frappe.throw(_("File not found"))
    if row.generated:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    doc.remove(row)
    doc.flags.purchase_action = True
    doc.save()
    attached = frappe.get_doc("File", file)
    if attached.attached_to_doctype != doc.doctype or attached.attached_to_name != doc.name:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    frappe.delete_doc("File", file, ignore_permissions=True)
    return {"name": doc.name}


def copy_evidence(source, target):
    for row in source.get("attachments", []):
        if row.kind != "bill":
            continue
        file = frappe.get_doc("File", row.file)
        file.check_permission("read")
        copied = frappe.get_doc({"doctype": "File", "file_name": file.file_name,
            "content": file.get_content(), "is_private": 1,
            "attached_to_doctype": target.doctype, "attached_to_name": target.name})
        copied.insert(ignore_permissions=True)
        target.append("attachments", {"file": copied.name, "file_url": copied.file_url,
                      "file_name": copied.file_name, "kind": row.kind})


def combined_hash(hashes):
    return hashes[0] if len(hashes) == 1 else hashlib.sha256("|".join(sorted(hashes)).encode()).hexdigest()


def evidence_hash(doc):
    hashes = []
    for row in doc.attachments:
        if row.kind != "bill":
            continue
        content = upload({"file": row.file}, doc).get_content()
        if isinstance(content, str):
            content = content.encode("utf-8")
        hashes.append(hashlib.sha256(content).hexdigest())
    return combined_hash(hashes) if hashes else None
