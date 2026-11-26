"""Workspace import RPCs, never aliases for buyer import. No new UI."""
import frappe
from frappe import _

from . import client_import, workspace_import, workspaces
from pearnly_core.transaction import conflicts


@frappe.whitelist(methods=["POST"])
def preview_workspace_import(rows):
    return workspace_import.preview(rows)


@frappe.whitelist(methods=["POST"])
def parse_workspace_import():
    workspaces.require_manager()
    from .workspace_table import parse
    incoming = frappe.request.files.get("file") if frappe.request else None
    if not incoming:
        frappe.throw(_("Select a file to upload."))
    content = incoming.stream.read(client_import.MAX_BYTES + 1)
    try:
        parsed = parse(content, incoming.filename or "")
    except ValueError as error:
        frappe.throw(_(str(error)))
    return {**parsed, **workspace_import.preview(parsed["rows"])}


@frappe.whitelist(methods=["POST"])
def commit_workspace_import(rows, request_key):
    # Standalone local transaction only: internal callers use the service.
    return conflicts.attempt(workspace_import.commit, rows, request_key)  # 两种冲突都整笔重来，用尽回「请稍后重试」（B ①）
