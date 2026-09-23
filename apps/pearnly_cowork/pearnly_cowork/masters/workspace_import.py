"""Site-scoped workspace import and private durable replay receipt. No commits or external IO."""
import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document

from . import client_import, workspace_fields, workspaces

DOCTYPE = "Cowork Workspace Import"
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


class ImportReceipt(Document):
    def check_permission(self, *args, **kwargs):
        if self.flags.cowork_workspace_import is not _WRITE:
            deny()

    def before_insert(self):
        self.check_permission()

    def autoname(self):
        self.check_permission()
        self.name = self.request_id

    def validate(self):
        self.check_permission()
        if not self.is_new():
            deny()

    before_validate = before_insert

    def on_trash(self):
        deny()

    def before_rename(self, *args, **kwargs):
        deny()


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def rows_input(rows):
    if isinstance(rows, str):
        if len(rows) > client_import.MAX_BYTES:
            frappe.throw(_("client_import.file_too_large"))
        try:
            rows = json.loads(rows)
        except ValueError:
            frappe.throw(_("The import rows are invalid."))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        frappe.throw(_("The import rows are invalid."))
    if len(rows) > client_import.MAX_ROWS:
        frappe.throw(_("client_import.too_many_rows"))
    result = []
    for index, row in enumerate(rows):
        if set(row) - {"row_index", *client_import.IMPORT_FIELDS}:
            frappe.throw(_("The import rows are invalid."))
        number = row.get("row_index", index)
        if type(number) is not int or number < 0 or number > 100_000:
            frappe.throw(_("The import rows are invalid."))
        if any(row.get(field) is not None and not isinstance(row[field], str) for field in client_import.IMPORT_FIELDS):
            frappe.throw(_("The import rows are invalid."))
        result.append({"row_index": number, **{field: (row.get(field) or "").strip() for field in client_import.IMPORT_FIELDS}})
    if len(_json(result).encode("utf-8")) > client_import.MAX_BYTES:
        frappe.throw(_("client_import.file_too_large"))
    return result


def _judge(row, *, dry_run):
    base = {field: row[field] for field in ("row_index", "name", "tax_id")}
    try:
        clean = workspace_fields.import_values(row)
    except workspace_fields.WorkspaceInputError as error:
        return {**base, "status": "error", "reason": str(error)}
    if workspaces.tax_taken(clean["tax_id"], current=not dry_run):
        return {**base, "status": "skip", "reason": "workspace.tax_id_duplicate"}
    if dry_run:
        return {**base, "status": "valid", "id": None}
    frappe.db.savepoint("cowork_workspace_import_row")
    try:
        created = workspaces.create(**clean)
    except (frappe.PermissionError, frappe.QueryDeadlockError):
        raise
    except frappe.ValidationError as error:
        frappe.db.rollback(save_point="cowork_workspace_import_row")
        duplicate = isinstance(error, workspaces.DuplicateWorkspaceTax)
        return {**base, "status": "skip" if duplicate else "error",
            "reason": "workspace.tax_id_duplicate" if duplicate else "client_import.err_generic"}
    return {**base, "status": "created", "id": created["name"]}


def preview(rows):
    workspaces.require_manager()
    results = [_judge(row, dry_run=True) for row in rows_input(rows)]
    return {"preview": results, "total": len(results),
        **{state + "_count": sum(row["status"] == state for row in results) for state in ("valid", "skip", "error")}}


def commit(rows, request_key):
    workspaces.require_manager()
    rows = rows_input(rows)
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
        frappe.throw(_("The import rows are invalid."))
    key = hashlib.sha256(_json([frappe.session.user, request_key]).encode()).hexdigest()
    fingerprint = hashlib.sha256(_json(rows).encode()).hexdigest()
    workspaces.lock()
    existing = frappe.db.get_value(DOCTYPE, key, ["request_fingerprint", "result"], as_dict=True, for_update=True)
    if existing:
        if existing.request_fingerprint != fingerprint:
            frappe.throw(_("connection.revision_conflict"))
        return {**json.loads(existing.result), "replayed": True}
    results = [_judge(row, dry_run=False) for row in rows]
    result = {"results": results, "total": len(results), "replayed": False,
        "created": sum(row["status"] == "created" for row in results),
        "skipped": sum(row["status"] == "skip" for row in results),
        "errors": sum(row["status"] == "error" for row in results)}
    doc = frappe.get_doc({"doctype": DOCTYPE, "request_id": key,
        "request_fingerprint": fingerprint, "result": _json(result)})
    doc.flags.cowork_workspace_import = _WRITE
    doc.flags.notify_update = False
    doc.insert(ignore_permissions=True)
    return result
